# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent
# Copyright (c) 2026 Anand Krishnamoorthy
# SPDX-License-Identifier: MIT
#
# Purpose-built for the 2026 Slack Hackathon. See LICENSE for terms.

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.agent_brain import BrainRunResult, QwenAgentBrain
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
        agent_brain: QwenAgentBrain | None = None,
    ) -> None:
        self.sec_node = sec_node or SecRiskExtractor()
        self.yfinance_node = yfinance_node or YahooFinanceOptionsWorker()
        self.trading_node = trading_node or TradingNode()
        self.mcp_client = mcp_client or AlphaChannelMCPClient()
        self.agent_brain = agent_brain or QwenAgentBrain(self.mcp_client)
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

        if not pending.tool_call_id or not pending.continuation_messages:
            raise RuntimeError("Pending MCP action does not contain a resumable Qwen continuation.")
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
        result: BrainRunResult,
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

    def generate_portfolio_dashboard(self) -> list[dict[str, Any]]:
        holdings = self.trading_node.get_portfolio_holdings()
        logger.info("portfolio_dashboard_requested", extra={"holding_count": len(holdings)})

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Portfolio Center", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Institutional Holdings Snapshot*\nReview exposure status and route assets for audit or trim logic.",
                },
            },
            {"type": "divider"},
        ]

        for holding in holdings:
            ticker = holding["Ticker"]
            blocks.extend(
                [
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Ticker*\n{ticker}"},
                            {"type": "mrkdwn", "text": f"*Allocation*\n{holding['Allocation']}"},
                            {"type": "mrkdwn", "text": f"*Status*\n{holding['Status']}"},
                        ],
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
                                "text": {"type": "plain_text", "text": "⚡ Trim", "emoji": True},
                                "value": f"TRIM_{ticker}",
                            },
                        ],
                    },
                    {"type": "divider"},
                ]
            )

        return blocks

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
