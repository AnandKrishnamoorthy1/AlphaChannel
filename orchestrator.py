"""
Corporate Intelligence Engine — Agentic AI Orchestrator

A production-grade LangGraph state machine that orchestrates a multi-agent
financial research and autonomous trading workflow:

1. Pydantic-based immutable state management
2. Qwen-powered triage with structured JSON routing output
3. Tri-Agent Adversarial Investment Committee (Bull / Bear / Director)
4. 4-path conditional graph routing: research | direct_trade | portfolio | general_q
5. Human-in-loop approval checkpoints before any trade execution
6. Robinhood Agentic Trading MCP integration with paper-trading fallback
7. Full audit logging via loguru for every execution step

LLM: Alibaba Qwen (qwen3.7-plus) via DashScope international endpoint.
External data: Yahoo Finance MCP (real-time quote + fundamentals) + SEC EDGAR 10-Q (XBRL).
"""

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Literal, Any, Dict
from dataclasses import dataclass

from loguru import logger
from pydantic import BaseModel, Field, ConfigDict
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from typing_extensions import TypedDict, Annotated

from app.llm import call_qwen_for_triage, call_qwen_for_research, call_qwen_for_general_qa
from app.agents import run_investment_committee
from app.tools import financial_tools
from app.trading import RobinhoodMCPClient, OrderSide, OrderType
from app.skills import invoke_skill

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

# Configure loguru with custom format
logger.remove()  # Remove default handler
logger.add(
    lambda msg: print(msg, end=""),
    format="<level>{time:YYYY-MM-DD HH:mm:ss}</level> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="INFO",
    colorize=True,
)


# ============================================================================
# STATE MODEL - PYDANTIC BASEMODEL
# ============================================================================

class AgentState(BaseModel):
    """Central state tracking all execution context through the graph."""
    
    model_config = ConfigDict(extra="allow")
    
    user_input: str = Field(
        description="The original user query or request"
    )
    current_target_ticker: str = Field(
        default="", 
        description="Stock ticker extracted by the router (e.g., 'NVDA', 'TSLA')"
    )
    routing_decision: str = Field(
        default="",
        description="Routing decision: 'research', 'general_q', or 'error'"
    )
    research_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Collected financial/research data"
    )
    report_markdown: str = Field(
        default="",
        description="Final formatted report in Markdown"
    )
    error_count: int = Field(
        default=0,
        description="Count of errors encountered during execution"
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="When this state was created"
    )


class RouterDecision(BaseModel):
    """Structured output from the Triage/Router LLM."""
    
    ticker: str = Field(
        description="Extracted stock ticker symbol (e.g., 'NVDA')"
    )
    routing_path: Literal["research", "general_q", "direct_trade", "portfolio"] = Field(
        description="Next node: 'research' for analysis, 'direct_trade' for immediate trade, 'general_q' for other queries, 'portfolio' for portfolio review"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score of the routing decision (0.0-1.0)"
    )
    reasoning: str = Field(
        description="Brief explanation of the routing decision"
    )
    trade_action: str = Field(
        default="",
        description="For direct_trade path: BUY or SELL action and quantity"
    )


# ============================================================================
# APPROVAL/CHECKPOINT MODELS - FOR HUMAN-IN-LOOP
# ============================================================================

class ApprovalRequest(BaseModel):
    """Request requiring human approval - human-in-loop checkpoint."""
    
    request_id: str = Field(
        description="Unique identifier for this approval request"
    )
    action: str = Field(
        description="Recommended action: BUY, SELL, HOLD, etc."
    )
    ticker: str = Field(
        description="Stock ticker symbol"
    )
    reasoning: str = Field(
        description="Qwen's reasoning for the recommendation"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score of the recommendation"
    )
    timestamp: str = Field(
        description="When the approval request was created"
    )


class ApprovalResponse(BaseModel):
    """Human's approval/rejection decision."""
    
    approved: bool = Field(
        description="Whether human approved the recommendation"
    )
    approver_notes: str = Field(
        default="",
        description="Human's notes/reasoning for approval or rejection"
    )


# ============================================================================
# GRAPH STATE TYPEDDICT (Required by LangGraph)
# ============================================================================

class GraphState(TypedDict, total=False):
    """Type definition for the state graph edges."""
    user_input: str
    conversation_history: list  # [{"role": "user"|"assistant", "content": str}]
    current_target_ticker: str
    routing_decision: str
    trade_action: str  # "BUY" or "SELL" for direct trades
    research_data: Dict[str, Any]
    report_markdown: str
    error_count: int
    pending_approval: Dict[str, Any]  # Approval request waiting for human
    approval_status: str  # "pending", "approved", "rejected"
    portfolio_data: Dict[str, Any]  # User's portfolio holdings and cash
    portfolio_analysis: Dict[str, Any]  # Results from Portfolio Analyzer skill
    risk_assessment: Dict[str, Any]  # Results from Risk Manager skill
    active_triggers: Dict[str, Any]  # Results from Stop-Loss/Take-Profit Triggers skill


# ============================================================================
# LLM ROUTING - TRIAGE NODE
# ============================================================================

def triage_node(state: GraphState) -> GraphState:
    """
    Triage/Router Node - Uses Qwen to analyze and route user input.
    
    This node:
    1. Takes the raw user input
    2. Uses Qwen LLM with structured output to parse the request
    3. Extracts stock ticker if present
    4. Decides the next routing path (research vs general_q)
    5. Returns updated state for conditional routing
    
    Args:
        state: Current graph state
        
    Returns:
        Updated state with routing_decision and current_target_ticker
    """
    logger.info("=" * 80)
    logger.info("[ENTERING TRIAGE NODE]")
    logger.info("-" * 80)
    
    # ────────────────────────────────────────────────────────────────────
    # QWEN LLM CALL - Structured output for routing decision
    # ────────────────────────────────────────────────────────────────────
    
    logger.info("Invoking Qwen LLM for structured routing decision...")
    
    try:
        qwen_routing_decision = call_qwen_for_triage(
            state["user_input"],
            conversation_history=state.get("conversation_history", []),
        )
        logger.info(f"Qwen Response: {json.dumps(qwen_routing_decision, indent=2)}")
        
        # ────────────────────────────────────────────────────────────────────
        # UPDATE STATE WITH QWEN RESPONSE
        # ────────────────────────────────────────────────────────────────────
        
        state["current_target_ticker"] = qwen_routing_decision.get("ticker", "")
        state["routing_decision"] = qwen_routing_decision.get("routing_path", "general_q")
        
        confidence = qwen_routing_decision.get("confidence", 0.0)
        reasoning = qwen_routing_decision.get("reasoning", "No reasoning provided")
        trade_action = qwen_routing_decision.get("trade_action", None)
        
        logger.info(f"Extracted Ticker: {state['current_target_ticker']}")
        logger.info(f"[TRIAGE COMPLETE] Set state['current_target_ticker'] = '{state['current_target_ticker']}'")
        logger.info(f"Routing Decision: {state['routing_decision']}")
        logger.info(f"Confidence: {confidence:.2%}")
        logger.info(f"Reasoning: {reasoning}")
        if trade_action:
            logger.info(f"Trade Action: {trade_action}")
            state['trade_action'] = trade_action  # Set at root level
        
        # Store trade action for direct_trade path
        if state['routing_decision'] == 'direct_trade' and trade_action:
            state['pending_approval'] = {
                'ticker': state['current_target_ticker'],
                'action': trade_action,
                'routing': state['routing_decision'],
                'user_input': state['user_input'],
            }
        
    except Exception as e:
        logger.error(f"Qwen triage call failed: {str(e)}")
        logger.warning("Falling back to general Q&A path")
        state["routing_decision"] = "general_q"
        state["current_target_ticker"] = ""
        state["error_count"] += 1
    
    logger.info("[TRIAGE NODE COMPLETE]")
    logger.info("=" * 80)
    
    return state


# ============================================================================
# RESEARCH NODE - STUB
# ============================================================================

