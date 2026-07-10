from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from engine.mcp_client import AlphaChannelMCPClient, MCPToolExecution

logger = logging.getLogger("alpha_channel.engine.brain")


class AgentSynthesis(BaseModel):
    summary: str = Field(min_length=1, max_length=3000)
    ticker: str | None = None
    internal_consensus_percent: float | None = Field(default=None, ge=0, le=100)
    external_risk_markers: list[str] = Field(default_factory=list, max_length=10)
    verdict: str = Field(min_length=1, max_length=800)
    recommended_actions: list[str] = Field(default_factory=list, max_length=8)
    sources: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)

    def as_slack_message(self) -> str:
        ticker_line = f"*Asset:* {self.ticker}\n" if self.ticker else ""
        consensus_line = (
            f"*Internal consensus:* {self.internal_consensus_percent:.0f}%\n"
            if self.internal_consensus_percent is not None
            else ""
        )
        risks = "\n".join(f"• {item}" for item in self.external_risk_markers) or "• No material marker returned"
        actions = "\n".join(f"• {item}" for item in self.recommended_actions) or "• Continue monitoring"
        sources = "\n".join(f"• {item}" for item in self.sources) or "• No source URL returned"
        return (
            f"{ticker_line}{consensus_line}*Verdict:* {self.verdict}\n\n"
            f"*Risk synthesis*\n{self.summary}\n\n"
            f"*External markers*\n{risks}\n\n"
            f"*Recommended actions*\n{actions}\n\n"
            f"*Sources*\n{sources}\n\n"
            f"_Confidence: {self.confidence:.0%}_"
        )


class BrainRunResult(BaseModel):
    message: str
    executions: list[MCPToolExecution] = Field(default_factory=list)
    pending_tool_name: str | None = None
    pending_arguments: dict[str, Any] = Field(default_factory=dict)
    pending_tool_call_id: str | None = None
    continuation_messages: list[dict[str, Any]] = Field(default_factory=list)
    synthesis: AgentSynthesis | None = None


