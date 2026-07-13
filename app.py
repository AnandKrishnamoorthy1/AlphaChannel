"""AlphaChannel Slack application for the 2026 Slack Hackathon."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
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
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
logger = logging.getLogger("alpha_channel.hackathon_2026.slack")


class FactCheckClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str | None = Field(default=None, min_length=1, max_length=12)
    metric: str | None = Field(default=None, min_length=1, max_length=100)
    claimed_value: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        return value.strip().upper().lstrip("$") if value else None


class FactCheckVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["VERIFIED", "OUTDATED", "DISPROVED", "INCONCLUSIVE"]
    actual_value: str = Field(min_length=1, max_length=200)
    variance: str = Field(min_length=1, max_length=200)
    source_period: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=1000)


class GeminiAuditSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_financial_metrics: list[str] = Field(min_length=1, max_length=3)
    sec_compliance: list[str] = Field(min_length=1, max_length=3)
    strategic_assessment: list[str] = Field(min_length=1, max_length=3)


class AuditMetricPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector: str = Field(min_length=1, max_length=80)
    industry: str = Field(min_length=1, max_length=120)
    node_header: str = Field(min_length=1, max_length=80)
    metrics: list[str] = Field(min_length=3, max_length=3)
    rationale: str = Field(min_length=1, max_length=500)

if DOTENV_LOAD_ERROR:
    logger.warning("dotenv_load_failed", extra={"env_file": str(ENV_FILE), "error": DOTENV_LOAD_ERROR})
elif not DOTENV_LOADED:
    logger.info("dotenv_file_not_found", extra={"env_file": str(ENV_FILE)})
if not (os.getenv("SEC_EDGAR_USER_AGENT") or os.getenv("EDGAR_IDENTITY")):
    logger.warning("sec_edgar_identity_missing live_sec_tool_will_fail_until_configured=true")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
slack_app = App(token=SLACK_BOT_TOKEN)
router = AlphaChannelRouter()
router.mcp_client.warmup_async()

_AUDIT_CHANNEL_NAME = "alphachannel-audit-logs"
_AUDIT_CHANNEL_ID: str | None = None
_AUDIT_CHANNEL_LOCK = threading.RLock()
_AUDIT_REDACTED_KEYS = {"token", "secret", "password", "authorization", "api_key", "cookie"}


def _sanitize_audit_details(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[depth-limited]"
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in list(value.items())[:30]:
            normalized_key = str(key)[:80]
            if any(secret in normalized_key.lower() for secret in _AUDIT_REDACTED_KEYS):
                sanitized[normalized_key] = "[redacted]"
            else:
                sanitized[normalized_key] = _sanitize_audit_details(item, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_audit_details(item, depth=depth + 1) for item in list(value)[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _resolve_audit_channel_id(client: WebClient) -> str:
    global _AUDIT_CHANNEL_ID
    configured_id = str(os.getenv("ALPHACHANNEL_AUDIT_CHANNEL_ID") or "").strip().upper()
    if configured_id:
        if not re.fullmatch(r"[CG][A-Z0-9]{8,}", configured_id):
            raise ValueError("ALPHACHANNEL_AUDIT_CHANNEL_ID is not a valid Slack channel ID.")
        return configured_id

    with _AUDIT_CHANNEL_LOCK:
        if _AUDIT_CHANNEL_ID:
            return _AUDIT_CHANNEL_ID
        cursor: str | None = None
        for _ in range(10):
            response = client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
            channels = response.get("channels") or []
            for channel in channels:
                if isinstance(channel, dict) and channel.get("name") == _AUDIT_CHANNEL_NAME:
                    channel_id = str(channel.get("id") or "")
                    if not re.fullmatch(r"[CG][A-Z0-9]{8,}", channel_id):
                        raise ValueError("Slack returned an invalid audit channel ID.")
                    _AUDIT_CHANNEL_ID = channel_id
                    return channel_id
            cursor = str((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                break
    raise RuntimeError(
        "Compliance channel #alphachannel-audit-logs was not found. Create it, invite AlphaChannel, "
        "or configure ALPHACHANNEL_AUDIT_CHANNEL_ID."
    )


def log_compliance_event(
    action_type: str,
    ticker: str,
    user_id: str,
    details: Mapping[str, Any] | str,
) -> str:
    """Append a sanitized trade-governance event to AlphaChannel's compliance channel."""
    event_id = str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    normalized_ticker = ticker.strip().upper().lstrip("$")
    normalized_action = re.sub(r"[^A-Z0-9_ -]", "", action_type.upper().strip())[:80]
    normalized_user = user_id.strip()[:80] or "unknown"
    if not normalized_ticker or len(normalized_ticker) > 12:
        raise ValueError("Compliance event ticker is invalid.")
    if not normalized_action:
        raise ValueError("Compliance event action type is required.")

    sanitized_details = _sanitize_audit_details(details)
    if isinstance(sanitized_details, dict):
        delta_variance = sanitized_details.get("delta_variance", "No delta variance supplied.")
    else:
        delta_variance = sanitized_details
    details_json = json.dumps(sanitized_details, sort_keys=True, default=str, indent=2)[:1800]
    try:
        channel_id = _resolve_audit_channel_id(slack_app.client)
        slack_app.client.chat_postMessage(
            channel=channel_id,
            text=f"AlphaChannel compliance event {event_id}: {normalized_action} {normalized_ticker}",
            blocks=_normalize_slack_blocks(
            [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "AlphaChannel Compliance Audit", "emoji": True},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Time (UTC)*\n`{occurred_at}`"},
                        {"type": "mrkdwn", "text": f"*Target Ticker*\n`{normalized_ticker}`"},
                        {"type": "mrkdwn", "text": f"*Executing User ID*\n`{normalized_user}`"},
                        {"type": "mrkdwn", "text": f"*Action Type*\n`{normalized_action}`"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Delta Variance*\n{delta_variance}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Validated Event Details*\n{_slack_code_block(details_json)}"},
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Event ID: `{event_id}` · AlphaChannel · 2026 Slack Hackathon audit archive",
                        }
                    ],
                },
            ]
            ),
        )
    except (SlackApiError, RuntimeError, ValueError) as exc:
        # Audit-channel configuration must never invalidate an approved action.
        # The event remains traceable in structured application logs for replay.
        global _AUDIT_CHANNEL_ID
        _AUDIT_CHANNEL_ID = None
        error = exc.response.get("error") if isinstance(exc, SlackApiError) else str(exc)
        logger.error(
            "alpha_channel_compliance_delivery_failed",
            extra={
                "event_id": event_id,
                "ticker": normalized_ticker,
                "action_type": normalized_action,
                "slack_error": error,
                "audit_channel": _AUDIT_CHANNEL_NAME,
                "event_details": sanitized_details,
            },
        )
    else:
        logger.info(
            "alpha_channel_compliance_event_archived",
            extra={"event_id": event_id, "ticker": normalized_ticker, "action_type": normalized_action},
        )
    return event_id

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
    normalized = str(text).replace("\r\n", "\n")

    def render_latex_math(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        expression = re.sub(r"\\text\{([^{}]*)\}", r"\1", expression)
        for _ in range(3):
            expression = re.sub(
                r"\\frac\{([^{}]+)\}\{([^{}]+)\}",
                r"(\1) / (\2)",
                expression,
            )
        expression = expression.replace(r"\\", " ")
        expression = expression.replace(r"\times", " x ")
        expression = expression.replace(r"\cdot", " x ")
        expression = re.sub(r"\s+", " ", expression).strip()
        return expression

    # Slack mrkdwn does not render LaTeX. Convert display and inline math to readable text.
    normalized = re.sub(r"\$\$(.*?)\$\$", render_latex_math, normalized, flags=re.DOTALL)
    normalized = re.sub(r"\$(?!\d)(.+?)(?<!\d)\$", render_latex_math, normalized, flags=re.DOTALL)
    normalized = re.sub(r"^\s*#{1,6}\s+(.+?)\s*$", r"*\1*", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"*\1*", normalized, flags=re.DOTALL)
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
                    "`@AlphaChannel portfolio` or `@AlphaChannel show holdings`\n\n"
                    "*Fact checking*\n"
                    "Reply after a financial claim with `@AlphaChannel fact check that`"
                ),
            },
        },
    ]


