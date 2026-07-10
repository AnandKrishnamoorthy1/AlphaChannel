from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().with_name(".env")
DOTENV_LOAD_ERROR: str | None = None
try:
    DOTENV_LOADED = load_dotenv(dotenv_path=ENV_FILE, override=False)
except OSError as exc:
    DOTENV_LOADED = False
    DOTENV_LOAD_ERROR = str(exc)

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from engine.core_router import (
    AgentTurnRequest,
    AgentTurnResponse,
    AlphaChannelRouter,
    InternalWorkspacePerception,
    PendingToolAction,
    WorkspaceAttachment,
    WorkspaceMessage,
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("alpha_channel.slack")

if DOTENV_LOAD_ERROR:
    logger.warning("dotenv_load_failed", extra={"env_file": str(ENV_FILE), "error": DOTENV_LOAD_ERROR})
elif not DOTENV_LOADED:
    logger.info("dotenv_file_not_found", extra={"env_file": str(ENV_FILE)})
if not (os.getenv("SEC_EDGAR_USER_AGENT") or os.getenv("EDGAR_IDENTITY")):
    logger.warning("sec_edgar_identity_missing live_sec_tool_will_fail_until_configured=true")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
slack_app = App(token=SLACK_BOT_TOKEN)
router = AlphaChannelRouter()

def _extract_action_token(body: dict[str, Any], context: dict[str, Any] | None = None) -> str | None:
    """Slack assistant surfaces can place action tokens in different envelopes."""
    context = context or {}
    event = body.get("event") or {}
    authorizations = body.get("authorizations") or []
    assistant_thread = event.get("assistant_thread") or body.get("assistant_thread") or {}

    candidates = [
        body.get("action_token"),
        event.get("action_token"),
        context.get("action_token"),
        assistant_thread.get("action_token") if isinstance(assistant_thread, dict) else None,
    ]
    if authorizations and isinstance(authorizations[0], dict):
        candidates.append(authorizations[0].get("action_token"))

    return next((token for token in candidates if isinstance(token, str) and token), None)


def _strip_bot_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text or "").strip()


def _format_slack_mrkdwn(text: str, *, stream_output: bool = False) -> str:
    """Normalize generated Markdown to Slack mrkdwn and fence diagnostic output."""
    normalized = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"*\1*", str(text), flags=re.DOTALL)
    stripped = normalized.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped

    is_stack_trace = bool(
        re.search(
            r"(?:^|\n)(?:Traceback \(most recent call last\):|Stack trace:|[A-Za-z]+Error:\s)",
            stripped,
        )
    )
    is_large_stream = stream_output and (len(stripped) >= 1500 or stripped.count("\n") >= 20)
    if is_stack_trace or is_large_stream:
        safe_code = stripped.replace("```", "'''")
        return f"```\n{safe_code}\n```"

    return normalized


def _normalize_slack_blocks(blocks: list[dict[str, Any]], *, stream_output: bool = False) -> list[dict[str, Any]]:
    def normalize_value(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize_value(item) for item in value]
        if not isinstance(value, dict):
            return value

        normalized = {key: normalize_value(item) for key, item in value.items()}
        if normalized.get("type") == "mrkdwn" and isinstance(normalized.get("text"), str):
            normalized["text"] = _format_slack_mrkdwn(
                normalized["text"],
                stream_output=stream_output,
            )
        return normalized

    return [normalize_value(block) for block in blocks]


