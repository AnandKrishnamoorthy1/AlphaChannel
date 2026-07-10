# Multi-Tenancy & Safe Public Deployment Architecture

## Overview

This guide explains how the Corporate Intelligence Engine safely supports both **live money trading** (for you) and **paper trading simulation** (for public users) without risk of real money execution.

## The Problem

❌ **Unsafe Approach:**
```python
# BAD - Direct live trading exposure
client = RobinhoodMCPClient()  # Always connects to real money!
await client.place_order("AMD", quantity=100)  # Could drain your account if exposed!
```

If you open-sourced this with live credentials, any public user could execute trades on your real account.

## The Solution: Broker Gateway Pattern

✅ **Safe Approach:**
```python
# GOOD - Dependency injection with safety gates
broker = get_broker_for_user(user_session)  # Returns simulator for public users
await broker.place_order("AMD", quantity=100)  # Safe! Only affects sim account
```

### Architecture Diagram

```
User Request
    │
    ▼
[Broker Factory]
    │
    ├─── Admin/Owner (ROBINHOOD_TRADING_ENABLED=true)
    │    │
    │    └─→ [RobinhoodMCPClient] → Real Robinhood → Live Money
    │
    └─── Public/Demo User (DEFAULT)
         │
         └─→ [MockSimulationEngine] → simulation_ledger.json → Paper Trading ($10k sim balance)
```

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# ==================== BROKER SELECTION ====================

# Broker type: 'live' or 'simulation'
# Set to 'simulation' for public GitHub/cloud deployments
BROKER_TYPE=simulation

# Enable Robinhood live trading (OWNER ONLY)
ROBINHOOD_TRADING_ENABLED=false  # Set to 'true' only for your admin session

# Path to simulation ledger (JSON file tracking paper trading state)
SIMULATION_LEDGER_PATH=simulation_ledger.json

# Generate per-user simulation accounts (for multi-user SaaS)
USE_SESSION_SIMULATION_ACCOUNT=false
```

### Usage Scenarios

#### Scenario 1: Local Development (You Only)
```bash
# .env
BROKER_TYPE=live
ROBINHOOD_TRADING_ENABLED=true
ROBINHOOD_ACCOUNT_ID=700320419
```
✅ You get live trading. Public users still get simulator.

#### Scenario 2: Public GitHub Repository
```bash
# .env
BROKER_TYPE=simulation  # ← Forces simulation for everyone
ROBINHOOD_TRADING_ENABLED=false
```
✅ No one can execute real trades, even if they fork your repo.

#### Scenario 3: Public SaaS with Session-Based Accounts
```bash
# .env
BROKER_TYPE=simulation
USE_SESSION_SIMULATION_ACCOUNT=true  # Each user gets SIM-{session_id}
SIMULATION_LEDGER_PATH=/data/simulation_ledgers.json
```
✅ Multiple users, each with their own $10k sim balance.

## How to Use in Your Code

### Option 1: Simple Usage (Most Common)

```python
from app.trading import get_broker_for_user, get_account_id_for_user

# In your FastAPI endpoint
@app.post("/api/execute-trade")
async def execute_trade(ticker: str, quantity: float):
    # Get the right broker based on user context
    broker = get_broker_for_user()  # Uses env config + session
    account_id = get_account_id_for_user()
    
    # Place order - automatically routes to live or sim!
    order = await broker.place_order(
        account_id=account_id,
        ticker=ticker,
        side=OrderSide.BUY,
        quantity=quantity,
    )
    
    return {"order_id": order.order_id, "status": order.status}
```

### Option 2: With User Session Context

```python
# In Streamlit or FastAPI
user_session = {
    "session_id": "user-12345",
    "is_admin": False,  # Public user
    "user_id": "user-12345"
}

broker = get_broker_for_user(user_session)
# → Returns MockSimulationEngine (safe!)

# With admin flag
user_session = {
    "is_admin": True,  # Admin/owner
}

