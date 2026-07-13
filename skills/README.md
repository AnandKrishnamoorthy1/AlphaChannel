# AlphaChannel Skills

AlphaChannel skills are deterministic governance policies used by the 2026 Slack Hackathon agent. Each skill keeps its operating instructions in `SKILL.md` and its executable policy logic in a colocated Python module.

## Active Skills

- `stop_loss_take_profit`: loss, return, single-stock, and sector concentration alerts
- `portfolio_analyzer`: allocation and diversification analysis
- `risk_manager`: portfolio-level governance checks

The Portfolio Center invokes the monitoring engine directly:

```python
from skills.stop_loss_take_profit.trigger_tools import StopLossTakeProfitEngine

report = StopLossTakeProfitEngine().assess_all_triggers(portfolio)
```

## Governance Thresholds

- Stop-loss alert: loss reaches 20% of cost basis
- Take-profit alert: gain exceeds 50% of cost basis
- Single-stock concentration: position exceeds 15% of total portfolio value, including cash
- Sector concentration: sector exceeds 30% of total portfolio value, including cash

Skills produce recommendations and alerts only. Any consequential portfolio action remains approval-gated in Slack.