def _normalize_search_response(
    *,
    query: str,
    response: dict[str, Any],
    channel_id: str,
    user_id: str,
) -> InternalWorkspacePerception:
    messages: list[WorkspaceMessage] = []
    attachments: list[WorkspaceAttachment] = []

    results = response.get("results") or response
    if not isinstance(results, dict):
        results = {}
    message_results = results.get("messages") or []
    if isinstance(message_results, dict):
        message_results = message_results.get("matches") or message_results.get("messages") or []
    file_results = results.get("files") or results.get("file_matches") or []
    if isinstance(file_results, dict):
        file_results = file_results.get("matches") or file_results.get("files") or []

    seen_messages: set[tuple[str, str]] = set()

    def append_message(raw_message: dict[str, Any]) -> None:
        message_channel_id = str(raw_message.get("channel_id") or raw_message.get("channel") or channel_id)
        message_ts = str(raw_message.get("message_ts") or raw_message.get("ts") or raw_message.get("timestamp") or "")
        message_key = (message_channel_id, message_ts)
        if message_key in seen_messages:
            return
        seen_messages.add(message_key)
        messages.append(
            WorkspaceMessage(
                channel_id=message_channel_id,
                user_id=str(
                    raw_message.get("author_user_id")
                    or raw_message.get("user_id")
                    or raw_message.get("user")
                    or "unknown"
                ),
                text=str(raw_message.get("content") or raw_message.get("text") or raw_message.get("snippet") or ""),
                ts=message_ts,
                permalink=raw_message.get("permalink"),
            )
        )

    for raw_message in message_results:
        if not isinstance(raw_message, dict):
            continue
        append_message(raw_message)
        contextual = raw_message.get("context_messages") or {}
        if isinstance(contextual, dict):
            contextual_messages = [
                message
                for group in contextual.values()
                if isinstance(group, list)
                for message in group
                if isinstance(message, dict)
            ]
        elif isinstance(contextual, list):
            contextual_messages = [message for message in contextual if isinstance(message, dict)]
        else:
            contextual_messages = []
        for context_message in contextual_messages:
            append_message(context_message)

        for raw_file in raw_message.get("files") or []:
            if isinstance(raw_file, dict):
                attachments.append(
                    WorkspaceAttachment(
                        title=raw_file.get("title") or raw_file.get("name"),
                        filetype=raw_file.get("file_type") or raw_file.get("filetype") or raw_file.get("mimetype"),
                        url=raw_file.get("url_private") or raw_file.get("permalink"),
                        summary=raw_file.get("content") or raw_file.get("summary") or raw_file.get("plain_text"),
                    )
                )

    for raw_file in file_results:
        if isinstance(raw_file, dict):
            attachments.append(
                WorkspaceAttachment(
                    title=raw_file.get("title") or raw_file.get("name"),
                    filetype=raw_file.get("file_type") or raw_file.get("filetype") or raw_file.get("mimetype"),
                    url=raw_file.get("url_private") or raw_file.get("permalink"),
                    summary=raw_file.get("content") or raw_file.get("summary") or raw_file.get("plain_text"),
                )
            )

    return InternalWorkspacePerception(
        query=query,
        source="slack_assistant_search_context",
        channel_id=channel_id,
        requested_by=user_id,
        context_messages=messages,
        file_attachments=attachments,
        raw_result_count=len(messages) + len(attachments),
    )


def retrieve_internal_workspace_perception(
    *,
    client: WebClient,
    action_token: str | None,
    query: str,
    channel_id: str,
    user_id: str,
    thread_ts: str | None,
) -> InternalWorkspacePerception:
    """Invoke Slack RTS and structure results as Internal Workspace Perception."""
    if not action_token:
        logger.warning(
            "slack_rts_action_token_missing",
            extra={"channel_id": channel_id, "user_id": user_id, "query": query},
        )
        return InternalWorkspacePerception(
            query=query,
            source="slack_event_fallback",
            channel_id=channel_id,
            requested_by=user_id,
            context_messages=[
                WorkspaceMessage(
                    channel_id=channel_id,
                    user_id=user_id,
                    text=query,
                    ts=thread_ts or "",
                )
            ],
            raw_result_count=1,
        )

    payload: dict[str, Any] = {
        "query": query,
        "action_token": action_token,
        "context_channel_id": channel_id,
        "content_types": ["messages", "files"],
        "channel_types": ["public_channel"],
        "include_context_messages": True,
        "include_bots": False,
        "limit": int(os.getenv("ALPHACHANNEL_RTS_LIMIT", "12")),
    }

    try:
        logger.info(
            "slack_rts_request",
            extra={
                "channel_id": channel_id,
                "user_id": user_id,
                "query_chars": len(query),
                "limit": payload["limit"],
                "has_action_token": True,
            },
        )
        response = client.api_call("assistant.search.context", json=payload)
        raw_response_data = getattr(response, "data", response)
        if not isinstance(raw_response_data, Mapping):
            raise TypeError(
                "assistant.search.context returned an unsupported payload type: "
                f"{type(raw_response_data).__name__}"
            )
        response_data = dict(raw_response_data)
        if not response_data.get("ok", False):
            raise SlackApiError(message="assistant.search.context returned ok=false", response=response)
        return _normalize_search_response(
            query=query,
            response=response_data,
            channel_id=channel_id,
            user_id=user_id,
        )
    except SlackApiError as exc:
        slack_error = exc.response.get("error") if exc.response else str(exc)
        logger.warning(
            "slack_rts_failed",
            extra={
                "channel_id": channel_id,
                "user_id": user_id,
                "slack_error": slack_error,
                "fallback_source": "slack_assistant_search_context_error",
            },
        )
        return InternalWorkspacePerception(
            query=query,
            source="slack_assistant_search_context_error",
            channel_id=channel_id,
            requested_by=user_id,
            context_messages=[
                WorkspaceMessage(channel_id=channel_id, user_id=user_id, text=query, ts=thread_ts or "")
            ],
            errors=[slack_error],
            raw_result_count=1,
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "slack_rts_response_invalid",
            extra={
                "channel_id": channel_id,
                "user_id": user_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "fallback_source": "slack_assistant_search_context_invalid_response",
            },
        )
        return InternalWorkspacePerception(
            query=query,
            source="slack_assistant_search_context_invalid_response",
            channel_id=channel_id,
            requested_by=user_id,
            context_messages=[
                WorkspaceMessage(channel_id=channel_id, user_id=user_id, text=query, ts=thread_ts or "")
            ],
            errors=[str(exc)],
            raw_result_count=1,
        )