def research_node(state: GraphState) -> GraphState:
    """
    Research Node - Uses Qwen to generate financial research analysis with human approval.
    
    This node:
    1. Accepts routed state with ticker
    2. Calls real Yahoo Finance MCP for live financial data
    3. Uses Qwen to generate comprehensive AI analysis
    4. Adds human-in-loop approval checkpoint for BUY/SELL recommendations
    5. Returns state with analysis and approval request (if needed)
    
    This demonstrates:
    - External tool invocation (Yahoo Finance MCP)
    - Human-in-loop checkpoints at critical decision points
    - Production-ready error handling
    
    Args:
        state: Current graph state with ticker set
        
    Returns:
        State with research_data, report_markdown, and optional pending_approval
    """
    logger.info("=" * 80)
    logger.info("[ENTERING RESEARCH NODE]")
    logger.info(f"Target Ticker: {state['current_target_ticker']}")
    logger.info("-" * 80)
    
    ticker = state["current_target_ticker"]
    
    if not ticker or ticker == "UNKNOWN":
        logger.warning("No valid ticker provided to research node")
        state["error_count"] += 1
        state["research_data"] = {"error": "No ticker provided"}
        logger.info("[RESEARCH NODE COMPLETE - ERROR]")
        logger.info("=" * 80)
        return state
    
    # ────────────────────────────────────────────────────────────────────
    # EXTERNAL TOOL: YAHOO FINANCE MCP - REAL DATA
    # ────────────────────────────────────────────────────────────────────
    
    logger.info("[EXTERNAL TOOL] Calling Yahoo Finance MCP for real stock data + fundamentals...")
    
    # Quote and OVERVIEW fetched in parallel inside get_enriched_stock_data
    real_stock_data = financial_tools.get_enriched_stock_data(ticker)
    
    logger.info(f"[EXTERNAL TOOL RESULT] {real_stock_data['data_source']}")
    logger.info(f"  Price: ${real_stock_data['price']}")
    logger.info(f"  Change: {real_stock_data['change_percent']:+.2f}%")
    logger.info(f"  Volume: {real_stock_data['volume']:,}")
    if real_stock_data.get("sector"):
        logger.info(f"  Sector: {real_stock_data['sector']}")
    if real_stock_data.get("pe_ratio"):
        logger.info(f"  P/E Ratio: {real_stock_data['pe_ratio']}")
    if real_stock_data.get("eps"):
        logger.info(f"  EPS: ${real_stock_data['eps']}")
    if real_stock_data.get("analyst_target_price"):
        logger.info(f"  Analyst Target: ${real_stock_data['analyst_target_price']}")
    sec = real_stock_data.get("sec_filings", {})
    if sec.get("available"):
        logger.info(f"  SEC Filings: {len(sec.get('quarters', []))} quarters loaded")
    else:
        logger.info(f"  SEC Filings: unavailable ({sec.get('reason', 'unknown')})") 
    
    # ────────────────────────────────────────────────────────────────────
    # BUILD PORTFOLIO DATA FOR SKILLS
    # Fetch user's holdings to provide context for Portfolio Analyzer & Risk Manager
    # ────────────────────────────────────────────────────────────────────
    
    portfolio_data_for_skills = {}
    try:
        from app.trading import get_broker_for_user, get_account_id_for_user
        
        broker = get_broker_for_user()
        account_id = get_account_id_for_user()
        account_info = asyncio.run(broker.get_account_info(account_id))
        
        if hasattr(broker, "ledger"):
            raw = broker.ledger.get("accounts", {}).get(account_id, {})
            raw_positions: dict = raw.get("positions", {})
        else:
            raw_positions = {}
        
        # Normalize positions
        positions: dict = {}
        for t, v in raw_positions.items():
            if isinstance(v, dict):
                positions[t] = {"quantity": float(v.get("quantity", 0)), "average_cost": float(v.get("avg_cost", 0))}
            else:
                positions[t] = {"quantity": float(v), "average_cost": 0.0}
        
        cash: float = float(getattr(account_info, "cash_balance", 0.0))
        
        if positions:
            # Fetch live prices for trigger detection
            import concurrent.futures as _cf
            live_prices = {}
            for t in positions.keys():
                result = financial_tools.get_stock_data(t)
                live_prices[t] = result if isinstance(result, dict) else {}
            
            # Build position list for skills
            position_list = []
            total_market_value = 0.0
            
            for pos_ticker, pos in positions.items():
                shares = float(pos.get("quantity", 0))
                avg_cost = float(pos.get("average_cost", 0))
                pdata = live_prices.get(pos_ticker, {})
                current_price = pdata.get("price", avg_cost) or avg_cost or 1.0  # Fallback chain
                market_value = shares * current_price
                total_market_value += market_value
                
                # SAFETY: If avg_cost is 0, assume it equals current price (breakeven for simulated trades)
                if avg_cost == 0 or avg_cost is None:
                    avg_cost = current_price
                    logger.debug(f"[PORTFOLIO] {pos_ticker}: avg_cost was 0, setting to current_price ${current_price}")
                
                position_list.append({
                    "ticker": pos_ticker,
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "current_price": current_price,
                    "value": market_value,
                    "sector": pdata.get("sector", "Unknown"),
                })
            
            total_portfolio_value = total_market_value + cash
            
            # Build portfolio structure for skills
            portfolio_data_for_skills = {
                "positions": [
                    {
                        "ticker": p["ticker"],
                        "shares": p["shares"],
                        "avg_cost": p["avg_cost"],
                        "current_price": p["current_price"],
                        "value": p["value"],
                        "cost_basis": p["shares"] * p["avg_cost"],
                        "unrealized_pnl": p["value"] - (p["shares"] * p["avg_cost"]),
                        "unrealized_pnl_pct": ((p["value"] - (p["shares"] * p["avg_cost"])) / (p["shares"] * p["avg_cost"]) * 100) if (p["shares"] * p["avg_cost"]) > 0 else 0,
                        "pct_of_portfolio": (p["value"] / total_portfolio_value * 100) if total_portfolio_value > 0 else 0,
                        "sector": p["sector"],
                    }
                    for p in position_list
                ],
                "history": position_list,
                "total_value": total_portfolio_value,
                "cash": cash,
                "total_invested": total_market_value,
            }
            
            logger.info(f"Portfolio data built: {len(position_list)} position(s), ${total_portfolio_value:,.2f} total value")
            # Log sample position for debugging
            if portfolio_data_for_skills["positions"]:
                sample = portfolio_data_for_skills["positions"][0]
                logger.debug(f"[PORTFOLIO] Sample position: {sample['ticker']} - shares={sample['shares']}, "
                           f"avg_cost=${sample['avg_cost']:.2f}, current=${sample['current_price']:.2f}, "
                           f"P&L={sample['unrealized_pnl_pct']:+.2f}%")
    
    except Exception as e:
        logger.warning(f"Could not fetch portfolio data for skills: {e}")
        portfolio_data_for_skills = {}
    
    # ────────────────────────────────────────────────────────────────────
    # INVOKE REUSABLE SKILLS: Portfolio Analysis & Risk Assessment
    # These skills provide context for the Investment Committee debate
    # ────────────────────────────────────────────────────────────────────
    
    logger.info("[SKILLS] Invoking Portfolio Analyzer skill...")
    try:
        portfolio_data = portfolio_data_for_skills
        if portfolio_data:
            portfolio_analysis = invoke_skill(
                "portfolio_analyzer",
                portfolio=portfolio_data
            )
            state["portfolio_analysis"] = portfolio_analysis
            logger.info(f"[SKILLS] Portfolio analysis: Diversification score = {portfolio_analysis.get('diversification_metrics', {}).get('diversification_score', 'N/A')}")
        else:
            logger.debug("[SKILLS] No portfolio data available, skipping portfolio analysis")
            state["portfolio_analysis"] = {}
    except Exception as e:
        logger.warning(f"[SKILLS] Portfolio Analyzer failed: {e}")
        state["portfolio_analysis"] = {}
    
    logger.info("[SKILLS] Invoking Risk Manager skill...")
    try:
        if portfolio_data:
            risk_assessment = invoke_skill(
                "risk_manager",
                portfolio=portfolio_data,
                risk_tolerance="moderate"
            )
            state["risk_assessment"] = risk_assessment
            logger.info(f"[SKILLS] Risk assessment: Score = {risk_assessment.get('overall_risk_score', 'N/A')}, Rating = {risk_assessment.get('risk_rating', 'N/A')}")
        else:
            logger.debug("[SKILLS] No portfolio data available, skipping risk assessment")
            state["risk_assessment"] = {}
    except Exception as e:
        logger.warning(f"[SKILLS] Risk Manager failed: {e}")
        state["risk_assessment"] = {}
    
    logger.info("[SKILLS] Checking stop-loss/take-profit triggers...")
    try:
        if portfolio_data:
            triggers = invoke_skill(
                "stop_loss_take_profit",
                portfolio=portfolio_data
            )
            state["active_triggers"] = triggers
            
            num_triggers = triggers.get("active_triggers", 0)
            if num_triggers > 0:
                logger.warning(f"[SKILLS] {num_triggers} active trigger(s) detected")
                logger.warning(f"[SKILLS] Summary: {triggers.get('summary', '')}")
                for trigger in triggers.get("triggers", []):
                    logger.warning(f"  • [{trigger.get('severity', 'INFO')}] {trigger.get('recommendation', '')}")
            else:
                logger.info("[SKILLS] No active triggers")
        else:
            logger.debug("[SKILLS] No portfolio data available, skipping trigger check")
            state["active_triggers"] = {}
    except Exception as e:
        logger.warning(f"[SKILLS] Stop-Loss/Take-Profit check failed: {e}")
        state["active_triggers"] = {}
    
    # ────────────────────────────────────────────────────────────────────
    # MULTI-AGENT ANALYSIS: TRI-AGENT ADVERSARIAL INVESTMENT COMMITTEE
    # Bull Analyst vs Bear Auditor, moderated by the Portfolio Director.
    # Now with portfolio risk context + active triggers from skills!
    # ────────────────────────────────────────────────────────────────────

    try:
        logger.info("Convening Tri-Agent Investment Committee on REAL data (with skill-driven context)...")
        logger.info(f"[RESEARCH NODE] About to call run_investment_committee with ticker={ticker}")
        logger.debug(f"[RESEARCH NODE] state['current_target_ticker']={state.get('current_target_ticker')}")

        # Save portfolio data to state for downstream use
        state["portfolio_data"] = portfolio_data_for_skills
        
        # Add skill results to market_data so the committee has full context
        market_data_with_skills = {
            **real_stock_data,
            "portfolio_analysis": state.get("portfolio_analysis", {}),
            "risk_assessment": state.get("risk_assessment", {}),
            "active_triggers": state.get("active_triggers", {}),
        }

        committee_result = run_investment_committee(
            ticker=ticker,
            market_data=market_data_with_skills,
            rounds=2,
        )
        logger.info(f"[RESEARCH NODE] Committee finished, verdict ticker would be from report")

        verdict = committee_result["verdict"]
        report_markdown = committee_result["report_markdown"]
        
        # DEBUG: Extract the ticker from the report heading to verify
        import re
        report_heading_match = re.search(r'Investment Committee Verdict: ([A-Z]{1,5})', report_markdown)
        if report_heading_match:
            report_ticker = report_heading_match.group(1)
            logger.info(f"[DEBUG] Report heading contains ticker: {report_ticker}")
            if report_ticker != ticker:
                logger.error(f"[ERROR] TICKER MISMATCH: Passed {ticker} to committee, but report shows {report_ticker}!")

        state["research_data"] = {
            **real_stock_data,
            "committee_verdict": verdict,
            "committee_transcript": committee_result["transcript"],
        }
        state["report_markdown"] = report_markdown

        recommendation = verdict.get("verdict", "HOLD")
        committee_confidence = float(verdict.get("confidence", 0.0))

        logger.info(
            f"Committee reached verdict: {recommendation} "
            f"({committee_confidence:.0%} confidence)"
        )

        # Research path always delivers a report — no approval gate here.
        # Approval gates are only triggered by the direct_trade path
        # (explicit "Buy $X of TICKER" commands routed via trading_node).
        state["routing_decision"] = "completed"
        logger.info("[RESEARCH NODE COMPLETE]")

        logger.info("=" * 80)
        return state

    except Exception as e:
        logger.error(f"Investment committee analysis failed: {str(e)}")
        logger.warning("Using fallback research data format")
        state["research_data"] = real_stock_data
        state["error_count"] += 1
        
        # Fallback report
        state["report_markdown"] = f"""# Financial Research Report: {ticker}

## Live Market Data (Real Yahoo Finance MCP)
- **Current Price:** ${real_stock_data['price']}
- **Change:** {real_stock_data['change_percent']:+.2f}%
- **Volume:** {real_stock_data['volume']:,}
- **Data Source:** {real_stock_data['data_source']}

## Analysis Status
*Detailed Qwen analysis generation failed. Please check Qwen API connection.*

---
*Generated at: {datetime.now().isoformat()}*
"""
        state["routing_decision"] = "completed"
        logger.info("[RESEARCH NODE COMPLETE - ERROR]")
        logger.info("=" * 80)
        return state


