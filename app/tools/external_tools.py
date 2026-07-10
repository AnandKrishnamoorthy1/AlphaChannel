"""
External tool integrations -- powered by Yahoo Finance MCP
==========================================================
All market data (price, fundamentals, news) is fetched through the
Yahoo Finance MCP server (stdio transport).  No API keys required,
no rate limits, no mock fallbacks for price data.

SEC EDGAR filings continue to be fetched via edgartools (unchanged).
"""

import concurrent.futures as _futures
from typing import Dict, Any, Optional
from datetime import datetime

from loguru import logger
from app.tools.yahoo_finance_mcp_client import yahoo_finance_mcp


class FinancialDataTools:
    """
    Financial data tool integration via Yahoo Finance MCP server.

    Provides the same interface as the previous Alpha Vantage implementation
    so the rest of the codebase requires no changes.
    """

    def __init__(self):
        self.api_key = None   # kept for backward compat
        self.api_key_2 = None
        self._mcp = yahoo_finance_mcp

    def get_stock_data(self, ticker: str, api_key: str = None) -> Dict[str, Any]:
        """Fetch real-time quote via Yahoo Finance MCP."""
        info = self._mcp.get_stock_info(ticker)
        if not info or not isinstance(info, dict):
            logger.warning(f"[YF-MCP] No quote data for {ticker}")
            return self._fallback_quote(ticker)

        price = (info.get("currentPrice") or info.get("regularMarketPrice")
                 or info.get("previousClose") or 0.0)
        result = {
            "ticker": ticker.upper(),
            "price": float(price),
            "change": float(info.get("regularMarketChange") or 0.0),
            "change_percent": float(info.get("regularMarketChangePercent") or 0.0),
            "volume": int(info.get("regularMarketVolume") or info.get("volume") or 0),
            "timestamp": datetime.now().strftime("%Y-%m-%d"),
            "data_source": "Yahoo Finance MCP",
            "fetched_at": datetime.now().isoformat(),
        }
        logger.info(f"[YF-MCP] {ticker}: ${result['price']:.2f} ({result['change_percent']:+.2f}%)")
        return result

    def get_fundamental_data(self, ticker: str) -> Dict[str, Any]:
        """Fetch fundamentals via Yahoo Finance MCP."""
        info = self._mcp.get_stock_info(ticker)
        if not info or not isinstance(info, dict):
            logger.warning(f"[YF-MCP] No fundamental data for {ticker}")
            return {}

        field_map = {
            "sector": "sector", "industry": "industry",
            "market_cap": "marketCap", "pe_ratio": "trailingPE",
            "forward_pe": "forwardPE", "eps": "trailingEps",
            "analyst_target_price": "targetMeanPrice",
            "week_52_high": "fiftyTwoWeekHigh", "week_52_low": "fiftyTwoWeekLow",
            "profit_margin": "profitMargins",
            "quarterly_revenue_growth_yoy": "revenueGrowth",
            "quarterly_earnings_growth_yoy": "earningsGrowth",
            "beta": "beta", "dividend_yield": "dividendYield",
            "return_on_equity": "returnOnEquity", "book_value": "bookValue",
            "revenue_per_share_ttm": "revenuePerShare",
        }
        fundamentals = {k: str(info[v]) for k, v in field_map.items() if info.get(v) is not None}
        logger.info(f"[YF-MCP] Fundamentals {ticker}: P/E={fundamentals.get('pe_ratio','N/A')}, Sector={fundamentals.get('sector','N/A')}")
        return fundamentals

    def get_enriched_stock_data(self, ticker: str) -> Dict[str, Any]:
        """Fetch quote + fundamentals + SEC filings. MCP call is made once."""
        from app.tools.sec_tools import get_sec_filings

        with _futures.ThreadPoolExecutor(max_workers=2) as pool:
            info_future = pool.submit(self._mcp.get_stock_info, ticker)
            sec_future  = pool.submit(get_sec_filings, ticker, 1)
            raw_info = info_future.result()
            sec_data = sec_future.result()

        if not raw_info or not isinstance(raw_info, dict):
            quote_data = self._fallback_quote(ticker)
        else:
            price = (raw_info.get("currentPrice") or raw_info.get("regularMarketPrice")
                     or raw_info.get("previousClose") or 0.0)
            quote_data = {
                "ticker": ticker.upper(),
                "price": float(price),
                "change": float(raw_info.get("regularMarketChange") or 0.0),
                "change_percent": float(raw_info.get("regularMarketChangePercent") or 0.0),
                "volume": int(raw_info.get("regularMarketVolume") or raw_info.get("volume") or 0),
                "timestamp": datetime.now().strftime("%Y-%m-%d"),
                "data_source": "Yahoo Finance MCP",
                "fetched_at": datetime.now().isoformat(),
            }
            field_map = {
                "sector": "sector", "industry": "industry",
                "market_cap": "marketCap", "pe_ratio": "trailingPE",
                "forward_pe": "forwardPE", "eps": "trailingEps",
                "analyst_target_price": "targetMeanPrice",
                "week_52_high": "fiftyTwoWeekHigh", "week_52_low": "fiftyTwoWeekLow",
                "profit_margin": "profitMargins",
                "quarterly_revenue_growth_yoy": "revenueGrowth",
                "quarterly_earnings_growth_yoy": "earningsGrowth",
                "beta": "beta", "dividend_yield": "dividendYield",
                "return_on_equity": "returnOnEquity", "book_value": "bookValue",
                "revenue_per_share_ttm": "revenuePerShare",
            }
            for our_key, yf_key in field_map.items():
                v = raw_info.get(yf_key)
                if v is not None:
                    quote_data[our_key] = str(v)

        if sec_data:
            quote_data["sec_filings"] = sec_data

        return quote_data

    def _fallback_quote(self, ticker: str) -> Dict[str, Any]:
        return {
            "ticker": ticker.upper(), "price": 0.0, "change": 0.0,
            "change_percent": 0.0, "volume": 0,
            "timestamp": datetime.now().strftime("%Y-%m-%d"),
            "data_source": "Unavailable (MCP server not responding)",
            "fetched_at": datetime.now().isoformat(),
        }


# Global singleton
financial_tools = FinancialDataTools()
