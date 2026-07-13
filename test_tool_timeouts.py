from __future__ import annotations

import asyncio
import time
from typing import Any

from engine.mcp_client import AlphaChannelMCPClient, MCPToolExecution
from engine.yfinance_node import (
    FundamentalSignal,
    OptionsSignal,
    YahooFinanceFundamentalsWorker,
    YahooFinanceOptionsWorker,
)
from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient


class SlowYahooWorker(YahooFinanceOptionsWorker):
    def _fetch_live_options_signal(self, ticker: str) -> OptionsSignal:
        time.sleep(1.2)
        return OptionsSignal(ticker=ticker)


class CountingYahooWorker(YahooFinanceOptionsWorker):
    calls = 0

    def _fetch_live_options_signal(self, ticker: str) -> OptionsSignal:
        type(self).calls += 1
        return OptionsSignal(ticker=ticker, current_price=100.0)


def test_yfinance_timeout_returns_typed_fallback() -> None:
    YahooFinanceOptionsWorker._cache.clear()
    result = SlowYahooWorker(timeout_seconds=1.0).fetch_options_signal("NOW")

    assert result.ticker == "NOW"
    assert result.raw_metadata["feed_status"] == "unavailable"
    assert result.risk_markers[0].label == "Options feed unavailable"
    assert "did not respond" in result.risk_markers[0].evidence


def test_yfinance_reuses_short_lived_cache() -> None:
    YahooFinanceOptionsWorker._cache.clear()
    CountingYahooWorker.calls = 0
    worker = CountingYahooWorker(timeout_seconds=1.0)

    first = worker.fetch_options_signal("NOW")
    second = worker.fetch_options_signal("now")

    assert first.current_price == second.current_price == 100.0
    assert CountingYahooWorker.calls == 1


def test_fundamental_signal_maps_investment_metrics() -> None:
    signal = YahooFinanceFundamentalsWorker._build_signal(
        "META",
        {
            "currentPrice": 667.4,
            "marketCap": 1_690_000_000_000,
            "totalRevenue": 214_000_000_000,
            "revenueGrowth": 0.33,
            "earningsGrowth": 0.62,
            "trailingPE": 24.3,
            "forwardPE": 18.1,
            "profitMargins": 0.32,
            "returnOnEquity": 0.33,
            "debtToEquity": 35.6,
            "longBusinessSummary": "Meta Platforms operates social and AI products.",
        },
    )

    assert isinstance(signal, FundamentalSignal)
    assert signal.ticker == "META"
    assert signal.trailing_pe == 24.3
    assert signal.revenue_growth == 0.33
    assert signal.forward_pe == 18.1


def test_yahoo_finance_mcp_decodes_fastmcp_json_result() -> None:
    payload = '{"currentPrice": 107.71, "trailingPE": 58.4, "totalRevenue": 12000000000}'

    decoded = YahooFinanceMCPClient._decode_payload(payload)

    assert decoded["currentPrice"] == 107.71
    assert decoded["trailingPE"] == 58.4


def test_mcp_timeout_returns_error_observation(monkeypatch: Any) -> None:
    client = AlphaChannelMCPClient(tool_timeout_seconds=2.0, persistent=False)

    async def slow_call(tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        await asyncio.sleep(2.5)
        return MCPToolExecution(tool_name=tool_name, arguments=arguments, output="late")

    monkeypatch.setattr(client, "_call_tool", slow_call)
    result = client.call_tool("system_status", {})

    assert result.is_error is True
    assert "mcp_tool_timeout" in result.output


def test_sec_tool_uses_dedicated_longer_timeout(monkeypatch: Any) -> None:
    monkeypatch.setenv("ALPHACHANNEL_SEC_TOOL_TIMEOUT_SECONDS", "60")
    client = AlphaChannelMCPClient(tool_timeout_seconds=35, persistent=False)

    assert client._execution_timeout("sec_risk_lookup") == 60
    assert client._execution_timeout("system_status") == 35


def test_sec_tool_isolated_from_persistent_session(monkeypatch: Any) -> None:
    client = AlphaChannelMCPClient(tool_timeout_seconds=35, persistent=True)
    sentinel = MCPToolExecution(tool_name="sec_risk_lookup", arguments={}, output="isolated")

    monkeypatch.setattr(client, "_call_tool_one_shot", lambda tool_name, arguments: sentinel)
    monkeypatch.setattr(
        client,
        "_call_tool_persistent",
        lambda tool_name, arguments: (_ for _ in ()).throw(AssertionError("persistent path used")),
    )

    assert client.call_tool("sec_risk_lookup", {"ticker": "AMZN"}) is sentinel