def _extract_recommendation(report_markdown: str) -> str:
    """
    Extract recommendation from Qwen report.
    
    Looks for keywords: BUY, SELL, HOLD, etc.
    Falls back to HOLD if unclear.
    """
    report_upper = report_markdown.upper()
    
    if "BUY" in report_upper:
        return "BUY"
    elif "SELL" in report_upper:
        return "SELL"
    elif "HOLD" in report_upper:
        return "HOLD"
    else:
        return "HOLD"  # Safe default


# ============================================================================
# GENERAL Q NODE - STUB
# ============================================================================

def general_q_node(state: GraphState) -> GraphState:
    """
    General Question Node - Uses Qwen to handle non-research queries.
    
    This node:
    1. Accepts routed state
    2. Detects if competitor comparison is being requested
    3. If yes, fetches competitor data from Yahoo Finance
    4. Uses Qwen to process general financial inquiries with context
    5. Generates comprehensive answer
    6. Returns enriched state with report
    
    Args:
        state: Current graph state
        
    Returns:
        State with report_markdown populated
    """
    logger.info("=" * 80)
    logger.info("[ENTERING GENERAL Q NODE]")
    logger.info(f"Query: {state['user_input']}")
    logger.info("-" * 80)
    
    # ────────────────────────────────────────────────────────────────────
    # DETECT COMPETITOR ANALYSIS REQUEST
    # ────────────────────────────────────────────────────────────────────
    
    user_input_lower = state['user_input'].lower()
    is_competitor_question = any(
        keyword in user_input_lower 
        for keyword in ['competitor', 'compare', 'vs ', 'versus', 'profitability', 'margin', 'peer', 'sector']
    )
    
    competitor_context = ""
    target_context = ""  # ADD: Build context for the target company
    if is_competitor_question and state.get('current_target_ticker'):
        ticker = state['current_target_ticker']
        logger.info(f"[GENERAL Q] Competitor analysis detected for {ticker}")
        
        try:
            # ────────────────────────────────────────────────────────────────────
            # FETCH TARGET COMPANY DATA
            # ────────────────────────────────────────────────────────────────────
            
            from app.tools.yahoo_finance_mcp_client import YahooFinanceMCPClient
            
            yf_client = YahooFinanceMCPClient()
            target_info = yf_client.get_stock_info(ticker)
            
            if target_info:
                logger.info(f"[GENERAL Q] Fetching target info for {ticker}...")
                target_name = target_info.get('shortName', ticker)
                target_sector = target_info.get('sector', 'Unknown')
                logger.info(f"[GENERAL Q] {ticker} ({target_name}) - Sector: {target_sector}")
                
                # BUILD TARGET CONTEXT (ADD THIS SECTION)
                target_context = f"\n\n## Target Company ({ticker}):\n"
                target_context += f"**{ticker}** ({target_name})\n"
                
                try:
                    price = float(target_info.get('regularMarketPrice') or target_info.get('currentPrice') or 0)
                    target_context += f"- Price: ${price:.2f}\n" if price > 0 else "- Price: N/A\n"
                except (ValueError, TypeError):
                    target_context += "- Price: N/A\n"
                
                try:
                    profit_margin = target_info.get('profitMargins')
                    if profit_margin is not None and profit_margin != '':
                        target_context += f"- Profit Margin: {float(profit_margin)*100:.2f}%\n"
                    else:
                        target_context += "- Profit Margin: N/A\n"
                except (ValueError, TypeError):
                    target_context += "- Profit Margin: N/A\n"
                
                try:
                    roe = target_info.get('returnOnEquity')
                    if roe is not None and roe != '':
                        target_context += f"- ROE: {float(roe)*100:.2f}%\n"
                    else:
                        target_context += "- ROE: N/A\n"
                except (ValueError, TypeError):
                    target_context += "- ROE: N/A\n"
                
                try:
                    pe = target_info.get('trailingPE') or target_info.get('forwardPE')
                    if pe is not None and pe != '' and float(pe) > 0:
                        target_context += f"- P/E: {float(pe):.2f}\n"
                    else:
                        target_context += "- P/E: N/A\n"
                except (ValueError, TypeError):
                    target_context += "- P/E: N/A\n"
                
                try:
                    revenue_growth = target_info.get('revenueGrowth')
                    if revenue_growth is not None and revenue_growth != '':
                        target_context += f"- Revenue Growth: {float(revenue_growth)*100:.2f}%\n"
                    else:
                        target_context += "- Revenue Growth: N/A\n"
                except (ValueError, TypeError):
                    target_context += "- Revenue Growth: N/A\n"
                
                logger.info(f"[GENERAL Q] Target context prepared ({len(target_context)} chars)")
                
                # Ask Qwen to identify competitors
                competitor_query = (
                    f"For {ticker} ({target_name}) in the {target_sector} sector, "
                    f"name exactly 3 main competitors (tickers only, comma-separated). "
                    f"Example format: NVDA, AMD, INTC"
                )
                logger.info(f"[GENERAL Q] Asking Qwen for competitors: {competitor_query}")
                
                competitor_response = call_qwen_for_general_qa(competitor_query)
                
                # Parse Qwen's response to extract tickers (first 3 uppercase words of 1-5 chars)
                import re
                ticker_matches = re.findall(r'\b([A-Z]{1,5})\b', competitor_response)
                # Filter out common non-ticker words
                common_words = {'FOR', 'THE', 'AND', 'ARE', 'MAIN', 'TOP', 'BEST', 'INCLUDE', 'SUCH', 'LIKE', 'WHICH', 'THAT', 'COMPETITORS', 'SECTOR', 'INDUSTRY'}
                competitors = [t for t in ticker_matches if t not in common_words][:3]
                
                if competitors:
                    logger.info(f"[GENERAL Q] Qwen identified competitors: {competitors}")
                else:
                    logger.warning(f"[GENERAL Q] Qwen response unclear, using fallback: {competitor_response[:100]}")
                    # Fallback to a generic competitor pool if parsing fails
                    fallback_pools = {
                        'Automotive': ['F', 'GM', 'TM'],
                        'Technology': ['MSFT', 'GOOGL', 'AAPL'],
                        'Retail': ['WMT', 'TGT', 'COST'],
                        'Finance': ['JPM', 'BAC', 'WFC'],
                    }
                    for sector_key, tickers in fallback_pools.items():
                        if sector_key.lower() in target_sector.lower():
                            competitors = [t for t in tickers if t != ticker][:3]
                            break
                    else:
                        competitors = ['MSFT', 'GOOGL', 'AAPL']  # Default tech peers
                    logger.info(f"[GENERAL Q] Using fallback competitors: {competitors}")
            else:
                logger.warning(f"[GENERAL Q] Could not fetch target info for {ticker}, using fallback")
                competitors = ['MSFT', 'GOOGL', 'AAPL']
            if competitors:
                logger.info(f"[GENERAL Q] Fetching data for competitors: {competitors}")
                
                from app.tools.yahoo_finance_mcp_client import YahooFinanceMCPClient
                yf_client = YahooFinanceMCPClient()
                
                comp_data = []
                for comp_ticker in competitors:
                    try:
                        comp_info = yf_client.get_stock_info(comp_ticker)
                        if comp_info:
                            logger.debug(f"[GENERAL Q] {comp_ticker} available fields: {list(comp_info.keys())}")
                            
                            # Use correct Yahoo Finance camelCase field names
                            profit_margin = comp_info.get('profitMargins')  # Already a decimal (0.25 = 25%)
                            roe = comp_info.get('returnOnEquity')  # Already a decimal (0.22 = 22%)
                            pe = comp_info.get('trailingPE') or comp_info.get('forwardPE')
                            revenue_growth = comp_info.get('revenueGrowth')  # Already a decimal (0.067 = 6.7%)
                            
                            comp_data.append({
                                'ticker': comp_ticker,
                                'price': comp_info.get('regularMarketPrice') or comp_info.get('currentPrice'),
                                'profit_margin': profit_margin,
                                'roe': roe,
                                'pe': pe,
                                'revenue_growth': revenue_growth,
                                'market_cap': comp_info.get('marketCap'),
                                'eps': comp_info.get('trailingEps'),
                                'dividend_yield': comp_info.get('trailingAnnualDividendYield'),
                                'gross_margin': comp_info.get('grossMargins'),
                                'operating_margin': comp_info.get('operatingMargins'),
                            })
                            logger.info(f"[GENERAL Q] {comp_ticker} → Margin: {profit_margin}, ROE: {roe}, P/E: {pe}, Revenue Growth: {revenue_growth}")
                    except Exception as e:
                        logger.warning(f"[GENERAL Q] Failed to fetch {comp_ticker}: {e}")
                        continue
                
                logger.info(f"[GENERAL Q] Competitor data retrieved: {len(comp_data)} records")
                if comp_data:
                    logger.debug(f"[GENERAL Q] Raw competitor data: {comp_data}")
                    competitor_context = "\n\n## Competitor Data (for comparison):\n"
                    for comp in comp_data:
                        competitor_context += f"\n**{comp['ticker']}:**\n"
                        
                        # Price - safely convert to float
                        try:
                            price = float(comp.get('price') or 0)
                            competitor_context += f"- Price: ${price:.2f}\n" if price > 0 else "- Price: N/A\n"
                        except (ValueError, TypeError):
                            competitor_context += "- Price: N/A\n"
                        
                        # Profit Margin - Yahoo returns as decimal (0.25 = 25%)
                        try:
                            profit_margin = comp.get('profit_margin')
                            if profit_margin is not None and profit_margin != '':
                                competitor_context += f"- Profit Margin: {float(profit_margin)*100:.2f}%\n"
                            else:
                                competitor_context += "- Profit Margin: N/A\n"
                        except (ValueError, TypeError):
                            competitor_context += "- Profit Margin: N/A\n"
                        
                        # ROE - Yahoo returns as decimal (0.22 = 22%)
                        try:
                            roe = comp.get('roe')
                            if roe is not None and roe != '':
                                competitor_context += f"- ROE: {float(roe)*100:.2f}%\n"
                            else:
                                competitor_context += "- ROE: N/A\n"
                        except (ValueError, TypeError):
                            competitor_context += "- ROE: N/A\n"
                        
                        # P/E Ratio - Yahoo returns as float
                        try:
                            pe = comp.get('pe')
                            if pe is not None and pe != '' and float(pe) > 0:
                                competitor_context += f"- P/E: {float(pe):.2f}\n"
                            else:
                                competitor_context += "- P/E: N/A\n"
                        except (ValueError, TypeError):
                            competitor_context += "- P/E: N/A\n"
                        
                        # Revenue Growth - Yahoo returns as decimal (0.067 = 6.7%)
                        try:
                            revenue_growth = comp.get('revenue_growth')
                            if revenue_growth is not None and revenue_growth != '':
                                competitor_context += f"- Revenue Growth: {float(revenue_growth)*100:.2f}%\n"
                            else:
                                competitor_context += "- Revenue Growth: N/A\n"
                        except (ValueError, TypeError):
                            competitor_context += "- Revenue Growth: N/A\n"
                    
                    logger.info(f"[GENERAL Q] Competitor context prepared ({len(competitor_context)} chars)")
        
        except Exception as e:
            logger.warning(f"[GENERAL Q] Failed to fetch competitor data: {e}")
            competitor_context = f"\n\n(Note: Unable to fetch competitor data - {str(e)})"
    
    # ────────────────────────────────────────────────────────────────────
    # QWEN GENERAL Q&A WITH TARGET + COMPETITOR CONTEXT
    # ────────────────────────────────────────────────────────────────────
    
    try:
        logger.info("Invoking Qwen for general Q&A...")
        
        # Enhance user input with BOTH target and competitor context if applicable
        # Target goes first (primary focus), then competitors for comparison
        enhanced_input = state['user_input'] + target_context + competitor_context

        answer = call_qwen_for_general_qa(
            enhanced_input,
            conversation_history=state.get("conversation_history", []),
        )
        state["report_markdown"] = answer
        
        logger.info(f"General Q&A response generated ({len(answer)} chars)")
        
    except Exception as e:
        logger.error(f"Qwen QA call failed: {str(e)}")
        logger.warning("Using fallback response format")
        state["error_count"] += 1
        
        # Fallback response
        state["report_markdown"] = f"""# Response to Your Question

**Question:** {state['user_input']}

**Answer:**
I encountered an issue retrieving a detailed response at this moment. 
Please check your Qwen API connection and try again.

**Tips:**
- Ensure QWEN_API_KEY environment variable is set
- Verify your Qwen API key has available credits
- Check your internet connection

---
*Generated at: {datetime.now().isoformat()}*
"""
    
    logger.info("General query processed")
    logger.info("[GENERAL Q NODE COMPLETE]")
    logger.info("=" * 80)
    
    return state


