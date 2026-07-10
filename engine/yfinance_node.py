from __future__ import annotations

import logging
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.engine.yfinance")


class OptionsRiskMarker(BaseModel):
    source: str = "Yahoo Finance"
    label: str
    severity: float = Field(ge=0.0, le=1.0)
    evidence: str

    def as_display_text(self) -> str:
        return f"{self.label} ({self.source}, severity {self.severity:.2f}): {self.evidence}"


class OptionsSignal(BaseModel):
    ticker: str
    current_price: float | None = None
    nearest_expiry: str | None = None
    front_month_iv: float | None = None
    put_call_open_interest_ratio: float | None = None
    risk_markers: list[OptionsRiskMarker] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class YahooFinanceOptionsWorker:
    """Fetch option chain and implied volatility signals for the orchestrator."""

    def fetch_options_signal(self, ticker: str) -> OptionsSignal:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance is required for options market reality checks.") from exc

        try:
            yf_ticker = yf.Ticker(ticker)
            current_price = self._resolve_current_price(yf_ticker)
            expiries = list(yf_ticker.options or [])
            if not expiries:
                return OptionsSignal(
                    ticker=ticker,
                    current_price=current_price,
                    risk_markers=[
                        OptionsRiskMarker(
                            label="No listed options chain",
                            severity=0.25,
                            evidence="Yahoo Finance returned no option expirations for this ticker.",
                        )
                    ],
                )

            nearest_expiry = expiries[0]
            chain = yf_ticker.option_chain(nearest_expiry)
            call_iv = self._safe_mean(chain.calls.get("impliedVolatility", []))
            put_iv = self._safe_mean(chain.puts.get("impliedVolatility", []))
            front_month_iv = self._safe_mean([value for value in [call_iv, put_iv] if value is not None])
            call_oi = float(chain.calls.get("openInterest", []).fillna(0).sum())
            put_oi = float(chain.puts.get("openInterest", []).fillna(0).sum())
            put_call_ratio = put_oi / call_oi if call_oi else None
            markers = self._derive_markers(front_month_iv, put_call_ratio, nearest_expiry)

            logger.info(
                "yfinance_signal_fetched",
                extra={
                    "ticker": ticker,
                    "nearest_expiry": nearest_expiry,
                    "front_month_iv": front_month_iv,
                    "put_call_open_interest_ratio": put_call_ratio,
                    "marker_count": len(markers),
                },
            )
            return OptionsSignal(
                ticker=ticker,
                current_price=current_price,
                nearest_expiry=nearest_expiry,
                front_month_iv=front_month_iv,
                put_call_open_interest_ratio=put_call_ratio,
                risk_markers=markers,
                raw_metadata={"call_open_interest": call_oi, "put_open_interest": put_oi},
            )
        except Exception as exc:
            logger.exception("yfinance_signal_failed", extra={"ticker": ticker})
            return OptionsSignal(
                ticker=ticker,
                risk_markers=[
                    OptionsRiskMarker(
                        label="Options feed unavailable",
                        severity=0.45,
                        evidence=f"Yahoo Finance options retrieval failed: {exc}",
                    )
                ],
            )

    @staticmethod
    def _resolve_current_price(yf_ticker: Any) -> float | None:
        fast_info = getattr(yf_ticker, "fast_info", None)
        if fast_info:
            for key in ("last_price", "lastPrice", "regular_market_price"):
                try:
                    value = fast_info.get(key)
                except AttributeError:
                    value = getattr(fast_info, key, None)
                if value:
                    return float(value)

        history = yf_ticker.history(period="5d")
        if history.empty:
            return None
        return float(history["Close"].dropna().iloc[-1])

    @staticmethod
    def _safe_mean(values: Any) -> float | None:
        clean_values = [float(value) for value in values if value is not None and float(value) > 0]
        if not clean_values:
            return None
        return float(mean(clean_values))

    @staticmethod
    def _derive_markers(
        front_month_iv: float | None,
        put_call_ratio: float | None,
        nearest_expiry: str,
    ) -> list[OptionsRiskMarker]:
        markers: list[OptionsRiskMarker] = []
        if front_month_iv is not None:
            if front_month_iv >= 0.80:
                markers.append(
                    OptionsRiskMarker(
                        label="Extreme front-month implied volatility",
                        severity=0.86,
                        evidence=f"Nearest expiry {nearest_expiry} mean IV is {front_month_iv:.1%}.",
                    )
                )
            elif front_month_iv >= 0.50:
                markers.append(
                    OptionsRiskMarker(
                        label="Elevated front-month implied volatility",
                        severity=0.68,
                        evidence=f"Nearest expiry {nearest_expiry} mean IV is {front_month_iv:.1%}.",
                    )
                )

        if put_call_ratio is not None:
            if put_call_ratio >= 1.75:
                markers.append(
                    OptionsRiskMarker(
                        label="Defensive options positioning",
                        severity=0.78,
                        evidence=f"Put/call open-interest ratio is {put_call_ratio:.2f}.",
                    )
                )
            elif put_call_ratio <= 0.35:
                markers.append(
                    OptionsRiskMarker(
                        label="Crowded upside options positioning",
                        severity=0.55,
                        evidence=f"Put/call open-interest ratio is {put_call_ratio:.2f}.",
                    )
                )

        if not markers:
            markers.append(
                OptionsRiskMarker(
                    label="No severe option-chain stress marker",
                    severity=0.20,
                    evidence="Front-month IV and put/call open interest did not breach risk thresholds.",
                )
            )
        return markers
