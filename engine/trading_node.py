# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent
# Copyright (c) 2026 Anand Krishnamoorthy
# SPDX-License-Identifier: MIT
#
# Purpose-built for the 2026 Slack Hackathon. See LICENSE for terms.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.engine.trading")

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


class TradingNode:
    """Map recommendations to explicit human checkpoints; never auto-executes."""

    def __init__(self) -> None:
        self._checkpoint_log: list[TradingDecisionCheckpoint] = []

    def get_portfolio_holdings(self) -> list[dict[str, str]]:
        """Return the institutional portfolio snapshot used by the Slack dashboard."""
        holdings = [
            {"Ticker": "NU", "Allocation": "$15M", "Status": "⚠️ OVEREXPOSED"},
            {"Ticker": "SNOW", "Allocation": "$8M", "Status": "✅ BALANCED"},
            {"Ticker": "NOW", "Allocation": "$12M", "Status": "✅ BALANCED"},
        ]
        logger.info("portfolio_holdings_loaded", extra={"holding_count": len(holdings)})
        return holdings

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
                order_type="defer_to_investment_committee",
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
