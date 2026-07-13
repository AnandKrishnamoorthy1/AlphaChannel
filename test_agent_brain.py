from __future__ import annotations

from engine.agent_brain import AgentSynthesis, BrainRunResult


def test_provider_neutral_synthesis_formats_for_slack() -> None:
    synthesis = AgentSynthesis(
        summary="Evidence supports monitored exposure.",
        ticker="NOW",
        internal_consensus_percent=62,
        external_risk_markers=["Premium valuation"],
        verdict="HOLD",
        recommended_actions=["Monitor revenue growth"],
        sources=["Yahoo Finance MCP"],
        confidence=0.82,
    )

    message = synthesis.as_slack_message()

    assert "*Asset:* NOW" in message
    assert "*Verdict:* HOLD" in message
    assert "• Premium valuation" in message


def test_brain_result_defaults_to_no_executions() -> None:
    result = BrainRunResult(message="Complete")

    assert result.executions == []
    assert result.pending_tool_name is None
