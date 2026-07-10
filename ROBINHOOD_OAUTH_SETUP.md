# Robinhood MCP OAuth Setup Guide

## Overview

Your trading system now uses **Robinhood's OAuth 2.1 PKCE** for authentication instead of static API keys. This is more secure and matches how Robinhood's agentic trading platform works.

## How It Works

### 1. **First Run: Browser Login**

When you first try to execute a trade, the system will:

```
Trade Request
    ↓
Check for cached OAuth token
    ↓
Token not found → Open browser login window
    ↓
You: Enter Robinhood credentials + complete 2FA
    ↓
System: Intercepts OAuth redirect
    ↓
Extract access_token + refresh_token
    ↓
Cache tokens locally (encrypted, in ~/.robinhood_agent/tokens/)
    ↓
Trade executes ✓
```

### 2. **Subsequent Runs: Auto-Refresh**

```
Trade Request
    ↓
Load cached token
    ↓
Token still valid? → Use it immediately ✓
    ↓
Token expired? → Silent refresh with refresh_token (no login needed)
    ↓
Trade executes ✓
```

### 3. **Token Storage**

Tokens are cached locally at:
```
~/.robinhood_agent/tokens/access_token.json
```

This file contains:
```json
{
  "access_token": "your_bearer_token_here",
  "refresh_token": "refresh_token_for_silent_refresh",
  "expires_in": 86400,
  "token_type": "Bearer",
  "cached_at": "2026-06-29T10:30:00"
}
```

**Only you** can read this file (OS-level permissions), so your token won't leak to other users on the same machine.

---

## Usage

### Option A: Automated Browser Flow (Recommended)

If **Playwright is installed**, the system will:
1. Open a headless browser window
2. Let you login to Robinhood
3. Complete 2FA
4. Automatically capture the token

**Install Playwright:**
```bash
pip install playwright
playwright install chromium
```

### Option B: Manual Copy-Paste

If Playwright isn't installed, the system will:
1. Print a login URL in the terminal
2. Ask you to open it in your browser
3. You complete login + 2FA
4. Copy the `access_token` from the redirect URL or browser console
5. Paste it back into the terminal

---

## Full MCP Trade Execution Flow

```python
# 1. User submits trade request
user_query = "Buy 10 NVDA shares"

# 2. LangGraph routing
# Query → Triage → Routing Decision: "direct_trade"

# 3. Trading Node
# Creates ApprovalRequest:
# {
#   "action": "BUY",
#   "ticker": "NVDA",
#   "quantity": 10,
#   "reasoning": "User direct request"
# }

# 4. Human Approval
# POST /api/approve/{request_id}
# Body: {"approved": true, "approver_notes": "Looks good"}

# 5. Approval Execution Node
# Calls RobinhoodMCPClient.place_order()
# 
# This internally:
# a) get_bearer_token()  → OAuth handler gets valid token
# b) POST to https://agent.robinhood.com/mcp/trading
# c) JSON-RPC 2.0 payload:
# {
#   "jsonrpc": "2.0",
#   "method": "tools/call",
#   "params": {
#     "name": "place_order",
#     "arguments": {
#       "account_id": "700320419",
#       "ticker": "NVDA",
#       "side": "buy",
#       "quantity": 10,
#       "time_in_force": "day"
#     }
#   }
# }
#
# d) Robinhood MCP executes order
# e) Returns: {"result": {"order_id": "12345", "status": "filled", ...}}

# 6. Report Generated
# ✓ BUY 10 NVDA @ Market | Order #12345 | Status: Filled
```

---

## Configuration (.env)

```env
# Robinhood MCP Endpoint
ROBINHOOD_MCP_ENDPOINT=https://agent.robinhood.com/mcp/trading

# Your Robinhood Account ID
ROBINHOOD_ACCOUNT_ID=700320419

# Enable trading (OAuth will handle actual tokens)
ROBINHOOD_TRADING_ENABLED=true

# OLD FIELDS (No longer used - remove if present):
# ROBINHOOD_API_KEY=your_robinhood_api_key_here   ← DELETE
# ROBINHOOD_API_SECRET=your_robinhood_api_secret_here  ← DELETE
```

---

## Testing the Integration

### 1. Check OAuth Initialization
```bash
python -c "
from app.trading import get_bearer_token
import asyncio

token = asyncio.run(get_bearer_token())
print(f'✓ Token obtained: {token[:20]}...')
"
```

### 2. Test Account Info Fetch
```bash
python -c "
from app.trading import RobinhoodMCPClient
import asyncio

client = RobinhoodMCPClient()
account = asyncio.run(client.get_account_info())
print(f'Account: {account.account_number}')
print(f'Buying Power: ${account.buying_power:.2f}')
"
```

### 3. Test Full Trade Flow (with human approval)
```bash
# Start backend
python backend.py

# In another terminal, send trade request
curl -X POST http://localhost:8002/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"user_input": "Buy 5 shares of TSLA"}'

# Gets back:
# {
#   "request_id": "abc123",
#   "routing_decision": "direct_trade",
#   "pending_approval": {
#     "action": "BUY",
#     "ticker": "TSLA",
#     "quantity": 5
#   }
# }

# Human approves:
curl -X POST http://localhost:8002/api/approve/abc123 \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "approver_notes": "Approved!"}'

# Trade executes, order created on Robinhood ✓
```

---

## Troubleshooting

### "Browser login failed" or "No valid token"
- Ensure Robinhood account exists and is set up for agentic trading
- Try manual copy-paste flow (Option B above)
- Check that `~/.robinhood_agent/tokens/access_token.json` is readable

### "Token expired" warnings
- Token refreshes automatically using refresh_token
- Delete cached token to force full re-login:
  ```bash
  rm ~/.robinhood_agent/tokens/access_token.json
  ```

### MCP Response Errors
- Check account has sufficient buying power
- Verify ticker symbol is correct
- Check market hours (Robinhood trading restricted outside market hours)
- Review MCP error message in backend logs

---

## Next Steps

1. **Enable trading in .env:**
   ```
   ROBINHOOD_TRADING_ENABLED=true
   ```

2. **Install Playwright (optional but recommended):**
   ```bash
   pip install playwright
   playwright install chromium
   ```

3. **Test OAuth flow:**
   ```bash
   python backend.py
   # Then trigger a trade to test login
   ```

4. **Run end-to-end test:**
   - Streamlit frontend: `streamlit run frontend.py`
   - Send trade: "Buy 5 NVDA"
   - Approve in /api/approve endpoint
   - Verify order on Robinhood account

---

## Security Notes

✅ **OAuth token is NOT stored in .env** - prevents accidental git commits
✅ **Bearer token cached locally** - expires in 24h, auto-refreshes silently
✅ **2FA required every 24h** - or uses refresh_token for silent renewal
✅ **No hardcoded credentials** - matches Robinhood's recommended pattern for agents

**Never share** `~/.robinhood_agent/tokens/access_token.json` with others - it grants full trading access!
