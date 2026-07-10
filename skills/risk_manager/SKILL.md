---
name: risk_manager
description: Reusable skill for comprehensive portfolio risk assessment including volatility, correlation, sector concentration, and liquidity risk. Provides risk scoring and alerts for portfolio decisions.
domains: ["risk_assessment", "portfolio_monitoring", "alert_triggering"]
---

# Risk Manager Skill

## Overview
The Risk Manager skill provides multi-factor portfolio risk assessment, identifying exposure to volatility, correlation risk, sector concentration, and liquidity challenges. It powers the Investment Committee debate by contextualizing portfolio state within risk constraints.

## Workflow

### Step 1: Collect Risk Factors
- Volatility (beta per position)
- Correlation matrix (are positions moving together?)
- Sector weights (overconcentration in single sector?)
- Liquidity metrics (can positions be sold quickly?)
- Position sizes relative to market cap
- Portfolio duration (time to rebalance if needed)

### Step 2: Calculate Individual Risk Metrics
- **Volatility Risk**: Weighted beta across portfolio
  - Measures how much portfolio swings relative to market
  - Beta 1.0 = market risk, >1.0 = higher volatility
  
- **Correlation Risk**: Average pairwise correlation between positions
  - Measures if positions move together (increases portfolio volatility)
  
- **Concentration Risk**: HHI-based metric (see Portfolio Analyzer skill)
  
- **Liquidity Risk**: Estimated time to liquidate without market impact
  - Smaller positions = lower liquidity risk

### Step 3: Aggregate Risk Score
- Composite risk score (0-100): Combines all metrics into single comparable number
- Risk rating (Low/Medium/High/Critical)
- Risk alerts (warnings for specific conditions)

### Step 4: Generate Risk Report
- Current risk profile vs. user risk tolerance
- Triggers: Does current portfolio breach risk thresholds?
- Mitigation recommendations

### Step 5: Continuous Monitoring
- Flag when portfolio enters high-risk regime
- Alert if new trade would increase risk beyond tolerance
- Suggest rebalancing triggers

## Entry Point

```python
# From risk_tools.py
risk_manager = RiskManager()
risk_report = risk_manager.assess(
    portfolio=portfolio_data,
    market_data=market_state,
    risk_tolerance="medium"  # User's risk appetite
)
# Returns: {
#   "overall_risk_score": 65,
#   "risk_rating": "MEDIUM",
#   "volatility_risk": 1.45,  # Portfolio beta
#   "correlation_risk": 0.72,  # Avg pairwise correlation
#   "concentration_risk": 0.45,  # HHI
#   "liquidity_risk": "LOW",
#   "alerts": [
#     "ALERT: Portfolio volatility (1.45) exceeds medium risk tolerance (1.2)",
#     "WARNING: TSLA + NVDA correlation 0.89 = tech concentration risk"
#   ],
#   "recommendations": [...]
# }
```

## Use Cases

1. **Pre-Trade Validation**: Before user executes trade, Risk Manager assesses portfolio impact
2. **Continuous Monitoring**: After market close, Risk Manager checks if portfolio entered risky regime
3. **Investment Committee Context**: IC personas use risk report to contextualize debate (e.g., "Don't recommend NVDA—already at tech exposure limit")
4. **Portfolio Alerts**: "Your portfolio beta increased to 1.8, exceeding your medium-risk tolerance"
5. **Rebalancing Triggers**: "Recommend rebalancing when TSLA reaches 45% of portfolio"

## Implementation

See `risk_tools.py` for:
- `VolatilityRiskCalculator` (beta-weighted volatility)
- `CorrelationRiskCalculator` (pairwise correlations)
- `LiquidityRiskCalculator` (estimated liquidation time)
- `RiskScorer` (composite risk scoring)
- `RiskManager` class (main entry point)
