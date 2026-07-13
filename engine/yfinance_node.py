"""Yahoo Finance MCP normalization for AlphaChannel's 2026 Slack Hackathon agent."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.hackathon_2026.engine.yahoo_finance_mcp")


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


class FundamentalSignal(BaseModel):
    ticker: str
    current_price: float | None = None
    market_cap: float | None = None
    revenue: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    profit_margin: float | None = None
    return_on_equity: float | None = None
    debt_to_equity: float | None = None
    risk_markers: list[str] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class YahooFinanceFundamentalsWorker:
    """Fetch investment fundamentals through Yahoo Finance MCP when enabled."""

    def __init__(self, timeout_seconds: float | None = None) -> None:
        configured_timeout = timeout_seconds or float(
            os.getenv("YFINANCE_FUNDAMENTALS_TIMEOUT_SECONDS", os.getenv("YFINANCE_TIMEOUT_SECONDS", "12"))
        )
        self.timeout_seconds = max(1.0, configured_timeout)

    def fetch_fundamental_signal(self, ticker: str) -> FundamentalSignal:
        normalized_ticker = ticker.strip().upper()
        result_queue: queue.Queue[FundamentalSignal | BaseException] = queue.Queue(maxsize=1)

        def fetch() -> None:
            try:
                info = self._fetch_info(normalized_ticker)
                result_queue.put_nowait(self._build_signal(normalized_ticker, info))
            except BaseException as exc:
                result_queue.put_nowait(exc)

        worker = threading.Thread(target=fetch, name=f"yfinance-fundamentals-{normalized_ticker}", daemon=True)
        worker.start()
        worker.join(timeout=self.timeout_seconds)
        if worker.is_alive():
            logger.error(
                "yfinance_fundamentals_timed_out ticker=%s timeout_seconds=%.1f",
                normalized_ticker,
                self.timeout_seconds,
            )
            return FundamentalSignal(
                ticker=normalized_ticker,
                risk_markers=[f"Yahoo Finance fundamentals timed out after {self.timeout_seconds:.0f} seconds."],
                raw_metadata={"feed_status": "timeout"},
            )
        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            return FundamentalSignal(
                ticker=normalized_ticker,
                risk_markers=["Yahoo Finance fundamentals returned no data."],
                raw_metadata={"feed_status": "unavailable"},
            )
        if isinstance(result, BaseException):
            logger.error(
                "yfinance_fundamentals_failed ticker=%s error_type=%s error=%s",
                normalized_ticker,
                type(result).__name__,
                result,
            )
            return FundamentalSignal(
                ticker=normalized_ticker,
                risk_markers=[f"Yahoo Finance fundamentals unavailable: {result}"],
                raw_metadata={"feed_status": "unavailable"},
            )
        logger.info(
            "yfinance_fundamentals_fetched ticker=%s trailing_pe=%s forward_pe=%s revenue_growth=%s",
            normalized_ticker,
            result.trailing_pe,
            result.forward_pe,
            result.revenue_growth,
        )
        return result

    @staticmethod
    def _fetch_info(ticker: str) -> dict[str, Any]:
        """Fetch fundamentals exclusively through the Yahoo Finance MCP server."""
        try:
            from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient

            info = YahooFinanceMCPClient().get_stock_info(ticker)
            if isinstance(info, dict) and info:
                normalized = YahooFinanceFundamentalsWorker._normalize_info(info)
                if YahooFinanceFundamentalsWorker._has_fundamental_data(normalized):
                    logger.info(
                        "yahoo_finance_mcp_fundamentals_fetched ticker=%s keys=%s",
                        ticker,
                        sorted(normalized.keys()),
                    )
                    return normalized
                raise RuntimeError(
                    "Yahoo Finance MCP returned no recognized fundamental metrics "
                    f"for {ticker}; response keys={sorted(info.keys())}"
                )
            raise RuntimeError("Yahoo Finance MCP returned an empty response.")
        except Exception as exc:
            logger.error(
                "yahoo_finance_mcp_fundamentals_unavailable ticker=%s error_type=%s error=%s",
                ticker,
                type(exc).__name__,
                exc,
            )
            raise RuntimeError(f"Yahoo Finance MCP is unavailable for {ticker}.") from exc

    @staticmethod
    def _normalize_info(info: dict[str, Any]) -> dict[str, Any]:
        """Translate common MCP field names into AlphaChannel's financial-info contract."""
        aliases = {
            "price": "currentPrice",
            "current_price": "currentPrice",
            "regular_market_price": "regularMarketPrice",
            "market_cap": "marketCap",
            "revenue": "totalRevenue",
            "total_revenue": "totalRevenue",
            "revenue_growth": "revenueGrowth",
            "earnings_growth": "earningsGrowth",
            "trailing_pe": "trailingPE",
            "forward_pe": "forwardPE",
            "profit_margin": "profitMargins",
            "return_on_equity": "returnOnEquity",
            "debt_to_equity": "debtToEquity",
        }
        normalized = dict(info)
        for source, target in aliases.items():
            if target not in normalized and info.get(source) is not None:
                normalized[target] = info[source]
        return normalized

    @staticmethod
    def _has_fundamental_data(info: dict[str, Any]) -> bool:
        return any(
            info.get(key) is not None
            for key in (
                "currentPrice",
                "regularMarketPrice",
                "trailingPE",
                "forwardPE",
                "totalRevenue",
                "revenueGrowth",
                "earningsGrowth",
            )
        )

    @staticmethod
    def _build_signal(ticker: str, info: dict[str, Any]) -> FundamentalSignal:
        def number(*keys: str) -> float | None:
            for key in keys:
                value = info.get(key)
                if value is not None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        continue
            return None

        signal = FundamentalSignal(
            ticker=ticker,
            current_price=number("currentPrice", "regularMarketPrice"),
            market_cap=number("marketCap"),
            revenue=number("totalRevenue", "totalRevenueTTM"),
            revenue_growth=number("revenueGrowth"),
            earnings_growth=number("earningsGrowth", "earningsQuarterlyGrowth"),
            trailing_pe=number("trailingPE"),
            forward_pe=number("forwardPE"),
            profit_margin=number("profitMargins"),
            return_on_equity=number("returnOnEquity"),
            debt_to_equity=number("debtToEquity"),
            raw_metadata={
                "feed_status": "ok" if YahooFinanceFundamentalsWorker._has_fundamental_data(info) else "partial",
                "business_summary": str(info.get("longBusinessSummary", ""))[:800],
            },
        )
        markers: list[str] = []
        if not YahooFinanceFundamentalsWorker._has_fundamental_data(info):
            markers.append("Yahoo Finance returned no usable fundamental metrics for this ticker.")
        if signal.trailing_pe is not None and signal.trailing_pe > 60:
            markers.append(f"Trailing P/E is elevated at {signal.trailing_pe:.1f}.")
        if signal.forward_pe is not None and signal.trailing_pe and signal.forward_pe < signal.trailing_pe:
            markers.append("Forward P/E is below trailing P/E, implying expected earnings growth.")
        if signal.revenue_growth is not None and signal.revenue_growth < 0:
            markers.append(f"Revenue growth is negative at {signal.revenue_growth:.1%}.")
        if signal.earnings_growth is not None and signal.earnings_growth < 0:
            markers.append(f"Earnings growth is negative at {signal.earnings_growth:.1%}.")
        if signal.debt_to_equity is not None and signal.debt_to_equity > 150:
            markers.append(f"Debt-to-equity is elevated at {signal.debt_to_equity:.1f}.")
        signal.risk_markers = markers
        return signal