broker = get_broker_for_user(user_session)
# → Returns RobinhoodMCPClient (if ROBINHOOD_TRADING_ENABLED=true)
```

### Option 3: Explicit Broker Selection

```python
from app.trading import get_live_broker, get_simulation_broker

# For testing, explicitly request simulator
broker = get_simulation_broker()

# For production admin-only code
broker = get_live_broker()  # Will fail if ROBINHOOD_TRADING_ENABLED=false
```

## The Mock Simulation Engine

### What It Does

```python
broker = MockSimulationEngine()

# Uses REAL Alpha Vantage prices
await broker.place_order(
    account_id="SIM-DEFAULT",
    ticker="AMD",
    side=OrderSide.BUY,
    amount_dollars=1000
)
# → Fetches real AMD price from Alpha Vantage
# → Calculates shares (1000 / $539.49 = 1.85 shares)
# → Deducts from sim balance ($10,000 → $9,000)
# → Adds shares to sim portfolio
# → Returns order exactly like Robinhood would
```

### Simulation State Storage

Ledger file: `simulation_ledger.json`

```json
{
  "accounts": {
    "SIM-DEFAULT": {
      "id": "SIM-DEFAULT",
      "account_type": "paper_trading",
      "cash_balance": 98500.25,
      "portfolio_value": 101234.50,
      "positions": {
        "AMD": 1.85,
        "NVDA": 0.42
      }
    }
  },
  "orders": [
    {
      "order_id": "sim-abc12345",
      "ticker": "AMD",
      "quantity": 1.85,
      "execution_price": 539.49,
      "total_value": 999.55
    }
  ]
}
```

## Security Properties

✅ **Prevents Live Money Exposure:**
- Public users cannot execute real trades
- No way to bypass (BROKER_TYPE=simulation enforces it)

✅ **Realistic Demo Experience:**
- Uses REAL stock prices
- Fractional share support (like real Robinhood)
- Real portfolio tracking
- $10k starting balance per account

✅ **Easy to Toggle for Testing:**
```python
# In your tests
import os
os.environ["BROKER_TYPE"] = "simulation"

broker = get_broker_for_user()  # Always simulator in tests
```

✅ **Audit Trail:**
- All orders logged to `simulation_ledger.json`
- Perfect for compliance/record-keeping

## Deployment Checklist

- [ ] Set `BROKER_TYPE=simulation` in production `.env`
- [ ] Set `ROBINHOOD_TRADING_ENABLED=false` in production `.env`
- [ ] Remove real `ROBINHOOD_ACCOUNT_ID` from repo
- [ ] Add `simulation_ledger.json` to `.gitignore`
- [ ] Test that public users get simulator (check logs for "🎮 SAFE MODE")
- [ ] Test that you get live client locally (check logs for "🚀 LIVE MODE")

## Transitioning to Live Trading

When Robinhood enables your account for agentic trading:

1. Keep all code as-is (no changes needed!)
2. Update your local `.env`:
   ```bash
   BROKER_TYPE=live
   ROBINHOOD_TRADING_ENABLED=true
   ROBINHOOD_ACCOUNT_ID=700320419
   ```
3. Restart backend
4. Live trading is now active for YOU, simulation still used for others

## FAQ

**Q: Can a public user somehow force live trading?**
A: No. The `BROKER_TYPE` environment variable is server-side only and controlled by you.

**Q: What if I forget to set BROKER_TYPE=simulation before pushing to GitHub?**
A: Your Robinhood credentials in `.env` won't be in the repo anyway (should be `.gitignore`d). But for safety, the code defaults to simulation if credentials are missing.

**Q: Does the simulator need internet?**
A: Yes, it fetches real prices from Alpha Vantage. But it works offline if you disable that check.

**Q: How do I reset sim accounts?**
A: Delete `simulation_ledger.json` and restart. New accounts auto-initialize with $10k.

**Q: Can I use this for real SaaS?**
A: Absolutely! That's the intended use case. Set `USE_SESSION_SIMULATION_ACCOUNT=true` to give each user their own $10k sandbox.
