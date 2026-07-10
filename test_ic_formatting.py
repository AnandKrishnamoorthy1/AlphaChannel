#!/usr/bin/env python3
"""
Quick test to debug Investment Committee text formatting issue.
Checks what the report looks like before it's sent to frontend.
"""

import json
import sys
from app.agents.investment_committee import run_investment_committee

# Sample market data - similar structure to real data
sample_market_data = {
    "ticker": "GOOGL",
    "price": 359.91,
    "change": -24.37,
    "change_percent": -6.33,
    "volume": 45823400,
    "timestamp": "2026-07-04",
    "data_source": "Yahoo Finance MCP",
    "sector": "Communication Services",
    "industry": "Internet Services",
    "market_cap": "$1.48T",
    "pe_ratio": 357.68,
    "forward_pe": 154.48,
    "eps": 1.1,
    "analyst_target_price": 423.4,
    "week_52_high": 498.83,
    "week_52_low": 288.77,
    "profit_margin": 0.03946,
    "quarterly_earnings_growth_yoy": 0.083,
    "quarterly_revenue_growth_yoy": 0.158,
    "beta": 1.802,
    "return_on_equity": 0.04901,
    "sec_filings": {
        "available": True,
        "quarters": [
            {
                "period": "2026-03-31",
                "filed": "2026-05-02",
                "revenue": 88270000000,
                "net_income": 3480000000,
                "gross_profit": 41260000000,
                "operating_income": 4870000000,
                "rd_expense": 11600000000,
                "free_cash_flow": 28340000000,
                "cash": 110260000000,
                "long_term_debt": 13720000000,
                "eps_diluted": 1.31,
            }
        ]
    }
}

print("=" * 80)
print("Testing Investment Committee text formatting")
print("=" * 80)

try:
    result = run_investment_committee("GOOGL", sample_market_data, rounds=1)
    
    print("\n✅ Committee result generated successfully\n")
    
    # Check thesis for spaces
    thesis = result["verdict"]["thesis"]
    print(f"THESIS ({len(thesis)} chars):")
    print(f"  Preview: {thesis[:200]}")
    print(f"  Has abnormal concatenations: {' concatenated' if 'concaten' not in thesis.lower() else 'maybe'}")
    
    # Check individual fields
    print("\nBULL POINTS:")
    for i, point in enumerate(result["verdict"]["bull_points"], 1):
        print(f"  [{i}] {point[:80]}...")
        
    print("\nBEAR POINTS:")
    for i, point in enumerate(result["verdict"]["bear_points"], 1):
        print(f"  [{i}] {point[:80]}...")
        
    print("\nKEY RISKS:")
    for i, risk in enumerate(result["verdict"]["key_risks"], 1):
        print(f"  [{i}] {risk[:80]}...")
    
    # Check full report
    report = result["report_markdown"]
    print(f"\n\nFULL REPORT ({len(report)} chars):")
    
    # Look for spacing issues
    if "thestocksis" in report.lower() or "analysttarget" in report.lower():
        print("  ❌ DETECTED: Missing spaces (concatenated words)")
        # Find examples
        idx = report.lower().find("thestocksis")
        if idx > 0:
            print(f"    Example: ...{report[max(0, idx-20):idx+30]}...")
    else:
        print("  ✅ No obvious concatenation issues detected")
    
    # Check for Unicode characters
    if "∗" in report or "•" in report or "−" in report:
        print("  ⚠️  Unicode characters detected (∗, •, −)")
    
    print("\n" + "=" * 80)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