def _is_portfolio_dashboard_query(query: str) -> bool:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    monitoring_terms = (
        "stop loss",
        "stop-loss",
        "take profit",
        "profit alert",
        "risk alert",
        "concentration",
        "overweight",
        "position limit",
        "sector exposure",
        "rebalance",
    )
    return bool(
        re.search(r"\b(portfolio|holdings|positions|dashboard)\b", normalized)
        or re.search(r"\b(list|show)\s+(all\s+)?my\s+(portfolio\s+)?positions\b", normalized)
        or re.search(r"\b(show|list)\s+(my\s+)?holdings\b", normalized)
        or any(term in normalized for term in monitoring_terms)
    )


def _is_fact_check_query(query: str) -> bool:
    return bool(re.search(r"\bfact[\s-]*check\b", query, flags=re.IGNORECASE))


def _gemini_structured_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    response_model: type[BaseModel],
) -> BaseModel:
    from google import genai
    from google.genai import types

    client = genai.Client()
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
        contents=[{"role": "user", "parts": [{"text": user_prompt}]}],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_json_schema=response_model.model_json_schema(),
            temperature=0,
        ),
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, response_model):
        return parsed
    if parsed is not None:
        return response_model.model_validate(parsed)
    return response_model.model_validate_json(str(getattr(response, "text", "") or ""))


def _retrieve_fact_check_history(
    *,
    client: WebClient,
    channel_id: str,
    event_ts: str,
    source_thread_ts: str | None,
    limit: int = 10,
) -> list[dict[str, str]]:
    if source_thread_ts:
        response = client.conversations_replies(
            channel=channel_id,
            ts=source_thread_ts,
            limit=100,
            inclusive=True,
        )
    else:
        response = client.conversations_history(
            channel=channel_id,
            latest=event_ts,
            inclusive=False,
            limit=limit,
        )

    raw_messages = response.get("messages") or []
    if not isinstance(raw_messages, list):
        raise ValueError("Slack history response did not contain a messages array.")

    history: list[dict[str, str]] = []
    for message in raw_messages:
        if not isinstance(message, dict) or message.get("ts") == event_ts:
            continue
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        history.append(
            {
                "ts": str(message.get("ts") or ""),
                "user": str(message.get("user") or message.get("bot_id") or "unknown"),
                "text": text[:3000],
            }
        )

    history.sort(key=lambda item: float(item["ts"]) if item["ts"].replace(".", "", 1).isdigit() else 0.0)
    return history[-limit:]