class YahooFinanceOptionsWorker:
    """Fetch option chain and implied volatility signals for the orchestrator."""

    _cache: dict[str, tuple[float, OptionsSignal]] = {}
    _cache_lock = threading.RLock()

    def __init__(self, timeout_seconds: float | None = None) -> None:
        configured_timeout = timeout_seconds or float(os.getenv("YFINANCE_TIMEOUT_SECONDS", "12"))
        self.timeout_seconds = max(1.0, configured_timeout)
        self.cache_ttl_seconds = max(0.0, float(os.getenv("YFINANCE_CACHE_TTL_SECONDS", "180")))

    def fetch_options_signal(self, ticker: str) -> OptionsSignal:
        """Run Yahoo retrieval behind a hard deadline so Slack workers cannot hang."""
        normalized_ticker = ticker.strip().upper()
        with self._cache_lock:
            cached = self._cache.get(normalized_ticker)
            if cached and time.monotonic() - cached[0] <= self.cache_ttl_seconds:
                logger.info("yfinance_signal_cache_hit ticker=%s", normalized_ticker)
                return cached[1].model_copy(deep=True)

        result_queue: queue.Queue[OptionsSignal | BaseException] = queue.Queue(maxsize=1)

        def fetch() -> None:
            try:
                result_queue.put_nowait(self._fetch_live_options_signal(normalized_ticker))
            except BaseException as exc:  # Preserve worker failures for the typed fallback below.
                result_queue.put_nowait(exc)

        worker = threading.Thread(
            target=fetch,
            name=f"yfinance-{normalized_ticker}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=self.timeout_seconds)
        if worker.is_alive():
            logger.error(
                "yfinance_signal_timed_out ticker=%s timeout_seconds=%.1f",
                normalized_ticker,
                self.timeout_seconds,
            )
            return self._unavailable_signal(
                normalized_ticker,
                f"Yahoo Finance did not respond within {self.timeout_seconds:.0f} seconds.",
            )

        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            return self._unavailable_signal(normalized_ticker, "Yahoo Finance worker exited without a result.")
        if isinstance(result, BaseException):
            logger.error(
                "yfinance_signal_worker_failed ticker=%s error_type=%s error=%s",
                normalized_ticker,
                type(result).__name__,
                result,
            )
            return self._unavailable_signal(normalized_ticker, str(result))
        with self._cache_lock:
            self._cache[normalized_ticker] = (time.monotonic(), result.model_copy(deep=True))
        return result

    def _fetch_live_options_signal(self, ticker: str) -> OptionsSignal:
        try:
            from engine.yahoo_finance_mcp_client import YahooFinanceMCPClient

            info = YahooFinanceMCPClient().get_stock_info(ticker)
            if not isinstance(info, dict) or not info:
                raise RuntimeError("Yahoo Finance MCP returned an empty response.")

            current_price = self._number(info, "currentPrice", "regularMarketPrice", "price", "current_price")
            expiry = str(info.get("nearestExpiry") or info.get("nearest_expiry") or "")
            front_month_iv = self._number(info, "frontMonthIV", "front_month_iv", "impliedVolatility")
            put_call_ratio = self._number(
                info,
                "putCallOpenInterestRatio",
                "put_call_open_interest_ratio",
                "putCallRatio",
            )
            if front_month_iv is None and put_call_ratio is None and not expiry:
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

            markers = self._derive_markers(front_month_iv, put_call_ratio, expiry)

            logger.info(
                "yfinance_signal_fetched",
                extra={
                    "ticker": ticker,
                    "nearest_expiry": expiry or None,
                    "front_month_iv": front_month_iv,
                    "put_call_open_interest_ratio": put_call_ratio,
                    "marker_count": len(markers),
                },
            )
            return OptionsSignal(
                ticker=ticker,
                current_price=current_price,
                nearest_expiry=expiry or None,
                front_month_iv=front_month_iv,
                put_call_open_interest_ratio=put_call_ratio,
                risk_markers=markers,
                raw_metadata={"source": "yahoo-finance-mcp", "mcp_keys": sorted(info.keys())},
            )
        except Exception as exc:
            logger.exception("yfinance_signal_failed", extra={"ticker": ticker})
            return self._unavailable_signal(ticker, str(exc))

    @staticmethod
    def _number(info: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = info.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _unavailable_signal(ticker: str, reason: str) -> OptionsSignal:
        return OptionsSignal(
            ticker=ticker,
            risk_markers=[
                OptionsRiskMarker(
                    label="Options feed unavailable",
                    severity=0.45,
                    evidence=f"Yahoo Finance options retrieval failed: {reason}",
                )
            ],
            raw_metadata={"feed_status": "unavailable", "reason": reason},
        )

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
