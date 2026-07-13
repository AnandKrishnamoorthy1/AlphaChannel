"""Provider-neutral result contracts for AlphaChannel's Gemini orchestration runtime.

AlphaChannel is purpose-built for the 2026 Slack Hackathon.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from engine.mcp_client import MCPToolExecution


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