def _resolve_fact_check_claim(history: list[dict[str, str]]) -> FactCheckClaim | None:
    system_prompt = (
        "You are the AlphaChannel Context Resolver. Analyze the provided conversation snippet between financial "
        "analysts. Your goal is to extract the underlying financial claim being discussed. Output a strict JSON "
        "object with these keys: ticker, metric, claimed_value. ticker is the stock symbol being discussed; metric "
        "is the target data point such as revenue, earnings, or guidance; claimed_value is the numerical value the "
        "analyst stated. Resolve company names to exchange tickers when confident. If no clear financial claim or "
        "ticker can be deduced, return an empty object. Never use facts outside the supplied conversation."
    )
    claim = _gemini_structured_completion(
        system_prompt=system_prompt,
        user_prompt=f"Conversation history:\n{json.dumps(history, ensure_ascii=True, indent=2)}",
        response_model=FactCheckClaim,
    )
    if not isinstance(claim, FactCheckClaim):
        raise TypeError("Gemini returned an invalid fact-check claim model.")
    if not claim.ticker or not claim.metric or not claim.claimed_value:
        return None
    return claim


def _fetch_fact_check_evidence(claim: FactCheckClaim) -> dict[str, Any]:
    from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient

    if not claim.ticker:
        raise ValueError("A ticker is required for Yahoo Finance MCP verification.")
    finance_client = YahooFinanceMCPClient()
    return {
        "ticker": claim.ticker,
        "stock_info": finance_client.get_stock_info(claim.ticker),
        "quarterly_income_statement": finance_client.get_financial_statement(
            claim.ticker,
            "quarterly_income_stmt",
        ),
        "annual_income_statement": finance_client.get_financial_statement(
            claim.ticker,
            "income_stmt",
        ),
        "source": "Yahoo Finance MCP",
    }


def _synthesize_fact_check_verdict(
    claim: FactCheckClaim,
    evidence: dict[str, Any],
) -> FactCheckVerdict:
    system_prompt = (
        "You are AlphaChannel's institutional financial fact checker. Compare the analyst's claimed value only "
        "against the supplied Yahoo Finance MCP corporate information and income statements. Return VERIFIED when "
        "the claim agrees with the matching reported period within normal rounding tolerance; OUTDATED when it was "
        "accurate for an older period but newer reported data differs; DISPROVED when the comparable reported value "
        "materially contradicts it; or INCONCLUSIVE when the metric, units, or period cannot be matched reliably. "
        "Do not invent values, periods, or calculations. Return every field required by the JSON schema."
    )
    evidence_json = json.dumps(evidence, ensure_ascii=True, default=str)
    verdict = _gemini_structured_completion(
        system_prompt=system_prompt,
        user_prompt=(
            f"Analyst claim:\n{claim.model_dump_json()}\n\n"
            f"Yahoo Finance MCP evidence:\n{evidence_json[:50_000]}"
        ),
        response_model=FactCheckVerdict,
    )
    if not isinstance(verdict, FactCheckVerdict):
        raise TypeError("Gemini returned an invalid fact-check verdict model.")
    return verdict


def _parse_financial_value(value: str) -> tuple[float, str] | None:
    """Normalize one financial value for deterministic variance comparison."""
    normalized = re.sub(r"\s+", " ", str(value).strip().lower())
    if not normalized or normalized in {"n/a", "na", "none", "unknown", "unavailable"}:
        return None
    if re.search(r"\d\s*(?:-|–|—|to)\s*[$€£]?\s*\d", normalized):
        return None

    match = re.search(r"[-+]?\s*[$€£]?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", normalized)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    prefix = normalized[: match.start()].strip()
    if "(" in prefix and ")" in normalized[match.end() :]:
        number = -number
    elif normalized.lstrip().startswith("-"):
        number = -abs(number)

    multiplier = 1.0
    if re.search(r"\btrillion\b|(?<=\d)t\b", normalized):
        multiplier = 1_000_000_000_000.0
    elif re.search(r"\bbillion\b|(?<=\d)b\b", normalized):
        multiplier = 1_000_000_000.0
    elif re.search(r"\bmillion\b|(?<=\d)m\b", normalized):
        multiplier = 1_000_000.0
    elif re.search(r"\bthousand\b|(?<=\d)k\b", normalized):
        multiplier = 1_000.0

    if "%" in normalized or "percent" in normalized:
        return number, "percent"
    if any(symbol in normalized for symbol in ("$", "€", "£")) or multiplier != 1.0:
        return number * multiplier, "monetary"
    return number, "scalar"


def _positive_direction_metric(metric: str | None) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", (metric or "").lower()).strip()
    return any(
        phrase in normalized
        for phrase in (
            "revenue",
            "sales",
            "net income",
            "operating income",
            "earnings",
            "eps",
            "guidance",
            "gross profit",
            "operating profit",
            "free cash flow",
            "cash flow",
        )
    )


