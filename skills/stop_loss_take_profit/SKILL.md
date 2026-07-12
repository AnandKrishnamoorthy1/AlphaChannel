---
name: stop_loss_take_profit
description: Monitor portfolio positions against risk thresholds and recommend stop-loss/take-profit actions when triggered. Alerts on concentration breaches, correlation spikes, and profit/loss milestones.
domains: ["portfolio_monitoring", "risk_management", "alert_triggering", "trade_validation"]
---

# Stop-Loss / Take-Profit Triggers Skill

## Overview
The Stop-Loss/Take-Profit skill provides real-time monitoring of portfolio positions against predefined risk thresholds. It triggers alerts when positions breach concentration limits, hit profit/loss targets, or show excessive correlation with other holdings.

## Workflow

### Step 1: Check Concentration Triggers
- Monitor each position as % of total portfolio
- **Policy threshold: 15%** of total portfolio equity per stock
- Alert if any single position exceeds threshold
- Recommend position reduction action

### Step 2: Check Correlation Spikes
- Identify positions that move together (high correlation)
- Alert if two correlated positions (>0.7) exceed 30% combined concentration
- Flag as "concentration risk through correlation"

### Step 3: Check Stop-Loss Triggers
- Monitor unrealized losses on each position
- **Policy threshold: -20%** unrealized return on cost basis
- Recommend exit if threshold breached

### Step 4: Check Take-Profit Triggers
- Monitor unrealized gains on each position
- **Policy threshold: +50%** unrealized return on cost basis
- Recommend profit-taking if threshold breached

### Step 5: Liquidity Constraint Check
- Verify recommended position exits won't cause liquidity issues
- Warn if selling would exceed 5% portfolio volume in single day

### Step 6: Sector Concentration Check
- Group holdings by sector or stock group.
- Alert when any sector exceeds 30% of total portfolio equity.

### Step 7: Generate Alerts & Recommendations
- Return list of active triggers
- Provide specific, actionable recommendations
- Rank triggers by severity (HIGH / MEDIUM / LOW)

## Entry Point

```python
# From trigger_tools.py
trigger_engine = StopLossTakeProfitEngine()
result = trigger_engine.assess_all_triggers(
    portfolio=portfolio_data,  # {positions: [...], history: [...]}
    correlation_matrix=optional_correlations
)
# Returns: {
#   "active_triggers": 3,
#   "triggers": [
#     {
#       "type": "concentration_breach",
#       "ticker": "TSLA",
#       "current": 52.3,
#       "threshold": 50.0,
#       "severity": "HIGH",
#       "recommendation": "Reduce TSLA by $450 to get below 50%"
#     },
#     {
#       "type": "take_profit",
#       "ticker": "AMD",
#       "unrealized_return": 32.5,
#       "recommendation": "AMD up 32.5% (+$156). Lock in profits?"
#     }
#   ],
#   "status": "success"
# }
```

## Use Cases

1. **Pre-Trade Validation**: "Buy 5 NVDA" → Check if this breaches 50% concentration
2. **Portfolio Review**: "Show my portfolio" → Display active triggers at top
3. **Pre-IC Analysis**: Before debate, alert IC to active concentration breaches
4. **Risk Dashboard**: Real-time trigger summary

## Portfolio Policy Thresholds

- **Single-stock alert**: greater than 15% of total portfolio equity
- **Sector/group alert**: greater than 30% of total portfolio equity
- **Stop-loss alert**: below -20% versus average cost
- **Take-profit alert**: above +50% versus average cost
- **Correlation alert**: retained as secondary context when correlated positions are material

## Integration Points

- **research_node**: Check triggers before IC debate
- **portfolio_node**: Show active triggers in portfolio review
- **trading_node**: Pre-trade validation before execution approval
- **frontend**: Display alert badge if triggers active