# ============================================================================
# TRADING NODE - DIRECT TRADE EXECUTION
# ============================================================================

def trading_node(state: GraphState) -> GraphState:
    """
    Trading Node - Processes direct trade requests with Robinhood integration.
    
    This node:
    1. Accepts routed state with trade action (BUY/SELL)
    2. Extracts dollar amount from user input
    3. Creates a human-in-loop approval request
    4. WAITS for human approval before executing
    5. Returns state with pending_approval for checkpoint
    
    This is crucial for safe agentic trading - NO TRADE is executed without approval!
    
    Args:
        state: Current graph state with trade action
        
    Returns:
        State with pending_approval and approval_status="pending"
    """
    import re
    
    logger.info("=" * 80)
    logger.info("[ENTERING TRADING NODE]")
    logger.info(f"Target Ticker: {state['current_target_ticker']}")
    logger.info(f"Trade Action: {state.get('trade_action', 'N/A')}")
    logger.info("-" * 80)
    
    ticker = state.get("current_target_ticker", "")
    trade_action = state.get("trade_action", "").upper()  # "BUY" or "SELL"
    user_input = state.get("user_input", "")
    
    if not ticker or not trade_action:
        logger.error("Invalid trade parameters")
        state["error_count"] += 1
        state["routing_decision"] = "completed"
        return state
    
    if trade_action not in ["BUY", "SELL"]:
        logger.error(f"Invalid trade action: {trade_action}")
        state["error_count"] += 1
        state["routing_decision"] = "completed"
        return state
    
    # ────────────────────────────────────────────────────────────────────
    # Extract dollar amount from user input
    # ────────────────────────────────────────────────────────────────────
    
    # Look for patterns like "$5", "$2", "$100.50"
    dollar_pattern = r'\$([0-9]+(?:\.[0-9]{2})?)'
    match = re.search(dollar_pattern, user_input)
    
    amount_dollars = None
    quantity = None
    
    if match:
        try:
            amount_dollars = float(match.group(1))
            logger.info(f"Extracted dollar amount: ${amount_dollars:.2f}")
        except ValueError:
            logger.warning(f"Could not parse dollar amount from: {match.group(0)}")
    else:
        # Look for quantity patterns like "10 shares", "5 shares of", "buy 10"
        quantity_pattern = r'(\d+(?:\.\d+)?)\s*(?:shares?)?'
        qty_match = re.search(quantity_pattern, user_input)
        if qty_match:
            try:
                quantity = float(qty_match.group(1))
                logger.info(f"Extracted quantity: {quantity} shares")
            except ValueError:
                logger.warning(f"Could not parse quantity from: {qty_match.group(0)}")
    
    if not amount_dollars and not quantity:
        logger.error(f"Could not extract dollar amount or quantity from: {user_input}")
        state["error_count"] += 1
        state["routing_decision"] = "completed"
        return state
    
    try:
        # ────────────────────────────────────────────────────────────────────
        # PRE-TRADE RISK VALIDATION (SKILL 3: Stop-Loss/Take-Profit)
        # ────────────────────────────────────────────────────────────────────
        
        logger.info("[SKILLS] Running pre-trade validation with stop-loss/take-profit checks...")
        
        portfolio_data = state.get("portfolio_data", {})
        if portfolio_data:
            # Estimate price for this trade (use live data if available)
            trade_price = None
            research_data = state.get("research_data", {})
            if research_data and research_data.get("ticker") == ticker:
                trade_price = research_data.get("price", None)
            
            # If we have a dollar amount but need quantity, calculate it
            if amount_dollars and not quantity and trade_price:
                quantity = amount_dollars / trade_price
            
            if quantity and trade_price:
                proposed_trade = {
                    "action": trade_action,
                    "ticker": ticker,
                    "shares": quantity,
                    "price": trade_price
                }
                
                pre_trade_check = invoke_skill(
                    "stop_loss_take_profit",
                    portfolio=portfolio_data,
                    proposed_trade=proposed_trade
                )
                
                if not pre_trade_check.get("approved", True):
                    warnings = pre_trade_check.get("warnings", [])
                    logger.warning(f"[SKILLS] Pre-trade validation: {len(warnings)} warning(s)")
                    for warning in warnings:
                        logger.warning(f"  ⚠️ {warning.get('warning', '')}")
                    
                    state["pre_trade_validation"] = {
                        "approved": False,
                        "warnings": warnings
                    }
                else:
                    logger.info("[SKILLS] Pre-trade validation: PASSED - No concentration breaches")
                    state["pre_trade_validation"] = {"approved": True}
            else:
                logger.debug("[SKILLS] Skipping pre-trade validation (insufficient price data)")
                state["pre_trade_validation"] = {"approved": True}
        else:
            logger.debug("[SKILLS] No portfolio data available for pre-trade validation")
            state["pre_trade_validation"] = {"approved": True}
        
        # ────────────────────────────────────────────────────────────────────
        # CREATE HUMAN APPROVAL REQUEST
        # ────────────────────────────────────────────────────────────────────
        
        request_id = f"{ticker}-{trade_action}-{datetime.now().timestamp()}"
        
        # Build reasoning with extracted amount
        if amount_dollars:
            reasoning_detail = f"Dollar-based trade: {trade_action} ${amount_dollars:.2f} of {ticker}"
            formatted_amount = f"${amount_dollars:.2f}"
        else:
            reasoning_detail = f"Share-based trade: {trade_action} {quantity} shares of {ticker}"
            formatted_amount = f"{quantity} shares"
        
        approval_request = ApprovalRequest(
            request_id=request_id,
            action=trade_action,
            ticker=ticker,
            reasoning=f"Direct trade request: {reasoning_detail}\n"
                     f"User query: {user_input}",
            confidence=0.95,
            timestamp=datetime.now().isoformat(),
        )
        
        state["pending_approval"] = {
            **approval_request.model_dump(),
            "amount": formatted_amount,
            "amount_dollars": amount_dollars,
            "quantity": quantity,
            "order_type": "market",
        }
        state["approval_status"] = "pending"
        state["routing_decision"] = "awaiting_approval"
        
        logger.warning("[CHECKPOINT] Trade requires human approval!")
        logger.warning(f"[CHECKPOINT] Approval request {request_id} created")
        logger.warning(f"[CHECKPOINT] Action: {trade_action} {ticker}")
        if amount_dollars:
            logger.warning(f"[CHECKPOINT] Amount: ${amount_dollars:.2f}")
        else:
            logger.warning(f"[CHECKPOINT] Quantity: {quantity} shares")
        logger.warning("[CHECKPOINT] Workflow PAUSED - awaiting human decision")
        
        # Generate pending report
        state["report_markdown"] = f"""# Pending Trade Approval

## Trade Details
- **Action:** {trade_action}
- **Ticker:** {ticker}
- **Amount:** ${amount_dollars:.2f}
- **Type:** Market Order
- **Status:** ⏳ AWAITING HUMAN APPROVAL

## User Request
{user_input}

## ⚠️ Important
This trade requires your explicit approval before execution. Please review the details above.
To approve or reject, use the approval endpoint or web interface.

---
*Trade request created at: {datetime.now().isoformat()}*
*Request ID: {request_id}*
"""
        
        logger.info("[TRADING NODE COMPLETE - AWAITING APPROVAL]")
        
    except Exception as e:
        logger.error(f"Trade parsing failed: {str(e)}")
        state["error_count"] += 1
        state["routing_decision"] = "completed"
        state["report_markdown"] = f"Trade execution failed: {str(e)}"
        logger.info("[TRADING NODE COMPLETE - ERROR]")
    
    logger.info("=" * 80)
    return state


