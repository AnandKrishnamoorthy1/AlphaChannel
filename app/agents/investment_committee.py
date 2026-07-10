"""
Tri-Agent Adversarial Investment Committee
==========================================

A multi-agent orchestration layer that replaces single-shot stock analysis with a
structured, adversarial debate between three distinct Qwen-powered personas:

    1. Bull Analyst   (Quantitative Growth)  - builds the aggressive long thesis
    2. Bear Auditor   (Forensic Risk)        - builds the brutal short thesis
    3. Portfolio Director (Compliance/Chair) - moderates, forces evidence, and issues
                                               a final vetted verdict + confidence score

Design goals (why this matters for the hackathon):
    - Distinct AI personas partition the task (long vs short vs adjudication)
    - Multi-turn debate where agents rebut each other (logic-conflict resolution)
    - The Director grounds every claim in the SAME real market-data payload, so no
      persona can invent facts (shared evidence contract)
    - A single structured verdict object is produced for the downstream trade workflow

The debate is deterministic in *structure* (fixed rounds) but non-deterministic in
*content*, which is exactly the property judges look for in agentic systems.
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.llm import call_qwen_persona, call_qwen_with_structured_output


# ============================================================================
# TEXT FORMATTING UTILITIES
# ============================================================================

def _format_debate_bullets(text: str) -> str:
    """Ensure debate bullet points (•) are on separate lines.
    
    Split on bullet character and rebuild with double newlines (markdown breaks).
    "• Point 1 • Point 2" → "• Point 1\n\n• Point 2"
    """
    # Split the text on bullet characters
    parts = text.split('•')
    
    # First part (before any bullet) doesn't need a bullet prefix
    result = [parts[0].strip()]
    
    # Each subsequent part gets a bullet prefix on a new line
    for part in parts[1:]:
        stripped = part.strip()
        if stripped:
            result.append('• ' + stripped)
    
    # Join with double newlines (paragraph breaks in markdown)
    return '\n\n'.join(result)



# ============================================================================
# DATA MODELS
# ============================================================================

class DebateTurn(BaseModel):
    """A single argument produced by one persona during the committee debate."""

    round_number: int = Field(description="Debate round (1-indexed)")
    persona: str = Field(description="Persona name, e.g. 'Bull Analyst'")
    role: Literal["bull", "bear", "director"] = Field(description="Structural role")
    argument: str = Field(description="The persona's markdown argument for this turn")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class CommitteeVerdict(BaseModel):
    """The Portfolio Director's final, vetted decision after the debate."""

    ticker: str
    verdict: Literal["BUY", "HOLD", "SELL"] = Field(
        description="Final committee recommendation"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Director's confidence in the verdict"
    )
    thesis: str = Field(description="One-paragraph vetted investment thesis")
    bull_points: List[str] = Field(default_factory=list, description="Strongest long arguments")
    bear_points: List[str] = Field(default_factory=list, description="Strongest short arguments")
    key_risks: List[str] = Field(default_factory=list, description="Material risks to monitor")
    dissent: str = Field(
        default="",
        description="Any unresolved disagreement the Director chose to flag",
    )


# ============================================================================
# PERSONA SYSTEM PROMPTS
# ============================================================================

_BULL_SYSTEM = """You are the BULL ANALYST on an institutional investment committee — a
Quantitative Growth specialist. Your mandate is to build the strongest possible
evidence-based LONG (bullish) case for the stock under review.

MARKDOWN FORMATTING - CRITICAL:
- Each bullet point MUST be on its own line. Never concatenate bullets: "• Point1 • Point2" is WRONG.
- Correct format: "• Point1\n• Point2" with each bullet on a separate line.
- Use ** for bold labels within bullets: "• **Label:** description text"
- Keep sections clearly separated with blank lines between major arguments.

Rules:
- Argue ONLY from the market-data payload you are given. Cite exact numbers.
- Be aggressive but intellectually honest — no invented fundamentals.
- When rebutting the Bear Auditor, attack their reasoning, not with hand-waving but
  with the data.
- ⚠️ IMPORTANT: If the portfolio context shows the position is already OVERWEIGHT
  (exceeds concentration threshold), acknowledge this constraint. Bullish arguments
  about fundamentals are valid, but entry sizing must respect portfolio risk limits.
  Recommend TRIM or HOLD if overweight, not additional BUY.
- Keep each turn under 180 words. Use tight markdown bullets.
Never output a final recommendation — that is the Director's job."""

