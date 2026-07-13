# AlphaChannel Architecture

AlphaChannel is a Gemini-native, MCP-enabled Slack governance agent built for the 2026 Slack Hackathon. The production entry point is `app.py`.

## Runtime Flow

```text
Slack mention / direct message / Block Kit action
                    |
                    v
          Slack Bolt Socket Mode
                 app.py
                    |
        +-----------+------------+
        |                        |
        v                        v
GeminiOrchestrationBrain   Deterministic UI workflows
        |                  portfolio / fact check / audit
        v                        |
Native Gemini function calls    |
        |                        |
        +-----------+------------+
                    v
          Governed MCP boundary
          engine/mcp_client.py
                    |
       +------------+-------------+
       |            |             |
       v            v             v
Yahoo Finance MCP  SEC EDGAR   Portfolio / diagnostics
       |            |             |
       +------------+-------------+
                    v
        Pydantic validation and policy
                    |
                    v
          Slack Block Kit response
```

## Components

| Component | Responsibility |
|---|---|
| `app.py` | Socket Mode listeners, thread history, Block Kit rendering, background workers, compliance archive |
| `engine/core_router.py` | Gemini intent classification, asset resolution, iterative tool reasoning, structured synthesis, approval checkpoints |
| `engine/mcp_client.py` | Persistent MCP transport, deadlines, tool policies, typed observations |
| `engine/mcp_server.py` | Registered AlphaChannel tools and execution boundary |
| `engine/yahoo_finance_mcp_client.py` | Persistent client for the Yahoo Finance MCP server |
| `engine/yfinance_node.py` | Normalizes Yahoo Finance MCP fundamentals and explicit options requests |
| `engine/sec_node.py` | EdgarTools retrieval, official SEC fallback, filing-text risk extraction |
| `engine/trading_node.py` | Paper portfolio, alert skills, mitigation plans, approval checkpoints |
| `engine/portfolio_store.py` | SQLite cash and equity position persistence |
| `skills/` | Stop-loss, take-profit, concentration, and portfolio-analysis policies |

## Agent Loop

1. Gemini classifies the request and resolves the requested company or ticker.
2. General educational questions are answered without exposing tools.
3. Financial assessments expose only approved read-only MCP tools.
4. Each tool observation returns to Gemini for the next reasoning turn.
5. Consequential tools stop at a Block Kit human-approval checkpoint.
6. Final risk output is validated against Pydantic structured-output contracts.

The runtime confidence gate defaults to `0.60`. Lower-confidence trade classifications use the conservative explicit-command fallback; investment questions remain analysis requests.

## Stateful Slack Workflows

- Thread memory is keyed by Slack channel and thread timestamp.
- Fact checking reads bounded thread/channel history, resolves a claim with Gemini, and verifies it against Yahoo Finance MCP statements.
- Portfolio actions prevent concurrent pending mutations for the same thread.
- Approved paper trades update SQLite only after human confirmation.
- Progress animations and external calls run on daemon workers so Socket Mode remains responsive.

## Governance Boundary

Read-only evidence tools execute autonomously. Buy, sell, trim, and hedge actions pause for explicit approval. AlphaChannel does not submit live brokerage orders.

Trade-related Block Kit events are archived to `#alphachannel-audit-logs` with UTC time, ticker, user ID, action type, delta details, redaction, and a unique event ID. Production deployments should configure `ALPHACHANNEL_AUDIT_CHANNEL_ID`.

## Data and Security

- Secrets load from `.env` and are never committed.
- SEC requests require an identifiable fair-access identity.
- MCP and provider calls have bounded deadlines.
- Audit details redact tokens, credentials, cookies, and authorization fields.
- Slack history and action payloads are bounded before model use.
- SQLite is appropriate for the single-instance Hackathon deployment; a managed transactional store is required for horizontally scaled production.

## Repository Layout

```text
alpha-channel/
|-- app.py
|-- engine/
|-- skills/
|-- scripts/
|-- assets/
|-- README.md
|-- ARCHITECTURE.md
|-- manifest.json
|-- mcp_servers.json
|-- requirements.txt
|-- requirements-dev.txt
|-- .env.example
`-- LICENSE
```

## Verification

```powershell
python -m pytest -q
python scripts/golden_path_smoke_test.py
python app.py
```