# ============================================================================
# APPROVAL EXECUTION NODE - EXECUTES APPROVED TRADES
# ============================================================================

def approval_execution_node(state: GraphState) -> GraphState:
    """
    Approval Execution Node - Executes trades after human approval.
    
    This node:
    1. Checks if approval_status is "approved"
    2. Calls Robinhood MCP to execute the trade
    3. Generates execution report with order details
    4. Handles execution errors gracefully
    
    This node runs ONLY if:
    - pending_approval exists
    - approval_status == "approved"
    
    Args:
        state: Current graph state with pending_approval
        
    Returns:
        State with execution results
    """
    logger.info("=" * 80)
    logger.info("[ENTERING APPROVAL EXECUTION NODE]")
    logger.info("-" * 80)
    
    if state.get("approval_status") != "approved":
        # IMPORTANT: Do NOT overwrite the report here unless a trade was
        # *explicitly rejected*. During the initial graph run, a research query
        # legitimately ends with approval_status == None (HOLD verdict) or
        # "pending" (BUY/SELL awaiting a human). In those cases the committee
        # report is already in state["report_markdown"] and must be preserved.
        if state.get("approval_status") == "rejected":
            logger.warning("[SKIPPING] Trade was rejected by the human approver.")
            state["report_markdown"] = """# Trade Cancelled

## Status
The pending trade was **REJECTED** by the human approver.

No trade was executed.
"""
            logger.info("[APPROVAL EXECUTION NODE COMPLETE - REJECTED]")
        else:
            logger.info(
                "[APPROVAL EXECUTION NODE] No approved trade to execute "
                f"(approval_status={state.get('approval_status')!r}); "
                "preserving existing report."
            )
            logger.info("[APPROVAL EXECUTION NODE COMPLETE - NO EXECUTION]")
        logger.info("=" * 80)
        return state
    
    try:
        pending = state.get("pending_approval", {})
        ticker = pending.get("ticker", "")
        action = pending.get("action", "")
        amount_dollars = pending.get("amount_dollars")
        quantity = pending.get("quantity")
        
        if amount_dollars:
            logger.info(f"Executing approved trade: {action} ${amount_dollars:.2f} of {ticker}")
            trade_detail = f"${amount_dollars:.2f}"
        else:
            logger.info(f"Executing approved trade: {action} {quantity} shares of {ticker}")
            trade_detail = f"{quantity} shares"
        
        # ────────────────────────────────────────────────────────────────────
        # BROKER EXECUTION — routes through BrokerFactory (simulation or live)
        # ────────────────────────────────────────────────────────────────────

        from app.trading import get_broker_for_user, get_account_id_for_user
        from app.trading.broker_interface import OrderSide as BS

        broker = get_broker_for_user()
        account_id = get_account_id_for_user()

        logger.info(f"[BROKER] Submitting {action} order via {type(broker).__name__}...")
        logger.info(f"[BROKER] Ticker: {ticker}  |  Amount: {trade_detail}")

        side = BS.BUY if action.upper() == "BUY" else BS.SELL
        order = asyncio.run(
            broker.place_order(
                account_id=account_id,
                ticker=ticker,
                side=side,
                amount_dollars=amount_dollars,
                quantity=quantity,
            )
        )

        order_id = order.order_id
        status_emoji = "✅" if order.status in ("filled", "submitted") else "❌"
        filled_price_str = (
            f"\\${order.filled_price:.2f}" if order.filled_price else "market"
        )
        filled_qty = f"{order.quantity:.4f}" if order.quantity else "?"
        total_val = f"\\${order.total_value:,.2f}" if order.total_value else trade_detail
        simulated_note = (
            "Paper-trading simulation (real Yahoo Finance MCP price, simulated balance)"
            if getattr(order, "simulated", True)
            else "Live order via Robinhood MCP"
        )

        execution_report = f"""# Trade Execution Report

## {status_emoji} Trade {"Executed" if order.status in ("filled", "submitted") else "Failed"}

### Order Details
| Field | Value |
|---|---|
| **Order ID** | `{order_id}` |
| **Ticker** | {ticker} |
| **Action** | {action} |
| **Shares** | {filled_qty} |
| **Execution Price** | {filled_price_str} |
| **Total Value** | {total_val} |
| **Status** | `{order.status.upper()}` |
| **Executed At** | {order.filled_at or datetime.now().isoformat()} |

### Execution Mode
{simulated_note}

{"### ⚠️ Rejection Reason\n" + order.error_message if order.error_message else ""}

---
*Trade submitted at: {datetime.now().isoformat()}*
*Request ID: {pending.get("request_id", "?")}*
"""

        state["report_markdown"] = execution_report
        state["routing_decision"] = "completed"

        logger.info(f"[BROKER] Order {order_id} → {order.status.upper()}")
        logger.info("[APPROVAL EXECUTION NODE COMPLETE - SUCCESS]")
        
    except Exception as e:
        logger.error(f"Trade execution failed: {str(e)}")
        state["report_markdown"] = f"""# Trade Execution Failed

## ❌ Error

Your trade could not be executed:

**Reason:** {str(e)}

Please contact support or retry with valid parameters.
"""
        state["error_count"] += 1
        logger.info("[APPROVAL EXECUTION NODE COMPLETE - ERROR]")
    
    logger.info("=" * 80)
    return state