_BEAR_SYSTEM = """You are the BEAR AUDITOR on an institutional investment committee — a
Forensic Risk specialist. Your mandate is to build the strongest possible
evidence-based SHORT (bearish) / caution case for the stock under review.

MARKDOWN FORMATTING - CRITICAL:
- Each bullet point MUST be on its own line. Never concatenate bullets: "• Point1 • Point2" is WRONG.
- Correct format: "• Point1\n• Point2" with each bullet on a separate line.
- Use ** for bold labels within bullets: "• **Label:** description text"
- Keep sections clearly separated with blank lines between major arguments.

Rules:
- Argue ONLY from the market-data payload you are given. Cite exact numbers.
- Hunt for downside: momentum exhaustion, valuation stretch, volume anomalies, volatility.
- When rebutting the Bull Analyst, dismantle their strongest claim explicitly.
- ⚠️ IMPORTANT: If portfolio context shows CONCENTRATION BREACH (position exceeds 
  threshold), use this as a primary argument for HOLD/SELL. Acknowledge good fundamentals
  but emphasize that adding to an already-overweight position violates risk discipline.
- Keep each turn under 180 words. Use tight markdown bullets.
Never output a final recommendation — that is the Director's job."""

_DIRECTOR_SYSTEM = """You are the PORTFOLIO DIRECTOR chairing an institutional investment
committee, acting as the compliance officer of record. Two analysts (Bull and Bear)
have debated a stock using a shared market-data payload.

MARKDOWN FORMATTING - CRITICAL:
- Use clean, well-formatted markdown with proper line breaks.
- Each bullet point MUST be on its own line if you use bullets.
- Use ** for bold labels and emphasis.
- Keep the verdict summary clearly formatted and easy to read.

Your mandate:
- Weigh both cases strictly on evidence quality, not rhetoric.
- Reward arguments grounded in the data; discount unsupported claims.
- ⚠️ CRITICAL: If portfolio context shows CONCENTRATION BREACH (position already 
  exceeds concentration threshold), this OVERRIDES bullish fundamental arguments.
  Recommend HOLD or SELL to respect risk limits, not BUY to add to overweight position.
- Produce a single vetted verdict (BUY / HOLD / SELL) with a calibrated confidence.
- Confidence must reflect genuine uncertainty: a close debate => lower confidence.
- Surface material risks even when recommending BUY.
You must return ONLY the requested JSON. No prose outside the JSON."""


# ============================================================================
# EVIDENCE FORMATTING
# ============================================================================

def _fmt_sec(val: float) -> str:
    """Format a raw dollar value from SEC as B/M string for evidence display."""
    from app.tools.sec_tools import fmt_sec_value
    return fmt_sec_value(val)