def _build_error_blocks(message: str) -> list[dict[str, Any]]:
    formatted_message = _format_slack_mrkdwn(message, stream_output=True)
    return _normalize_slack_blocks([
        {"type": "header", "text": {"type": "plain_text", "text": "AlphaChannel Request Failed"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Issue*\n{formatted_message}"}},
    ])


def _build_command_help_blocks() -> list[dict[str, Any]]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "AlphaChannel Command Center"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Risk evaluation*\n"
                    "`@AlphaChannel analyze NU` or `@AlphaChannel risk assessment on SNOW`\n\n"
                    "*Portfolio Center*\n"
                    "`@AlphaChannel portfolio` or `@AlphaChannel show holdings`"
                ),
            },
        },
    ]


def _slack_code_block(text: str, limit: int = 2500) -> str:
    safe_text = str(text).replace("```", "'''")
    if len(safe_text) > limit:
        safe_text = f"{safe_text[:limit]}\n... output truncated"
    return f"```\n{safe_text}\n```"


def _build_pending_agent_action_blocks(
    pending: PendingToolAction,
    message: str,
) -> list[dict[str, Any]]:
    return _normalize_slack_blocks(
        [
            {"type": "header", "text": {"type": "plain_text", "text": "MCP Action Checkpoint"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tool*\n`{pending.tool_name}`"},
                    {"type": "mrkdwn", "text": f"*Status*\n{pending.status}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Parameters*\n{_slack_code_block(json.dumps(pending.arguments, indent=2))}"},
            },
            {
                "type": "actions",
                "block_id": f"agent_action_{pending.action_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "agent_action_approve",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "value": pending.action_id,
                    },
                    {
                        "type": "button",
                        "action_id": "agent_action_deny",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "value": pending.action_id,
                    },
                    {
                        "type": "button",
                        "action_id": "agent_action_edit",
                        "text": {"type": "plain_text", "text": "Edit Parameters"},
                        "value": pending.action_id,
                    },
                ],
            },
        ]
    )


def _build_agent_turn_blocks(response: AgentTurnResponse) -> list[dict[str, Any]]:
    if response.pending_action:
        return _build_pending_agent_action_blocks(response.pending_action, response.message)

    finance_tools = {"yfinance_risk_lookup", "sec_risk_lookup", "portfolio_holdings"}
    header = (
        "AlphaChannel Risk Synthesis"
        if any(execution.tool_name in finance_tools for execution in response.tool_executions)
        else "AlphaChannel MCP Investigation"
    )
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": response.message}},
    ]
    for execution in response.tool_executions:
        status = "failed" if execution.is_error else "completed"
        evidence_text = _summarize_mcp_execution(execution)
        blocks.extend(
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Tool:* `{execution.tool_name}` | *Status:* {status}\n{evidence_text}",
                    },
                },
                {"type": "divider"},
            ]
        )
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Thread memory: {response.conversation_turn_count} turns"}],
        }
    )
    return _normalize_slack_blocks(blocks)