# ============================================================================
# REPORTING NODE - FINAL STEP
# ============================================================================

def reporting_node(state: GraphState) -> GraphState:
    """
    Reporting Node - Consolidates findings into a final report.
    
    This node:
    1. Takes processed state (from either research or general_q)
    2. Consolidates findings into final report
    3. If report_markdown already exists (from research/general_q nodes), uses it
    4. Otherwise builds from raw data
    5. Returns completed state
    
    Args:
        state: Current graph state
        
    Returns:
        State with complete report_markdown
    """
    logger.info("=" * 80)
    logger.info("[ENTERING REPORTING NODE]")
    logger.info("-" * 80)
    
    # If report_markdown is already populated from research or general_q node, use it
    if state.get("report_markdown"):
        logger.info("Using pre-generated report from analysis node")
        logger.info("Report generation complete")
        logger.info("[REPORTING NODE COMPLETE]")
        logger.info("=" * 80)
        return state
    
    # Otherwise, build from raw data (for fallback paths)
    if state.get("research_data") and state["current_target_ticker"]:
        ticker = state["current_target_ticker"]
        data = state["research_data"]
        
        if "error" in data:
            report = f"### Error Processing {ticker}\n\n{data['error']}"
        else:
            # Build report from Yahoo Finance MCP data
            report = f"""# Financial Research Report: {ticker}

## Current Market Data
- **Price:** ${data.get('price', 'N/A')}
- **Daily Change:** {data.get('change_percent', 'N/A')}%
- **Volume:** {data.get('volume', 'N/A'):,}
- **Data Source:** {data.get('data_source', 'N/A')}
- **Last Updated:** {data.get('timestamp', 'N/A')}

## Summary
This report was generated using real market data from Yahoo Finance MCP combined with Qwen AI analysis.
"""
        
        state["report_markdown"] = report
    
    logger.info("Report generation complete")
    logger.info("[REPORTING NODE COMPLETE]")
    logger.info("=" * 80)
    
    return state


