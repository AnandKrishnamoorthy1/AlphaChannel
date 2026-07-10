---
name: portfolio_analyzer
description: Reusable skill to analyze simulated portfolio asset allocations, fetch historical tickers, cross-reference market data, and compute diversification metrics. Provides sector weighting analysis and concentration risk assessment.
domains: ["portfolio_analysis", "diversification", "asset_allocation"]
---

# Portfolio Analyzer Skill

## Overview
The Portfolio Analyzer skill provides deep insights into portfolio composition, asset diversification, and sector concentration. It bridges simulation state with market data to deliver comprehensive allocation analysis.

## Workflow

### Step 1: Read Portfolio State
- Access current portfolio from session state or simulation ledger
- Extract held positions: `[{ticker: "TSLA", shares: 10, value: 3934.50}, ...]`
- Retrieve cash position and total portfolio value

### Step 2: Cross-Reference Market Data
- For each ticker, fetch:
  - Current price (Yahoo Finance)
  - Sector classification (from financial database)
  - Market cap (determine position size relative to market)
  - Historical volatility (for concentration context)

### Step 3: Calculate Asset Allocation
- Compute percentage allocation per position: `position_value / total_portfolio_value`
- Group by sector and calculate sector weights
- Identify concentrated positions (>20% single holding)

### Step 4: Compute Diversification Score
- Herfindahl-Hirschman Index (HHI): Measures concentration (0-1 scale)
- Sector concentration: Are holdings clustered in single sector?
- Correlation analysis: Do positions move together?
- Result: Diversification score (0-1, where 1 = perfectly diversified)

### Step 5: Generate Analysis Report
- Asset allocation breakdown (by position and sector)
- Risk factors (concentration, correlation, sector bias)
- Actionable recommendations (e.g., "Reduce NVDA to <15% for better diversification")

## Entry Point

```python
# From analysis_tools.py
portfolio_analyzer = PortfolioAnalyzer()
report = portfolio_analyzer.analyze(
    portfolio=portfolio_data,  # {positions: [...], cash: 1000, total_value: 10000}
    market_data_provider=yahoo_finance_client  # For real-time data
)
# Returns: {
#   "total_value": 10000,
#   "allocation": [{ticker, value, pct, sector}, ...],
#   "sector_weights": {tech: 0.65, healthcare: 0.25, ...},
#   "diversification_score": 0.72,
#   "concentrated_positions": ["TSLA (42%)"],
#   "recommendations": ["Reduce tech exposure", "Consider healthcare hedge"]
# }
```

## Use Cases

1. **Portfolio Check-In**: "Show me my current portfolio allocation" → Triggers this skill
2. **Pre-Trade Analysis**: Before user asks to buy NVDA, skill warns if tech already >60%
3. **Risk Assessment Context**: Investment Committee uses allocation data in debate
4. **Rebalancing Recommendation**: "My portfolio is 80% tech, should I rebalance?" → Skill provides target allocation

## Implementation

See `analysis_tools.py` for:
- `PortfolioAnalyzer` class (main entry point)
- `AssetAllocationCalculator` (percentage math)
- `DiversificationScorer` (HHI + correlation)
- `SectorAnalyzer` (sector weight computation)
