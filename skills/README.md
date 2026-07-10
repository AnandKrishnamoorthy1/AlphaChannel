"""Skills Architecture Documentation

This directory contains reusable, modular AI agent skills that encapsulate
domain-specific workflows and tools.

## What are Skills?

Skills are self-contained, procedural artifacts that bundle:
- Task-specific execution instructions (SKILL.md)
- Implementation code (tools.py)
- Reusable algorithms and logic

Instead of overwhelming the LLM with all available tools upfront, skills enable
**progressive disclosure**: the agent sees lightweight metadata initially, loading
full procedures only when a task triggers that domain.

## Skill Discovery

The SkillRegistry loads YAML manifests from each skill's SKILL.md file:

```python
from app.skills import get_skill_registry

registry = get_skill_registry()
available_skills = registry.list_all_skills()  # ["portfolio_analyzer", "risk_manager", ...]
```

## Invoking Skills

Skills are invoked by name with domain-specific parameters:

```python
from app.skills import invoke_skill

# Portfolio analysis
portfolio_report = invoke_skill(
    "portfolio_analyzer",
    portfolio={"positions": [...], "cash": 1000, "total_value": 10000}
)

# Risk assessment
risk_report = invoke_skill(
    "risk_manager",
    portfolio=portfolio_data,
    risk_tolerance="moderate"
)
```

## Available Skills

1. **portfolio_analyzer**: Analyze diversification, asset allocation, sector exposure
2. **risk_manager**: Multi-factor portfolio risk assessment with alerts
3. **stop_loss_take_profit**: Monitor positions against thresholds, trigger alerts, pre-trade validation

See individual SKILL.md files for detailed documentation.

## Demo-Ready Thresholds

**Skill 3 (Stop-Loss/Take-Profit) is configured with LOW thresholds for easy demonstration:**
- Concentration alert: **50%** (triggers readily with TSLA, NVDA, etc.)
- Stop-loss: -10%
- Take-profit: +30%
"""