def _format_evidence(ticker: str, market_data: Dict[str, Any]) -> str:
    """Render the shared, immutable market-data payload every persona must cite."""

    def _v(key: str, prefix: str = "", suffix: str = "") -> str:
        val = market_data.get(key)
        return f"{prefix}{val}{suffix}" if val else "N/A"

    lines = [
        "SHARED MARKET-DATA PAYLOAD (the ONLY facts any persona may cite)",
        "",
        "=== PRICE / QUOTE ===",
        f"- Ticker:         {ticker}",
        f"- Current Price:  ${market_data.get('price', 'N/A')}",
        f"- Daily Change:   {market_data.get('change', 'N/A')} ({market_data.get('change_percent', 'N/A')}%)",
        f"- Volume:         {market_data.get('volume', 'N/A'):,}" if isinstance(market_data.get('volume'), int) else f"- Volume:         {market_data.get('volume', 'N/A')}",
        f"- As-of:          {market_data.get('timestamp', 'N/A')}",
    ]

    # Fundamentals block — only rendered when OVERVIEW data is present
    fund_lines = []
    _fund_map = [
        ("sector",                        "Sector"),
        ("industry",                      "Industry"),
        ("market_cap",                    "Market Cap",         "$"),
        ("pe_ratio",                      "P/E Ratio (TTM)"),
        ("forward_pe",                    "Forward P/E"),
        ("eps",                           "EPS (TTM)",          "$"),
        ("analyst_target_price",          "Analyst Target",     "$"),
        ("week_52_high",                  "52-Week High",       "$"),
        ("week_52_low",                   "52-Week Low",        "$"),
        ("profit_margin",                 "Profit Margin"),
        ("quarterly_earnings_growth_yoy", "Earnings Growth YoY"),
        ("quarterly_revenue_growth_yoy",  "Revenue Growth YoY"),
        ("revenue_per_share_ttm",         "Revenue/Share TTM",  "$"),
        ("return_on_equity",              "Return on Equity"),
        ("beta",                          "Beta"),
        ("dividend_yield",                "Dividend Yield"),
    ]
    for entry in _fund_map:
        key = entry[0]
        label = entry[1]
        prefix = entry[2] if len(entry) > 2 else ""
        val = market_data.get(key)
        if val:
            fund_lines.append(f"- {label + ':':<30} {prefix}{val}")

    if fund_lines:
        lines.append("")
        lines.append("=== FUNDAMENTALS ===")
        lines.extend(fund_lines)

    lines.append("")
    lines.append(f"- Source: {market_data.get('data_source', 'N/A')}")

    # SEC 10-Q filings block
    sec = market_data.get("sec_filings", {})
    if sec.get("available") and sec.get("quarters"):
        lines.append("")
        lines.append("=== SEC 10-Q FILINGS (AUDITED GROUND TRUTH — last 2 quarters) ===")
        quarters = sec["quarters"]
        _metrics = [
            ("revenue",           "Revenue"),
            ("net_income",        "Net Income"),
            ("gross_profit",      "Gross Profit"),
            ("operating_income",  "Operating Income"),
            ("rd_expense",        "R&D Expense"),
            ("eps_diluted",       None),  # handled specially
            ("cash",              "Cash & Equivalents"),
            ("long_term_debt",    "Long-term Debt"),
            ("free_cash_flow",    "Free Cash Flow"),
            ("total_assets",      "Total Assets"),
            ("shareholders_equity", "Shareholders' Equity"),
        ]
        for i, q in enumerate(quarters):
            lines.append(f"")
            lines.append(
                f"Quarter {i + 1}: Period ending {q.get('period', 'N/A')} "
                f"(Filed: {q.get('filed', 'N/A')})"
            )
            for key, label in _metrics:
                val = q.get(key)
                if val is None:
                    continue
                if key == "eps_diluted":
                    lines.append(f"  - {'EPS (Diluted)':<28} ${val:.2f}")
                else:
                    lines.append(f"  - {label:<28} {_fmt_sec(val)}")
        # QoQ comparison when both quarters available
        if len(quarters) >= 2:
            q_new, q_old = quarters[0], quarters[1]
            comparisons = []
            for key, label in [("revenue", "Revenue"), ("net_income", "Net Income")]:
                v_new = q_new.get(key)
                v_old = q_old.get(key)
                if v_new is not None and v_old and v_old != 0:
                    pct = (v_new - v_old) / abs(v_old) * 100
                    comparisons.append(f"  {label} QoQ: {pct:+.1f}%")
            if comparisons:
                lines.append("")
                lines.append("QoQ Trends (most-recent vs prior quarter):")
                lines.extend(comparisons)

    # ── PORTFOLIO CONTEXT ──────────────────────────────────────────────────────
    # Include concentration and risk alerts so personas can factor in portfolio risk
    lines.append("")
    lines.append("=== PORTFOLIO CONTEXT ===")
    
    portfolio_analysis = market_data.get("portfolio_analysis", {})
    if portfolio_analysis and portfolio_analysis.get("status") == "success":
        diversification = portfolio_analysis.get("diversification_metrics", {})
        if diversification:
            score = diversification.get("diversification_score", "N/A")
            lines.append(f"- Portfolio Diversification Score: {score}/100")
    
    risk_assessment = market_data.get("risk_assessment", {})
    if risk_assessment and risk_assessment.get("status") == "success":
        risk_score = risk_assessment.get("overall_risk_score", "N/A")
        risk_rating = risk_assessment.get("risk_rating", "N/A")
        lines.append(f"- Overall Portfolio Risk: {risk_score}/100 ({risk_rating})")
    
    # Active Triggers — CRITICAL FOR DECISION MAKING
    active_triggers = market_data.get("active_triggers", {})
    if active_triggers and active_triggers.get("active_triggers", 0) > 0:
        lines.append("")
        lines.append("⚠️ ACTIVE PORTFOLIO TRIGGERS (MUST FACTOR INTO VERDICT):")
        for trigger in active_triggers.get("triggers", []):
            trigger_type = trigger.get("type", "unknown")
            severity = trigger.get("severity", "INFO")
            if trigger_type == "concentration_breach":
                ticker = trigger.get("ticker", "UNKNOWN")
                current_pct = trigger.get("current_pct", 0)
                threshold_pct = trigger.get("threshold_pct", 50)
                excess_value = trigger.get("excess_value", 0)
                lines.append(
                    f"  🚨 CONCENTRATION BREACH: {ticker} is {current_pct:.1f}% "
                    f"(exceeds {threshold_pct:.0f}% threshold by ${excess_value:,.0f})"
                )
                lines.append(
                    f"     → Risk discipline requires reducing this position, not adding to it"
                )
            elif trigger_type == "correlation_spike":
                pair = trigger.get("ticker_pair", ("?", "?"))
                corr = trigger.get("correlation", 0)
                combined = trigger.get("combined_pct", 0)
                lines.append(
                    f"  ⚠️ CORRELATION SPIKE: {pair[0]} + {pair[1]} are "
                    f"{corr:.2f} correlated ({combined:.1f}% combined)"
                )
            elif trigger_type == "stop_loss":
                ticker = trigger.get("ticker", "UNKNOWN")
                loss_pct = abs(trigger.get("unrealized_return_pct", 0))
                lines.append(f"  ⛔ STOP-LOSS TRIGGERED: {ticker} down {loss_pct:.1f}%")
            elif trigger_type == "take_profit":
                ticker = trigger.get("ticker", "UNKNOWN")
                gain_pct = trigger.get("unrealized_return_pct", 0)
                lines.append(f"  💰 TAKE-PROFIT TRIGGERED: {ticker} up {gain_pct:.1f}%")
    else:
        lines.append("- No active portfolio triggers")

    return "\n".join(lines)


