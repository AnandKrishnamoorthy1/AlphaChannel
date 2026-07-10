"""External tool integrations for business workflows."""

import concurrent.futures as _futures
import requests
from typing import Dict, Any, Optional
from datetime import datetime

from loguru import logger
from config.settings import settings


class FinancialDataTools:
    """Real financial data tool integration using Alpha Vantage API."""
    
    def __init__(self):
        self.api_key = settings.alpha_vantage_api_key
        # Second key for OVERVIEW — eliminates rate-limit collisions.
        # Falls back to the primary key if ALPHA_VANTAGE_API_KEY_2 is not set.
        self.api_key_2 = settings.alpha_vantage_api_key_2 or self.api_key
        self.base_url = "https://www.alphavantage.co/query"
        self.timeout = 10
        
    def get_stock_data(self, ticker: str, api_key: str = None) -> Dict[str, Any]:
        """
        Invoke REAL Alpha Vantage API to get stock quote data.

        Args:
            ticker: Stock symbol (e.g., "NVDA", "TSLA")
            api_key: Override the default API key (use to alternate between keys).

        Returns:
            Dict with price, change, volume, etc.
            Falls back to mock data if API is unavailable.
        """
        key = api_key or self.api_key
        if not key:
            logger.warning("Alpha Vantage API key not set. Using mock data.")
            logger.info("To use real data, set ALPHA_VANTAGE_API_KEY in .env")
            return self._mock_stock_data(ticker)

        try:
            logger.info(f"[EXTERNAL TOOL] Fetching REAL stock data for {ticker} from Alpha Vantage API...")

            params = {
                "function": "GLOBAL_QUOTE",
                "symbol": ticker.upper(),
                "apikey": key,
            }

            response = requests.get(
                self.base_url,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            data = response.json()

            # Detect AV rate-limit responses (returned as 200 with info message)
            if "Information" in data or "Note" in data:
                msg = data.get("Information") or data.get("Note", "")
                logger.warning(f"[AV RATE LIMIT] {ticker} — quota exceeded. Using fallback. ({msg[:60]})")
                return self._mock_stock_data(ticker)

            if "Global Quote" not in data or not data["Global Quote"]:
                logger.warning(f"No data returned from Alpha Vantage for {ticker}")
                return self._mock_stock_data(ticker)
            
            quote = data["Global Quote"]
            
            try:
                price = float(quote.get("05. price", 0))
                change = float(quote.get("09. change", 0))
                change_percent_str = quote.get("10. change percent", "0").rstrip("%")
                change_percent = float(change_percent_str) if change_percent_str else 0.0
                volume = int(quote.get("06. volume", 0))
                timestamp = quote.get("07. latest trading day", "")
                
            except (ValueError, TypeError):
                logger.warning("Error parsing Alpha Vantage response")
                return self._mock_stock_data(ticker)
            
            result = {
                "ticker": ticker.upper(),
                "price": price,
                "change": change,
                "change_percent": change_percent,
                "volume": volume,
                "timestamp": timestamp,
                "data_source": "Alpha Vantage (REAL API)",
                "fetched_at": datetime.now().isoformat(),
            }
            
            logger.info(f"[EXTERNAL TOOL SUCCESS] Retrieved REAL data: {result}")
            return result
            
        except requests.exceptions.Timeout:
            logger.error(f"Alpha Vantage API timeout for {ticker}")
            return self._mock_stock_data(ticker)
            
        except requests.exceptions.ConnectionError:
            logger.error("Connection error to Alpha Vantage API")
            return self._mock_stock_data(ticker)
            
        except Exception as e:
            logger.error(f"Alpha Vantage API error: {type(e).__name__}: {str(e)}")
            return self._mock_stock_data(ticker)

    def get_fundamental_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch company fundamentals from the Alpha Vantage OVERVIEW endpoint.

        Returns fields like P/E ratio, EPS, analyst target price, 52-week range,
        revenue growth, profit margin, market cap, sector, and more.
        Returns an empty dict silently on failure (non-critical enrichment).
        """
        if not self.api_key_2:
            return {}

        try:
            params = {
                "function": "OVERVIEW",
                "symbol": ticker.upper(),
                "apikey": self.api_key_2,
            }
            response = requests.get(self.base_url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            # Alpha Vantage returns rate-limit messages as top-level keys
            if "Information" in data or "Note" in data:
                msg = data.get("Information") or data.get("Note", "")
                logger.warning(f"[EXTERNAL TOOL] OVERVIEW rate-limited for {ticker}: {msg[:80]}")
                return None  # None signals caller to retry

            if not data or "Symbol" not in data:
                logger.warning(f"[EXTERNAL TOOL] No OVERVIEW data for {ticker} (ticker not supported or no data)")
                return {}

            def _f(key: str) -> Optional[str]:
                val = data.get(key)
                return val if val and val not in ("None", "-", "") else None

            fundamentals = {}
            _fields = {
                "sector": "Sector",
                "industry": "Industry",
                "market_cap": "MarketCapitalization",
                "pe_ratio": "PERatio",
                "forward_pe": "ForwardPE",
                "eps": "EPS",
                "analyst_target_price": "AnalystTargetPrice",
                "week_52_high": "52WeekHigh",
                "week_52_low": "52WeekLow",
                "revenue_per_share_ttm": "RevenuePerShareTTM",
                "profit_margin": "ProfitMargin",
                "quarterly_earnings_growth_yoy": "QuarterlyEarningsGrowthYOY",
                "quarterly_revenue_growth_yoy": "QuarterlyRevenueGrowthYOY",
                "beta": "Beta",
                "dividend_yield": "DividendYield",
                "return_on_equity": "ReturnOnEquityTTM",
                "book_value": "BookValue",
            }
            for key, av_key in _fields.items():
                val = _f(av_key)
                if val is not None:
                    fundamentals[key] = val

            logger.info(
                f"[EXTERNAL TOOL] Fundamentals for {ticker}: "
                f"P/E={fundamentals.get('pe_ratio', 'N/A')}, "
                f"EPS={fundamentals.get('eps', 'N/A')}, "
                f"Analyst target=${fundamentals.get('analyst_target_price', 'N/A')}, "
                f"Sector={fundamentals.get('sector', 'N/A')}"
            )
            return fundamentals

        except Exception as e:
            logger.warning(f"[EXTERNAL TOOL] Could not fetch fundamentals for {ticker}: {e}")
            return {}

    def get_enriched_stock_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch quote + fundamentals + SEC filings and merge.

        GLOBAL_QUOTE uses ALPHA_VANTAGE_API_KEY.
        OVERVIEW uses ALPHA_VANTAGE_API_KEY_2 (falls back to key 1 if not set).
        Using separate keys means both calls run fully in parallel with no
        rate-limit collisions and double the daily quota (50 req/day).
        """
        from app.tools.sec_tools import get_sec_filings  # local import avoids circular

        with _futures.ThreadPoolExecutor(max_workers=3) as pool:
            quote_future        = pool.submit(self.get_stock_data, ticker)
            fundamentals_future = pool.submit(self.get_fundamental_data, ticker)
            sec_future          = pool.submit(get_sec_filings, ticker, 2)
            quote_data   = quote_future.result()
            fundamentals = fundamentals_future.result()
            sec_data     = sec_future.result()

        if fundamentals:
            quote_data.update(fundamentals)
        if sec_data:
            quote_data["sec_filings"] = sec_data

        return quote_data

    def _mock_stock_data(self, ticker: str) -> Dict[str, Any]:
        """Fallback mock data when API fails or key is not set."""
        return {
            "ticker": ticker.upper(),
            "price": 150.25,
            "change": 5.50,
            "change_percent": 3.8,
            "volume": 45_000_000,
            "timestamp": datetime.now().strftime("%Y-%m-%d"),
            "data_source": "Mock (API unavailable - configure ALPHA_VANTAGE_API_KEY)",
            "fetched_at": datetime.now().isoformat(),
            "note": "This is mock data. Connect real Alpha Vantage API for production use."
        }


# Global instance
financial_tools = FinancialDataTools()
