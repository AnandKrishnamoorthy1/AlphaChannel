from engine.portfolio_store import PortfolioStore
from engine.trading_node import TradingNode, build_seed_portfolio
from pathlib import Path
from skills.stop_loss_take_profit.trigger_tools import StopLossTakeProfitEngine


def test_seed_portfolio_calculates_equity_only_metrics() -> None:
    snapshot = build_seed_portfolio()

    assert snapshot.portfolio_name == "Robinhood Agentic Account #X-4821"
    assert snapshot.cash_balance == 8_518.80
    assert snapshot.unallocated_buying_power == 8_518.80
    assert [holding.ticker for holding in snapshot.holdings] == ["NOW", "SNOW", "NU"]
    assert snapshot.total_cost_basis == 1_481.20
    assert snapshot.total_portfolio_equity == 10_194.22
    assert snapshot.total_return_dollars == 194.22
    assert snapshot.total_return_percent == 13.11


def test_trading_node_holdings_contract_contains_no_options_data() -> None:
    db_path = Path("data/test_holdings_contract.db")
    if db_path.exists():
        db_path.unlink()
    try:
        holdings = TradingNode(portfolio_store=PortfolioStore(db_path)).get_portfolio_holdings()

        assert {holding["ticker"] for holding in holdings} == {"NOW", "SNOW", "NU"}
        assert all(holding["asset_type"] == "equity" for holding in holdings)
        assert all("option" not in key.lower() for holding in holdings for key in holding)
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_approved_paper_sell_updates_persistent_position() -> None:
    db_path = Path("data/test_portfolio_state.db")
    if db_path.exists():
        db_path.unlink()
    try:
        store = PortfolioStore(db_path)
        trading_node = TradingNode(portfolio_store=store)

        result = trading_node.apply_dry_run_trade(ticker="NU", side="sell", notional_usd=56.00)
        snapshot = trading_node.get_portfolio_snapshot()
        nu = next(holding for holding in snapshot.holdings if holding.ticker == "NU")

        assert result["state_changed"] is True
        assert result["shares_changed"] == 4.0
        assert nu.shares_held == 36.0
        assert snapshot.cash_balance == 8_574.80
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_new_ticker_buy_uses_live_reference_price() -> None:
    db_path = Path("data/test_new_ticker_buy.db")
    if db_path.exists():
        db_path.unlink()
    try:
        store = PortfolioStore(db_path)

        result = store.apply_dry_run_trade(
            ticker="META",
            side="buy",
            notional_usd=500.0,
            reference_price=625.0,
            company_name="Meta Platforms, Inc.",
            sector="Communication Services",
        )
        stored = store.load()
        meta = next(position for position in stored["positions"] if position["ticker"] == "META")

        assert result["state_changed"] is True
        assert result["shares_changed"] == 0.8
        assert meta["company_name"] == "Meta Platforms, Inc."
        assert meta["fallback_market_price"] == 625.0
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_live_price_extraction_supports_nested_yahoo_mcp_envelope() -> None:
    payload = {
        "quote": {
            "symbol": "SNOW",
            "regularMarketPrice": {"raw": 214.37, "fmt": "214.37"},
        }
    }

    assert TradingNode._extract_live_price(payload) == 214.37


def test_portfolio_monitor_uses_loss_profit_and_concentration_policy() -> None:
    report = StopLossTakeProfitEngine().assess_all_triggers(
        {
            "total_value": 1000.0,
            "positions": [
                {"ticker": "NOW", "sector": "Software", "value": 200.0, "pct_of_portfolio": 20.0},
                {"ticker": "SNOW", "sector": "Software", "value": 150.0, "pct_of_portfolio": 15.0},
                {"ticker": "NU", "sector": "Financial Services", "value": 100.0, "pct_of_portfolio": 10.0},
            ],
            "history": [
                {"ticker": "NOW", "shares": 2, "avg_cost": 100.0, "current_price": 75.0},
                {"ticker": "SNOW", "shares": 1, "avg_cost": 100.0, "current_price": 160.0},
                {"ticker": "NU", "shares": 1, "avg_cost": 100.0, "current_price": 110.0},
            ],
        }
    )

    trigger_types = {trigger["type"] for trigger in report["triggers"]}
    assert "stop_loss" in trigger_types
    assert "take_profit" in trigger_types
    assert "concentration_breach" in trigger_types
    assert "sector_concentration_breach" in trigger_types


def test_sector_weight_includes_cash_in_total_portfolio_value() -> None:
    report = StopLossTakeProfitEngine().assess_all_triggers(
        {
            "total_value": 1000.0,
            "positions": [
                {"ticker": "NOW", "sector": "Software", "value": 250.0, "pct_of_portfolio": 25.0},
                {"ticker": "NU", "sector": "Financial Services", "value": 100.0, "pct_of_portfolio": 10.0},
            ],
            "history": [],
        }
    )

    assert not any(trigger["type"] == "sector_concentration_breach" for trigger in report["triggers"])