# ============================================================================
# COMMITTEE ENGINE
# ============================================================================

class InvestmentCommittee:
    """
    Orchestrates a multi-round adversarial debate and a moderated verdict.

    Usage:
        committee = InvestmentCommittee(rounds=2)
        transcript, verdict = committee.deliberate(ticker, market_data)
    """

    def __init__(self, rounds: int = 2):
        # Each round = one Bull turn + one Bear turn. Kept small for demo latency/cost.
        self.rounds = max(1, min(rounds, 4))

    # ---- individual persona turns -------------------------------------------------

    def _bull_turn(self, round_no: int, evidence: str, transcript: str) -> DebateTurn:
        prompt = (
            f"{evidence}\n\n"
            f"DEBATE SO FAR:\n{transcript or '(you open the debate)'}\n\n"
            f"Round {round_no}: Deliver the LONG case. If the Bear has spoken, "
            f"rebut their single strongest point using the data above."
        )
        argument = call_qwen_persona(_BULL_SYSTEM, prompt, temperature=0.5)
        logger.info(f"[COMMITTEE] Bull Analyst delivered round {round_no} argument")
        return DebateTurn(
            round_number=round_no, persona="Bull Analyst", role="bull", argument=argument
        )

    def _bear_turn(self, round_no: int, evidence: str, transcript: str) -> DebateTurn:
        prompt = (
            f"{evidence}\n\n"
            f"DEBATE SO FAR:\n{transcript}\n\n"
            f"Round {round_no}: Deliver the SHORT/caution case and dismantle the "
            f"Bull Analyst's strongest claim using the data above."
        )
        argument = call_qwen_persona(_BEAR_SYSTEM, prompt, temperature=0.5)
        logger.info(f"[COMMITTEE] Bear Auditor delivered round {round_no} argument")
        return DebateTurn(
            round_number=round_no, persona="Bear Auditor", role="bear", argument=argument
        )

    def _director_verdict(
        self, ticker: str, evidence: str, transcript: str, market_data: Dict[str, Any] = None
    ) -> CommitteeVerdict:
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "thesis": {"type": "string"},
                "bull_points": {"type": "array", "items": {"type": "string"}},
                "bear_points": {"type": "array", "items": {"type": "string"}},
                "key_risks": {"type": "array", "items": {"type": "string"}},
                "dissent": {"type": "string"},
            },
            "required": ["verdict", "confidence", "thesis", "key_risks"],
        }
        
        # Check for concentration breach — this overrides fundamental bullishness
        concentration_breach = False
        breach_description = ""
        if market_data:
            active_triggers = market_data.get("active_triggers", {})
            for trigger in active_triggers.get("triggers", []):
                if trigger.get("type") == "concentration_breach":
                    concentration_breach = True
                    current_pct = trigger.get("current_pct", 0)
                    threshold = trigger.get("threshold_pct", 50)
                    breach_description = (
                        f"Position already at {current_pct:.1f}% (exceeds {threshold:.0f}% limit)"
                    )
                    break
        
        prompt = (
            f"{evidence}\n\n"
            f"FULL DEBATE TRANSCRIPT:\n{transcript}\n\n"
            f"Adjudicate the debate for {ticker}. Weigh evidence quality. Return the "
            f"verdict JSON. Confidence must reflect how close the debate was."
        )
        
        # Add concentration override note to director's prompt if breach exists
        if concentration_breach:
            prompt += (
                f"\n\n⚠️ CRITICAL CONSTRAINT: {breach_description}. "
                f"Risk discipline requires HOLD or reduction, NOT BUY. "
                f"Adjust your verdict and confidence accordingly."
            )
        
        logger.info(f"[COMMITTEE] Director verdict request: evidence={len(evidence)} chars, transcript={len(transcript)} chars, total={len(prompt)} chars")
        
        if len(transcript) == 0:
            logger.warning("[COMMITTEE] ⚠️ DIRECTOR: Empty transcript! No debate rounds recorded.")
        
        raw = call_qwen_with_structured_output(
            system_prompt=_DIRECTOR_SYSTEM,
            user_message=prompt,
            response_schema=schema,
            temperature=0.2,  # Director is highly deterministic for consistent verdicts
        )
        
        verdict_str = raw.get("verdict", "HOLD")
        confidence = float(raw.get("confidence", 0.5))
        
        # Hard override: if concentration breach and verdict is BUY, force to HOLD
        if concentration_breach and verdict_str == "BUY":
            logger.warning(
                f"[COMMITTEE] ⚠️ DIRECTOR OVERRIDE: Concentration breach detected. "
                f"Overriding BUY → HOLD to respect risk discipline."
            )
            verdict_str = "HOLD"
            confidence = min(confidence, 0.65)  # Lower confidence for constrained decision
        
        logger.info(
            f"[COMMITTEE] Director verdict: {verdict_str} "
            f"@ {confidence} confidence"
        )
        return CommitteeVerdict(
            ticker=ticker,
            verdict=verdict_str,
            confidence=confidence,
            thesis=raw.get("thesis", ""),
            bull_points=raw.get("bull_points", []),
            bear_points=raw.get("bear_points", []),
            key_risks=raw.get("key_risks", []),
            dissent=raw.get("dissent", ""),
        )

    # ---- orchestration ------------------------------------------------------------

    def deliberate(
        self, ticker: str, market_data: Dict[str, Any]
    ) -> tuple[List[DebateTurn], CommitteeVerdict]:
        """
        Run the full multi-round debate and return (transcript, verdict).

        Parallelism: within each round, Bull and Bear both receive the same
        transcript of *prior* rounds and argue simultaneously via a thread pool.
        This preserves the adversarial rebuttal dynamic (each persona rebuts the
        other's previous-round arguments) while cutting wall-clock time from
        O(2*rounds + 1) sequential Qwen calls to O(rounds + 1) parallel steps.
        """
        logger.info("=" * 80)
        logger.info(f"[COMMITTEE] Convening investment committee for {ticker}")
        logger.info(f"[COMMITTEE] Debate rounds: {self.rounds}")
        # DEBUG: Validate ticker matches market data
        market_ticker = market_data.get('ticker', 'UNKNOWN')
        if market_ticker != 'UNKNOWN' and market_ticker != ticker:
            logger.warning(f"[COMMITTEE] ⚠️ TICKER MISMATCH: Received ticker={ticker} but market_data has ticker={market_ticker}")
        logger.info("-" * 80)

        evidence = _format_evidence(ticker, market_data)
        turns: List[DebateTurn] = []

        def render(ts: List[DebateTurn]) -> str:
            return "\n\n".join(
                f"[Round {t.round_number}] {t.persona}:\n{t.argument}" for t in ts
            )

        for r in range(1, self.rounds + 1):
            # Snapshot the transcript BEFORE this round so both personas see
            # the same prior context and can be dispatched simultaneously.
            prior_transcript = render(turns)

            with _futures.ThreadPoolExecutor(max_workers=2) as pool:
                bull_future = pool.submit(self._bull_turn, r, evidence, prior_transcript)
                bear_future = pool.submit(self._bear_turn, r, evidence, prior_transcript)
                bull = bull_future.result()
                bear = bear_future.result()

            turns.extend([bull, bear])

        verdict = self._director_verdict(ticker, evidence, render(turns), market_data)
        logger.info("[COMMITTEE] Deliberation complete")
        logger.info("=" * 80)
        return turns, verdict