def _fact_check_variance_and_direction(
    claim: FactCheckClaim,
    verdict: FactCheckVerdict,
) -> tuple[str, str | None]:
    claimed = _parse_financial_value(str(claim.claimed_value or ""))
    actual = _parse_financial_value(verdict.actual_value)
    if claimed is None or actual is None or claimed[1] != actual[1]:
        direction = (
            "⚪ *Direction:* Not assessed (Comparable numeric values were unavailable.)"
            if _positive_direction_metric(claim.metric)
            else None
        )
        return verdict.variance, direction

    claimed_number, _ = claimed
    actual_number, _ = actual
    if claimed_number == 0:
        variance_text = "Not calculable from a zero claimed baseline"
    else:
        variance_percent = ((actual_number - claimed_number) / abs(claimed_number)) * 100
        accuracy_label = (
            "Highly Accurate"
            if abs(variance_percent) <= 5
            else "Material Variance"
        )
        variance_text = f"{variance_percent:+.2f}% ({accuracy_label})"

    if not _positive_direction_metric(claim.metric):
        return variance_text, None
    if actual_number > claimed_number:
        return (
            variance_text,
            "📈 *Direction:* *Positive Beat* (The actual metric outperformed the analyst's claim).",
        )
    if actual_number < claimed_number:
        return (
            variance_text,
            "📉 *Direction:* *Underperformance Deficit* (The actual metric fell short of the analyst's claim).",
        )
    return variance_text, "➡️ *Direction:* *In Line* (The actual metric matched the analyst's claim)."


def _build_fact_check_blocks(
    claim: FactCheckClaim,
    verdict: FactCheckVerdict,
) -> list[dict[str, Any]]:
    status_display = {
        "VERIFIED": "✅ VERIFIED",
        "OUTDATED": "⚠️ OUTDATED",
        "DISPROVED": "❌ DISPROVED",
        "INCONCLUSIVE": "⚪ INCONCLUSIVE",
    }[verdict.status]
    variance_text, direction_text = _fact_check_variance_and_direction(claim, verdict)
    comparison_lines = [
        f"• *Metric:* {claim.metric or 'n/a'}",
        f"• *Claimed Value:* {claim.claimed_value or 'n/a'}",
        f"• *Actual Value:* {verdict.actual_value}",
        f"• *Variance:* {variance_text}",
    ]
    if direction_text:
        comparison_lines.append(f"• {direction_text}")
    comparison_lines.append(f"• *Source Period:* {verdict.source_period}")
    return _normalize_slack_blocks(
        [
            {"type": "header", "text": {"type": "plain_text", "text": status_display, "emoji": True}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Ticker:* `{claim.ticker}`\n\n" + "\n".join(comparison_lines),
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Assessment*\n{verdict.explanation}"},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "Verified against Yahoo Finance MCP financial statements."}],
            },
        ]
    )


