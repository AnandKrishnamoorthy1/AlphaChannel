# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent
# Copyright (c) 2026 Anand Krishnamoorthy
# SPDX-License-Identifier: MIT
#
# Purpose-built for the 2026 Slack Hackathon. See LICENSE for terms.

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from engine.portfolio_store import PortfolioStore

logger = logging.getLogger("alpha_channel.hackathon_2026.engine.trading")

DecisionType = Literal["approve_buy_allocation", "execute_protective_hedge", "reject", "defer"]


class TargetMitigationOrder(BaseModel):
    order_type: str
    ticker: str
    rationale: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class TradingDecisionCheckpoint(BaseModel):
    assessment_id: str
    ticker: str
    verdict: str
    decision: DecisionType
    decided_by: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dry_run: bool = True


class PortfolioHolding(BaseModel):
    """Stock-only position fields aligned with a Robinhood-style cash account."""

    ticker: str
    company_name: str
    sector: str = "Unknown"
    asset_type: Literal["equity"] = "equity"
    shares_held: float = Field(gt=0)
    average_buy_price: float = Field(gt=0)
    current_market_price: float = Field(gt=0)
    total_cost_basis: float = Field(ge=0)
    current_market_value: float = Field(ge=0)
    unrealized_pnl_percent: float


class PortfolioSnapshot(BaseModel):
    """Paper Robinhood Agentic Account snapshot for the Slack Portfolio Center."""

    portfolio_name: str
    account_type: Literal["robinhood_agentic_account"] = "robinhood_agentic_account"
    cash_balance: float = Field(ge=0)
    unallocated_buying_power: float = Field(ge=0)
    holdings: list[PortfolioHolding]
    total_cost_basis: float = Field(ge=0)
    total_portfolio_equity: float = Field(ge=0)
    total_return_dollars: float
    total_return_percent: float
    market_data_source: str = "Stored reference prices"
    market_data_live: bool = False
    market_data_refreshed_at: datetime | None = None


def build_seed_portfolio() -> PortfolioSnapshot:
    """Build the deterministic paper portfolio used to initialize account metrics."""
    cash_balance = 8_518.80
    positions = (
        ("NOW", "ServiceNow", "Software", 2.0, 104.10, 107.71),
        ("SNOW", "Snowflake", "Software", 5.0, 165.00, 180.00),
        ("NU", "Nu Holdings", "Financial Services", 40.0, 11.20, 14.00),
    )
    holdings: list[PortfolioHolding] = []
    for ticker, company_name, sector, shares, average_buy, current_price in positions:
        cost_basis = round(shares * average_buy, 2)
        market_value = round(shares * current_price, 2)
        pnl_percent = round(((market_value - cost_basis) / cost_basis) * 100, 2)
        holdings.append(
            PortfolioHolding(
                ticker=ticker,
                company_name=company_name,
                sector=sector,
                shares_held=shares,
                average_buy_price=average_buy,
                current_market_price=current_price,
                total_cost_basis=cost_basis,
                current_market_value=market_value,
                unrealized_pnl_percent=pnl_percent,
            )
        )

    total_cost_basis = round(sum(item.total_cost_basis for item in holdings), 2)
    invested_market_value = round(sum(item.current_market_value for item in holdings), 2)
    total_return_dollars = round(invested_market_value - total_cost_basis, 2)
    total_return_percent = round((total_return_dollars / total_cost_basis) * 100, 2)
    return PortfolioSnapshot(
        portfolio_name="Robinhood Agentic Account #X-4821",
        cash_balance=cash_balance,
        unallocated_buying_power=cash_balance,
        holdings=holdings,
        total_cost_basis=total_cost_basis,
        total_portfolio_equity=round(cash_balance + invested_market_value, 2),
        total_return_dollars=total_return_dollars,
        total_return_percent=total_return_percent,
    )