# ============================================================================
# REPORT RENDERING + PUBLIC ENTRY POINT
# ============================================================================

def _esc(text: str) -> str:
    """Escape bare $ signs so Streamlit/KaTeX doesn't treat currency as math delimiters."""
    return str(text).replace("$", r"\$")


def render_committee_report(
    ticker: str,
    market_data: Dict[str, Any],
    turns: List[DebateTurn],
    verdict: CommitteeVerdict,
) -> str:
    """Render the full committee proceedings as a judge-friendly markdown report."""
    # DEBUG: Log what we're rendering
    logger.info(f"[RENDER] Generating report for ticker: {ticker}")
    logger.debug(f"[RENDER] Market data keys: {list(market_data.keys())}")
    logger.debug(f"[RENDER] Market data ticker: {market_data.get('ticker', 'NOT SET')}")
    
    # Safety: If market_data has a different ticker, log warning
    market_ticker = market_data.get('ticker', None)
    if market_ticker and market_ticker != ticker:
        logger.warning(f"[RENDER] ⚠️ TICKER MISMATCH in render_committee_report: param ticker={ticker} but market_data[ticker]={market_ticker}")
    
    verdict_emoji = {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴"}.get(verdict.verdict, "⚪")

    lines: List[str] = []
    logger.info(f"[RENDER] Writing header: Investment Committee Verdict: {ticker}")
    lines.append(f"# 🏛️ Investment Committee Verdict: {ticker}")
    lines.append("")
    lines.append(
        f"## {verdict_emoji} Final Verdict: **{verdict.verdict}** "
        f"({verdict.confidence:.0%} confidence)"
    )
    lines.append("")
    lines.append(f"> {_esc(verdict.thesis)}")
    lines.append("")

    lines.append("### 📊 Shared Evidence")
    vol = market_data.get('volume')
    vol_str = f"{vol:,}" if isinstance(vol, int) else str(vol or 'N/A')
    lines.append(
        f"- **Price:** \\${market_data.get('price', 'N/A')} "
        f"&nbsp;|&nbsp; **Change:** {market_data.get('change_percent', 'N/A')}% "
        f"&nbsp;|&nbsp; **Volume:** {vol_str} "
        f"`[1]`"
    )
    lines.append(f"- **As-of:** {market_data.get('timestamp', 'N/A')}")
    lines.append(f"- **Source:** {market_data.get('data_source', 'N/A')}")

    # Fundamentals — shown only when OVERVIEW data was fetched
    _report_fields = [
        ("sector",                        "Sector"),
        ("industry",                      "Industry"),
        ("market_cap",                    "Market Cap",          "\\$"),
        ("pe_ratio",                      "P/E Ratio (TTM)"),
        ("forward_pe",                    "Forward P/E"),
        ("eps",                           "EPS (TTM)",           "\\$"),
        ("analyst_target_price",          "Analyst Target",      "\\$"),
        ("week_52_high",                  "52-Week High",        "\\$"),
        ("week_52_low",                   "52-Week Low",         "\\$"),
        ("profit_margin",                 "Profit Margin"),
        ("quarterly_earnings_growth_yoy", "Earnings Growth YoY"),
        ("quarterly_revenue_growth_yoy",  "Revenue Growth YoY"),
        ("beta",                          "Beta"),
        ("return_on_equity",              "Return on Equity"),
        ("dividend_yield",                "Dividend Yield"),
    ]
    fund_items = []
    for entry in _report_fields:
        key, label = entry[0], entry[1]
        prefix = entry[2] if len(entry) > 2 else ""
        val = market_data.get(key)
        if val:
            fund_items.append(f"- **{label}:** {prefix}{val}")
    if fund_items:
        lines.append("")
        lines.append("#### Fundamental Data `[2]`")
        lines.extend(fund_items)

    # SEC 10-Q filings — shown only when edgartools successfully fetched data
    sec = market_data.get("sec_filings", {})
    if sec.get("available") and sec.get("quarters"):
        lines.append("")
        lines.append("#### 📑 SEC 10-Q Filings (Audited)")
        _sec_display = [
            ("revenue",             "Revenue"),
            ("net_income",          "Net Income"),
            ("gross_profit",        "Gross Profit"),
            ("operating_income",    "Operating Income"),
            ("rd_expense",          "R&D Expense"),
            ("free_cash_flow",      "Free Cash Flow"),
            ("cash",                "Cash & Equivalents"),
            ("long_term_debt",      "Long-term Debt"),
            ("eps_diluted",         "EPS (Diluted)"),
        ]
        for i, q in enumerate(sec["quarters"]):
            cite_num = 3 + i
            label = (
                f"**Q-{i + 1}** (period {q.get('period', 'N/A')}, "
                f"filed {q.get('filed', 'N/A')}) `[{cite_num}]`"
            )
            items = []
            for key, name in _sec_display:
                val = q.get(key)
                if val is None:
                    continue
                if key == "eps_diluted":
                    items.append(f"EPS \\${val:.2f}")
                else:
                    items.append(f"{name} {_esc(_fmt_sec(val))}")
            if items:
                lines.append(f"- {label}: " + " &nbsp;|&nbsp; ".join(items))
        # QoQ trend line
        if len(sec["quarters"]) >= 2:
            q_new, q_old = sec["quarters"][0], sec["quarters"][1]
            trend_parts = []
            for key, name in [("revenue", "Rev"), ("net_income", "NI")]:
                v_new = q_new.get(key)
                v_old = q_old.get(key)
                if v_new is not None and v_old and v_old != 0:
                    pct = (v_new - v_old) / abs(v_old) * 100
                    trend_parts.append(f"{name} QoQ {pct:+.1f}%")
            if trend_parts:
                lines.append(f"- *Trend: {' &nbsp;|&nbsp; '.join(trend_parts)}*")

    # Citation footnotes footer
    _cite_footer = ["---", "**Sources:**"]
    _cite_footer.append("**[1]** Alpha Vantage — Live Quote")
    av_fund_present = any(market_data.get(k) for k in ("pe_ratio", "eps", "sector"))
    if av_fund_present:
        _cite_footer.append(" &nbsp;·&nbsp; **[2]** Alpha Vantage — Company Overview (EDGAR XBRL)")
    if sec.get("available") and sec.get("quarters"):
        for i, q in enumerate(sec["quarters"]):
            _cite_footer.append(f" &nbsp;·&nbsp; **[{3 + i}]** SEC 10-Q (period {q.get('period', 'N/A')})")
    lines.append("")
    lines.append(" ".join(_cite_footer))
    lines.append("")

    if verdict.bull_points:
        lines.append("### 🟢 Strongest Bull Points")
        lines.extend(f"- {_esc(p)}" for p in verdict.bull_points)
        lines.append("")
    if verdict.bear_points:
        lines.append("### 🔴 Strongest Bear Points")
        lines.extend(f"- {_esc(p)}" for p in verdict.bear_points)
        lines.append("")
    
    # ── Skill Results: Portfolio Analysis ──────────────────────────────────
    portfolio_analysis = market_data.get("portfolio_analysis", {})
    if portfolio_analysis and portfolio_analysis.get("status") == "success":
        lines.append("### 📊 Portfolio Context (from Analyzer Skill)")
        diversification = portfolio_analysis.get("diversification_metrics", {})
        if diversification:
            score = diversification.get("diversification_score", "N/A")
            hhi = diversification.get("herfindahl_index", "N/A")
            lines.append(f"- **Diversification Score:** {score}/100")
            lines.append(f"- **Concentration Index (HHI):** {hhi:.3f}")
        sector_analysis = portfolio_analysis.get("sector_analysis", {})
        if sector_analysis and sector_analysis.get("concentration_risk"):
            lines.append(f"- **Sector Risk:** {_esc(sector_analysis.get('concentration_risk'))}")
        lines.append("")
    
    # ── Skill Results: Risk Manager ────────────────────────────────────────
    risk_assessment = market_data.get("risk_assessment", {})
    if risk_assessment and risk_assessment.get("status") == "success":
        lines.append("### ⚠️ Portfolio Risk Assessment (from Risk Manager Skill)")
        risk_score = risk_assessment.get("overall_risk_score", "N/A")
        risk_rating = risk_assessment.get("risk_rating", "N/A")
        lines.append(f"- **Overall Risk Score:** {risk_score}/100")
        lines.append(f"- **Risk Rating:** {risk_rating}")
        
        vol_risk = risk_assessment.get("volatility_risk", {}).get("classification", "N/A")
        corr_risk = risk_assessment.get("correlation_risk", {}).get("risk_level", "N/A")
        liquidity_risk = risk_assessment.get("liquidity_risk", {}).get("risk_level", "N/A")
        
        lines.append(f"- **Volatility Risk:** {vol_risk}")
        lines.append(f"- **Correlation Risk:** {corr_risk}")
        lines.append(f"- **Liquidity Risk:** {liquidity_risk}")
        lines.append("")
    if verdict.key_risks:
        lines.append("### ⚠️ Key Risks to Monitor")
        lines.extend(f"- {_esc(r)}" for r in verdict.key_risks)
        lines.append("")
    if verdict.dissent:
        lines.append("### 🧩 Unresolved Dissent")
        lines.append(f"> {_esc(verdict.dissent)}")
        lines.append("")

    # ── Active Triggers from Stop-Loss/Take-Profit Skill ────────────────────
    active_triggers = market_data.get("active_triggers", {})
    if active_triggers and active_triggers.get("active_triggers", 0) > 0:
        lines.append("### 🚨 Active Portfolio Triggers")
        trigger_summary = active_triggers.get("summary", "")
        if trigger_summary:
            lines.append(f"> **Alert:** {_esc(trigger_summary)}")
            lines.append("")
        
        for trigger in active_triggers.get("triggers", []):
            severity = trigger.get("severity", "INFO")
            icon = "🔴" if severity == "CRITICAL" else "🟠" if severity == "WARNING" else "🟡"
            alert_type = trigger.get("type", "Unknown")
            recommendation = trigger.get("recommendation", "No recommendation")
            lines.append(f"- {icon} **[{severity}]** {alert_type}: {_esc(recommendation)}")
        lines.append("")

    lines.append("---")
    lines.append("## 🗣️ Full Debate Transcript")
    lines.append("")
    for i, t in enumerate(turns):
        icon = "🟢" if t.role == "bull" else "🔴"
        lines.append(f"**{icon} {t.persona} — Round {t.round_number}**")
        lines.append("")
        lines.append(_format_debate_bullets(_esc(t.argument)))
        lines.append("")
        # Add extra spacing between rounds (after every 2 turns = 1 round)
        if (i + 1) % 2 == 0 and i < len(turns) - 1:
            lines.append("---")
            lines.append("")

    lines.append("---")
    lines.append(f"*Committee convened at {datetime.now().isoformat()} — "
                 f"{len(turns)} arguments across {max(t.round_number for t in turns)} rounds.*")
    return "\n".join(lines)


def run_investment_committee(
    ticker: str,
    market_data: Dict[str, Any],
    rounds: int = 2,
) -> Dict[str, Any]:
    """
    Public entry point: run the committee and return a structured result bundle.

    Returns a dict with:
        - verdict:   CommitteeVerdict as dict
        - transcript: list of DebateTurn as dicts
        - report_markdown: rendered proceedings
    """
    logger.info(f"[COMMITTEE ENTRY] run_investment_committee called with ticker={ticker}")
    logger.debug(f"[COMMITTEE ENTRY] market_data ticker: {market_data.get('ticker', 'NOT SET')}")
    
    committee = InvestmentCommittee(rounds=rounds)
    turns, verdict = committee.deliberate(ticker, market_data)
    logger.info(f"[COMMITTEE REPORT] Calling render_committee_report with ticker={ticker}")
    report = render_committee_report(ticker, market_data, turns, verdict)
    return {
        "verdict": verdict.model_dump(),
        "transcript": [t.model_dump() for t in turns],
        "report_markdown": report,
    }