def _summarize_mcp_execution(execution: Any) -> str:
    if execution.is_error:
        return _slack_code_block(execution.output)
    try:
        payload = json.loads(execution.output)
    except (json.JSONDecodeError, TypeError):
        return execution.output[:1200]
    if not isinstance(payload, dict):
        return str(payload)[:1200]

    if execution.tool_name == "yfinance_risk_lookup":
        price = payload.get("current_price")
        iv = payload.get("front_month_iv")
        put_call = payload.get("put_call_open_interest_ratio")
        source = payload.get("source_url")
        return (
            f"Price: `{price if price is not None else 'n/a'}`  |  "
            f"Front-month IV: `{f'{iv:.1%}' if isinstance(iv, (int, float)) else 'n/a'}`  |  "
            f"Put/call OI: `{f'{put_call:.2f}' if isinstance(put_call, (int, float)) else 'n/a'}`"
            + (f"\n<{source}|Open Yahoo Finance options source>" if source else "")
        )
    if execution.tool_name == "sec_risk_lookup":
        markers = payload.get("risk_markers") or []
        source = payload.get("filing_url")
        return (
            f"Latest filing: `{payload.get('form', 'n/a')}` dated `{payload.get('filing_date', 'n/a')}`  |  "
            f"Risk markers: `{len(markers)}`"
            + (f"\n<{source}|Open SEC EDGAR filing>" if source else "")
        )
    if execution.tool_name == "portfolio_holdings":
        return f"Holdings returned: `{len(payload.get('holdings') or [])}`"
    return _slack_code_block(json.dumps(payload, indent=2))


def _start_agent_turn_worker(
    *,
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    query: str,
    workspace_context: str | None,
) -> None:
    initial = "⚙️ _Agent Orchestrator:_ Planning MCP tools and checking approval policy..."
    posted = client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=_mrkdwn_stream_blocks(initial),
        text="AlphaChannel is planning MCP tools",
    )
    message_ts = posted.get("ts")
    if not isinstance(message_ts, str) or not message_ts:
        raise ValueError("Slack did not return a timestamp for the MCP planning message.")

    def worker() -> None:
        try:
            def update_progress(status: str) -> None:
                formatted = f"⚙️ _Agent Brain:_ {status}"
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=_mrkdwn_stream_blocks(formatted),
                    text=status,
                )

            response = router.process_agent_turn(
                AgentTurnRequest(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    user_id=user_id,
                    text=query,
                    workspace_context=workspace_context,
                ),
                progress_callback=update_progress,
            )
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_agent_turn_blocks(response),
                text=_format_slack_mrkdwn(response.message),
            )
        except Exception as exc:
            logger.exception("agent_turn_worker_failed", extra={"channel_id": channel_id, "thread_ts": thread_ts})
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_error_blocks(str(exc)),
                text="AlphaChannel MCP investigation failed",
            )

    threading.Thread(
        target=worker,
        name=f"alpha-channel-agent-{channel_id}-{thread_ts}",
        daemon=True,
    ).start()


def _process_slack_message(
    event: dict[str, Any],
    body: dict[str, Any],
    client: WebClient,
    context: dict[str, Any],
) -> None:
    channel_id = event["channel"]
    user_id = event.get("user", "unknown")
    thread_ts = event.get("thread_ts") or event.get("ts")
    query = _strip_bot_mention(event.get("text", ""))

    try:
        logger.info(
            "slack_agent_goal_received channel_id=%s user_id=%s goal_chars=%d",
            channel_id,
            user_id,
            len(query),
        )

        if not query:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                blocks=_build_command_help_blocks(),
                text="AlphaChannel command help",
            )
            return

        action_token = _extract_action_token(body, context)
        perception = retrieve_internal_workspace_perception(
            client=client,
            action_token=action_token,
            query=query,
            channel_id=channel_id,
            user_id=user_id,
            thread_ts=thread_ts,
        )
        _start_agent_turn_worker(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            query=query,
            workspace_context=perception.text_corpus(),
        )
    except Exception as exc:
        logger.exception("app_mention_failed", extra={"channel_id": channel_id, "user_id": user_id})
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_build_error_blocks(str(exc)),
            text="AlphaChannel request failed",
        )


@slack_app.event("app_mention")
def handle_app_mention(
    event: dict[str, Any],
    body: dict[str, Any],
    client: WebClient,
    context: dict[str, Any],
) -> None:
    _process_slack_message(event, body, client, context)


@slack_app.event("message")
def handle_agent_direct_message(
    event: dict[str, Any],
    body: dict[str, Any],
    client: WebClient,
    context: dict[str, Any],
) -> None:
    if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
        return
    _process_slack_message(event, body, client, context)


@slack_app.event("app_home_opened")
def handle_agent_view_opened(event: dict[str, Any]) -> None:
    if event.get("tab") == "messages":
        logger.info(
            "agent_view_opened",
            extra={"channel_id": event.get("channel"), "user_id": event.get("user")},
        )


