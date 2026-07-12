# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent
# Copyright (c) 2026 Anand Krishnamoorthy
# SPDX-License-Identifier: MIT
#
# Purpose-built for the 2026 Slack Hackathon. See LICENSE for terms.

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from engine.agent_brain import BrainRunResult
from engine.sec_node import SecRiskExtractor, SecRiskMarker
from engine.mcp_client import AlphaChannelMCPClient, MCPToolExecution
from engine.trading_node import TargetMitigationOrder, TradingNode
from engine.yfinance_node import OptionsRiskMarker, YahooFinanceOptionsWorker

logger = logging.getLogger("alpha_channel.engine.router")


class WorkspaceMessage(BaseModel):
    channel_id: str
    user_id: str
    text: str
    ts: str
    permalink: str | None = None


class WorkspaceAttachment(BaseModel):
    title: str | None = None
    filetype: str | None = None
    url: str | None = None
    summary: str | None = None


class InternalWorkspacePerception(BaseModel):
    query: str
    source: str
    channel_id: str
    requested_by: str
    context_messages: list[WorkspaceMessage] = Field(default_factory=list)
    file_attachments: list[WorkspaceAttachment] = Field(default_factory=list)
    raw_result_count: int = 0
    errors: list[str] = Field(default_factory=list)

    def text_corpus(self) -> str:
        message_text = "\n".join(message.text for message in self.context_messages)
        attachment_text = "\n".join(
            part
            for attachment in self.file_attachments
            for part in [attachment.title or "", attachment.summary or ""]
            if part
        )
        return f"{self.query}\n{message_text}\n{attachment_text}".strip()


class RiskAssessmentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    ticker: str
    query: str
    requested_by: str
    channel_id: str
    internal_workspace_perception: InternalWorkspacePerception
    sec_filing_text: str | None = None
    sec_pdf_paths: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        ticker = value.upper().strip().lstrip("$")
        if not ticker:
            raise ValueError("ticker is required")
        return ticker


class RiskAssessmentResponse(BaseModel):
    assessment_id: str
    ticker: str
    internal_consensus_percent: float = Field(ge=0.0, le=100.0)
    internal_evidence: list[str] = Field(default_factory=list)
    external_risk_markers: list[str] = Field(default_factory=list)
    max_external_severity: float = Field(ge=0.0, le=1.0)
    verdict: str
    target_mitigation_orders: list[TargetMitigationOrder] = Field(default_factory=list)

    def to_standard_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "Ticker": self.ticker,
            "Internal Consensus %": f"{self.internal_consensus_percent:.0f}%",
            "External Risk Markers": self.external_risk_markers,
            "Verdict": self.verdict,
            "Target Mitigation Orders": [order.model_dump(mode="json") for order in self.target_mitigation_orders],
        }


class AssessmentState(TypedDict, total=False):
    request: RiskAssessmentRequest
    sec_markers: list[SecRiskMarker]
    options_markers: list[OptionsRiskMarker]
    external_markers: list[SecRiskMarker | OptionsRiskMarker]
    internal_consensus_percent: float
    internal_evidence: list[str]
    max_external_severity: float
    verdict: str
    target_mitigation_orders: list[TargetMitigationOrder]


class AgentTurnRequest(BaseModel):
    channel_id: str
    thread_ts: str
    user_id: str
    text: str = Field(min_length=1, max_length=12_000)
    workspace_context: str | None = Field(default=None, max_length=20_000)


class DirectTradeIntent(BaseModel):
    """Explicit trade request routed directly to the approval boundary."""

    ticker: str
    side: Literal["buy", "sell", "hedge"]
    notional_usd: float | None = Field(default=None, gt=0)


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingToolAction(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel_id: str
    thread_ts: str
    requested_by: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "edited", "executing", "executed", "denied"] = "pending"
    tool_call_id: str | None = Field(default=None, exclude=True)
    continuation_messages: list[dict[str, Any]] = Field(default_factory=list, exclude=True)


class AgentTurnResponse(BaseModel):
    channel_id: str
    thread_ts: str
    message: str
    tool_executions: list[MCPToolExecution] = Field(default_factory=list)
    pending_action: PendingToolAction | None = None
    conversation_turn_count: int


class GeminiRiskSynthesis(BaseModel):
    """Strict governance contract returned by Gemini structured output."""

    model_config = ConfigDict(extra="forbid")

    internal_consensus_percent: int = Field(ge=0, le=100)
    external_risk_markers: list[str] = Field(min_length=0, max_length=12)
    divergence_analysis: str = Field(min_length=1, max_length=3000)
    verdict: str = Field(
        min_length=1,
        max_length=32,
        json_schema_extra={"enum": ["BUY", "SELL", "HOLD", "TRIMMING_REQUIRED"]},
    )
    recommended_actions: list[str] = Field(min_length=0, max_length=8)

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"BUY", "SELL", "HOLD", "TRIMMING_REQUIRED"}
        if normalized not in allowed:
            raise ValueError(f"verdict must be one of {sorted(allowed)}")
        return normalized

    def as_slack_message(self) -> str:
        markers = "\n".join(f"- {item}" for item in self.external_risk_markers)
        actions = "\n".join(f"- {item}" for item in self.recommended_actions)
        return (
            f"*Internal consensus:* {self.internal_consensus_percent}%\n"
            f"*Verdict:* {self.verdict}\n\n"
            f"*Perception vs. reality*\n{self.divergence_analysis}\n\n"
            f"*External risk markers*\n{markers or '- No material marker returned'}\n\n"
            f"*Recommended actions*\n{actions or '- Continue monitoring'}"
        )


class AssetResolution(BaseModel):
    """LLM-resolved security target, isolated from noisy workspace context."""

    model_config = ConfigDict(extra="forbid")

    ticker: str | None = Field(default=None, max_length=12)
    company_name: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0, le=1)
    ambiguous: bool = False


