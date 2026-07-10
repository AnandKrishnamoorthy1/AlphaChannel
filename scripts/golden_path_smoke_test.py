from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core_router import AgentTurnRequest, AlphaChannelRouter

load_dotenv(ROOT / ".env", override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> None:
    router = AlphaChannelRouter()
    response = router.process_agent_turn(
        AgentTurnRequest(
            channel_id="GOLDEN_PATH",
            thread_ts="1.0",
            user_id="DEMO_OPERATOR",
            text="Check if ServiceNow is a risky buy right now.",
            workspace_context=(
                "Engineering leadership is optimistic about enterprise AI demand. Procurement is monitoring "
                "renewal pressure and sales-cycle duration."
            ),
        ),
        progress_callback=lambda status: print(f"[progress] {status}"),
    )
    called_tools = [execution.tool_name for execution in response.tool_executions]
    required_tools = {"yfinance_risk_lookup", "sec_risk_lookup"}
    missing_tools = required_tools.difference(called_tools)
    if missing_tools:
        raise RuntimeError(f"Golden path did not call required MCP tools: {sorted(missing_tools)}")
    failed_tools = [
        execution.tool_name
        for execution in response.tool_executions
        if execution.tool_name in required_tools and execution.is_error
    ]
    if failed_tools:
        raise RuntimeError(f"Golden path MCP tools returned errors: {failed_tools}")
    if response.pending_action:
        raise RuntimeError(f"Golden path unexpectedly paused for {response.pending_action.tool_name}")
    print(f"Golden path passed. Tools: {called_tools}")
    print(response.message)


if __name__ == "__main__":
    main()