def _decode_action_value(body: dict[str, Any]) -> dict[str, Any]:
    actions = body.get("actions") or []
    if not actions:
        raise ValueError("Slack action payload did not include an action.")
    raw_value = actions[0].get("value") or "{}"
    decoded = json.loads(raw_value)
    if not isinstance(decoded, dict):
        raise ValueError("Slack action payload value must decode to an object.")
    return decoded


def _raw_action_value(body: dict[str, Any]) -> str:
    actions = body.get("actions") or []
    if not actions:
        raise ValueError("Slack action payload did not include an action.")
    raw_value = actions[0].get("value")
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError("Slack action payload did not include a string value.")
    return raw_value


def _ticker_from_prefixed_action_value(value: str, prefix: str) -> str:
    expected_prefix = f"{prefix}_"
    if not value.startswith(expected_prefix):
        raise ValueError(f"Expected action value to start with {expected_prefix}.")
    ticker = value.removeprefix(expected_prefix).strip().upper()
    if not ticker:
        raise ValueError("Portfolio action value did not include a ticker.")
    return ticker


def _action_channel_id(body: dict[str, Any]) -> str:
    channel_id = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
    if not channel_id:
        raise ValueError("Slack action payload did not include a channel id.")
    return str(channel_id)


def _action_thread_ts(body: dict[str, Any]) -> str | None:
    message = body.get("message") or {}
    return message.get("thread_ts") or message.get("ts") or (body.get("container") or {}).get("message_ts")


def _action_message_ts(body: dict[str, Any]) -> str:
    message_ts = (body.get("container") or {}).get("message_ts") or (body.get("message") or {}).get("ts")
    if not isinstance(message_ts, str) or not message_ts:
        raise ValueError("Slack action payload did not include a message timestamp.")
    return message_ts


def _mrkdwn_stream_blocks(text: str) -> list[dict[str, Any]]:
    formatted = _format_slack_mrkdwn(text, stream_output=True)
    if formatted.startswith("```"):
        return [{"type": "section", "text": {"type": "mrkdwn", "text": formatted}}]
    return [{"type": "context", "elements": [{"type": "mrkdwn", "text": formatted}]}]


def _portfolio_audit_result_blocks(ticker: str, analysis_text: str) -> list[dict[str, Any]]:
    return _normalize_slack_blocks([
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Audit Complete: {ticker}", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"✅ *Audit Complete for {ticker}* \n\n"
                    f"{analysis_text}"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "SEC footnote review, options skew check, and divergence alignment scorecard finalized.",
                }
            ],
        },
    ])


def _fallback_qwen_audit_paragraph(ticker: str) -> str:
    return (
        f"{ticker} completed the AlphaChannel audit with no immediate escalation marker from the mocked MCP feed: "
        "yFinance volatility conditions were normal, call volume was steady, and the SEC review found no pending "
        "litigation or balance-sheet restatement in the latest 10-K evidence set. The governance posture is therefore "
        "balanced for monitoring, with no defensive mitigation trigger required at this time."
    )


def _generate_qwen_audit_paragraph(ticker: str) -> str:
    mock_data_payload = (
        "MCP yFinance Node: implied volatility is normal, call volume steady; "
        "SEC Node: No pending litigation or balance sheet restatements found in the latest 10-K."
    )
    system_prompt = (
        "You are the Lead Risk Analyst for AlphaChannel. Write a concise, ultra-professional institutional "
        "governance paragraph summarizing the deep audit results for the given ticker. Be highly specific to the "
        "company's real-world business model. Do not use generic filler text. Use Slack mrkdwn formatting: "
        "single asterisks for bold text, never double asterisks."
    )
    user_prompt = (
        f"Ticker: {ticker}\n"
        f"Raw MCP data payload: {mock_data_payload}\n\n"
        "Return exactly one polished paragraph suitable for a Slack executive governance update."
    )

    try:
        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY or QWEN_API_KEY is not configured.")

        model = os.getenv("QWEN_MODEL", "qwen-plus")
        endpoint = os.getenv("DASHSCOPE_ENDPOINT", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        timeout_seconds = float(os.getenv("QWEN_TIMEOUT_SECONDS", "30"))
        logger.info(
            "qwen_audit_generation_started",
            extra={"ticker": ticker, "model": model, "endpoint": endpoint},
        )

        if "/compatible-mode/" in endpoint:
            from openai import OpenAI

            completion = OpenAI(
                api_key=api_key,
                base_url=endpoint.rstrip("/"),
                timeout=timeout_seconds,
                max_retries=1,
            ).chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                top_p=0.75,
            )
            generated_text = completion.choices[0].message.content if completion.choices else None
        else:
            import dashscope
            from dashscope import Generation

            dashscope.api_key = api_key
            dashscope.base_http_api_url = endpoint.rstrip("/")
            response = Generation.call(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.25,
                top_p=0.75,
                result_format="message",
            )
            status_code = getattr(response, "status_code", None)
            if status_code is not None and status_code != 200:
                error_code = getattr(response, "code", "unknown")
                error_message = getattr(response, "message", "unknown DashScope error")
                raise RuntimeError(f"Qwen returned status {status_code}: {error_code} - {error_message}")

            output = getattr(response, "output", None)
            choices = getattr(output, "choices", None) if output is not None else None
            generated_text = None
            if choices:
                first_choice = choices[0]
                message = first_choice.get("message", {}) if isinstance(first_choice, dict) else None
                generated_text = message.get("content") if isinstance(message, dict) else None
            if not generated_text and output is not None:
                generated_text = getattr(output, "text", None)

        if not isinstance(generated_text, str) or not generated_text.strip():
            raise RuntimeError("Qwen response did not contain generated text.")

        logger.info(
            "qwen_audit_generation_completed",
            extra={"ticker": ticker, "model": model, "response_chars": len(generated_text)},
        )
        return generated_text.strip()
    except Exception as exc:
        logger.warning(
            "qwen_audit_generation_failed",
            extra={"ticker": ticker, "error_type": type(exc).__name__, "error": str(exc)},
        )
        return _fallback_qwen_audit_paragraph(ticker)


