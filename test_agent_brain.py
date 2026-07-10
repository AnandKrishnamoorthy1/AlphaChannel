from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from engine.agent_brain import QwenAgentBrain
from engine.mcp_client import MCPToolExecution, MCPToolPolicy


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def openai_tool_schemas() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "yfinance_risk_lookup",
                    "description": "Fetch live options risk.",
                    "parameters": {
                        "type": "object",
                        "properties": {"ticker": {"type": "string"}},
                        "required": ["ticker"],
                    },
                },
            }
        ]

    @staticmethod
    def policy_for(tool_name: str) -> MCPToolPolicy:
        return MCPToolPolicy(
            name=tool_name,
            description=tool_name,
            read_only=tool_name != "execute_trade_checkpoint",
            requires_approval=tool_name == "execute_trade_checkpoint",
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        self.calls.append((tool_name, arguments))
        return MCPToolExecution(
            tool_name=tool_name,
            arguments=arguments,
            output='{"ticker":"NOW","front_month_iv":0.31}',
        )


class FakeCompletions:
    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages

    def create(self, **_: Any) -> Any:
        return SimpleNamespace(choices=[SimpleNamespace(message=self._messages.pop(0))])


def _brain(mcp_client: FakeMCPClient, messages: list[Any]) -> QwenAgentBrain:
    brain = object.__new__(QwenAgentBrain)
    brain.mcp_client = mcp_client
    brain.max_iterations = 4
    brain.model = "test-qwen"
    brain.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(messages)))
    return brain


def test_qwen_selects_safe_tool_then_synthesizes() -> None:
    tool_call = SimpleNamespace(
        id="call-market",
        function=SimpleNamespace(name="yfinance_risk_lookup", arguments='{"ticker":"NOW"}'),
    )
    final_payload = {
        "summary": "ServiceNow options risk is contained based on the live MCP result.",
        "ticker": "NOW",
        "internal_consensus_percent": 58,
        "external_risk_markers": ["Front-month IV is 31%"],
        "verdict": "Governance review before allocation",
        "recommended_actions": ["Monitor option skew"],
        "confidence": 0.83,
    }
    mcp_client = FakeMCPClient()
    brain = _brain(
        mcp_client,
        [
            SimpleNamespace(content="", tool_calls=[tool_call]),
            SimpleNamespace(content=json.dumps(final_payload), tool_calls=None),
        ],
    )

    result = brain.run(goal="Is ServiceNow risky?", conversation=[], workspace_context="Team is bullish.")

    assert mcp_client.calls == [("yfinance_risk_lookup", {"ticker": "NOW"})]
    assert result.synthesis is not None
    assert result.synthesis.ticker == "NOW"
    assert result.executions[0].tool_name == "yfinance_risk_lookup"


def test_qwen_consequential_tool_pauses_before_execution() -> None:
    tool_call = SimpleNamespace(
        id="call-trade",
        function=SimpleNamespace(
            name="execute_trade_checkpoint",
            arguments='{"ticker":"NOW","side":"hedge","notional_usd":100000,"rationale":"Reduce delta"}',
        ),
    )
    mcp_client = FakeMCPClient()
    brain = _brain(mcp_client, [SimpleNamespace(content="", tool_calls=[tool_call])])

    result = brain.run(goal="Hedge NOW", conversation=[], workspace_context=None)

    assert result.pending_tool_name == "execute_trade_checkpoint"
    assert result.pending_tool_call_id == "call-trade"
    assert mcp_client.calls == []