class GeminiBrainRunResult(BaseModel):
    message: str
    executions: list[MCPToolExecution] = Field(default_factory=list)
    pending_tool_name: str | None = None
    pending_arguments: dict[str, Any] = Field(default_factory=dict)
    pending_tool_call_id: str | None = None
    continuation_messages: list[dict[str, Any]] = Field(default_factory=list)
    synthesis: GeminiRiskSynthesis | None = None


class GeminiOrchestrationBrain:
    """Gemini-native planner that delegates all intent selection to function calling."""

    SYSTEM_INSTRUCTION = """You are AlphaChannel's institutional governance orchestrator.
Select the registered MCP tools needed to satisfy the user's goal. Use live yFinance and SEC tools for
public-company financial risk questions, portfolio tools for holdings questions, and transaction tools only
when the user explicitly requests a consequential action. Never fabricate tool observations. Select exactly
one function per reasoning turn. The host enforces execution policy and human approval. When sufficient
evidence has been collected, stop calling functions so the host can request the final structured synthesis.
For an investment or risk assessment, MUST use yfinance_fundamental_lookup for valuation and operating metrics
(P/E, revenue, growth, margins, ROE, debt-to-equity) and sec_risk_lookup for filing evidence. Never use
yfinance_risk_lookup for a normal investment assessment; that tool is only for explicit options, volatility,
hedging, or trading requests.
Never call git_history, system_status, or run_diagnostic for a financial or portfolio request unless the user
explicitly asks about repository code, runtime health, logs, or tests. Do not repeat the same tool with the same
arguments. For a simple holdings request, portfolio_holdings alone is sufficient."""

    FINAL_SYNTHESIS_INSTRUCTION = (
        "Using only the workspace context and MCP observations in this conversation, produce the final "
        "institutional perception-versus-reality risk assessment. Return every field required by the schema."
    )

    ASSET_RESOLUTION_INSTRUCTION = """Extract the investment asset explicitly requested in the user's goal.
Resolve company names, common aliases, exchange symbols, and obvious spelling mistakes. Ignore every company
or ticker mentioned in the workspace context; it is not part of asset identity. Return ticker=null and
ambiguous=true if the goal names multiple plausible securities or cannot be resolved confidently. Do not
invent a ticker."""

    ASSET_ALIASES = {
        "meta platforms": "META",
        "facebook": "META",
        "meta": "META",
        "cloudflare": "NET",
        "net": "NET",
        "nubank": "NU",
        "service now": "NOW",
        "servicenow": "NOW",
    }

    def __init__(
        self,
        mcp_client: AlphaChannelMCPClient,
        *,
        client: Any | None = None,
        max_iterations: int = 6,
    ) -> None:
        self.mcp_client = mcp_client
        self.client = client or genai.Client()
        self.model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.max_iterations = max_iterations
        self.max_tool_calls = max(1, int(os.getenv("GEMINI_MAX_TOOL_CALLS", "4")))

    def run(
        self,
        *,
        goal: str,
        conversation: list[dict[str, str]],
        workspace_context: str | None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> GeminiBrainRunResult:
        contents: list[dict[str, Any]] = [
            {
                "role": "model" if message.get("role") == "assistant" else "user",
                "parts": [{"text": message.get("content", "")}],
            }
            for message in conversation[-12:]
        ]
        context = (workspace_context or "No Slack RTS context was available.")[:20_000]
        asset = self._resolve_requested_asset(goal)
        requested_ticker = asset.ticker
        options_requested = self._options_requested(goal)
        portfolio_requested = self._portfolio_requested(goal)
        target_instruction = (
            f"LLM-resolved requested asset: {requested_ticker} ({asset.company_name or 'company name unavailable'}), "
            f"confidence={asset.confidence:.2f}. This asset is authoritative. Do not substitute an asset "
            "mentioned in Slack history."
            if requested_ticker
            else "No single requested asset was detected; follow the user's portfolio or engineering goal."
        )
        contents.append(
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"Goal:\n{goal}\n\n{target_instruction}\n\nInternal Workspace Perception:\n{context}",
                    }
                ],
            }
        )
        logger.info(
            "gemini_planning_started model=%s goal_chars=%d requested_asset=%s",
            self.model,
            len(goal),
            requested_ticker,
        )
        if progress_callback:
            progress_callback("Gemini is planning the investigation and selecting MCP evidence tools...")
        return self._run_tool_loop(
            contents,
            [],
            progress_callback,
            expected_ticker=requested_ticker,
            allowed_tool_names=self._allowed_tool_names(
                options_requested=options_requested,
                portfolio_requested=portfolio_requested,
            ),
        )

    def _resolve_requested_asset(self, goal: str) -> AssetResolution:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[{"role": "user", "parts": [{"text": f"User goal:\n{goal}"}]}],
                config=types.GenerateContentConfig(
                    system_instruction=self.ASSET_RESOLUTION_INSTRUCTION,
                    response_mime_type="application/json",
                    response_json_schema=AssetResolution.model_json_schema(),
                    temperature=0,
                ),
            )
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, AssetResolution):
                return parsed
            if parsed is not None:
                return AssetResolution.model_validate(parsed)
            return AssetResolution.model_validate_json(response.text or "")
        except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
            fallback = self._extract_requested_ticker(goal)
            logger.warning(
                "gemini_asset_resolution_fallback error_type=%s fallback_ticker=%s",
                type(exc).__name__,
                fallback,
            )
            return AssetResolution(
                ticker=fallback,
                confidence=0.35 if fallback else 0.0,
                ambiguous=fallback is None,
            )

    def resume(
        self,
        *,
        continuation_messages: list[dict[str, Any]],
        tool_call_id: str,
        execution: MCPToolExecution,
        progress_callback: Callable[[str], None] | None = None,
    ) -> GeminiBrainRunResult:
        contents = [
            *continuation_messages,
            self._function_response_content(tool_call_id, execution),
        ]
        logger.info(
            "gemini_resumed_after_approval tool=%s tool_call_id=%s",
            execution.tool_name,
            tool_call_id,
        )
        expected_ticker = str(execution.arguments.get("ticker") or "").upper() or None
        return self._run_tool_loop(
            contents,
            [execution],
            progress_callback,
            expected_ticker=expected_ticker,
            allowed_tool_names=None,
        )

    def _run_tool_loop(
        self,
        contents: list[dict[str, Any]],
        executions: list[MCPToolExecution],
        progress_callback: Callable[[str], None] | None,
        expected_ticker: str | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> GeminiBrainRunResult:
        tools = self._gemini_tools(allowed_tool_names)
        seen_tool_calls = {
            (execution.tool_name, json.dumps(execution.arguments, sort_keys=True, default=str))
            for execution in executions
        }
        for iteration in range(1, self.max_iterations + 1):
            logger.info(
                "gemini_reasoning_iteration iteration=%d observation_count=%d",
                iteration,
                len(executions),
            )
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_INSTRUCTION,
                    tools=tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(mode="AUTO")
                    ),
                    temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.15")),
                    top_p=float(os.getenv("GEMINI_TOP_P", "0.8")),
                ),
            )
            function_calls = list(response.function_calls or [])
            if len(function_calls) > 1:
                logger.warning(
                    "gemini_parallel_calls_rejected iteration=%d call_count=%d",
                    iteration,
                    len(function_calls),
                )
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": "Select exactly one MCP function for the next reasoning step."}],
                    }
                )
                continue

            if not function_calls:
                contents.append(self._model_content(response))
                if progress_callback:
                    progress_callback("Gemini is producing the validated governance synthesis...")
                synthesis = self._generate_structured_synthesis(contents)
                logger.info(
                    "gemini_synthesis_completed verdict=%s consensus=%d",
                    synthesis.verdict,
                    synthesis.internal_consensus_percent,
                )
                return GeminiBrainRunResult(
                    message=synthesis.as_slack_message(),
                    executions=executions,
                    synthesis=synthesis,
                )

            function_call = function_calls[0]
            tool_name = str(function_call.name or "")
            arguments = dict(function_call.args or {})
            tool_call_id = str(function_call.id or f"gemini-{uuid.uuid4()}")
            contents.append(self._model_content(response, fallback_call_id=tool_call_id))
            if tool_name == "yfinance_risk_lookup" and allowed_tool_names is not None:
                tool_name = "yfinance_fundamental_lookup"
                logger.warning("gemini_investment_data_guard_replaced_options_tool ticker=%s", expected_ticker)
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "Host investment data guard: use yfinance_fundamental_lookup for this "
                                    "investment assessment; options data is not relevant unless explicitly requested."
                                )
                            }
                        ],
                    }
                )
            if expected_ticker and tool_name in {
                "yfinance_risk_lookup",
                "yfinance_fundamental_lookup",
                "sec_risk_lookup",
            }:
                selected_ticker = str(arguments.get("ticker") or "").strip().upper().lstrip("$")
                if selected_ticker != expected_ticker:
                    logger.warning(
                        "gemini_asset_guard_corrected_tool_argument tool=%s selected=%s requested=%s",
                        tool_name,
                        selected_ticker or None,
                        expected_ticker,
                    )
                    arguments["ticker"] = expected_ticker
                    contents.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": (
                                        f"Host asset guard: execute {tool_name} for {expected_ticker}; "
                                        f"the requested asset is authoritative, not {selected_ticker or 'an unknown asset'}."
                                    )
                                }
                            ],
                        }
                    )

            call_signature = (tool_name, json.dumps(arguments, sort_keys=True, default=str))
            if call_signature in seen_tool_calls:
                logger.warning(
                    "gemini_duplicate_tool_call_blocked tool=%s arguments=%s",
                    tool_name,
                    json.dumps(arguments, sort_keys=True, default=str),
                )
                duplicate_execution = MCPToolExecution(
                    tool_name=tool_name,
                    arguments=arguments,
                    output=json.dumps(
                        {
                            "error": "duplicate_tool_call_blocked",
                            "message": "This exact MCP lookup already ran in the current assessment.",
                        }
                    ),
                    is_error=True,
                )
                contents.append(self._function_response_content(tool_call_id, duplicate_execution))
                synthesis = self._generate_structured_synthesis(contents)
                return GeminiBrainRunResult(
                    message=synthesis.as_slack_message(),
                    executions=executions,
                    synthesis=synthesis,
                )
            seen_tool_calls.add(call_signature)
            policy = self.mcp_client.policy_for(tool_name)
            logger.info(
                "gemini_tool_selected iteration=%d tool=%s read_only=%s requires_approval=%s arguments=%s",
                iteration,
                tool_name,
                policy.read_only,
                policy.requires_approval,
                json.dumps(arguments, sort_keys=True, default=str),
            )
            if progress_callback:
                progress_callback(f"Gemini selected {tool_name}; checking governance policy...")

            if policy.requires_approval or not policy.read_only:
                return GeminiBrainRunResult(
                    message=(
                        f"Gemini selected `{tool_name}`. This consequential action is paused pending "
                        "human approval."
                    ),
                    executions=executions,
                    pending_tool_name=tool_name,
                    pending_arguments=arguments,
                    pending_tool_call_id=tool_call_id,
                    continuation_messages=contents,
                )

            if progress_callback:
                progress_callback(f"Running {tool_name} through the persistent MCP session...")
            execution = self.mcp_client.call_tool(tool_name, arguments)
            executions.append(execution)
            contents.append(self._function_response_content(tool_call_id, execution))
            if progress_callback:
                progress_callback(f"MCP returned {tool_name} evidence; Gemini is evaluating it...")
            if len(executions) >= self.max_tool_calls:
                if progress_callback:
                    progress_callback("Evidence budget reached; Gemini is finalizing the risk synthesis...")
                synthesis = self._generate_structured_synthesis(contents)
                return GeminiBrainRunResult(
                    message=synthesis.as_slack_message(),
                    executions=executions,
                    synthesis=synthesis,
                )

        raise RuntimeError(f"Gemini exceeded the {self.max_iterations}-iteration safety limit.")

    @classmethod
    def _extract_requested_ticker(cls, goal: str) -> str | None:
        normalized = goal.lower()
        for alias, ticker in sorted(cls.ASSET_ALIASES.items(), key=lambda item: -len(item[0])):
            if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", normalized):
                return ticker

        excluded = {
            "RUN", "RISK", "ASSESSMENT", "ASSESS", "ANALYZE", "ANALYSIS",
            "CHECK", "STOCK", "SHARES", "THE", "FOR",
        }
        for candidate in re.findall(r"(?<![A-Za-z])\$?([A-Z]{1,5})(?![A-Za-z])", goal):
            if candidate not in excluded:
                return candidate
        return None

    def _gemini_tools(self, allowed_tool_names: set[str] | None = None) -> list[types.Tool]:
        declarations = [
            types.FunctionDeclaration(**definition)
            for definition in self.mcp_client.gemini_function_declarations()
            if allowed_tool_names is None or definition["name"] in allowed_tool_names
        ]
        return [types.Tool(function_declarations=declarations)]

    def _allowed_tool_names(
        self,
        *,
        options_requested: bool,
        portfolio_requested: bool,
    ) -> set[str]:
        names = {item["name"] for item in self.mcp_client.gemini_function_declarations()}
        if not options_requested:
            names.discard("yfinance_risk_lookup")
        if not portfolio_requested:
            names.discard("portfolio_holdings")
        return names

    @staticmethod
    def _options_requested(goal: str) -> bool:
        normalized = goal.lower()
        return any(term in normalized for term in ("option", "volatility", "implied", "hedge", "derivative", "trade"))

    @staticmethod
    def _portfolio_requested(goal: str) -> bool:
        normalized = goal.lower()
        return any(term in normalized for term in ("portfolio", "holdings", "positions", "allocation", "exposure"))

    def _generate_structured_synthesis(
        self,
        contents: list[dict[str, Any]],
    ) -> GeminiRiskSynthesis:
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                *contents,
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"{self.FINAL_SYNTHESIS_INSTRUCTION} Do not discuss or synthesize a "
                                "different asset from prior Slack context."
                            )
                        }
                    ],
                },
            ],
            config=types.GenerateContentConfig(
                system_instruction=self.SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_json_schema=GeminiRiskSynthesis.model_json_schema(),
                temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.15")),
                top_p=float(os.getenv("GEMINI_TOP_P", "0.8")),
            ),
        )
        parsed = getattr(response, "parsed", None)
        try:
            if isinstance(parsed, GeminiRiskSynthesis):
                return parsed
            if parsed is not None:
                return GeminiRiskSynthesis.model_validate(parsed)
            return GeminiRiskSynthesis.model_validate_json(response.text or "")
        except (ValidationError, ValueError, TypeError) as exc:
            raise RuntimeError(f"Gemini synthesis failed Pydantic validation: {exc}") from exc

    @staticmethod
    def _model_content(response: Any, fallback_call_id: str | None = None) -> dict[str, Any]:
        candidates = list(getattr(response, "candidates", None) or [])
        if candidates and getattr(candidates[0], "content", None) is not None:
            content = candidates[0].content
            if hasattr(content, "model_dump"):
                serialized = content.model_dump(mode="json", exclude_none=True)
                if fallback_call_id:
                    for part in serialized.get("parts", []):
                        function_call = part.get("function_call")
                        if function_call is not None and not function_call.get("id"):
                            function_call["id"] = fallback_call_id
                return serialized

        function_calls = list(getattr(response, "function_calls", None) or [])
        if function_calls:
            function_call = function_calls[0]
            return {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": function_call.id or fallback_call_id,
                            "name": function_call.name,
                            "args": dict(function_call.args or {}),
                        }
                    }
                ],
            }
        return {"role": "model", "parts": [{"text": response.text or ""}]}

    @staticmethod
    def _function_response_content(
        tool_call_id: str,
        execution: MCPToolExecution,
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "parts": [
                {
                    "function_response": {
                        "id": tool_call_id,
                        "name": execution.tool_name,
                        "response": {
                            "output": execution.output[:20_000],
                            "is_error": execution.is_error,
                        },
                    }
                }
            ],
        }


