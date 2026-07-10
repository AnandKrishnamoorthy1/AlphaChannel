from __future__ import annotations

from typing import Any

from engine.agent_brain import BrainRunResult
from engine.core_router import AgentTurnRequest, AlphaChannelRouter
from engine.mcp_client import MCPToolExecution, MCPToolPolicy


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def policy_for(tool_name: str) -> MCPToolPolicy:
        return MCPToolPolicy(
            name=tool_name,
            description=tool_name,
            read_only=tool_name != "run_diagnostic",
            requires_approval=tool_name == "run_diagnostic",
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolExecution:
        self.calls.append((tool_name, arguments))
        return MCPToolExecution(tool_name=tool_name, arguments=arguments, output="ok")


class FakeAgentBrain:
    def __init__(self, mcp_client: FakeMCPClient) -> None:
        self.mcp_client = mcp_client
        self.received_conversations: list[list[dict[str, str]]] = []

    def run(
        self,
        *,
        goal: str,
        conversation: list[dict[str, str]],
        workspace_context: str | None,
        progress_callback: Any = None,
    ) -> BrainRunResult:
        self.received_conversations.append(conversation)
        if "traceback" in goal:
            executions = [
                self.mcp_client.call_tool("system_status", {}),
                self.mcp_client.call_tool("git_history", {"limit": 5}),
            ]
            return BrainRunResult(message="Investigation complete", executions=executions)
        if "show me more" in goal:
            execution = self.mcp_client.call_tool("git_history", {"limit": 15})
            return BrainRunResult(message="More history loaded", executions=[execution])
        return BrainRunResult(
            message="Approval required",
            pending_tool_name="run_diagnostic",
            pending_arguments={"check": "unit_tests", "target": "app.py"},
            pending_tool_call_id="call-1",
            continuation_messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "run_diagnostic",
                                "arguments": '{"check":"unit_tests","target":"app.py"}',
                            },
                        }
                    ],
                }
            ],
        )

    @staticmethod
    def resume(
        *,
        continuation_messages: list[dict[str, Any]],
        tool_call_id: str,
        execution: MCPToolExecution,
    ) -> BrainRunResult:
        assert continuation_messages
        assert tool_call_id == "call-1"
        return BrainRunResult(message="Diagnostic synthesized", executions=[execution])


def test_error_investigation_calls_read_only_mcp_tools() -> None:
    mcp_client = FakeMCPClient()
    brain = FakeAgentBrain(mcp_client)
    router = AlphaChannelRouter(mcp_client=mcp_client, agent_brain=brain)  # type: ignore[arg-type]

    response = router.process_agent_turn(
        AgentTurnRequest(
            channel_id="C1",
            thread_ts="1.0",
            user_id="U1",
            text="Investigate this traceback error",
        )
    )

    assert [name for name, _ in mcp_client.calls] == ["system_status", "git_history"]
    assert response.pending_action is None
    assert response.conversation_turn_count == 4

    router.process_agent_turn(
        AgentTurnRequest(
            channel_id="C1",
            thread_ts="1.0",
            user_id="U1",
            text="show me more",
        )
    )
    assert mcp_client.calls[-1] == ("git_history", {"limit": 15})
    assert brain.received_conversations[-1]


def test_diagnostic_can_be_edited_approved_and_denied() -> None:
    mcp_client = FakeMCPClient()
    router = AlphaChannelRouter(  # type: ignore[arg-type]
        mcp_client=mcp_client,
        agent_brain=FakeAgentBrain(mcp_client),
    )
    request = AgentTurnRequest(
        channel_id="C1",
        thread_ts="1.0",
        user_id="U1",
        text="run the tests",
    )

    pending = router.process_agent_turn(request).pending_action
    assert pending is not None
    assert mcp_client.calls == []

    edited = router.edit_agent_action(
        pending.action_id,
        {"check": "python_syntax", "target": "engine/core_router.py"},
        "U1",
    )
    approved = router.approve_agent_action(edited.action_id, "U1")
    assert approved.tool_executions[0].arguments["target"] == "engine/core_router.py"

    second_pending = router.process_agent_turn(request).pending_action
    assert second_pending is not None
    denied = router.process_agent_turn(
        AgentTurnRequest(
            channel_id="C1",
            thread_ts="1.0",
            user_id="U1",
            text="deny it",
        )
    )
    assert "not executed" in denied.message