def _start_fact_check_worker(
    *,
    client: WebClient,
    channel_id: str,
    response_thread_ts: str,
    event_ts: str,
    source_thread_ts: str | None,
) -> None:
    posted = client.chat_postMessage(
        channel=channel_id,
        thread_ts=response_thread_ts,
        blocks=_mrkdwn_stream_blocks("◐ _Fact-Checking Engine:_ Resolving the prior financial claim..."),
        text="AlphaChannel is resolving the prior financial claim",
    )
    message_ts = posted.get("ts")
    if not isinstance(message_ts, str) or not message_ts:
        raise ValueError("Slack did not return a timestamp for the fact-check status message.")

    def update_status(text: str) -> None:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=_mrkdwn_stream_blocks(text),
            text=_format_slack_mrkdwn(text),
        )

    def worker() -> None:
        try:
            history = _retrieve_fact_check_history(
                client=client,
                channel_id=channel_id,
                event_ts=event_ts,
                source_thread_ts=source_thread_ts,
            )
            logger.info(
                "fact_check_history_retrieved",
                extra={"channel_id": channel_id, "message_count": len(history)},
            )
            if not history:
                raise ValueError("No earlier Slack messages were available to fact-check.")

            update_status("◓ _Context Resolver:_ Extracting the ticker, metric, and claimed value...")
            claim = _resolve_fact_check_claim(history)
            if claim is None:
                update_status(
                    "⚪ *Fact check inconclusive*\nNo clear ticker and numerical financial claim could be resolved "
                    "from the preceding messages."
                )
                return

            update_status(f"◑ _Yahoo Finance MCP:_ Retrieving reported statements for {claim.ticker}...")
            evidence = _fetch_fact_check_evidence(claim)
            update_status("◒ _Gemini Verifier:_ Comparing the claim with reported corporate figures...")
            verdict = _synthesize_fact_check_verdict(claim, evidence)
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_fact_check_blocks(claim, verdict),
                text=f"{verdict.status}: fact check for {claim.ticker} {claim.metric}",
            )
            logger.info(
                "fact_check_completed",
                extra={"channel_id": channel_id, "ticker": claim.ticker, "status": verdict.status},
            )
        except SlackApiError as exc:
            slack_error = exc.response.get("error") if exc.response else str(exc)
            logger.exception(
                "fact_check_slack_api_failed",
                extra={"channel_id": channel_id, "slack_error": slack_error},
            )
            guidance = (
                "Slack could not read the preceding conversation. Reinstall AlphaChannel after granting "
                "`channels:history`, `groups:history`, `im:history`, and `mpim:history`."
                if slack_error in {"missing_scope", "not_in_channel", "channel_not_found"}
                else f"Slack history retrieval failed: `{slack_error}`."
            )
            try:
                update_status(f"⚠️ *Fact check unavailable*\n{guidance}")
            except SlackApiError:
                logger.exception("fact_check_error_status_update_failed", extra={"channel_id": channel_id})
        except Exception as exc:
            logger.exception(
                "fact_check_failed",
                extra={"channel_id": channel_id, "error_type": type(exc).__name__},
            )
            try:
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=_build_error_blocks(str(exc)),
                    text="AlphaChannel fact check failed",
                )
            except SlackApiError:
                logger.exception("fact_check_failure_update_failed", extra={"channel_id": channel_id})

    threading.Thread(
        target=worker,
        name=f"alpha-channel-fact-check-{channel_id}-{event_ts}",
        daemon=True,
    ).start()


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

    finance_tools = {"yfinance_risk_lookup", "yfinance_fundamental_lookup", "sec_risk_lookup", "portfolio_holdings"}
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
        ticker = execution.arguments.get("ticker") if isinstance(execution.arguments, dict) else None
        tool_label = f"`{execution.tool_name}`" + (f" (`{ticker}`)" if ticker else "")
        blocks.extend(
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Tool:* {tool_label} | *Status:* {status}\n{evidence_text}",
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
    if execution.tool_name == "yfinance_fundamental_lookup":
        source = payload.get("source_url")
        def display_number(key: str, suffix: str = "") -> str:
            value = payload.get(key)
            if not isinstance(value, (int, float)):
                return "n/a"
            return f"{value:.1%}" if suffix == "%" else f"{value:.2f}{suffix}"
        return (
            f"Price: `{payload.get('current_price', 'n/a')}` | "
            f"Trailing P/E: `{display_number('trailing_pe')}` | "
            f"Forward P/E: `{display_number('forward_pe')}`\n"
            f"Revenue: `{payload.get('revenue', 'n/a')}` | "
            f"Revenue growth: `{display_number('revenue_growth', '%')}` | "
            f"Earnings growth: `{display_number('earnings_growth', '%')}` | "
            f"ROE: `{display_number('return_on_equity', '%')}` | "
            f"Debt/equity: `{display_number('debt_to_equity')}`"
            + (f"\n<{source}|Open Yahoo Finance fundamentals source>" if source else "")
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
    initial = "◐ _AlphaChannel is thinking_ · Planning MCP tools and checking approval policy..."
    posted = client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=_mrkdwn_stream_blocks(initial),
        text="AlphaChannel is planning MCP tools",
    )
    message_ts = posted.get("ts")
    if not isinstance(message_ts, str) or not message_ts:
        raise ValueError("Slack did not return a timestamp for the MCP planning message.")

    progress_lock = threading.RLock()
    progress_state = {"status": "Planning the investigation and selecting evidence tools..."}
    animation_stop = threading.Event()

    def animate_progress() -> None:
        frames = ("◐", "◓", "◑", "◒")
        frame_index = 0
        while not animation_stop.wait(1.6):
            with progress_lock:
                status = progress_state["status"]
            animated = f"{frames[frame_index % len(frames)]} _AlphaChannel is thinking_ · {status}"
            frame_index += 1
            try:
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=_mrkdwn_stream_blocks(animated),
                    text=f"AlphaChannel is thinking: {status}",
                )
            except SlackApiError as exc:
                logger.warning(
                    "agent_progress_animation_failed",
                    extra={"channel_id": channel_id, "error": exc.response.get("error")},
                )

    animation_thread = threading.Thread(
        target=animate_progress,
        name=f"alpha-channel-progress-{channel_id}-{thread_ts}",
        daemon=True,
    )
    animation_thread.start()

    def worker() -> None:
        try:
            def update_progress(status: str) -> None:
                with progress_lock:
                    progress_state["status"] = status

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
            animation_stop.set()
            animation_thread.join(timeout=2.0)
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_agent_turn_blocks(response),
                text=_format_slack_mrkdwn(response.message),
            )
        except Exception as exc:
            animation_stop.set()
            animation_thread.join(timeout=2.0)
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


