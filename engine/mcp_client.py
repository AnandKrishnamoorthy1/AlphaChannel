"""Governed MCP transport for AlphaChannel's 2026 Slack Hackathon agent."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import logging
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.hackathon_2026.engine.mcp")


class MCPToolPolicy(BaseModel):
    name: str
    description: str
    read_only: bool = True
    requires_approval: bool = False


class MCPToolExecution(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: str
    is_error: bool = False


class AlphaChannelMCPClient:
    """Synchronous facade over an MCP stdio session for Slack worker threads."""

    def __init__(
        self,
        config_path: Path | None = None,
        tool_timeout_seconds: float | None = None,
        persistent: bool = True,
    ) -> None:
        self._root = Path(__file__).resolve().parents[1]
        self._config_path = config_path or self._root / "mcp_servers.json"
        config = json.loads(self._config_path.read_text(encoding="utf-8"))
        server = config["servers"]["alpha_channel_local"]
        self._command = sys.executable if server["command"] == "${PYTHON_EXECUTABLE}" else str(server["command"])
        self._args = [str(self._root / value) if value.startswith("engine/") else str(value) for value in server["args"]]
        self._policies = {
            item["name"]: MCPToolPolicy.model_validate(item)
            for item in server["tools"]
        }
        self._tool_definitions = {item["name"]: item for item in server["tools"]}
        configured_timeout = tool_timeout_seconds or float(
            os.getenv("ALPHACHANNEL_MCP_TOOL_TIMEOUT_SECONDS", "35")
        )
        self._tool_timeout_seconds = max(2.0, configured_timeout)
        self._persistent = persistent
        self._connection_lock = threading.RLock()
        self._tool_call_lock = threading.Lock()
        self._connection_ready = threading.Event()
        self._connection_thread: threading.Thread | None = None
        self._connection_error: BaseException | None = None
        self._connection_loop: asyncio.AbstractEventLoop | None = None
        self._connection_session: Any | None = None
        self._connection_stop: asyncio.Event | None = None

    def policies(self) -> list[MCPToolPolicy]:
        return list(self._policies.values())

    def policy_for(self, tool_name: str) -> MCPToolPolicy:
        try:
            return self._policies[tool_name]
        except KeyError as exc:
            raise ValueError(f"Unregistered MCP tool: {tool_name}") from exc

    def gemini_function_declarations(self) -> list[dict[str, Any]]:
        """Expose MCP input schemas as native google-genai declarations."""
        return [
            {
                "name": definition["name"],
                "description": definition["description"],
                "parameters_json_schema": copy.deepcopy(
                    definition.get("input_schema", {"type": "object", "properties": {}})
                ),
            }
            for definition in self._tool_definitions.values()
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        self.policy_for(tool_name)
        # SEC libraries perform synchronous network and document parsing work.
        # Isolate each lookup so a timed-out operation cannot poison the shared
        # persistent MCP session or delay later Slack requests.
        if tool_name == "sec_risk_lookup":
            return self._call_tool_one_shot(tool_name, arguments)
        if self._persistent:
            return self._call_tool_persistent(tool_name, arguments)
        return self._call_tool_one_shot(tool_name, arguments)

    def warmup_async(self) -> None:
        """Start one reusable MCP stdio session without delaying Slack startup."""
        if not self._persistent:
            return
        with self._connection_lock:
            if self._connection_thread and self._connection_thread.is_alive():
                return
            self._connection_ready.clear()
            self._connection_error = None
            self._connection_thread = threading.Thread(
                target=self._connection_thread_main,
                name="alpha-channel-mcp-session",
                daemon=True,
            )
            self._connection_thread.start()

    def close(self) -> None:
        loop = self._connection_loop
        stop = self._connection_stop
        if loop and stop and loop.is_running():
            loop.call_soon_threadsafe(stop.set)

    def _execution_timeout(self, tool_name: str) -> float:
        if tool_name == "sec_risk_lookup":
            return max(
                10.0,
                float(os.getenv("ALPHACHANNEL_SEC_TOOL_TIMEOUT_SECONDS", "30")),
            )
        return self._tool_timeout_seconds

    def _call_tool_persistent(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolExecution:
        self.warmup_async()
        execution_timeout = self._execution_timeout(tool_name)
        startup_timeout = min(15.0, execution_timeout)
        if not self._connection_ready.wait(timeout=startup_timeout):
            return self._timeout_execution(
                tool_name,
                arguments,
                phase="startup",
                timeout_seconds=execution_timeout,
            )
        if self._connection_error is not None:
            raise RuntimeError("Persistent MCP session failed to start.") from self._connection_error

        loop = self._connection_loop
        session = self._connection_session
        if loop is None or session is None or not loop.is_running():
            raise RuntimeError("Persistent MCP session is not available.")

        logger.info(
            "mcp_tool_call_started tool=%s arguments=%s transport=persistent_stdio",
            tool_name,
            json.dumps(arguments, sort_keys=True, default=str),
        )
        with self._tool_call_lock:
            future = asyncio.run_coroutine_threadsafe(
                session.call_tool(tool_name, arguments=arguments),
                loop,
            )
            try:
                result = future.result(timeout=execution_timeout)
            except concurrent.futures.TimeoutError:
                future.cancel()
                return self._timeout_execution(
                    tool_name,
                    arguments,
                    phase="execution",
                    timeout_seconds=execution_timeout,
                )
        return self._execution_from_result(tool_name, arguments, result)

    def _call_tool_one_shot(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolExecution:
        execution_timeout = self._execution_timeout(tool_name)
        result_queue: queue.Queue[MCPToolExecution | BaseException] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                result_queue.put_nowait(
                    asyncio.run(
                        asyncio.wait_for(
                            self._call_tool(tool_name, arguments),
                            timeout=execution_timeout,
                        )
                    )
                )
            except BaseException as exc:
                result_queue.put_nowait(exc)

        worker = threading.Thread(
            target=invoke,
            name=f"mcp-{tool_name}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=execution_timeout)
        if worker.is_alive():
            return self._timeout_execution(tool_name, arguments, timeout_seconds=execution_timeout)

        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            return self._timeout_execution(tool_name, arguments, timeout_seconds=execution_timeout)
        if isinstance(result, TimeoutError):
            return self._timeout_execution(tool_name, arguments, timeout_seconds=execution_timeout)
        if isinstance(result, BaseException):
            raise result
        return result

    def _timeout_execution(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        phase: str = "execution",
        timeout_seconds: float | None = None,
    ) -> MCPToolExecution:
        deadline = timeout_seconds or self._execution_timeout(tool_name)
        logger.error(
            "mcp_tool_call_timed_out tool=%s phase=%s timeout_seconds=%.1f",
            tool_name,
            phase,
            deadline,
        )
        return MCPToolExecution(
            tool_name=tool_name,
            arguments=arguments,
            output=json.dumps(
                {
                    "error": "mcp_tool_timeout",
                    "phase": phase,
                    "message": (
                        f"{tool_name} did not complete within "
                        f"{deadline:.0f} seconds."
                    ),
                }
            ),
            is_error=True,
        )

    def _connection_thread_main(self) -> None:
        try:
            asyncio.run(self._serve_persistent_session())
        except BaseException as exc:
            self._connection_error = exc
            self._connection_ready.set()
            logger.exception("mcp_persistent_session_failed")

    async def _serve_persistent_session(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(self._server_parameters()) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=12.0)
                self._connection_loop = asyncio.get_running_loop()
                self._connection_session = session
                self._connection_stop = asyncio.Event()
                self._connection_ready.set()
                logger.info("mcp_persistent_session_ready")
                await self._connection_stop.wait()

    def _server_parameters(self) -> Any:
        from mcp import StdioServerParameters

        return StdioServerParameters(
            command=self._command,
            args=self._args,
            env={**os.environ, "ALPHACHANNEL_REPOSITORY_ROOT": str(self._root)},
        )

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        logger.info(
            "mcp_tool_call_started tool=%s arguments=%s transport=one_shot_stdio",
            tool_name,
            json.dumps(arguments, sort_keys=True, default=str),
        )
        async with stdio_client(self._server_parameters()) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=10.0)
                result = await session.call_tool(tool_name, arguments=arguments)

        return self._execution_from_result(tool_name, arguments, result)

    @staticmethod
    def _execution_from_result(
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> MCPToolExecution:
        from mcp import types

        structured = getattr(result, "structuredContent", None)
        if structured:
            output = json.dumps(structured, indent=2, default=str)
        else:
            text_parts = [item.text for item in result.content if isinstance(item, types.TextContent)]
            output = "\n".join(text_parts) or "MCP tool returned no text output."
        execution = MCPToolExecution(
            tool_name=tool_name,
            arguments=arguments,
            output=output,
            is_error=bool(getattr(result, "isError", False)),
        )
        logger.info(
            "mcp_tool_call_completed tool=%s is_error=%s output_chars=%d",
            tool_name,
            execution.is_error,
            len(output),
        )
        return execution
