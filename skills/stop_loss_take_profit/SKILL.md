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
- **Default threshold: 50%** (easily demonstrable)
- Alert if any single position exceeds threshold
- Recommend position reduction action

### Step 2: Check Correlation Spikes
- Identify positions that move together (high correlation)
- Alert if two correlated positions (>0.7) exceed 30% combined concentration
- Flag as "concentration risk through correlation"

### Step 3: Check Stop-Loss Triggers
- Monitor unrealized losses on each position
- **Default threshold: -10%** (triggered if down 10%)
- Recommend exit if threshold breached

### Step 4: Check Take-Profit Triggers
- Monitor unrealized gains on each position
- **Default threshold: +30%** (triggered if up 30%)
- Recommend profit-taking if threshold breached

### Step 5: Liquidity Constraint Check
- Verify recommended position exits won't cause liquidity issues
- Warn if selling would exceed 5% portfolio volume in single day

### Step 6: Generate Alerts & Recommendations
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

## Thresholds (Demonstration-Friendly)

- **Concentration Alert**: 50% (instead of 75% for easy demo)
- **Stop-Loss Alert**: -10% unrealized loss
- **Take-Profit Alert**: +30% unrealized gain
- **Correlation Alert**: >0.7 with combined >30% concentration

## Integration Points

- **research_node**: Check triggers before IC debate
- **portfolio_node**: Show active triggers in portfolio review
- **trading_node**: Pre-trade validation before execution approval
- **frontend**: Display alert badge if triggers active
