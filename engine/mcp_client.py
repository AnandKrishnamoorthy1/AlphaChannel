from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.engine.mcp")


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

    def __init__(self, config_path: Path | None = None) -> None:
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

    def policies(self) -> list[MCPToolPolicy]:
        return list(self._policies.values())

    def policy_for(self, tool_name: str) -> MCPToolPolicy:
        try:
            return self._policies[tool_name]
        except KeyError as exc:
            raise ValueError(f"Unregistered MCP tool: {tool_name}") from exc

    def openai_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition["name"],
                    "description": definition["description"],
                    "parameters": definition.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for definition in self._tool_definitions.values()
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        self.policy_for(tool_name)
        return asyncio.run(self._call_tool(tool_name, arguments))

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        from mcp import ClientSession, StdioServerParameters, types
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(
            command=self._command,
            args=self._args,
            env={**os.environ, "ALPHACHANNEL_REPOSITORY_ROOT": str(self._root)},
        )
        logger.info(
            "mcp_tool_call_started tool=%s arguments=%s",
            tool_name,
            json.dumps(arguments, sort_keys=True, default=str),
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)

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