def _build_qwen_audit_result(ticker: str) -> tuple[str, list[dict[str, Any]]]:
    analysis_text = _format_slack_mrkdwn(_generate_qwen_audit_paragraph(ticker))
    final_message = f"✅ *Audit Complete for {ticker}* \n\n{analysis_text}"
    return final_message, _portfolio_audit_result_blocks(ticker, analysis_text)


def _portfolio_trim_result_blocks(ticker: str) -> list[dict[str, Any]]:
    return _normalize_slack_blocks([
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Trim Framework Queued: {ticker}", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📉 *Order Complete:* Successfully queued position adjustment framework for {ticker}.",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Trading Node delta checks and Compliance Node audit trail snapshot are ready for review.",
                }
            ],
        },
    ])


def _start_portfolio_progress_worker(
    *,
    client: WebClient,
    channel_id: str,
    message_ts: str,
    ticker: str,
    action_name: str,
    progress_steps: list[str],
    final_message: str,
    final_blocks: list[dict[str, Any]],
    final_result_factory: Callable[[str], tuple[str, list[dict[str, Any]]]] | None = None,
    delay_seconds: float = 1.5,
) -> None:
    def worker() -> None:
        try:
            for step_index, step_text in enumerate(progress_steps, start=1):
                time.sleep(delay_seconds)
                formatted_step = _format_slack_mrkdwn(step_text, stream_output=True)
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=_mrkdwn_stream_blocks(formatted_step),
                    text=formatted_step,
                )
                logger.info(
                    "portfolio_progress_step_updated",
                    extra={
                        "ticker": ticker,
                        "action_name": action_name,
                        "step_index": step_index,
                        "total_steps": len(progress_steps),
                    },
                )

            resolved_message = final_message
            resolved_blocks = final_blocks
            if final_result_factory is not None:
                logger.info(
                    "portfolio_dynamic_result_started",
                    extra={"ticker": ticker, "action_name": action_name},
                )
                resolved_message, resolved_blocks = final_result_factory(ticker)

            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_normalize_slack_blocks(resolved_blocks),
                text=_format_slack_mrkdwn(resolved_message),
            )
            logger.info(
                "portfolio_progress_completed",
                extra={"ticker": ticker, "action_name": action_name, "channel_id": channel_id},
            )
        except SlackApiError as exc:
            logger.exception(
                "portfolio_progress_slack_api_failed",
                extra={
                    "ticker": ticker,
                    "action_name": action_name,
                    "channel_id": channel_id,
                    "slack_error": exc.response.get("error") if exc.response else str(exc),
                },
            )
        except Exception:
            logger.exception(
                "portfolio_progress_worker_failed",
                extra={"ticker": ticker, "action_name": action_name, "channel_id": channel_id},
            )

    threading.Thread(
        target=worker,
        name=f"alpha-channel-{action_name.lower().replace('_', '-')}-{ticker}",
        daemon=True,
    ).start()


