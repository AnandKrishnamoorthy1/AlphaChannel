"""Bounded persistent client for the community Yahoo Finance MCP server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from typing import Any

logger = logging.getLogger("alpha_channel.engine.yahoo_mcp")


class YahooFinanceMCPClient:
    """Run yahoo-finance-mcp over stdio without blocking AlphaChannel startup."""

    _instance: YahooFinanceMCPClient | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> YahooFinanceMCPClient:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._session: Any | None = None
        self._loop = asyncio.new_event_loop()
        self._connect_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="alpha-channel-yahoo-mcp",
            daemon=True,
        )
        self._thread.start()

    def get_stock_info(self, ticker: str) -> dict[str, Any]:
        result = self.call_tool("get_stock_info", {"ticker": ticker.strip().upper()})
        if not isinstance(result, dict):
            raise RuntimeError("Yahoo Finance MCP returned an empty stock-info response.")
        return result

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self._ensure_connected()
        if self._session is None:
            raise RuntimeError("Yahoo Finance MCP session is unavailable.")
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(tool_name, arguments),
            self._loop,
        )
        timeout = max(2.0, float(os.getenv("YAHOO_FINANCE_MCP_TIMEOUT_SECONDS", "20")))
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        with self._connect_lock:
            if self._session is not None:
                return
            future = asyncio.run_coroutine_threadsafe(self._connect_async(), self._loop)
            timeout = max(5.0, float(os.getenv("YAHOO_FINANCE_MCP_CONNECT_TIMEOUT_SECONDS", "15")))
            future.result(timeout=timeout)

    async def _connect_async(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # Launch through the interpreter so Windows uses the same subprocess
        # permissions and environment as AlphaChannel's own MCP server.
        command = sys.executable
        args = ["-m", "yahoo_finance_mcp.server"]
        parameters = StdioServerParameters(
            command=command,
            args=args,
            env={**os.environ},
        )
        logger.info("yahoo_finance_mcp_connecting command=%s", command)
        self._stdio_context = stdio_client(parameters)
        read_stream, write_stream = await self._stdio_context.__aenter__()
        self._session_context = ClientSession(read_stream, write_stream)
        self._session = await self._session_context.__aenter__()
        await asyncio.wait_for(self._session.initialize(), timeout=10.0)
        logger.info("yahoo_finance_mcp_connected")

    async def _call_tool_async(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await self._session.call_tool(tool_name, arguments=arguments)
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return structured.get("result", structured)
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return None
