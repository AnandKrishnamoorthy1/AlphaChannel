# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent
# Copyright (c) 2026 Anand Krishnamoorthy
# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

REPOSITORY_ROOT = Path(
    os.getenv("ALPHACHANNEL_REPOSITORY_ROOT", Path(__file__).resolve().parents[1])
).resolve()
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from engine.sec_node import SecEdgarClient, SecRiskExtractor
from engine.trading_node import TradingNode
from engine.yfinance_node import YahooFinanceFundamentalsWorker, YahooFinanceOptionsWorker

MAX_OUTPUT_CHARS = 12_000

mcp = FastMCP(
    "AlphaChannel Repository Tools",
    instructions=(
        "Read repository context autonomously. Diagnostic execution is exposed separately so the Slack client can "
        "enforce a human approval checkpoint before invocation."
    ),
)


def _run_process(arguments: list[str], *, timeout_seconds: int = 30) -> dict[str, object]:
    completed = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout_seconds,
    )
    output = (completed.stdout + completed.stderr).strip()
    return {
        "command": arguments,
        "exit_code": completed.returncode,
        "output": output[:MAX_OUTPUT_CHARS],
        "truncated": len(output) > MAX_OUTPUT_CHARS,
    }


@mcp.tool()
def git_history(limit: int = 5, path: str | None = None) -> dict[str, object]:
    """Return recent git commits, optionally restricted to a repository-relative path."""
    safe_limit = max(1, min(limit, 20))
    arguments = ["git", "log", f"-{safe_limit}", "--date=iso", "--pretty=format:%h | %ad | %an | %s"]
    if path:
        candidate = (REPOSITORY_ROOT / path).resolve()
        if candidate != REPOSITORY_ROOT and REPOSITORY_ROOT not in candidate.parents:
            raise ValueError("path must remain inside the AlphaChannel repository")
        arguments.extend(["--", str(candidate.relative_to(REPOSITORY_ROOT))])
    return _run_process(arguments)


@mcp.tool()
def system_status() -> dict[str, object]:
    """Return runtime and repository status suitable for engineering incident triage."""
    git_status = _run_process(["git", "status", "--short"])
    branch = _run_process(["git", "branch", "--show-current"])
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "repository_root": str(REPOSITORY_ROOT),
        "git_branch": branch["output"],
        "changed_paths": len(str(git_status["output"]).splitlines()) if git_status["output"] else 0,
        "git_status": git_status["output"],
    }


@mcp.tool()
def yfinance_risk_lookup(ticker: str) -> dict[str, object]:
    """Fetch live price, option-chain implied volatility, positioning, and market risk markers for a ticker."""
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker or len(normalized_ticker) > 10:
        raise ValueError("ticker must be a valid market symbol")
    result = YahooFinanceOptionsWorker().fetch_options_signal(normalized_ticker).model_dump(mode="json")
    result["source_url"] = f"https://finance.yahoo.com/quote/{normalized_ticker}/options"
    return result


@mcp.tool()
def yfinance_fundamental_lookup(ticker: str) -> dict[str, object]:
    """Fetch investment fundamentals: valuation, revenue, growth, margins, ROE, leverage, and price."""
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker or len(normalized_ticker) > 10:
        raise ValueError("ticker must be a valid market symbol")
    result = YahooFinanceFundamentalsWorker().fetch_fundamental_signal(normalized_ticker).model_dump(mode="json")
    result["source_url"] = f"https://finance.yahoo.com/quote/{normalized_ticker}/key-statistics"
    return result


@mcp.tool()
def sec_risk_lookup(ticker: str, filing_text: str = "") -> dict[str, object]:
    """Fetch the latest SEC 10-K, 10-Q, 20-F, or 6-K and extract risk markers, or parse supplied text."""
    normalized_ticker = ticker.strip().upper()
    filing_metadata: dict[str, object]
    if filing_text.strip():
        source_text = filing_text
        filing_metadata = {"form": "supplied_text", "filing_date": None, "filing_url": None}
    else:
        filing = SecEdgarClient().fetch_latest_filing(normalized_ticker)
        source_text = filing.text
        filing_metadata = {
            "company_name": filing.company_name,
            "cik": filing.cik,
            "form": filing.form,
            "filing_date": filing.filing_date,
            "accession_number": filing.accession_number,
            "filing_url": filing.filing_url,
        }
    markers = SecRiskExtractor().extract_risk_markers(
        ticker=normalized_ticker,
        filing_text=source_text,
    )
    return {
        "ticker": normalized_ticker,
        "filing_text_supplied": bool(filing_text.strip()),
        **filing_metadata,
        "risk_markers": [marker.model_dump(mode="json") for marker in markers],
    }


@mcp.tool()
def portfolio_holdings() -> dict[str, object]:
    """Return the stock-only Robinhood Agentic Account snapshot."""
    return TradingNode().get_portfolio_snapshot(refresh_market_data=True).model_dump(mode="json")


@mcp.tool()
def execute_trade_checkpoint(
    ticker: str,
    side: Literal["buy", "sell", "hedge"],
    notional_usd: float,
    rationale: str,
) -> dict[str, object]:
    """Create an approved dry-run trade checkpoint. The Slack client gates this tool before MCP invocation."""
    if notional_usd <= 0:
        raise ValueError("notional_usd must be positive")
    return {
        "ticker": ticker.strip().upper(),
        "side": side,
        "notional_usd": notional_usd,
        "rationale": rationale,
        "status": "approved_dry_run_checkpoint",
        "trade_executed": False,
    }


@mcp.tool()
def run_diagnostic(
    check: Literal["git_status", "python_syntax", "unit_tests"],
    target: str = "app.py",
) -> dict[str, object]:
    """Run an allowlisted diagnostic. The Slack MCP client requires approval before calling this tool."""
    if check == "git_status":
        return _run_process(["git", "status", "--short"])
    if check == "unit_tests":
        return _run_process([sys.executable, "-m", "pytest", "-q"], timeout_seconds=120)

    candidate = (REPOSITORY_ROOT / target).resolve()
    if candidate != REPOSITORY_ROOT and REPOSITORY_ROOT not in candidate.parents:
        raise ValueError("target must remain inside the AlphaChannel repository")
    if candidate.suffix != ".py":
        raise ValueError("python_syntax only accepts a .py target")
    return _run_process(
        [sys.executable, "-c", "import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text('utf-8'))", str(candidate)]
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