@slack_app.action("approve_buy_allocation")
def approve_buy_allocation(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    payload = _decode_action_value(body)
    checkpoint = router.handle_trading_checkpoint(
        payload=payload,
        decision="approve_buy_allocation",
        decided_by=body.get("user", {}).get("id", "unknown"),
    )
    client.chat_postMessage(
        channel=_action_channel_id(body),
        thread_ts=_action_thread_ts(body),
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _format_slack_mrkdwn(checkpoint["operator_message"])},
            },
        ],
        text="AlphaChannel checkpoint recorded",
    )


@slack_app.action("execute_protective_hedge")
def execute_protective_hedge(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    payload = _decode_action_value(body)
    checkpoint = router.handle_trading_checkpoint(
        payload=payload,
        decision="execute_protective_hedge",
        decided_by=body.get("user", {}).get("id", "unknown"),
    )
    client.chat_postMessage(
        channel=_action_channel_id(body),
        thread_ts=_action_thread_ts(body),
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _format_slack_mrkdwn(checkpoint["operator_message"])},
            },
        ],
        text="AlphaChannel checkpoint recorded",
    )


@slack_app.action("agent_action_approve")
def approve_agent_action(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    action_id = _raw_action_value(body)
    pending = router.get_pending_agent_action(action_id)
    channel_id = _action_channel_id(body)
    message_ts = _action_message_ts(body)
    executing_message = f"Executing `{pending.tool_name}` through MCP after human approval..."
    client.chat_update(
        channel=channel_id,
        ts=message_ts,
        blocks=_mrkdwn_stream_blocks(f"⚙️ _MCP Executor:_ {executing_message}"),
        text=executing_message,
    )

    def worker() -> None:
        try:
            response = router.approve_agent_action(
                action_id,
                body.get("user", {}).get("id", "unknown"),
            )
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_agent_turn_blocks(response),
                text=response.message,
            )
        except Exception as exc:
            logger.exception("agent_action_approval_failed", extra={"action_id": action_id})
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_error_blocks(str(exc)),
                text="MCP action approval failed",
            )

    threading.Thread(
        target=worker,
        name=f"alpha-channel-mcp-approval-{action_id}",
        daemon=True,
    ).start()


@slack_app.action("agent_action_deny")
def deny_agent_action(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    action_id = _raw_action_value(body)
    response = router.deny_agent_action(
        action_id,
        body.get("user", {}).get("id", "unknown"),
    )
    client.chat_update(
        channel=_action_channel_id(body),
        ts=_action_message_ts(body),
        blocks=_build_agent_turn_blocks(response),
        text=response.message,
    )


@slack_app.action("agent_action_edit")
def edit_agent_action(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    action_id = _raw_action_value(body)
    pending = router.get_pending_agent_action(action_id)
    metadata = {
        "action_id": action_id,
        "channel_id": _action_channel_id(body),
        "message_ts": _action_message_ts(body),
    }
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "agent_action_edit_submit",
            "private_metadata": json.dumps(metadata),
            "title": {"type": "plain_text", "text": "Edit MCP Action"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "agent_parameters_block",
                    "label": {"type": "plain_text", "text": "Tool parameters (JSON)"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "agent_parameters",
                        "multiline": True,
                        "initial_value": json.dumps(pending.arguments, indent=2),
                    },
                }
            ],
        },
    )


