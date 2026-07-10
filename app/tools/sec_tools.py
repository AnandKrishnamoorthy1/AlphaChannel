"""
SEC EDGAR tool using edgartools.

Fetches the last N quarterly 10-Q filings for a ticker and extracts
key financial metrics via XBRL parsing. Used to give the Investment
Committee verified, audited ground-truth data to debate against.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from edgar import Company, set_identity as _set_identity
    _EDGAR_AVAILABLE = True
except ImportError:
    _EDGAR_AVAILABLE = False
    logger.warning(
        "[SEC] edgartools not installed — SEC filing data unavailable. "
        "Run: pip install edgartools"
    )

# SEC requires any HTTP client to declare a contact identity.
# This is not verified; it's just used for SEC rate-limit attribution.
_EDGAR_IDENTITY = "corporate-intelligence-engine@hackathon.demo"

# XBRL concept names tried in priority order for each metric.
# Different companies use different tags; we try the most common first.
_CONCEPT_MAP: Dict[str, List[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "TotalRevenues",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncome",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ],
    "rd_expense": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
        "Cash",
    ],
    "long_term_debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermNotesPayable",
    ],
    "total_assets": ["Assets"],
    "shareholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpenditures",
        "PaymentsForCapitalImprovements",
    ],
}


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _first_col_value(df: Any, concepts: List[str]) -> Optional[float]:
    """
    Try each concept name against a DataFrame index and return the first match.
    Takes the most recent column value (iloc[0] for the first period column).
    """
    for concept in concepts:
        try:
            if concept in df.index:
                val = _safe_float(df.loc[concept].iloc[0])
                if val is not None:
                    return val
        except Exception:
            continue
    return None


def _extract_quarter_metrics(filing: Any) -> Dict[str, Any]:
    """
    Pull key financial metrics out of a single 10-Q filing via XBRL.
    Returns a flat dict; keys are absent if data not available.
    """
    metrics: Dict[str, Any] = {}

    # ── Filing metadata ──────────────────────────────────────────────────
    try:
        metrics["period"] = str(getattr(filing, "period_of_report", "Unknown"))
        metrics["filed"] = str(getattr(filing, "filing_date", "Unknown"))
    except Exception:
        pass

    # ── XBRL financials ──────────────────────────────────────────────────
    try:
        financials = getattr(filing, "financials", None)
        if financials is None:
            return metrics

        # Income statement
        try:
            income = getattr(financials, "income_statement", None)
            if income is not None:
                df = income.to_dataframe()
                if df is not None and not df.empty:
                    for key, concepts in _CONCEPT_MAP.items():
                        if key in ("cash", "long_term_debt", "total_assets",
                                   "shareholders_equity", "operating_cash_flow", "capex"):
                            continue  # handled below
                        val = _first_col_value(df, concepts)
                        if val is not None:
                            metrics[key] = val
        except Exception as e:
            logger.debug(f"[SEC] Income statement parse error: {e}")

        # Balance sheet
        try:
            balance = getattr(financials, "balance_sheet", None)
            if balance is not None:
                df = balance.to_dataframe()
                if df is not None and not df.empty:
                    for key in ("cash", "long_term_debt", "total_assets", "shareholders_equity"):
                        val = _first_col_value(df, _CONCEPT_MAP[key])
                        if val is not None:
                            metrics[key] = val
        except Exception as e:
            logger.debug(f"[SEC] Balance sheet parse error: {e}")

        # Cash flow statement
        try:
            cf = getattr(financials, "cash_flow_statement", None)
            if cf is not None:
                df = cf.to_dataframe()
                if df is not None and not df.empty:
                    for key in ("operating_cash_flow", "capex"):
                        val = _first_col_value(df, _CONCEPT_MAP[key])
                        if val is not None:
                            metrics[key] = val
        except Exception as e:
            logger.debug(f"[SEC] Cash flow parse error: {e}")

        # Derived: free cash flow
        ocf = metrics.get("operating_cash_flow")
        capex = metrics.get("capex")
        if ocf is not None and capex is not None:
            metrics["free_cash_flow"] = ocf - abs(capex)

    except Exception as e:
        logger.debug(f"[SEC] Financials extraction error: {e}")

    return metrics


def fmt_sec_value(val: float) -> str:
    """Format a raw dollar value as B/M/K string."""
    sign = "-" if val < 0 else ""
    abs_val = abs(val)
    if abs_val >= 1e9:
        return f"{sign}${abs_val / 1e9:.2f}B"
    if abs_val >= 1e6:
        return f"{sign}${abs_val / 1e6:.1f}M"
    return f"{sign}${abs_val:,.0f}"


def get_sec_filings(ticker: str, num_quarters: int = 2) -> Dict[str, Any]:
    """
    Fetch the last ``num_quarters`` quarterly 10-Q filings from SEC EDGAR
    and return structured financial metrics for each.

    Returns a dict with:
        available (bool)   — False if edgartools is missing or EDGAR lookup failed
        ticker (str)
        quarters (list)    — most-recent first; each entry is a metrics dict
        reason (str)       — populated when available=False

    Fails silently — callers receive empty/unavailable and continue normally.
    """
    if not _EDGAR_AVAILABLE:
        return {"available": False, "reason": "edgartools not installed"}

    try:
        _set_identity(_EDGAR_IDENTITY)
        company = Company(ticker.upper())

        if company is None:
            logger.warning(f"[SEC] Company not found in EDGAR: {ticker}")
            return {"available": False, "reason": f"{ticker} not found in EDGAR"}

        filings = company.get_filings(form="10-Q")
        if not filings or len(filings) == 0:
            logger.warning(f"[SEC] No 10-Q filings found for {ticker}")
            return {"available": False, "reason": "No 10-Q filings found"}

        quarters: List[Dict[str, Any]] = []
        for i in range(min(num_quarters, len(filings))):
            try:
                filing = filings[i]
                metrics = _extract_quarter_metrics(filing)
                if metrics:
                    quarters.append(metrics)
                    rev = metrics.get("revenue")
                    ni = metrics.get("net_income")
                    logger.info(
                        f"[SEC] {ticker} Q-{i + 1} "
                        f"({metrics.get('period', '?')}): "
                        f"Rev={fmt_sec_value(rev) if rev else 'N/A'}, "
                        f"NI={fmt_sec_value(ni) if ni else 'N/A'}"
                    )
            except Exception as e:
                logger.warning(f"[SEC] Could not extract Q-{i + 1} for {ticker}: {e}")

        if not quarters:
            return {"available": False, "reason": "XBRL metrics extraction returned no data"}

        logger.info(f"[SEC] Loaded {len(quarters)} quarter(s) for {ticker}")
        return {"available": True, "ticker": ticker.upper(), "quarters": quarters}

    except Exception as e:
        logger.warning(f"[SEC] EDGAR fetch failed for {ticker}: {type(e).__name__}: {e}")
        return {"available": False, "reason": str(e)}