class AlphaChannelRouter:
    """Stateful multi-agent orchestrator for Slack-driven governance risk checks."""

    BULLISH_TERMS = {
        "buy",
        "bullish",
        "long",
        "allocate",
        "accumulate",
        "upside",
        "breakout",
        "conviction",
        "undervalued",
        "greenlight",
        "growth",
    }
    BEARISH_TERMS = {
        "sell",
        "bearish",
        "short",
        "hedge",
        "avoid",
        "risk",
        "overvalued",
        "drawdown",
        "liquidity",
        "delinquency",
        "default",
        "impairment",
    }

    def __init__(
        self,
        *,
        sec_node: SecRiskExtractor | None = None,
        yfinance_node: YahooFinanceOptionsWorker | None = None,
        trading_node: TradingNode | None = None,
        mcp_client: AlphaChannelMCPClient | None = None,
        agent_brain: Any | None = None,
    ) -> None:
        self.sec_node = sec_node or SecRiskExtractor()
        self.yfinance_node = yfinance_node or YahooFinanceOptionsWorker()
        self.trading_node = trading_node or TradingNode()
        self.mcp_client = mcp_client or AlphaChannelMCPClient()
        self.agent_brain = agent_brain or GeminiOrchestrationBrain(self.mcp_client)
        self._conversation_lock = threading.RLock()
        self._conversations: dict[str, list[ConversationTurn]] = {}
        self._pending_actions: dict[str, PendingToolAction] = {}
        self._graph = self._build_graph()

    @staticmethod
    def _conversation_key(channel_id: str, thread_ts: str) -> str:
        return f"{channel_id}:{thread_ts}"

    def process_agent_turn(
        self,
        request: AgentTurnRequest,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentTurnResponse:
        key = self._conversation_key(request.channel_id, request.thread_ts)
        with self._conversation_lock:
            turns = self._conversations.setdefault(key, [])
            prior_turns = list(turns)
            turns.append(ConversationTurn(role="user", content=request.text))

        pending_for_thread = self._latest_pending_action(request.channel_id, request.thread_ts)
        normalized_request = request.text.strip().lower()
        if pending_for_thread and normalized_request in {"approve", "approve it", "run it", "proceed"}:
            return self.approve_agent_action(pending_for_thread.action_id, request.user_id)
        if pending_for_thread and normalized_request in {"deny", "deny it", "cancel", "cancel it"}:
            return self.deny_agent_action(pending_for_thread.action_id, request.user_id)
        if pending_for_thread and normalized_request in {"edit", "edit it", "edit parameters", "change parameters"}:
            message = "Use the Edit Parameters button on the pending MCP checkpoint to submit validated JSON."
            with self._conversation_lock:
                turns.append(ConversationTurn(role="assistant", content=message))
                turn_count = len(turns)
            return AgentTurnResponse(
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
                message=message,
                pending_action=pending_for_thread,
                conversation_turn_count=turn_count,
            )

        direct_trade = self._extract_direct_trade_intent(request.text)
        if direct_trade is not None:
            return self._create_direct_trade_checkpoint(request, direct_trade, key)

        conversation = [
            {
                "role": turn.role if turn.role in {"user", "assistant"} else "assistant",
                "content": turn.content if turn.role != "tool" else f"Prior MCP observation: {turn.content}",
            }
            for turn in prior_turns[-12:]
        ]
        brain_result = self.agent_brain.run(
            goal=request.text,
            conversation=conversation,
            workspace_context=request.workspace_context,
            progress_callback=progress_callback,
        )
        executions = brain_result.executions
        pending_action = self._pending_action_from_brain(request, brain_result)
        message = brain_result.message
        with self._conversation_lock:
            turns = self._conversations[key]
            for execution in executions:
                turns.append(ConversationTurn(role="tool", content=f"{execution.tool_name}: {execution.output}"))
            turns.append(ConversationTurn(role="assistant", content=message))
            turn_count = len(turns)

        logger.info(
            "agent_turn_completed",
            extra={
                "channel_id": request.channel_id,
                "thread_ts": request.thread_ts,
                "tool_count": len(executions),
                "pending_action_id": pending_action.action_id if pending_action else None,
                "conversation_turn_count": turn_count,
            },
        )
        return AgentTurnResponse(
            channel_id=request.channel_id,
            thread_ts=request.thread_ts,
            message=message,
            tool_executions=executions,
            pending_action=pending_action,
            conversation_turn_count=turn_count,
        )

    def _create_direct_trade_checkpoint(
        self,
        request: AgentTurnRequest,
        intent: DirectTradeIntent,
        conversation_key: str,
    ) -> AgentTurnResponse:
        with self._conversation_lock:
            for existing in reversed(self._pending_actions.values()):
                if (
                    existing.channel_id == request.channel_id
                    and existing.thread_ts == request.thread_ts
                    and existing.tool_name == "execute_trade_checkpoint"
                    and existing.status in {"pending", "edited"}
                ):
                    message = (
                        f"A trade checkpoint is already pending for *{existing.arguments.get('ticker', 'the requested asset')}*. "
                        "Approve, deny, or edit that checkpoint before creating another sell request."
                    )
                    turns = self._conversations[conversation_key]
                    turns.append(ConversationTurn(role="assistant", content=message))
                    logger.info(
                        "duplicate_trade_checkpoint_ignored",
                        extra={"action_id": existing.action_id, "ticker": existing.arguments.get("ticker")},
                    )
                    return AgentTurnResponse(
                        channel_id=request.channel_id,
                        thread_ts=request.thread_ts,
                        message=message,
                        pending_action=existing.model_copy(deep=True),
                        conversation_turn_count=len(turns),
                    )

        if intent.notional_usd is None:
            message = (
                f"Trade intent detected for *{intent.side.upper()} {intent.ticker}*, but no dollar amount was provided. "
                "Specify a notional amount, for example `Buy $500 of NOW`. No market-data lookup was run."
            )
            with self._conversation_lock:
                turns = self._conversations[conversation_key]
                turns.append(ConversationTurn(role="assistant", content=message))
                turn_count = len(turns)
            return AgentTurnResponse(
                channel_id=request.channel_id,
                thread_ts=request.thread_ts,
                message=message,
                conversation_turn_count=turn_count,
            )

        pending = PendingToolAction(
            channel_id=request.channel_id,
            thread_ts=request.thread_ts,
            requested_by=request.user_id,
            tool_name="execute_trade_checkpoint",
            arguments={
                "ticker": intent.ticker,
                "side": intent.side,
                "notional_usd": intent.notional_usd,
                "rationale": "Explicit user trade request; awaiting human approval before dry-run checkpoint execution.",
            },
        )
        message = (
            f"Trade request detected: *{intent.side.upper()} ${intent.notional_usd:,.2f} of {intent.ticker}*.\n\n"
            "This request is routed directly to the human approval checkpoint. No risk analysis or market-data "
            "lookup was triggered. Review the parameters before approving; this remains a dry-run action."
        )
        with self._conversation_lock:
            self._pending_actions[pending.action_id] = pending
            turns = self._conversations[conversation_key]
            turns.append(ConversationTurn(role="assistant", content=message))
            turn_count = len(turns)
        logger.info(
            "direct_trade_checkpoint_created",
            extra={
                "ticker": intent.ticker,
                "side": intent.side,
                "notional_usd": intent.notional_usd,
                "action_id": pending.action_id,
            },
        )
        return AgentTurnResponse(
            channel_id=request.channel_id,
            thread_ts=request.thread_ts,
            message=message,
            pending_action=pending,
            conversation_turn_count=turn_count,
        )

    @classmethod
    def _extract_direct_trade_intent(cls, text: str) -> DirectTradeIntent | None:
        normalized = text.strip()
        lowered = normalized.lower()
        side: Literal["buy", "sell", "hedge"] | None = None
        if re.search(r"\b(buy|purchase|acquire)\b", lowered):
            side = "buy"
        elif re.search(r"\b(sell|liquidate|reduce)\b", lowered):
            side = "sell"
        elif re.search(r"\b(hedge|protect)\b", lowered):
            side = "hedge"
        if side is None:
            return None

        aliases = {
            "service now": "NOW",
            "servicenow": "NOW",
            "snowflake": "SNOW",
            "nu holdings": "NU",
            "nubank": "NU",
            "amazon": "AMZN",
            "meta platforms": "META",
            "facebook": "META",
        }
        ticker: str | None = None
        for company_name, symbol in aliases.items():
            if re.search(rf"\b{re.escape(company_name)}\b", lowered):
                ticker = symbol
                break
        if ticker is None:
            ignored = {"BUY", "SELL", "PURCHASE", "ACQUIRE", "OF", "THE", "STOCK", "SHARES", "USD"}
            candidates = re.findall(r"\$?\b[A-Za-z]{1,5}\b", normalized)
            for candidate in candidates:
                symbol = candidate.upper().lstrip("$")
                if symbol not in ignored:
                    ticker = symbol
                    break
        if ticker is None:
            return None

        amount_match = re.search(
            r"(?:\$\s*|\b(?:usd|dollars?)\s*)([0-9][0-9,]*(?:\.[0-9]{1,2})?)|\b([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:usd|dollars?)\b",
            lowered,
        )
        amount = None
        if amount_match:
            raw_amount = next((group for group in amount_match.groups() if group), None)
            if raw_amount:
                amount = float(raw_amount.replace(",", ""))
        return DirectTradeIntent(ticker=ticker, side=side, notional_usd=amount)

    def approve_agent_action(self, action_id: str, decided_by: str) -> AgentTurnResponse:
        with self._conversation_lock:
            pending = self._require_pending_action(action_id)
            pending.status = "executing"

        try:
            execution = self.mcp_client.call_tool(pending.tool_name, pending.arguments)
        except Exception:
            with self._conversation_lock:
                pending.status = "pending"
            raise

        if pending.tool_name == "execute_trade_checkpoint" and not pending.tool_call_id:
            try:
                portfolio_update = self.trading_node.apply_dry_run_trade(
                    ticker=str(pending.arguments["ticker"]),
                    side=str(pending.arguments["side"]),
                    notional_usd=float(pending.arguments["notional_usd"]),
                )
            except Exception:
                with self._conversation_lock:
                    pending.status = "pending"
                raise
            with self._conversation_lock:
                pending.status = "executed"
                key = self._conversation_key(pending.channel_id, pending.thread_ts)
                turns = self._conversations.setdefault(key, [])
                message = (
                    f"Approved by <@{decided_by}>.\n\n"
                    f"`{pending.arguments['side'].upper()} ${float(pending.arguments['notional_usd']):,.2f} "
                    f"{pending.arguments['ticker']}` dry-run checkpoint recorded. No live trade was executed.\n\n"
                    f"Paper portfolio updated: `{portfolio_update.get('shares_changed', 0):.4g}` shares "
                    f"affected; buying power is now `${float(portfolio_update['cash_balance']):,.2f}`.\n\n"
                    "*Brokerage integration note:* Robinhood, Webull, or another trading platform can be connected "
                    "through its official API or MCP server, subject to separately governed credentials and explicit approval."
                )
                turns.append(ConversationTurn(role="tool", content=f"{execution.tool_name}: {execution.output}"))
                turns.append(ConversationTurn(role="assistant", content=message))
                turn_count = len(turns)
            return AgentTurnResponse(
                channel_id=pending.channel_id,
                thread_ts=pending.thread_ts,
                message=message,
                tool_executions=[execution],
                conversation_turn_count=turn_count,
            )

        if not pending.tool_call_id or not pending.continuation_messages:
            raise RuntimeError("Pending MCP action does not contain a resumable Gemini continuation.")
        try:
            brain_result = self.agent_brain.resume(
                continuation_messages=pending.continuation_messages,
                tool_call_id=pending.tool_call_id,
                execution=execution,
            )
        except Exception:
            with self._conversation_lock:
                pending.status = "pending"
            raise
        follow_up_pending = self._pending_action_from_brain(
            AgentTurnRequest(
                channel_id=pending.channel_id,
                thread_ts=pending.thread_ts,
                user_id=pending.requested_by,
                text=f"Resume approved action {pending.tool_name}",
            ),
            brain_result,
        )

        with self._conversation_lock:
            pending.status = "executed"
            key = self._conversation_key(pending.channel_id, pending.thread_ts)
            turns = self._conversations.setdefault(key, [])
            turns.append(ConversationTurn(role="tool", content=f"{execution.tool_name}: {execution.output}"))
            message = f"Approved by <@{decided_by}>.\n\n{brain_result.message}"
            turns.append(ConversationTurn(role="assistant", content=message))
            turn_count = len(turns)

        return AgentTurnResponse(
            channel_id=pending.channel_id,
            thread_ts=pending.thread_ts,
            message=message,
            tool_executions=brain_result.executions,
            pending_action=follow_up_pending,
            conversation_turn_count=turn_count,
        )

    def deny_agent_action(self, action_id: str, decided_by: str) -> AgentTurnResponse:
        with self._conversation_lock:
            pending = self._require_pending_action(action_id)
            pending.status = "denied"
            key = self._conversation_key(pending.channel_id, pending.thread_ts)
            turns = self._conversations.setdefault(key, [])
            message = f"Denied by <@{decided_by}>. `{pending.tool_name}` was not executed."
            turns.append(ConversationTurn(role="assistant", content=message))
            turn_count = len(turns)
        return AgentTurnResponse(
            channel_id=pending.channel_id,
            thread_ts=pending.thread_ts,
            message=message,
            conversation_turn_count=turn_count,
        )

    def edit_agent_action(self, action_id: str, arguments: dict[str, Any], edited_by: str) -> PendingToolAction:
        with self._conversation_lock:
            pending = self._require_pending_action(action_id)
            pending.arguments = arguments
            pending.status = "edited"
            if pending.tool_call_id:
                for message in pending.continuation_messages:
                    for tool_call in message.get("tool_calls", []):
                        if tool_call.get("id") == pending.tool_call_id:
                            tool_call["function"]["arguments"] = json.dumps(arguments)
                    for part in message.get("parts", []):
                        function_call = part.get("function_call")
                        if function_call and function_call.get("id") == pending.tool_call_id:
                            function_call["args"] = arguments
            key = self._conversation_key(pending.channel_id, pending.thread_ts)
            self._conversations.setdefault(key, []).append(
                ConversationTurn(
                    role="assistant",
                    content=f"<@{edited_by}> edited `{pending.tool_name}` parameters: {arguments}",
                )
            )
            return pending.model_copy(deep=True)

    def get_pending_agent_action(self, action_id: str) -> PendingToolAction:
        with self._conversation_lock:
            return self._require_pending_action(action_id).model_copy(deep=True)

    def _require_pending_action(self, action_id: str) -> PendingToolAction:
        action = self._pending_actions.get(action_id)
        if action is None:
            raise ValueError("Agent action was not found or has expired.")
        if action.status not in {"pending", "edited"}:
            raise ValueError(f"Agent action is already {action.status}.")
        return action

    def _latest_pending_action(self, channel_id: str, thread_ts: str) -> PendingToolAction | None:
        with self._conversation_lock:
            for action in reversed(list(self._pending_actions.values())):
                if (
                    action.channel_id == channel_id
                    and action.thread_ts == thread_ts
                    and action.status in {"pending", "edited"}
                ):
                    return action.model_copy(deep=True)
        return None

    def _pending_action_from_brain(
        self,
        request: AgentTurnRequest,
        result: BrainRunResult | GeminiBrainRunResult,
    ) -> PendingToolAction | None:
        if not result.pending_tool_name:
            return None
        pending = PendingToolAction(
            channel_id=request.channel_id,
            thread_ts=request.thread_ts,
            requested_by=request.user_id,
            tool_name=result.pending_tool_name,
            arguments=result.pending_arguments,
            tool_call_id=result.pending_tool_call_id,
            continuation_messages=result.continuation_messages,
        )
        with self._conversation_lock:
            self._pending_actions[pending.action_id] = pending
        return pending

    def run_assessment(self, request: RiskAssessmentRequest) -> RiskAssessmentResponse:
        logger.info(
            "risk_assessment_started",
            extra={
                "ticker": request.ticker,
                "channel_id": request.channel_id,
                "requested_by": request.requested_by,
                "context_result_count": request.internal_workspace_perception.raw_result_count,
            },
        )
        state: AssessmentState = {"request": request}
        if self._graph is not None:
            final_state = self._graph.invoke(state)
        else:
            final_state = self._decision_checkpoint(self._sentiment_synthesis(self._external_reality(state)))

        response = RiskAssessmentResponse(
            assessment_id=str(uuid.uuid4()),
            ticker=request.ticker,
            internal_consensus_percent=final_state["internal_consensus_percent"],
            internal_evidence=final_state["internal_evidence"],
            external_risk_markers=[marker.as_display_text() for marker in final_state["external_markers"]],
            max_external_severity=final_state["max_external_severity"],
            verdict=final_state["verdict"],
            target_mitigation_orders=final_state["target_mitigation_orders"],
        )
        logger.info(
            "risk_assessment_completed",
            extra={
                "assessment_id": response.assessment_id,
                "ticker": response.ticker,
                "internal_consensus_percent": response.internal_consensus_percent,
                "max_external_severity": response.max_external_severity,
                "verdict": response.verdict,
            },
        )
        return response

    def handle_trading_checkpoint(
        self,
        *,
        payload: dict[str, Any],
        decision: str,
        decided_by: str,
    ) -> dict[str, Any]:
        return self.trading_node.record_checkpoint(payload=payload, decision=decision, decided_by=decided_by)

    def generate_portfolio_dashboard(self, *, refresh_market_data: bool = False) -> list[dict[str, Any]]:
        snapshot = self.trading_node.get_portfolio_snapshot(refresh_market_data=refresh_market_data)
        alert_report = self.trading_node.evaluate_portfolio_alerts(snapshot)
        logger.info("portfolio_dashboard_requested", extra={"holding_count": len(snapshot.holdings)})

        return_blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Portfolio Center", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{snapshot.portfolio_name}*  |  Stocks only  |  Cash account  |  "
                        f"Price source: *{snapshot.market_data_source}*  |  Approval-gated actions"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Cash / Buying Power:* ${snapshot.cash_balance:,.2f}  |  "
                        f"*Invested in Stocks:* ${snapshot.total_cost_basis:,.2f}  |  "
                        f"*Portfolio Equity:* ${snapshot.total_portfolio_equity:,.2f}  |  "
                        f"*Total Return:* ${snapshot.total_return_dollars:+,.2f}  |  "
                        f"*Return:* {snapshot.total_return_percent:+.2f}%"
                    ),
                },
            },
            {"type": "divider"},
        ]
        triggers = alert_report.get("triggers", [])
        if triggers:
            alert_lines = ["*Portfolio Alerts*"]
            for trigger in triggers[:8]:
                severity = str(trigger.get("severity", "LOW"))
                icon = "🔴" if severity in {"CRITICAL", "HIGH"} else "🟡"
                target = trigger.get("ticker") or trigger.get("sector") or "Portfolio"
                alert_lines.append(
                    f"{icon} *{target}* · {trigger.get('type', 'risk alert')} · "
                    f"{trigger.get('recommendation', trigger.get('reason', 'Review exposure.'))}"
                )
            return_blocks.extend(
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(alert_lines)}},
                    {"type": "divider"},
                ]
            )
        else:
            return_blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "✅ Risk checks clear · stop-loss −20% · take-profit +50% · stock 15% · sector 30%",
                        }
                    ],
                }
            )

        for holding in snapshot.holdings:
            ticker = holding.ticker
            return_blocks.extend(
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{ticker} · {holding.company_name}*  |  "
                                f"*Shares:* {holding.shares_held:g}  |  "
                                f"*Avg:* ${holding.average_buy_price:,.2f}  |  "
                                f"*Now:* ${holding.current_market_price:,.2f}  |  "
                                f"*Cost:* ${holding.total_cost_basis:,.2f}  |  "
                                f"*Value:* ${holding.current_market_value:,.2f}  |  "
                                f"*P&L:* {'🟢' if holding.unrealized_pnl_percent >= 0 else '🔴'} "
                                f"{holding.unrealized_pnl_percent:+.2f}%"
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "block_id": f"portfolio_actions_{ticker}",
                        "elements": [
                            {
                                "type": "button",
                                "action_id": "portfolio_deep_audit",
                                "text": {"type": "plain_text", "text": "🔍 Deep Audit", "emoji": True},
                                "value": f"AUDIT_{ticker}",
                            },
                            {
                                "type": "button",
                                "action_id": "portfolio_trim_allocation",
                                "text": {"type": "plain_text", "text": "⚡ Sell / Trim", "emoji": True},
                                "value": f"TRIM_{ticker}",
                            },
                        ],
                    },
                    {"type": "divider"},
                ]
            )

        return return_blocks

    def _build_graph(self) -> Any | None:
        try:
            from langgraph.graph import END, StateGraph

            graph = StateGraph(AssessmentState)
            graph.add_node("external_reality", self._external_reality)
            graph.add_node("sentiment_synthesis", self._sentiment_synthesis)
            graph.add_node("decision_checkpoint", self._decision_checkpoint)
            graph.set_entry_point("external_reality")
            graph.add_edge("external_reality", "sentiment_synthesis")
            graph.add_edge("sentiment_synthesis", "decision_checkpoint")
            graph.add_edge("decision_checkpoint", END)
            return graph.compile()
        except Exception as exc:
            logger.warning("langgraph_compile_failed_using_sequential_router", extra={"error": str(exc)})
            return None

    def _external_reality(self, state: AssessmentState) -> AssessmentState:
        request = state["request"]
        sec_markers = self.sec_node.extract_risk_markers(
            ticker=request.ticker,
            filing_text=request.sec_filing_text,
            pdf_paths=request.sec_pdf_paths,
        )
        options_signal = self.yfinance_node.fetch_options_signal(request.ticker)
        external_markers: list[SecRiskMarker | OptionsRiskMarker] = [*sec_markers, *options_signal.risk_markers]
        return {
            **state,
            "sec_markers": sec_markers,
            "options_markers": options_signal.risk_markers,
            "external_markers": external_markers,
        }

    def _sentiment_synthesis(self, state: AssessmentState) -> AssessmentState:
        request = state["request"]
        corpus = request.internal_workspace_perception.text_corpus()
        bullish_hits = self._count_terms(corpus, self.BULLISH_TERMS)
        bearish_hits = self._count_terms(corpus, self.BEARISH_TERMS)
        internal_consensus_percent = ((bullish_hits + 1) / (bullish_hits + bearish_hits + 2)) * 100
        evidence = self._extract_internal_evidence(request.internal_workspace_perception)
        markers = state["external_markers"]
        max_external_severity = max((marker.severity for marker in markers), default=0.0)
        verdict = self._evaluate_divergence(internal_consensus_percent, max_external_severity)

        logger.info(
            "sentiment_synthesis_completed",
            extra={
                "ticker": request.ticker,
                "bullish_hits": bullish_hits,
                "bearish_hits": bearish_hits,
                "internal_consensus_percent": internal_consensus_percent,
                "max_external_severity": max_external_severity,
                "verdict": verdict,
            },
        )
        return {
            **state,
            "internal_consensus_percent": internal_consensus_percent,
            "internal_evidence": evidence,
            "max_external_severity": max_external_severity,
            "verdict": verdict,
        }

    def _decision_checkpoint(self, state: AssessmentState) -> AssessmentState:
        request = state["request"]
        orders = self.trading_node.build_mitigation_orders(
            ticker=request.ticker,
            verdict=state["verdict"],
            internal_consensus_percent=state["internal_consensus_percent"],
            max_external_severity=state["max_external_severity"],
        )
        return {**state, "target_mitigation_orders": orders}

    @classmethod
    def _count_terms(cls, corpus: str, terms: set[str]) -> int:
        normalized = corpus.lower()
        return sum(len(re.findall(rf"\b{re.escape(term)}\b", normalized)) for term in terms)

    @staticmethod
    def _extract_internal_evidence(perception: InternalWorkspacePerception) -> list[str]:
        evidence: list[str] = []
        for message in perception.context_messages[:5]:
            if message.text:
                evidence.append(message.text[:240])
        for attachment in perception.file_attachments[:3]:
            label = attachment.title or "Slack attachment"
            summary = attachment.summary or attachment.filetype or "No summary supplied"
            evidence.append(f"{label}: {summary[:220]}")
        return evidence or [perception.query]

    @staticmethod
    def _evaluate_divergence(internal_consensus_percent: float, max_external_severity: float) -> str:
        if internal_consensus_percent >= 65 and max_external_severity >= 0.65:
            return (
                "Executive Blind Spot: internal Slack sentiment is bullish while SEC/options reality "
                "signals material downside risk."
            )
        if internal_consensus_percent <= 40 and max_external_severity <= 0.35:
            return (
                "Contrarian Opportunity: internal sentiment is cautious, but external risk markers "
                "are currently contained."
            )
        if max_external_severity >= 0.75:
            return "External Risk Override: market or SEC stress markers require mitigation before allocation."
        if internal_consensus_percent >= 65:
            return "Aligned Bullish Case: internal conviction is constructive and external stress is manageable."
        return "Governance Review Required: perception and external reality are mixed."
