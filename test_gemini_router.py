from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from google.genai import types

from engine.core_router import AgentTurnRequest, AlphaChannelRouter, AssetResolution, GeminiOrchestrationBrain, GeminiRiskSynthesis
from engine.mcp_client import MCPToolExecution, MCPToolPolicy


class FakeMCPClient:
    def __init__(self, consequential: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.consequential = consequential

    def gemini_function_declarations(self) -> list[dict[str, Any]]:
        name = "execute_trade_checkpoint" if self.consequential else "yfinance_fundamental_lookup"
        return [
            {
                "name": name,
                "description": "Test MCP tool",
                "parameters_json_schema": {
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
            }
        ]

    def policy_for(self, tool_name: str) -> MCPToolPolicy:
        return MCPToolPolicy(
            name=tool_name,
            description=tool_name,
            read_only=not self.consequential,
            requires_approval=self.consequential,
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        self.calls.append((tool_name, arguments))
        return MCPToolExecution(
            tool_name=tool_name,
            arguments=arguments,
            output='{"ticker":"NOW","front_month_iv":0.31}',
        )


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _function_response(name: str) -> Any:
    return SimpleNamespace(
        function_calls=[SimpleNamespace(id="call-1", name=name, args={"ticker": "NOW"})],
        candidates=[],
        text="",
    )


def _asset_response(ticker: str, company_name: str) -> Any:
    asset = AssetResolution(ticker=ticker, company_name=company_name, confidence=0.98)
    return SimpleNamespace(parsed=asset, text=asset.model_dump_json())


def test_gemini_executes_safe_mcp_tool_then_returns_structured_output() -> None:
    synthesis = GeminiRiskSynthesis(
        internal_consensus_percent=67,
        external_risk_markers=["Front-month implied volatility is 31%"],
        divergence_analysis="Internal conviction is stronger than the external risk evidence supports.",
        verdict="HOLD",
        recommended_actions=["Monitor volatility skew"],
    )
    models = FakeModels(
        [
            _asset_response("NOW", "ServiceNow"),
            _function_response("yfinance_risk_lookup"),
            SimpleNamespace(function_calls=[], candidates=[], text="Evidence complete."),
            SimpleNamespace(parsed=synthesis, text=synthesis.model_dump_json()),
        ]
    )
    mcp_client = FakeMCPClient()
    brain = GeminiOrchestrationBrain(
        mcp_client,  # type: ignore[arg-type]
        client=SimpleNamespace(models=models),
    )

    result = brain.run(
        goal="Evaluate ServiceNow",
        conversation=[],
        workspace_context="The team is bullish.",
    )

    assert mcp_client.calls == [("yfinance_fundamental_lookup", {"ticker": "NOW"})]
    assert result.synthesis == synthesis
    assert models.requests[1]["model"] == "gemini-flash-latest"
    assert models.requests[1]["config"].tools
    assert models.requests[1]["config"].automatic_function_calling.disable is True
    synthesis_config = models.requests[3]["config"]
    assert synthesis_config.response_schema is None
    assert synthesis_config.response_json_schema["additionalProperties"] is False
    assert set(synthesis_config.response_json_schema["properties"]) == {
        "internal_consensus_percent",
        "external_risk_markers",
        "divergence_analysis",
        "verdict",
        "recommended_actions",
    }


def test_gemini_pauses_consequential_tool_before_execution() -> None:
    mcp_client = FakeMCPClient(consequential=True)
    models = FakeModels([_asset_response("NOW", "ServiceNow"), _function_response("execute_trade_checkpoint")])
    brain = GeminiOrchestrationBrain(
        mcp_client,  # type: ignore[arg-type]
        client=SimpleNamespace(models=models),
    )

    result = brain.run(
        goal="Buy ServiceNow",
        conversation=[],
        workspace_context=None,
    )

    assert result.pending_tool_name == "execute_trade_checkpoint"
    assert result.pending_tool_call_id == "call-1"
    assert mcp_client.calls == []


def test_requested_meta_asset_overrides_stale_slack_context() -> None:
    synthesis = GeminiRiskSynthesis(
        internal_consensus_percent=50,
        external_risk_markers=[],
        divergence_analysis="META evidence requires follow-up.",
        verdict="HOLD",
        recommended_actions=["Retry the META evidence lookup"],
    )
    models = FakeModels(
        [
            _asset_response("META", "Meta Platforms"),
            _function_response("yfinance_risk_lookup"),
            SimpleNamespace(function_calls=[], candidates=[], text="Evidence complete."),
            SimpleNamespace(parsed=synthesis, text=synthesis.model_dump_json()),
        ]
    )
    mcp_client = FakeMCPClient()
    brain = GeminiOrchestrationBrain(mcp_client, client=SimpleNamespace(models=models))  # type: ignore[arg-type]

    brain.run(
        goal="Run Meta/facebook Risk Assessment",
        conversation=[],
        workspace_context="Prior thread discussed Nubank NU and its allocation.",
    )

    assert mcp_client.calls == [("yfinance_fundamental_lookup", {"ticker": "META"})]


def test_net_alias_resolves_to_cloudflare_ticker() -> None:
    assert GeminiOrchestrationBrain._extract_requested_ticker("Check if Net stock is a good buy") == "NET"
    assert GeminiOrchestrationBrain._extract_requested_ticker("Evaluate Cloudflare") == "NET"


def test_duplicate_tool_lookup_is_blocked_after_first_attempt() -> None:
    synthesis = GeminiRiskSynthesis(
        internal_consensus_percent=50,
        external_risk_markers=["Fundamentals unavailable"],
        divergence_analysis="The single permitted lookup failed, so evidence is incomplete.",
        verdict="HOLD",
        recommended_actions=["Retry after the data provider recovers"],
    )
    models = FakeModels(
        [
            _asset_response("PANW", "Palo Alto Networks"),
            _function_response("yfinance_fundamental_lookup"),
            _function_response("yfinance_fundamental_lookup"),
            SimpleNamespace(parsed=synthesis, text=synthesis.model_dump_json()),
        ]
    )
    mcp_client = FakeMCPClient()
    brain = GeminiOrchestrationBrain(mcp_client, client=SimpleNamespace(models=models))  # type: ignore[arg-type]

    result = brain.run(
        goal="Check if Palo Alto Networks is a good or bad buy",
        conversation=[],
        workspace_context=None,
    )

    assert mcp_client.calls == [("yfinance_fundamental_lookup", {"ticker": "PANW"})]
    assert len(result.executions) == 1
    assert result.executions[0].is_error is False


def test_explicit_buy_request_skips_risk_analysis_and_creates_checkpoint() -> None:
    mcp_client = FakeMCPClient()
    router = AlphaChannelRouter.__new__(AlphaChannelRouter)
    router.mcp_client = mcp_client
    router.agent_brain = SimpleNamespace(run=lambda **_: (_ for _ in ()).throw(AssertionError("LLM should not run")))
    router._conversation_lock = __import__("threading").RLock()
    router._conversations = {}
    router._pending_actions = {}

    result = router.process_agent_turn(
        AgentTurnRequest(
            channel_id="C123",
            thread_ts="123.456",
            user_id="U123",
            text="Buy $500 of NOW stock",
        )
    )

    assert result.pending_action is not None
    assert result.pending_action.tool_name == "execute_trade_checkpoint"
    assert result.pending_action.arguments["ticker"] == "NOW"
    assert result.pending_action.arguments["side"] == "buy"
    assert result.pending_action.arguments["notional_usd"] == 500.0
    assert "No risk analysis" in result.message


def test_duplicate_trade_request_reuses_existing_checkpoint() -> None:
    mcp_client = FakeMCPClient()
    router = AlphaChannelRouter.__new__(AlphaChannelRouter)
    router.mcp_client = mcp_client
    router.agent_brain = SimpleNamespace(run=lambda **_: (_ for _ in ()).throw(AssertionError("LLM should not run")))
    router._conversation_lock = __import__("threading").RLock()
    router._conversations = {}
    router._pending_actions = {}
    request = AgentTurnRequest(
        channel_id="C123",
        thread_ts="123.456",
        user_id="U123",
        text="Sell $56 of NU",
    )

    first = router.process_agent_turn(request)
    second = router.process_agent_turn(request)

    assert first.pending_action is not None
    assert second.pending_action is not None
    assert second.pending_action.action_id == first.pending_action.action_id
    assert len(router._pending_actions) == 1