class TradingNode:
    """Map recommendations to explicit human checkpoints; never auto-executes."""

    def __init__(self, portfolio_store: PortfolioStore | None = None) -> None:
        self._checkpoint_log: list[TradingDecisionCheckpoint] = []
        self.portfolio_store = portfolio_store or PortfolioStore()
        logger.info("portfolio_store_configured db_path=%s", self.portfolio_store.db_path)

    def get_portfolio_snapshot(self, *, refresh_market_data: bool = False) -> PortfolioSnapshot:
        stored = self.portfolio_store.load()
        account = stored["account"]
        positions = stored["positions"]
        live_prices: dict[str, float] = {}
        if refresh_market_data:
            live_prices = self._fetch_live_prices([str(position["ticker"]) for position in positions])

        holdings: list[PortfolioHolding] = []
        for position in positions:
            ticker = str(position["ticker"])
            current_price = live_prices.get(ticker, float(position["fallback_market_price"]))
            cost_basis = round(float(position["shares_held"]) * float(position["average_buy_price"]), 2)
            market_value = round(float(position["shares_held"]) * current_price, 2)
            holdings.append(
                PortfolioHolding(
                    ticker=ticker,
                    company_name=str(position["company_name"]),
                    sector=str(position.get("sector") or "Unknown"),
                    shares_held=float(position["shares_held"]),
                    average_buy_price=float(position["average_buy_price"]),
                    current_market_price=current_price,
                    total_cost_basis=cost_basis,
                    current_market_value=market_value,
                    unrealized_pnl_percent=round(((market_value - cost_basis) / cost_basis) * 100, 2),
                )
            )
        total_cost_basis = round(sum(item.total_cost_basis for item in holdings), 2)
        invested_market_value = round(sum(item.current_market_value for item in holdings), 2)
        total_return_dollars = round(invested_market_value - total_cost_basis, 2)
        live_count = len(live_prices)
        snapshot = PortfolioSnapshot(
            portfolio_name=str(account["portfolio_name"]),
            cash_balance=float(account["cash_balance"]),
            unallocated_buying_power=float(account["unallocated_buying_power"]),
            holdings=holdings,
            total_cost_basis=total_cost_basis,
            total_portfolio_equity=round(float(account["cash_balance"]) + invested_market_value, 2),
            total_return_dollars=total_return_dollars,
            total_return_percent=round((total_return_dollars / total_cost_basis) * 100, 2),
            market_data_source=(
                "Yahoo Finance MCP"
                if live_count == len(positions)
                else "Yahoo Finance MCP + stored reference prices"
                if live_count
                else "Stored reference prices"
            ),
            market_data_live=live_count == len(positions) and bool(positions),
            market_data_refreshed_at=datetime.now(timezone.utc) if refresh_market_data else None,
        )
        logger.info(
            "portfolio_snapshot_loaded",
            extra={
                "portfolio_name": snapshot.portfolio_name,
                "holding_count": len(snapshot.holdings),
                "total_portfolio_equity": snapshot.total_portfolio_equity,
                "total_return_percent": snapshot.total_return_percent,
                "market_data_source": snapshot.market_data_source,
                "market_data_live": snapshot.market_data_live,
            },
        )
        return snapshot

    def _fetch_live_prices(self, tickers: list[str]) -> dict[str, float]:
        from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient

        prices: dict[str, float] = {}
        finance_client = YahooFinanceMCPClient()
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(tickers)))) as executor:
            futures = {
                executor.submit(finance_client.get_stock_info, ticker): ticker
                for ticker in tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    price = self._extract_live_price(future.result())
                    if price is None:
                        raise ValueError("Yahoo Finance MCP response contained no recognized live price.")
                    prices[ticker] = price
                    logger.info(
                        "portfolio_live_price_loaded",
                        extra={"ticker": ticker, "current_market_price": price, "source": "Yahoo Finance MCP"},
                    )
                except Exception as exc:
                    logger.warning(
                        "portfolio_live_price_failed",
                        extra={"ticker": ticker, "error_type": type(exc).__name__, "error": str(exc)},
                    )
        return prices

    @staticmethod
    def _extract_live_price(payload: Any) -> float | None:
        """Extract a positive quote from common Yahoo Finance MCP response envelopes."""
        price_keys = (
            "currentPrice",
            "regularMarketPrice",
            "current_price",
            "regular_market_price",
            "price",
        )

        def visit(value: Any, depth: int = 0) -> float | None:
            if depth > 4 or not isinstance(value, dict):
                return None
            for key in price_keys:
                candidate = value.get(key)
                if isinstance(candidate, dict):
                    candidate = candidate.get("raw", candidate.get("value"))
                try:
                    price = float(candidate)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    return price
            for nested in value.values():
                result = visit(nested, depth + 1)
                if result is not None:
                    return result
            return None

        return visit(payload)

    def evaluate_portfolio_alerts(self, snapshot: PortfolioSnapshot) -> dict[str, Any]:
        """Run the portfolio monitoring skill against the current snapshot."""
        from skills.stop_loss_take_profit.trigger_tools import StopLossTakeProfitEngine

        positions = [
            {
                "ticker": holding.ticker,
                "sector": holding.sector,
                "value": holding.current_market_value,
                "pct_of_portfolio": (
                    holding.current_market_value / snapshot.total_portfolio_equity * 100
                    if snapshot.total_portfolio_equity > 0
                    else 0
                ),
            }
            for holding in snapshot.holdings
        ]
        history = [
            {
                "ticker": holding.ticker,
                "shares": holding.shares_held,
                "avg_cost": holding.average_buy_price,
                "current_price": holding.current_market_price,
            }
            for holding in snapshot.holdings
        ]
        report = StopLossTakeProfitEngine().assess_all_triggers(
            {
                "positions": positions,
                "history": history,
                "total_value": snapshot.total_portfolio_equity,
                "cash": snapshot.cash_balance,
            }
        )
        logger.info(
            "portfolio_alerts_evaluated",
            extra={"active_triggers": report.get("active_triggers", 0)},
        )
        return report

    def apply_dry_run_trade(self, *, ticker: str, side: str, notional_usd: float) -> dict[str, Any]:
        market_context: dict[str, Any] = {}
        try:
            from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient

            market_context = YahooFinanceMCPClient().get_stock_info(ticker)
        except Exception as exc:
            logger.warning(
                "paper_trade_market_context_unavailable",
                extra={"ticker": ticker, "side": side, "error_type": type(exc).__name__, "error": str(exc)},
            )

        reference_price: float | None = None
        for key in ("currentPrice", "regularMarketPrice", "price"):
            try:
                candidate = float(market_context[key])
            except (KeyError, TypeError, ValueError):
                continue
            if candidate > 0:
                reference_price = candidate
                break
        result = self.portfolio_store.apply_dry_run_trade(
            ticker=ticker,
            side=side,
            notional_usd=notional_usd,
            reference_price=reference_price,
            company_name=str(
                market_context.get("longName")
                or market_context.get("shortName")
                or ticker.upper()
            ),
            sector=str(market_context.get("sector") or "Unknown"),
        )
        logger.info("paper_portfolio_updated", extra=result)
        return result

    def get_portfolio_holdings(self) -> list[dict[str, Any]]:
        """Return calculated stock positions for the existing MCP/dashboard contract."""
        return [holding.model_dump(mode="json") for holding in self.get_portfolio_snapshot().holdings]

    def build_mitigation_orders(
        self,
        *,
        ticker: str,
        verdict: str,
        internal_consensus_percent: float,
        max_external_severity: float,
    ) -> list[TargetMitigationOrder]:
        if "Executive Blind Spot" in verdict:
            return [
                TargetMitigationOrder(
                    order_type="protective_put_spread",
                    ticker=ticker,
                    rationale="Internal conviction is bullish while external risk markers are elevated.",
                    parameters={"max_premium_bps": 75, "tenor_days": 45, "requires_human_approval": True},
                ),
                TargetMitigationOrder(
                    order_type="allocation_cap",
                    ticker=ticker,
                    rationale="Cap net new exposure until SEC and options risk markers are reviewed.",
                    parameters={"max_position_delta_bps": 25, "requires_human_approval": True},
                ),
            ]

        if internal_consensus_percent >= 65 and max_external_severity < 0.50:
            return [
                TargetMitigationOrder(
                    order_type="staged_buy_allocation",
                    ticker=ticker,
                    rationale="Internal consensus is constructive and external risk markers are contained.",
                    parameters={"tranches": 3, "max_daily_notional_pct": 0.33, "requires_human_approval": True},
                )
            ]

        if max_external_severity >= 0.70:
            return [
                TargetMitigationOrder(
                    order_type="protective_hedge_review",
                    ticker=ticker,
                    rationale="External risk markers warrant downside protection before adding exposure.",
                    parameters={"hedge_menu": ["put_spread", "collar", "notional_reduction"], "requires_human_approval": True},
                )
            ]

        return [
            TargetMitigationOrder(
                order_type="defer_to_governance_review",
                ticker=ticker,
                rationale="Signals are mixed; route to governance review instead of automatic execution.",
                parameters={"requires_human_approval": True},
            )
        ]

    def record_checkpoint(self, *, payload: dict[str, Any], decision: DecisionType, decided_by: str) -> dict[str, Any]:
        checkpoint = TradingDecisionCheckpoint(
            assessment_id=str(payload["assessment_id"]),
            ticker=str(payload["ticker"]),
            verdict=str(payload["verdict"]),
            decision=decision,
            decided_by=decided_by,
        )
        self._checkpoint_log.append(checkpoint)
        logger.info(
            "trading_checkpoint_recorded",
            extra={
                "assessment_id": checkpoint.assessment_id,
                "ticker": checkpoint.ticker,
                "decision": checkpoint.decision,
                "decided_by": checkpoint.decided_by,
                "dry_run": checkpoint.dry_run,
            },
        )

        if decision == "approve_buy_allocation":
            operator_message = (
                f"*Checkpoint recorded:* buy-allocation approval for *{checkpoint.ticker}* is queued for "
                "governance review. No trade was executed."
            )
        elif decision == "execute_protective_hedge":
            operator_message = (
                f"*Checkpoint recorded:* protective hedge request for *{checkpoint.ticker}* is queued for "
                "governance review. No trade was executed."
            )
        else:
            operator_message = f"*Checkpoint recorded:* {decision} for *{checkpoint.ticker}*."

        return {
            "checkpoint": checkpoint.model_dump(mode="json"),
            "operator_message": operator_message,
        }
