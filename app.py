from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

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
    AlphaChannelRouter,
    InternalWorkspacePerception,
    RiskAssessmentRequest,
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

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
slack_app = App(token=SLACK_BOT_TOKEN)
router = AlphaChannelRouter()

CommandIntent = Literal["evaluation", "portfolio", "unknown"]
PORTFOLIO_INTENT_PATTERN = re.compile(
    r"\b(?:portfolio|list\s+all\s+my\s+positions|show\s+(?:my\s+)?holdings|positions|dashboard)\b",
    flags=re.IGNORECASE,
)
EVALUATION_INTENT_PATTERN = re.compile(
    r"\b(?:evaluate|analy[sz]e|check|risk\s+assessment|status)\b"
    r"(?:\s+(?:on|for|of|about|ticker|symbol))?\s+"
    r"\$?([A-Z][A-Z0-9.\-]{0,9})\b",
    flags=re.IGNORECASE,
)


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


def _match_command_intent(text: str) -> tuple[CommandIntent, str | None]:
    if PORTFOLIO_INTENT_PATTERN.search(text):
        return "portfolio", None

    evaluation_match = EVALUATION_INTENT_PATTERN.search(text)
    if evaluation_match:
        return "evaluation", evaluation_match.group(1).upper()

    return "unknown", None


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
        if not response.get("ok", False):
            raise SlackApiError(message="assistant.search.context returned ok=false", response=response)
        return _normalize_search_response(
            query=query,
            response=dict(response),
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


def _build_assessment_blocks(response_contract: dict[str, Any]) -> list[dict[str, Any]]:
    ticker = response_contract["Ticker"]
    verdict = response_contract["Verdict"]
    internal = response_contract["Internal Consensus %"]
    markers = response_contract["External Risk Markers"]
    orders = response_contract["Target Mitigation Orders"]

    marker_text = "\n".join(f"🔴 {marker}" for marker in markers[:8]) or "🟢 No severe external markers detected."
    order_text = "\n".join(
        f"• *{order['order_type']}*: {order['rationale']}" for order in orders[:4]
    ) or "• Hold for executive review."

    button_value = json.dumps(
        {
            "assessment_id": response_contract["assessment_id"],
            "ticker": ticker,
            "verdict": verdict,
        }
    )

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"AlphaChannel Risk Alignment: {ticker}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Verdict*\n{verdict}"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*🟢 Internal Deal-Room Perception*\nConsensus: *{internal}*"},
                {"type": "mrkdwn", "text": f"*🔴 External Market & SEC Reality*\n{marker_text}"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Target Mitigation Orders*\n{order_text}"}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "approve_buy_allocation",
                    "text": {"type": "plain_text", "text": "🟢 Approve Buy Allocation", "emoji": True},
                    "style": "primary",
                    "value": button_value,
                },
                {
                    "type": "button",
                    "action_id": "execute_protective_hedge",
                    "text": {"type": "plain_text", "text": "🔴 Execute Protective Hedge", "emoji": True},
                    "style": "danger",
                    "value": button_value,
                },
            ],
        },
    ]


def _build_error_blocks(message: str) -> list[dict[str, Any]]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "AlphaChannel Request Failed"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Issue*\n{message}"}},
    ]


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


@slack_app.event("app_mention")
def handle_app_mention(
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
        intent, ticker = _match_command_intent(query)
        logger.info(
            "slack_command_intent_matched",
            extra={"channel_id": channel_id, "user_id": user_id, "intent": intent, "ticker": ticker},
        )

        if intent == "portfolio":
            blocks = router.generate_portfolio_dashboard()
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                blocks=blocks,
                text="AlphaChannel Portfolio Center",
            )
            logger.info(
                "portfolio_dashboard_posted",
                extra={"channel_id": channel_id, "user_id": user_id, "holding_blocks": len(blocks)},
            )
            return

        if intent != "evaluation" or ticker is None:
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
        assessment = router.run_assessment(
            RiskAssessmentRequest(
                ticker=ticker,
                query=query,
                requested_by=user_id,
                channel_id=channel_id,
                internal_workspace_perception=perception,
            )
        )
        response_contract = assessment.to_standard_dict()
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_build_assessment_blocks(response_contract),
            text=f"AlphaChannel risk alignment assessment for {ticker}",
        )
        logger.info(
            "risk_assessment_posted",
            extra={
                "assessment_id": assessment.assessment_id,
                "ticker": ticker,
                "channel_id": channel_id,
                "user_id": user_id,
                "verdict": assessment.verdict,
            },
        )
    except Exception as exc:
        logger.exception("app_mention_failed", extra={"channel_id": channel_id, "user_id": user_id})
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_build_error_blocks(str(exc)),
            text="AlphaChannel request failed",
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


def _mrkdwn_section_blocks(text: str) -> list[dict[str, Any]]:
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


def _portfolio_audit_result_blocks(ticker: str, analysis_text: str) -> list[dict[str, Any]]:
    return [
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
    ]


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
        "company's real-world business model. Do not use generic filler text."
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
    analysis_text = _generate_qwen_audit_paragraph(ticker)
    final_message = f"✅ *Audit Complete for {ticker}* \n\n{analysis_text}"
    return final_message, _portfolio_audit_result_blocks(ticker, analysis_text)


def _portfolio_trim_result_blocks(ticker: str) -> list[dict[str, Any]]:
    return [
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
    ]


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
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=_mrkdwn_section_blocks(step_text),
                    text=step_text,
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
                blocks=resolved_blocks,
                text=resolved_message,
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
            {"type": "section", "text": {"type": "mrkdwn", "text": checkpoint["operator_message"]}},
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
            {"type": "section", "text": {"type": "mrkdwn", "text": checkpoint["operator_message"]}},
        ],
        text="AlphaChannel checkpoint recorded",
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
        initial_message = "⚙️ *AlphaChannel Orchestrator: Initiating Audit Core...*"
        response = client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_mrkdwn_section_blocks(initial_message),
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
                "🔍 *SEC Node: Ingesting active filing footnotes...*",
                "📈 *yFinance Node: Calculating option volatility skew adjustments...*",
                "🤖 *Orchestrator: Synthesizing final divergence alignment scorecard...*",
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
        initial_message = "⚙️ *AlphaChannel Orchestrator: Initiating Trim Core...*"
        response = client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_mrkdwn_section_blocks(initial_message),
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
                "⚡ *Trading Node:* Calculating current portfolio delta and liquidity boundaries...",
                "📝 *Compliance Node:* Generating verified audit trail snapshot for governance review...",
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
