"""
Yahoo Finance MCP Client
========================
Persistent client for the Yahoo Finance MCP server.

Runs the MCP server as a subprocess (stdio transport) and keeps
the connection alive across all calls — one process, one event loop,
zero rate limits, no API key required.

MCP server: https://github.com/Alex2Yang97/yahoo-finance-mcp
"""

import asyncio
import json
import sys
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional

from loguru import logger


class YahooFinanceMCPClient:
    """
    Singleton MCP client that maintains a persistent connection to the
    Yahoo Finance MCP server subprocess.

    Usage (sync, drop-in compatible with existing code):
        client = YahooFinanceMCPClient()
        info   = client.get_stock_info("AMD")
        price  = info.get("currentPrice")
    """

    _instance: Optional["YahooFinanceMCPClient"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                cls._instance = obj
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._session = None
        self._connected = False
        self._loop = asyncio.new_event_loop()

        # Background thread owns the event loop for the lifetime of the process
        self._bg_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcp-event-loop"
        )
        self._bg_thread.start()

        # Connect synchronously during init so callers can use immediately
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        try:
            future.result(timeout=60)
            self._connected = True
            logger.info("✅ Yahoo Finance MCP server connected (stdio transport)")
        except Exception as e:
            logger.error(f"❌ Yahoo Finance MCP connection failed: {e}")
            logger.warning("Financial data will fall back to mock values.")

    def __del__(self):
        """Clean up MCP connection when singleton is destroyed."""
        try:
            if self._connected and hasattr(self, "_loop") and self._loop:
                # Schedule cleanup on the event loop before stopping
                asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop)
                # Give cleanup a moment to complete
                import time
                time.sleep(0.1)
        except Exception:
            # Suppress any errors during cleanup at shutdown
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Internal connection management
    # ──────────────────────────────────────────────────────────────────────

    async def _connect(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # Prepare bulletproof environment for MCP subprocess
        # Even if bootstrap partially fails, this ensures the subprocess can find all modules
        env = os.environ.copy()
        
        # Explicitly inject cloud runtime locations into PYTHONPATH
        # This makes the subprocess independent of parent environment state
        pythonpath_dirs = [
            "/code",          # Application code
            "/opt/python",    # Custom layer (from s build --use-docker)
            env.get("PYTHONPATH", "")  # Preserve any existing PYTHONPATH
        ]
        env["PYTHONPATH"] = ":".join(filter(None, pythonpath_dirs))
        logger.info(f"[MCP] Subprocess PYTHONPATH: {env['PYTHONPATH']}")

        # Locate the installed entry-point executable
        scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
        exe = os.path.join(scripts_dir, "yahoo-finance-mcp.exe")
        if not os.path.exists(exe):
            exe = os.path.join(scripts_dir, "yahoo-finance-mcp")
        if not os.path.exists(exe):
            # Fallback: run the package's server module directly
            # -m flag executes the package's internal server entry point
            exe = sys.executable
            args = ["-m", "yahoo_finance_mcp.server"]
        else:
            args = []

        # Suppress subprocess stdout/stderr to prevent startup messages from
        # polluting the JSON-RPC stdio channel (which causes ValidationError)
        server_params = StdioServerParameters(
            command=exe, 
            args=args, 
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info(f"[MCP] Starting Yahoo Finance MCP server: {exe}")

        # Keep references so the context managers stay alive
        self._stdio_cm = stdio_client(server_params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

        tools = await self._session.list_tools()
        names = [t.name for t in tools.tools]
        logger.info(f"[MCP] Available tools: {', '.join(names)}")

    async def _cleanup(self):
        """Clean up MCP connections gracefully."""
        try:
            if hasattr(self, "_session_cm") and self._session_cm:
                try:
                    await self._session_cm.__aexit__(None, None, None)
                except Exception:
                    # Suppress cleanup errors — they don't affect functionality
                    pass
        except Exception:
            pass

        try:
            if hasattr(self, "_stdio_cm") and self._stdio_cm:
                try:
                    await self._stdio_cm.__aexit__(None, None, None)
                except Exception:
                    # Suppress cleanup errors from async task scope mismatch
                    pass
        except Exception:
            pass

        self._session = None
        self._connected = False

    # ──────────────────────────────────────────────────────────────────────
    # Generic tool call (sync)
    # ──────────────────────────────────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool synchronously. Returns parsed JSON or raw text."""
        if not self._connected or self._session is None:
            logger.warning(f"[MCP] Not connected — cannot call {tool_name}")
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool_name, arguments),
            self._loop,
        )
        try:
            result = future.result(timeout=30)
        except Exception as e:
            logger.error(f"[MCP] {tool_name} timed out or errored: {e}")
            return None

        # MCP results carry a list of content items; extract text/JSON
        if result and result.content:
            for item in result.content:
                text = getattr(item, "text", None)
                if text:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
        return None

    # ──────────────────────────────────────────────────────────────────────
    # High-level helpers matching the existing FinancialDataTools interface
    # ──────────────────────────────────────────────────────────────────────

    def get_stock_info(self, ticker: str) -> Optional[Dict]:
        """
        Comprehensive stock data: price, change, volume, P/E, EPS,
        market cap, 52-week range, sector, analyst target, etc.
        Equivalent to AV GLOBAL_QUOTE + OVERVIEW in one call.
        """
        logger.info(f"[MCP] get_stock_info → {ticker}")
        return self.call_tool("get_stock_info", {"ticker": ticker.upper()})

    def get_financial_statement(
        self,
        ticker: str,
        statement_type: str = "income",
        period: str = "quarterly",
    ) -> Optional[Dict]:
        """Income statement / balance sheet / cash flow via MCP."""
        logger.info(f"[MCP] get_financial_statement → {ticker} ({statement_type}/{period})")
        return self.call_tool(
            "get_financial_statement",
            {"ticker": ticker.upper(), "statement_type": statement_type, "period": period},
        )

    def get_recommendations(self, ticker: str) -> Optional[List]:
        """Analyst recommendations via MCP."""
        logger.info(f"[MCP] get_recommendations → {ticker}")
        return self.call_tool("get_recommendations", {"ticker": ticker.upper()})

    def get_news(self, ticker: str) -> Optional[List]:
        """Latest news articles via MCP."""
        logger.info(f"[MCP] get_yahoo_finance_news → {ticker}")
        return self.call_tool("get_yahoo_finance_news", {"ticker": ticker.upper()})

    def get_historical_prices(
        self, ticker: str, period: str = "1mo", interval: str = "1d"
    ) -> Optional[Any]:
        """Historical OHLCV data via MCP."""
        logger.info(f"[MCP] get_historical_stock_prices → {ticker}")
        return self.call_tool(
            "get_historical_stock_prices",
            {"ticker": ticker.upper(), "period": period, "interval": interval},
        )


# Module-level singleton — imported by external_tools.py
yahoo_finance_mcp = YahooFinanceMCPClient()