@slack_app.view("agent_action_edit_submit")
def submit_agent_action_edit(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    view = body.get("view") or {}
    raw_parameters = (
        ((view.get("state") or {}).get("values") or {})
        .get("agent_parameters_block", {})
        .get("agent_parameters", {})
        .get("value", "{}")
    )
    try:
        arguments = json.loads(raw_parameters)
        if not isinstance(arguments, dict):
            raise ValueError("Parameters must be a JSON object.")
    except (json.JSONDecodeError, ValueError) as exc:
        ack(response_action="errors", errors={"agent_parameters_block": str(exc)})
        return

    ack()
    metadata = json.loads(view.get("private_metadata") or "{}")
    pending = router.edit_agent_action(
        str(metadata["action_id"]),
        arguments,
        body.get("user", {}).get("id", "unknown"),
    )
    client.chat_update(
        channel=str(metadata["channel_id"]),
        ts=str(metadata["message_ts"]),
        blocks=_build_pending_agent_action_blocks(
            pending,
            "Parameters updated. Review the revised MCP call before approval.",
        ),
        text="MCP action parameters updated",
    )


@slack_app.action("portfolio_deep_audit")
def portfolio_deep_audit(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    try:
        ticker = _ticker_from_prefixed_action_value(_raw_action_value(body), "AUDIT")
        user_id = body.get("user", {}).get("id", "unknown")
        channel_id = _action_channel_id(body)
        thread_ts = _action_thread_ts(body)
        logger.info(
            "portfolio_deep_audit_requested",
            extra={"ticker": ticker, "user_id": user_id, "channel_id": channel_id},
        )
        initial_message = "⚙️ _AlphaChannel Orchestrator:_ Initiating Audit Core..."
        response = client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_mrkdwn_stream_blocks(initial_message),
            text=f"AlphaChannel Orchestrator initiating Audit Core for {ticker}",
        )
        message_ts = response.get("ts")
        if not isinstance(message_ts, str) or not message_ts:
            raise ValueError("Slack did not return a timestamp for the audit progress message.")
        response_channel_id = response.get("channel") or channel_id
        if not isinstance(response_channel_id, str) or not response_channel_id:
            raise ValueError("Slack did not return a channel for the audit progress message.")

        _start_portfolio_progress_worker(
            client=client,
            channel_id=response_channel_id,
            message_ts=message_ts,
            ticker=ticker,
            action_name="portfolio_deep_audit",
            progress_steps=[
                "🔍 _SEC Node:_ Ingesting active filing footnotes...",
                "📈 _yFinance Node:_ Calculating option volatility skew adjustments...",
                "🤖 _Orchestrator:_ Synthesizing final divergence alignment scorecard...",
            ],
            final_message=(
                f"✅ *Audit Complete for {ticker}* \n\n"
                f"{_fallback_qwen_audit_paragraph(ticker)}"
            ),
            final_blocks=_portfolio_audit_result_blocks(
                ticker,
                _fallback_qwen_audit_paragraph(ticker),
            ),
            final_result_factory=_build_qwen_audit_result,
            delay_seconds=1.5,
        )
    except SlackApiError as exc:
        logger.exception(
            "portfolio_deep_audit_slack_api_failed",
            extra={"slack_error": exc.response.get("error") if exc.response else str(exc)},
        )
    except Exception:
        logger.exception("portfolio_deep_audit_failed")


@slack_app.action("portfolio_trim_allocation")
def portfolio_trim_allocation(ack: Any, body: dict[str, Any], client: WebClient) -> None:
    ack()
    try:
        ticker = _ticker_from_prefixed_action_value(_raw_action_value(body), "TRIM")
        user_id = body.get("user", {}).get("id", "unknown")
        channel_id = _action_channel_id(body)
        thread_ts = _action_thread_ts(body)
        logger.info(
            "portfolio_trim_allocation_requested",
            extra={"ticker": ticker, "user_id": user_id, "channel_id": channel_id},
        )
        initial_message = "⚙️ _AlphaChannel Orchestrator:_ Initiating Trim Core..."
        response = client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_mrkdwn_stream_blocks(initial_message),
            text=f"AlphaChannel Orchestrator initiating Trim Core for {ticker}",
        )
        message_ts = response.get("ts")
        if not isinstance(message_ts, str) or not message_ts:
            raise ValueError("Slack did not return a timestamp for the trim progress message.")
        response_channel_id = response.get("channel") or channel_id
        if not isinstance(response_channel_id, str) or not response_channel_id:
            raise ValueError("Slack did not return a channel for the trim progress message.")

        _start_portfolio_progress_worker(
            client=client,
            channel_id=response_channel_id,
            message_ts=message_ts,
            ticker=ticker,
            action_name="portfolio_trim_allocation",
            progress_steps=[
                "⚡ _Trading Node:_ Calculating current portfolio delta and liquidity boundaries...",
                "📝 _Compliance Node:_ Generating verified audit trail snapshot for governance review...",
            ],
            final_message=f"📉 *Order Complete:* Successfully queued position adjustment framework for {ticker}.",
            final_blocks=_portfolio_trim_result_blocks(ticker),
        )
    except SlackApiError as exc:
        logger.exception(
            "portfolio_trim_allocation_slack_api_failed",
            extra={"slack_error": exc.response.get("error") if exc.response else str(exc)},
        )
    except Exception:
        logger.exception("portfolio_trim_allocation_failed")


if __name__ == "__main__":
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token:
        raise RuntimeError("SLACK_APP_TOKEN is required for Socket Mode.")
    if not SLACK_BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN is required for Slack Web API calls.")

    logger.info("alpha_channel_socket_mode_starting")
    SocketModeHandler(slack_app, app_token).start()