def _start_portfolio_dashboard_worker(
    *,
    client: WebClient,
    channel_id: str,
    thread_ts: str,
) -> None:
    """Refresh portfolio prices off the Socket Mode event loop."""
    initial_message = "◐ _Portfolio Center:_ Refreshing live market prices..."
    posted = client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=_mrkdwn_stream_blocks(initial_message),
        text="Portfolio Center refreshing live market prices",
    )
    message_ts = posted.get("ts")
    if not isinstance(message_ts, str) or not message_ts:
        raise ValueError("Slack did not return a timestamp for the portfolio refresh message.")

    def worker() -> None:
        try:
            blocks = router.generate_portfolio_dashboard(refresh_market_data=True)
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_normalize_slack_blocks(blocks),
                text="AlphaChannel Portfolio Center",
            )
        except Exception as exc:
            logger.exception("portfolio_dashboard_refresh_failed", extra={"channel_id": channel_id})
            client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=_build_error_blocks(str(exc)),
                text="Portfolio Center refresh failed",
            )

    threading.Thread(
        target=worker,
        name=f"alpha-channel-portfolio-{channel_id}-{thread_ts}",
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

        if _is_fact_check_query(query):
            event_ts = str(event.get("ts") or "")
            if not event_ts:
                raise ValueError("Slack fact-check event did not include a message timestamp.")
            logger.info(
                "fact_check_intent_matched",
                extra={"channel_id": channel_id, "user_id": user_id, "threaded": bool(event.get("thread_ts"))},
            )
            _start_fact_check_worker(
                client=client,
                channel_id=channel_id,
                response_thread_ts=str(thread_ts),
                event_ts=event_ts,
                source_thread_ts=str(event["thread_ts"]) if event.get("thread_ts") else None,
            )
            return

        if _is_portfolio_dashboard_query(query):
            logger.info(
                "portfolio_dashboard_intent_matched",
                extra={"channel_id": channel_id, "user_id": user_id},
            )
            _start_portfolio_dashboard_worker(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
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
                    "text": "Fundamental performance, SEC footnote risk, and strategic alignment review finalized.",
                }
            ],
        },
    ])


def _load_audit_company_context(ticker: str) -> dict[str, Any]:
    from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient

    try:
        stock_info = YahooFinanceMCPClient().get_stock_info(ticker)
        safe_evidence = {
            key: stock_info.get(key)
            for key in (
                "sector",
                "industry",
                "currentPrice",
                "totalRevenue",
                "revenueGrowth",
                "earningsGrowth",
                "grossMargins",
                "profitMargins",
                "operatingCashflow",
                "capitalExpenditures",
                "trailingPE",
                "forwardPE",
            )
            if stock_info.get(key) is not None
        }
        logger.info(
            "portfolio_audit_company_context_loaded",
            extra={
                "ticker": ticker,
                "sector": safe_evidence.get("sector"),
                "industry": safe_evidence.get("industry"),
                "metric_count": len(safe_evidence),
            },
        )
        return safe_evidence
    except Exception as exc:
        logger.warning(
            "portfolio_audit_company_context_unavailable",
            extra={"ticker": ticker, "error_type": type(exc).__name__, "error": str(exc)},
        )
        return {}


def _fallback_audit_metric_plan(evidence: dict[str, Any]) -> AuditMetricPlan:
    sector = str(evidence.get("sector") or "Corporate")
    industry = str(evidence.get("industry") or "Public Company")
    return AuditMetricPlan(
        sector=sector,
        industry=industry,
        node_header=f"{industry[:58]} Financial Metrics Node",
        metrics=[
            "YoY Quarterly Revenue Growth",
            "Net Income Margin",
            "Operating Cash Flow Trajectory",
        ],
        rationale="Selected broadly comparable reported fundamentals because specialized disclosures were unavailable.",
    )


def _plan_audit_metrics(ticker: str, evidence: dict[str, Any]) -> AuditMetricPlan:
    if not evidence:
        return _fallback_audit_metric_plan(evidence)
    prompt = (
        "You are AlphaChannel's financial metric planner. Determine the company's sector and industry from the "
        "supplied Yahoo Finance MCP evidence, then select exactly three decision-useful investment metrics for a "
        "deep audit. Prefer metrics with values present in the evidence. You may select specialized metrics such as "
        "NRR, RPO, deposit growth, net interest margin, capex, or bit growth only when the evidence indicates that "
        "the metric is applicable and available from current corporate reporting. Otherwise choose comparable "
        "reported fundamentals such as revenue growth, gross margin, net margin, free cash flow, ROE, or valuation. "
        "Do not select options, volatility, or trading metrics. Return a concise node_header without emoji or Markdown."
    )
    try:
        planned = _gemini_structured_completion(
            system_prompt=prompt,
            user_prompt=(
                f"Ticker: {ticker}\n"
                f"Available evidence fields: {json.dumps(evidence, default=str)}"
            ),
            response_model=AuditMetricPlan,
        )
        if not isinstance(planned, AuditMetricPlan):
            raise TypeError("Gemini returned an invalid audit metric plan.")
        planned.node_header = re.sub(r"[^A-Za-z0-9 &/()'-]", "", planned.node_header).strip()[:80]
        if not planned.node_header:
            raise ValueError("Gemini returned an empty audit node header.")
        logger.info(
            "portfolio_audit_metric_plan_completed",
            extra={"ticker": ticker, "sector": planned.sector, "industry": planned.industry},
        )
        return planned
    except Exception as exc:
        logger.warning(
            "portfolio_audit_metric_plan_fallback",
            extra={"ticker": ticker, "error_type": type(exc).__name__, "error": str(exc)},
        )
        return _fallback_audit_metric_plan(evidence)