# ============================================================================
# CONDITIONAL ROUTING LOGIC
# ============================================================================

def portfolio_node(state: GraphState) -> GraphState:
    """
    Portfolio Analysis Node — analyses the user's current simulation holdings.

    Steps:
    1. Fetch account info from the broker (MockSimulationEngine)
    2. Pull live prices for every holding in parallel (Yahoo Finance MCP)
    3. Compute market value, allocation %, unrealised P&L, risk score
    4. Call Qwen for a concise risk assessment + recommendations
    5. Build a structured markdown report
    """
    import concurrent.futures as _cf

    logger.info("=" * 80)
    logger.info("[ENTERING PORTFOLIO NODE]")
    logger.info("-" * 80)

    try:
        from app.trading import get_broker_for_user, get_account_id_for_user

        broker = get_broker_for_user()
        account_id = get_account_id_for_user()

        account_info = asyncio.run(broker.get_account_info(account_id))
        # AccountInfo doesn't carry positions — they live in the raw ledger.
        if hasattr(broker, "ledger"):
            raw = broker.ledger.get("accounts", {}).get(account_id, {})
            raw_positions: dict = raw.get("positions", {})  # {ticker: {quantity, avg_cost}}
        else:
            raw_positions = {}
        # Normalise — support both new dict format and legacy float entries.
        positions: dict = {}
        for t, v in raw_positions.items():
            if isinstance(v, dict):
                positions[t] = {"quantity": float(v.get("quantity", 0)), "average_cost": float(v.get("avg_cost", 0))}
            else:
                positions[t] = {"quantity": float(v), "average_cost": 0.0}
        cash: float = float(getattr(account_info, "cash_balance", 0.0))

        logger.info(f"Account loaded: {len(positions)} position(s), ${cash:,.2f} cash")

        if not positions:
            state["report_markdown"] = (
                "# 📊 Portfolio Analysis\n\n"
                "## Empty Portfolio\n\n"
                "No open positions found. Start trading to build your portfolio!\n\n"
                f"**Available Cash:** ${cash:,.2f}"
            )
            state["routing_decision"] = "completed"
            logger.info("[PORTFOLIO NODE COMPLETE — empty portfolio]")
            logger.info("=" * 80)
            return state

        # ── Live price fetch via Yahoo Finance MCP (no rate limits) ──────────
        tickers = list(positions.keys())
        logger.info(f"Fetching live prices for: {', '.join(tickers)}")
        live = {}
        for t in tickers:
            result = financial_tools.get_stock_data(t)
            live[t] = result if isinstance(result, dict) else {}

        # ── Compute per-position metrics ──────────────────────────────────
        holdings = []
        invested_value = 0.0
        for ticker, pos in positions.items():
            shares = float(pos.get("quantity", 0))
            avg_cost = float(pos.get("average_cost", 0))
            pdata = live.get(ticker) or {}
            is_mock = "Mock" in pdata.get("data_source", "")
            raw_price = pdata.get("price")
            live_price = avg_cost if (is_mock or not raw_price) else float(raw_price)
            market_val = shares * live_price
            cost_basis = shares * avg_cost
            unrealised_pnl = market_val - cost_basis
            unrealised_pct = (unrealised_pnl / cost_basis * 100) if cost_basis else 0.0
            invested_value += market_val
            holdings.append({
                "ticker": ticker,
                "shares": shares,
                "avg_cost": avg_cost,
                "live_price": live_price,
                "market_value": market_val,
                "cost_basis": cost_basis,
                "unrealised_pnl": unrealised_pnl,
                "unrealised_pct": unrealised_pct,
            })

        holdings.sort(key=lambda h: h["market_value"], reverse=True)
        total_value = invested_value + cash
        for h in holdings:
            h["allocation_pct"] = (h["market_value"] / total_value * 100) if total_value else 0.0
        cash_pct = (cash / total_value * 100) if total_value else 0.0
        total_pnl = sum(h["unrealised_pnl"] for h in holdings)
        total_pnl_pct = (total_pnl / (total_value - total_pnl) * 100) if (total_value - total_pnl) else 0.0

        # ── Risk score ────────────────────────────────────────────────────
        max_alloc = max(h["allocation_pct"] for h in holdings)
        n = len(holdings)

        # Herfindahl-Hirschman Index: sum of squared allocation weights (0→1).
        # HHI = 1.0 means a single-stock portfolio (maximum concentration).
        # HHI ≤ 0.18 is conventionally "diversified" across 5+ equal positions.
        hhi = sum((h["allocation_pct"] / 100) ** 2 for h in holdings)
        # Normalised HHI removes the cash drag so it reflects equity concentration.

        if max_alloc > 50 or n < 2 or hhi > 0.5:
            risk_level = "HIGH 🔴"
        elif max_alloc > 30 or n < 4 or hhi > 0.25:
            risk_level = "MEDIUM 🟡"
        else:
            risk_level = "LOW 🟢"

        logger.info(
            f"Portfolio: total=${total_value:,.2f}, "
            f"invested=${invested_value:,.2f}, cash=${cash:,.2f}, "
            f"P&L={total_pnl:+,.2f} ({total_pnl_pct:+.1f}%), risk={risk_level}"
        )

        # ── Qwen analysis ─────────────────────────────────────────────────
        logger.info("Requesting Qwen portfolio risk analysis...")
        holding_lines = "\n".join(
            f"  - {h['ticker']}: {h['shares']:.4f} shares @ ${h['live_price']:.2f} "
            f"= ${h['market_value']:.2f} ({h['allocation_pct']:.1f}%), "
            f"P&L {h['unrealised_pnl']:+.2f} ({h['unrealised_pct']:+.1f}%)"
            for h in holdings
        )
        llm_prompt = (
            f"Analyse this paper trading portfolio and provide:\n"
            f"1. Concentration / diversification risk assessment\n"
            f"2. Sector exposure commentary\n"
            f"3. Two to three specific, actionable recommendations\n"
            f"4. Overall portfolio health score (1-10) with one-line rationale\n\n"
            f"Portfolio summary:\n"
            f"  Total value: ${total_value:,.2f}\n"
            f"  Cash: ${cash:,.2f} ({cash_pct:.1f}%)\n"
            f"  Invested: ${invested_value:,.2f}\n"
            f"  Unrealised P&L: {total_pnl:+,.2f} ({total_pnl_pct:+.1f}%)\n"
            f"  Herfindahl-Hirschman Index (concentration): {hhi:.3f} "
            f"({'highly concentrated' if hhi > 0.5 else 'moderately concentrated' if hhi > 0.25 else 'diversified'})\n"
            f"  Risk level: {risk_level}\n"
            f"Positions:\n{holding_lines}\n\n"
            f"Be concise and direct. Use markdown bullet points."
        )
        analysis = call_qwen_for_general_qa(llm_prompt)

        # ── Build report ──────────────────────────────────────────────────
        pnl_sign = "+" if total_pnl >= 0 else ""
        lines = [
            "# 📊 Portfolio Analysis",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Total Value | ${total_value:,.2f} |",
            f"| Invested | ${invested_value:,.2f} |",
            f"| Cash | ${cash:,.2f} ({cash_pct:.1f}%) |",
            f"| Unrealised P&L | {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{total_pnl_pct:.1f}%) |",
            f"| Concentration (HHI) | {hhi:.3f} — {'high' if hhi > 0.5 else 'moderate' if hhi > 0.25 else 'diversified'} |",
            f"| Risk Level | {risk_level} |",
            f"| Positions | {n} |",
            "",
            "## Holdings",
            "",
            "| Ticker | Shares | Avg Cost | Live Price | Market Value | Allocation | P&L |",
            "|---|---|---|---|---|---|---|",
        ]
        for h in holdings:
            pnl_str = f"{h['unrealised_pnl']:+.2f} ({h['unrealised_pct']:+.1f}%)"
            lines.append(
                f"| **{h['ticker']}** | {h['shares']:.4f} | "
                f"\\${h['avg_cost']:.2f} | \\${h['live_price']:.2f} | "
                f"\\${h['market_value']:.2f} | {h['allocation_pct']:.1f}% | {pnl_str} |"
            )
        lines += [
            f"| 💵 Cash | — | — | — | \\${cash:,.2f} | {cash_pct:.1f}% | — |",
            "",
            "## 🤖 AI Risk Analysis",
            "",
            analysis,
        ]
        state["report_markdown"] = "\n".join(lines)
        state["routing_decision"] = "completed"

        logger.info("[PORTFOLIO NODE COMPLETE]")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"Portfolio analysis failed: {e}")
        state["error_count"] = state.get("error_count", 0) + 1
        state["report_markdown"] = f"# Portfolio Analysis\n\n**Error:** {e}"
        state["routing_decision"] = "completed"

    return state