class QwenAgentBrain:
    """Qwen-centered goal loop: plan, call MCP tools, observe, and synthesize."""

    SYSTEM_PROMPT = """You are AlphaChannel's central autonomous dispatcher and institutional risk analyst.
Given a user's goal, decide which registered MCP tools to call, in what order, and with which parameters.
For any request about whether a public company is risky, investable, or suitable to buy, you MUST call both
yfinance_risk_lookup and sec_risk_lookup before answering. Use portfolio_holdings
for portfolio questions. Never invent live market values or tool output. Call one tool at a time.

Read-only tools may execute autonomously. Consequential tools are interrupted by the host for human approval.
After enough evidence is collected, return ONLY a JSON object matching this schema:
{
  "summary": "specific evidence-grounded analysis",
  "ticker": "symbol or null",
  "internal_consensus_percent": 0-100 or null,
  "external_risk_markers": ["specific marker"],
  "verdict": "clear governance verdict",
  "recommended_actions": ["action"],
  "sources": ["source URL from tool output"],
  "confidence": 0.0-1.0
}
Do not wrap the JSON in Markdown. Use evidence from the Slack context and MCP results. If evidence is missing,
say so explicitly and lower confidence."""

    def __init__(self, mcp_client: AlphaChannelMCPClient, max_iterations: int = 6) -> None:
        self.mcp_client = mcp_client
        self.max_iterations = max_iterations
        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY or QWEN_API_KEY is required for the agent brain.")
        endpoint = os.getenv("DASHSCOPE_ENDPOINT", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        self.model = os.getenv("QWEN_MODEL", "qwen-plus")
        self.client = OpenAI(
            api_key=api_key,
            base_url=endpoint.rstrip("/"),
            timeout=float(os.getenv("QWEN_TIMEOUT_SECONDS", "45")),
            max_retries=1,
        )

    def run(
        self,
        *,
        goal: str,
        conversation: list[dict[str, str]],
        workspace_context: str | None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> BrainRunResult:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        messages.extend(conversation[-12:])
        context = (workspace_context or "No Slack RTS context was available.")[:12_000]
        messages.append(
            {
                "role": "user",
                "content": f"Goal:\n{goal}\n\nInternal Workspace Perception:\n{context}",
            }
        )
        logger.info(
            "qwen_agent_planning_started model=%s goal_chars=%d",
            self.model,
            len(goal),
        )
        if progress_callback:
            progress_callback("Qwen is decomposing the goal and selecting evidence tools...")
        return self._run_loop(messages, [], progress_callback)

    def resume(
        self,
        *,
        continuation_messages: list[dict[str, Any]],
        tool_call_id: str,
        execution: MCPToolExecution,
        progress_callback: Callable[[str], None] | None = None,
    ) -> BrainRunResult:
        messages = [*continuation_messages, self._tool_result_message(tool_call_id, execution)]
        logger.info(
            "qwen_agent_resumed_after_approval",
            extra={"tool_name": execution.tool_name, "tool_call_id": tool_call_id},
        )
        return self._run_loop(messages, [execution], progress_callback)

    def _run_loop(
        self,
        messages: list[dict[str, Any]],
        executions: list[MCPToolExecution],
        progress_callback: Callable[[str], None] | None,
    ) -> BrainRunResult:
        tools = self.mcp_client.openai_tool_schemas()
        for iteration in range(1, self.max_iterations + 1):
            logger.info(
                "qwen_agent_reasoning_iteration iteration=%d tool_result_count=%d",
                iteration,
                len(executions),
            )
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.15,
                top_p=0.8,
                parallel_tool_calls=False,
            )
            if not completion.choices:
                raise RuntimeError("Qwen agent returned no completion choices.")
            response_message = completion.choices[0].message
            assistant_message = self._assistant_message(response_message)
            messages.append(assistant_message)
            tool_calls = list(response_message.tool_calls or [])
            if not tool_calls:
                if progress_callback:
                    progress_callback("Qwen is validating the final governance synthesis...")
                synthesis = self._parse_synthesis(response_message.content or "")
                logger.info(
                    "qwen_agent_synthesis_completed ticker=%s confidence=%.2f",
                    synthesis.ticker,
                    synthesis.confidence,
                )
                return BrainRunResult(
                    message=synthesis.as_slack_message(),
                    executions=executions,
                    synthesis=synthesis,
                )

            tool_call = tool_calls[0]
            tool_name = tool_call.function.name
            arguments = self._parse_arguments(tool_call.function.arguments)
            policy = self.mcp_client.policy_for(tool_name)
            logger.info(
                "qwen_agent_tool_selected iteration=%d tool=%s requires_approval=%s arguments=%s",
                iteration,
                tool_name,
                policy.requires_approval,
                json.dumps(arguments, sort_keys=True),
            )
            if progress_callback:
                progress_callback(f"Qwen selected {tool_name}; checking execution policy...")
            if policy.requires_approval:
                return BrainRunResult(
                    message=f"Qwen selected `{tool_name}` to advance the goal. Human approval is required.",
                    executions=executions,
                    pending_tool_name=tool_name,
                    pending_arguments=arguments,
                    pending_tool_call_id=tool_call.id,
                    continuation_messages=messages,
                )

            execution = self.mcp_client.call_tool(tool_name, arguments)
            executions.append(execution)
            if progress_callback:
                progress_callback(f"MCP returned {tool_name} evidence; Qwen is evaluating the observation...")
            messages.append(self._tool_result_message(tool_call.id, execution))

        raise RuntimeError(f"Qwen agent exceeded the {self.max_iterations}-iteration safety limit.")

    @staticmethod
    def _assistant_message(message: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in message.tool_calls
            ]
        return payload

    @staticmethod
    def _tool_result_message(tool_call_id: str, execution: MCPToolExecution) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": execution.output[:20_000],
        }

    @staticmethod
    def _parse_arguments(raw_arguments: str) -> dict[str, Any]:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Qwen produced invalid tool arguments: {exc}") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError("Qwen tool arguments must be a JSON object.")
        return arguments

    @staticmethod
    def _parse_synthesis(content: str) -> AgentSynthesis:
        normalized = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            normalized = fenced.group(1)
        try:
            return AgentSynthesis.model_validate_json(normalized)
        except (ValidationError, ValueError) as exc:
            raise RuntimeError(f"Qwen final synthesis failed schema validation: {exc}") from exc