def _load_audit_sec_evidence(ticker: str) -> dict[str, Any]:
    execution = router.mcp_client.call_tool("sec_risk_lookup", {"ticker": ticker})
    if execution.is_error:
        return {"status": "unavailable", "error": execution.output[:1000]}
    try:
        payload = json.loads(execution.output)
    except (json.JSONDecodeError, TypeError):
        return {"status": "available", "summary": execution.output[:3000]}
    return {"status": "available", "filing": payload}


def _render_gemini_audit_synthesis(
    synthesis: GeminiAuditSynthesis,
    metric_plan: AuditMetricPlan,
) -> str:
    def section(emoji: str, title: str, points: list[str]) -> str:
        clean_points = [re.sub(r"^[\s•*-]+", "", point.strip()) for point in points if point.strip()]
        bullets = "\n".join(f"• {point}" for point in clean_points)
        return f"{emoji} *{title}*\n{bullets}"

    return "\n\n".join(
        [
            section(
                "📈",
                metric_plan.node_header,
                synthesis.primary_financial_metrics,
            ),
            section("⚖️", "SEC Compliance Node", synthesis.sec_compliance),
            section("💡", "Strategic Assessment", synthesis.strategic_assessment),
        ]
    )


def _fallback_gemini_audit_summary(
    ticker: str,
    metric_plan: AuditMetricPlan | None = None,
    sec_evidence: dict[str, Any] | None = None,
) -> str:
    metric_plan = metric_plan or _fallback_audit_metric_plan({})
    requested_metrics = ", ".join(metric_plan.metrics)
    sec_status = str((sec_evidence or {}).get("status") or "unavailable")
    sec_message = (
        "SEC filing evidence was retrieved, but the structured Gemini synthesis was unavailable."
        if sec_status == "available"
        else "SEC evidence is unavailable; no clean-compliance conclusion can be reached."
    )
    return _render_gemini_audit_synthesis(
        GeminiAuditSynthesis(
            primary_financial_metrics=[
                f"Review scope prioritized {requested_metrics}.",
                "No unsupported sector-specific metric was substituted into the audit.",
            ],
            sec_compliance=[
                sec_message,
            ],
            strategic_assessment=[
                f"Defer a final {ticker} assessment until the unavailable evidence or synthesis is restored.",
            ],
        ),
        metric_plan,
    )


def _generate_gemini_audit_summary(ticker: str) -> str:
    financial_evidence = _load_audit_company_context(ticker)
    metric_plan = _plan_audit_metrics(ticker, financial_evidence)
    sec_evidence = _load_audit_sec_evidence(ticker)
    system_prompt = (
        "You are the Lead Risk Analyst for AlphaChannel. Produce an ultra-professional institutional audit "
        "synthesis for Slack using the required structured JSON schema. Separate the analysis across exactly "
        "three active agent nodes: primary_financial_metrics, sec_compliance, and strategic_assessment. For the "
        "primary_financial_metrics node, use only the validated metric plan supplied in the request. If a planned "
        "metric is absent from the supplied evidence, say that the metric is unavailable and use another planned "
        "metric with reported evidence; do not manufacture a value or claim that an unsupported specialized metric "
        "was reviewed. For sec_compliance, use only the supplied SEC tool observation. If its status is unavailable, "
        "state that no SEC conclusion can be reached; never infer a clean filing. Do not discuss options, "
        "implied volatility, option chains, or trading signals. Each node "
        "must contain one to three concise, evidence-grounded bullet statements. Be specific to the company's "
        "real-world business model. Do not return a dense paragraph, Markdown headings, tables, emoji, or bullet "
        "characters; the host application adds Slack formatting, emoji anchors, bold headers, bullets, and newlines."
    )
    user_prompt = (
        f"Ticker: {ticker}\n"
        f"Resolved sector: {metric_plan.sector}\n"
        f"Resolved industry: {metric_plan.industry}\n"
        f"Required first-node header: {metric_plan.node_header}\n"
        f"Validated metric plan: {json.dumps(metric_plan.metrics)}\n"
        f"Metric-plan rationale: {metric_plan.rationale}\n"
        f"Yahoo Finance MCP evidence: {json.dumps(financial_evidence, default=str)}\n"
        f"SEC MCP evidence: {json.dumps(sec_evidence, default=str)[:20_000]}\n\n"
        "Return only the structured audit synthesis required by the response schema."
    )

    try:
        model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        logger.info("gemini_audit_generation_started", extra={"ticker": ticker, "model": model})
        generated = _gemini_structured_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=GeminiAuditSynthesis,
        )
        if not isinstance(generated, GeminiAuditSynthesis):
            raise TypeError("Gemini returned an invalid audit synthesis model.")
        rendered = _render_gemini_audit_synthesis(generated, metric_plan)
        logger.info(
            "gemini_audit_generation_completed",
            extra={"ticker": ticker, "model": model, "response_chars": len(rendered)},
        )
        return rendered
    except Exception as exc:
        logger.warning(
            "gemini_audit_generation_failed",
            extra={"ticker": ticker, "error_type": type(exc).__name__, "error": str(exc)},
        )
        return _fallback_gemini_audit_summary(ticker, metric_plan, sec_evidence)


