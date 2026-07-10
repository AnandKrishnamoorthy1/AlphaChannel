# Migration Guide: Broker Abstraction Layer

## Quick Summary

You now have a **Broker Gateway** that routes between:
- 🚀 **Live Robinhood** (for you, when admin flag is set)
- 🎮 **Simulated Trading** (for public users, default)

This document shows exactly how to integrate it.

---

## Step 1: Update `.env` for Safe Defaults

```bash
# Add these lines to .env

# BROKER SELECTION (choose one)
BROKER_TYPE=simulation              # ← DEFAULT: Safe for public
# BROKER_TYPE=live                  # ← For local dev with real trading

# Live trading guard (only enable locally)
ROBINHOOD_TRADING_ENABLED=false     # ← DEFAULT: Safe for public
# ROBINHOOD_TRADING_ENABLED=true    # ← For local dev only!

# Optional: Session-based sim accounts
USE_SESSION_SIMULATION_ACCOUNT=false
SIMULATION_LEDGER_PATH=simulation_ledger.json
```

---

## Step 2: Update `backend.py`

### Before (Current Code)

```python
from app.trading import RobinhoodMCPClient

@app.post("/api/execute/{request_id}")
async def execute_approved_trade(request_id: str):
    # Always creates live client!
    client = RobinhoodMCPClient()
    
    order = await client.place_order(
        ticker=ticker,
        quantity=quantity,
    )
```

### After (With Broker Gateway)

```python
from app.trading import get_broker_for_user, get_account_id_for_user

@app.post("/api/execute/{request_id}")
async def execute_approved_trade(request_id: str):
    # Gets the right broker (live or sim) automatically!
    user_session = {}  # Could add user context here if needed
    broker = get_broker_for_user(user_session)
    account_id = get_account_id_for_user(user_session)
    
    order = await broker.place_order(
        account_id=account_id,
        ticker=ticker,
        side=side,
        quantity=quantity,
    )
    
    await broker.close()
```

**Key Changes:**
- Import `get_broker_for_user` and `get_account_id_for_user`
- Call factory functions instead of directly creating `RobinhoodMCPClient()`
- Pass `account_id` to `place_order()` (broker interface requires it)

---

## Step 3: Update `orchestrator.py` (Trading Node)

### Current Code

The `trading_node` currently just creates an `ApprovalRequest` - it doesn't execute. That's correct! The execution happens in `backend.py`.

**No changes needed to trading_node itself** - it just routes the request.

---

## Step 4: Update `frontend.py` (Session Handling)

### Optional: Pass User Context

```python
import streamlit as st
from datetime import datetime

# Generate or retrieve session ID
if "session_id" not in st.session_state:
    st.session_state.session_id = f"user-{datetime.now().timestamp()}"

# Pass session context to API
user_session = {
    "session_id": st.session_state.session_id,
    "is_admin": False,  # Public Streamlit user
}

# Call backend with context
response = requests.post(
    "http://localhost:8002/api/analyze",
    json={
        "user_input": user_input,
        "user_session": user_session,  # ← New!
    }
)
```

---

## Step 5: Verify It Works

### Check 1: Live Broker (Local Dev)

```bash
# Set in .env
BROKER_TYPE=live
ROBINHOOD_TRADING_ENABLED=true

# Start backend
python backend.py

# Logs should show:
# 🚀 LIVE MODE: Using Robinhood MCP client (real money!)
```

### Check 2: Simulation Broker (Public)

```bash
# Set in .env (or just leave defaults)
BROKER_TYPE=simulation
ROBINHOOD_TRADING_ENABLED=false

# Start backend
python backend.py

# Logs should show:
# 🎮 SAFE MODE: Using mock trading engine (paper trading)
# 🎮 Initializing simulation engine
# 🎮 Ledger: simulation_ledger.json
# Starting balance: $10,000
```

### Check 3: Execute a Trade

In Streamlit:
1. Type: "Buy $10 AMD"
2. Click Approve
3. Watch logs

**With live trading:**
```
🚀 LIVE MODE: Using Robinhood MCP client (real money!)
🔑 Bearer token for MCP request...
📤 Sending MCP API request...
Status: 200 ✅
```

**With simulation:**
```
🎮 SAFE MODE: Using mock trading engine (paper trading)
📊 Fetching real price for AMD...
Current price: $539.49
✅ Simulated order executed!
Order ID: sim-abc12345
```

---

## Code Examples

### Example 1: Simple Trade Execution

```python
# In backend.py or trading service

from app.trading import get_broker_for_user, get_account_id_for_user

async def execute_trade(ticker: str, quantity: float):
    broker = get_broker_for_user()
    account_id = get_account_id_for_user()
    
    try:
        order = await broker.place_order(
            account_id=account_id,
            ticker=ticker,
            side="buy",
            quantity=quantity,
        )
        return order
    finally:
        await broker.close()
```

### Example 2: With User Session

```python
# In FastAPI endpoint

@app.post("/api/trade")
async def trade_endpoint(
    ticker: str,
    quantity: float,
    user_session: dict = None
):
    broker = get_broker_for_user(user_session)
    account_id = get_account_id_for_user(user_session)
    
    order = await broker.place_order(
        account_id=account_id,
        ticker=ticker,
        side="buy",
        quantity=quantity,
    )
    
    await broker.close()
    return order.dict()
```

### Example 3: Testing

```python
# In tests - force simulator

import os
os.environ["BROKER_TYPE"] = "simulation"

from app.trading import get_broker_for_user

async def test_buy_order():
    broker = get_broker_for_user()  # Always simulator in tests
    account_id = "SIM-TEST"
    
    order = await broker.place_order(
        account_id=account_id,
        ticker="TEST",
        side="buy",
        quantity=10,
    )
    
    assert order.status == "filled"
    assert order.simulated == True
```

---

## Troubleshooting

### Issue: "Still seeing live mode when I want simulation"

**Solution:** Make sure `.env` has:
```bash
BROKER_TYPE=simulation
ROBINHOOD_TRADING_ENABLED=false
```

Restart backend. Check logs for "🎮 SAFE MODE".

### Issue: "Trades not executing in simulation"

**Check:**
1. Is `simulation_ledger.json` writable?
2. Does account have enough balance? (Default $10k)
3. Is Alpha Vantage API key configured?

### Issue: "Account ID missing"

**Solution:** Call `get_account_id_for_user()` before placing order:
```python
account_id = get_account_id_for_user()  # Don't forget this!
order = await broker.place_order(
    account_id=account_id,  # ← Required!
    ...
)
```

---

## Summary

| Component | Change | Purpose |
|-----------|--------|---------|
| `.env` | Add `BROKER_TYPE`, `ROBINHOOD_TRADING_ENABLED` | Control live vs simulation |
| `backend.py` | Use `get_broker_for_user()` factory | Route to live or sim |
| `orchestrator.py` | No changes | trading_node just routes |
| `frontend.py` | Optional: pass user_session | Track multi-user sim accounts |
| `broker_factory.py` | NEW | Dependency injection layer |
| `mock_simulation_engine.py` | NEW | Paper trading engine |
| `broker_interface.py` | NEW | Abstract contract |

---

## What's Next?

1. ✅ Implement broker abstraction (DONE!)
2. 🔄 Update backend.py to use `get_broker_for_user()`
3. 🔄 Update orchestrator.py trading node (if needed)
4. 🔄 Test with BROKER_TYPE=simulation
5. 🔄 Commit to GitHub with safe defaults
6. 🚀 Flip to BROKER_TYPE=live + ROBINHOOD_TRADING_ENABLED=true when Robinhood activates account