def route_after_triage(state: GraphState) -> str:
    """
    Conditional router that determines the next node based on routing_decision.
    
    This function is called after the Triage Node and reads state["routing_decision"]
    to determine which downstream node to invoke:
    - "research" -> research_node (detailed analysis)
    - "direct_trade" -> trading_node (immediate trade with approval)
    - "general_q" -> general_q_node (Q&A)
    
    Args:
        state: Current graph state
        
    Returns:
        Name of the next node to execute
    """
    logger.info(f"[CONDITIONAL ROUTING] Decision: {state['routing_decision']}")
    
    decision = state.get("routing_decision", "general_q")
    
    if decision == "research":
        logger.info("[CONDITIONAL EDGE] Taking RESEARCH path")
        return "research"
    elif decision == "direct_trade":
        logger.info("[CONDITIONAL EDGE] Taking DIRECT_TRADE path")
        return "trading"
    elif decision == "portfolio":
        logger.info("[CONDITIONAL EDGE] Taking PORTFOLIO path")
        return "portfolio"
    else:
        logger.info("[CONDITIONAL EDGE] Taking GENERAL_Q path")
        return "general_q"


# ============================================================================
# GRAPH CONSTRUCTION
# ============================================================================

def build_graph() -> StateGraph:
    """
    Constructs the LangGraph state graph with all nodes and conditional edges.
    
    Graph structure:
    ```
    triage_node (START)
        |
        +-- route_after_triage (conditional)
        |
        +-> research_node --------+
        |                          |
        +-> trading_node ----------+-> approval_execution_node (conditional)
        |                          |
        +-> general_q_node --------+
        |                          |
        +-- reporting_node (END)
    ```
    
    The approval_execution_node only runs if approval_status == "approved"
    
    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("\n" + "=" * 80)
    logger.info("BUILDING STATE GRAPH")
    logger.info("=" * 80)
    
    # Create graph with GraphState
    graph = StateGraph(GraphState)
    
    # Add nodes
    graph.add_node("triage", triage_node)
    graph.add_node("research", research_node)
    graph.add_node("trading", trading_node)
    graph.add_node("general_q", general_q_node)
    graph.add_node("portfolio", portfolio_node)
    graph.add_node("approval_execution", approval_execution_node)
    graph.add_node("reporting", reporting_node)
    
    logger.info("✓ Nodes added: triage, research, trading, general_q, portfolio, approval_execution, reporting")
    
    # Define entry point
    graph.set_entry_point("triage")
    logger.info("✓ Entry point: triage")
    
    # Add conditional edge from triage
    graph.add_conditional_edges(
        source="triage",
        path=route_after_triage,
        path_map={
            "research": "research",
            "trading": "trading",
            "general_q": "general_q",
            "portfolio": "portfolio",
        },
    )
    logger.info("✓ Conditional edge added: triage -> (research | trading | general_q | portfolio)")
    
    graph.add_edge("research", "approval_execution")
    graph.add_edge("trading", "approval_execution")
    graph.add_edge("general_q", "approval_execution")
    graph.add_edge("portfolio", "approval_execution")
    logger.info("✓ Edges added: research -> approval_execution, trading -> approval_execution, general_q -> approval_execution, portfolio -> approval_execution")
    
    # Connect approval_execution to reporting
    graph.add_edge("approval_execution", "reporting")
    logger.info("✓ Edge added: approval_execution -> reporting")
    
    # End state
    graph.add_edge("reporting", END)
    logger.info("✓ Edge added: reporting -> END")
    
    logger.info("=" * 80)
    logger.info("GRAPH COMPILATION COMPLETE")
    logger.info("=" * 80 + "\n")
    
    # Compile the graph
    compiled_graph = graph.compile()
    
    return compiled_graph


# ============================================================================
# EXECUTION HELPERS
# ============================================================================

def execute_graph(graph: StateGraph, user_input: str, conversation_history: list = None) -> GraphState:
    """
    Executes the compiled graph with the given user input.
    
    Args:
        graph: Compiled StateGraph
        user_input: User query string
        
    Returns:
        Final state after full execution
    """
    logger.info(f"\n{'#' * 80}")
    logger.info(f"# GRAPH EXECUTION - User Query")
    logger.info(f"{'#' * 80}")
    logger.info(f"Input: {user_input}\n")
    
    # Initialize state
    initial_state: GraphState = {
        "user_input": user_input,
        "conversation_history": conversation_history or [],
        "current_target_ticker": "",
        "routing_decision": "",
        "research_data": {},
        "report_markdown": "",
        "error_count": 0,
    }
    
    # Execute
    final_state = graph.invoke(initial_state)
    
    logger.info(f"\n{'#' * 80}")
    logger.info(f"# GRAPH EXECUTION - COMPLETE")
    logger.info(f"{'#' * 80}\n")
    
    return final_state


def print_final_report(state: GraphState) -> None:
    """Pretty-prints the final state and report."""
    logger.info("\n" + "=" * 80)
    logger.info("FINAL STATE SUMMARY")
    logger.info("=" * 80)
    logger.info(f"User Input: {state['user_input']}")
    logger.info(f"Target Ticker: {state['current_target_ticker']}")
    logger.info(f"Routing Decision: {state['routing_decision']}")
    logger.info(f"Errors Encountered: {state['error_count']}")
    logger.info(f"Research Data Keys: {list(state.get('research_data', {}).keys())}")
    logger.info("=" * 80)
    
    if state.get("report_markdown"):
        logger.info("\n" + "=" * 80)
        logger.info("FINAL REPORT")
        logger.info("=" * 80)
        print("\n" + state["report_markdown"] + "\n")
        logger.info("=" * 80 + "\n")


# ============================================================================
# MAIN EXECUTION - TEST CASES
# ============================================================================

if __name__ == "__main__":
    logger.info("\n\n")
    logger.info("╔" + "═" * 78 + "╗")
    logger.info("║" + " " * 78 + "║")
    logger.info("║" + "  CORPORATE INTELLIGENCE ENGINE — QWEN EDITION  ".center(78) + "║")
    logger.info("║" + "  Tri-Agent Committee · LangGraph · Robinhood MCP  ".center(78) + "║")
    logger.info("║" + " " * 78 + "║")
    logger.info("╚" + "═" * 78 + "╝")
    logger.info("\n")
    
    # Build the graph once
    graph = build_graph()
    
    # Test cases demonstrating different routing paths
    test_queries = [
        # Test 1: Research query with specific ticker
        "Analyze the latest earnings for NVDA",
        
        # Test 2: Research query without explicit ticker
        "Show me the forecast for Tesla stock revenue",
        
        # Test 3: General question (non-research)
        "What are the top machine learning frameworks in 2024?",
    ]
    
    logger.info("\n" + "▼" * 80)
    logger.info("EXECUTING TEST CASES")
    logger.info("▼" * 80 + "\n")
    
    for idx, query in enumerate(test_queries, 1):
        logger.info(f"\n{'─' * 80}")
        logger.info(f"TEST CASE {idx}/{len(test_queries)}")
        logger.info(f"{'─' * 80}\n")
        
        final_state = execute_graph(graph, query)
        print_final_report(final_state)
        
        logger.info("\n")
    
    logger.info("\n" + "▲" * 80)
    logger.info("ALL TEST CASES COMPLETE")
    logger.info("▲" * 80 + "\n")