def _build_gemini_audit_result(ticker: str) -> tuple[str, list[dict[str, Any]]]:
    analysis_text = _format_slack_mrkdwn(_generate_gemini_audit_summary(ticker))
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
    user_id = body.get("user", {}).get("id", "unknown")
    checkpoint = router.handle_trading_checkpoint(
        payload=payload,
        decision="approve_buy_allocation",
        decided_by=user_id,
    )
    checkpoint_data = checkpoint["checkpoint"]
    log_compliance_event(
        "BUY_ALLOCATION_CHECKPOINT",
        str(checkpoint_data["ticker"]),
        user_id,
        {
            "assessment_id": checkpoint_data.get("assessment_id"),
            "verdict": checkpoint_data.get("verdict"),
            "dry_run": checkpoint_data.get("dry_run", True),
            "delta_variance": "Buy allocation approval recorded; no live brokerage delta was applied.",
        },
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
    user_id = body.get("user", {}).get("id", "unknown")
    checkpoint = router.handle_trading_checkpoint(
        payload=payload,
        decision="execute_protective_hedge",
        decided_by=user_id,
    )
    checkpoint_data = checkpoint["checkpoint"]
    log_compliance_event(
        "PROTECTIVE_HEDGE_CHECKPOINT",
        str(checkpoint_data["ticker"]),
        user_id,
        {
            "assessment_id": checkpoint_data.get("assessment_id"),
            "verdict": checkpoint_data.get("verdict"),
            "dry_run": checkpoint_data.get("dry_run", True),
            "delta_variance": "Protective hedge checkpoint recorded; no live brokerage delta was applied.",
        },
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
            decided_by = body.get("user", {}).get("id", "unknown")
            response = router.approve_agent_action(
                action_id,
                decided_by,
            )
            if pending.tool_name == "execute_trade_checkpoint":
                side = str(pending.arguments.get("side") or "trade").upper()
                notional = float(pending.arguments.get("notional_usd") or 0.0)
                log_compliance_event(
                    f"PAPER_PORTFOLIO_{side}_APPROVED",
                    str(pending.arguments.get("ticker") or ""),
                    decided_by,
                    {
                        "action_id": action_id,
                        "side": side.lower(),
                        "notional_usd": notional,
                        "trade_executed": False,
                        "delta_variance": (
                            f"Paper portfolio {side.lower()} delta approved for ${notional:,.2f}; "
                            "no live brokerage order was submitted."
                        ),
                    },
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
                "📊 _Sector Router:_ Selecting industry-specific financial metrics from Yahoo Finance MCP...",
                "🤖 _Orchestrator:_ Synthesizing final divergence alignment scorecard...",
            ],
            final_message=(
                f"✅ *Audit Complete for {ticker}* \n\n"
                f"{_fallback_gemini_audit_summary(ticker)}"
            ),
            final_blocks=_portfolio_audit_result_blocks(
                ticker,
                _fallback_gemini_audit_summary(ticker),
            ),
            final_result_factory=_build_gemini_audit_result,
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
        thread_ts = _action_thread_ts(body) or _action_message_ts(body)
        snapshot = router.trading_node.get_portfolio_snapshot(refresh_market_data=True)
        holding = next((item for item in snapshot.holdings if item.ticker == ticker), None)
        if holding is None:
            raise ValueError(f"No stock holding found for {ticker}.")
        trim_notional = round(holding.current_market_value * 0.10, 2)
        logger.info(
            "portfolio_trim_sell_checkpoint_requested",
            extra={
                "ticker": ticker,
                "user_id": user_id,
                "channel_id": channel_id,
                "notional_usd": trim_notional,
                "trim_percent": 10,
            },
        )
        response = router.process_agent_turn(
            AgentTurnRequest(
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                text=f"Sell ${trim_notional:.2f} of {ticker}",
            )
        )
        if response.pending_action is None:
            raise RuntimeError("AlphaChannel did not create a sell approval checkpoint.")
        if response.message.startswith("A trade checkpoint is already pending"):
            logger.info(
                "duplicate_portfolio_trim_ignored",
                extra={"ticker": ticker, "channel_id": channel_id, "action_id": response.pending_action.action_id},
            )
            return
        log_compliance_event(
            "TRIM_SELL_CHECKPOINT_CREATED",
            ticker,
            user_id,
            {
                "action_id": response.pending_action.action_id,
                "trim_percent": 10,
                "notional_usd": trim_notional,
                "estimated_shares": round(holding.shares_held * 0.10, 6),
                "delta_variance": (
                    f"Proposed 10% trim equals ${trim_notional:,.2f} and approximately "
                    f"{holding.shares_held * 0.10:.4g} shares; awaiting human approval."
                ),
            },
        )
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=_build_pending_agent_action_blocks(
                response.pending_action,
                (
                    f"*Trim request converted to a sell checkpoint for {ticker}.*\n"
                    f"Proposed trim: *10%* of the position, `${trim_notional:,.2f}` notional.\n"
                    f"This represents approximately `{holding.shares_held * 0.10:.4g}` shares at the current reference price.\n\n"
                    "Review the parameters before approval. This is still a dry-run action; no live order was submitted."
                ),
            ),
            text=f"Sell checkpoint created for {ticker}",
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

    logger.info("alpha_channel_2026_slack_hackathon_socket_mode_starting")
    SocketModeHandler(slack_app, app_token).start()
