# AlphaChannel Architecture

AlphaChannel is a Gemini-native, MCP-enabled Slack governance agent built for the 2026 Slack Hackathon. The production entry point is `app.py`.

## Runtime Flow

```text
Slack mention / DM / Block Kit action
            |
            v
Slack Bolt (Socket Mode)
            |
            +--> Slack Real-Time Search API
            +--> Thread conversation state
            |
            v
Gemini Flash (Latest)
            |
            v
Intent classification
            |
            +--> Confidence >= 70%: agent planning
            +--> Confidence < 70%: deterministic intent fallback
            |
            v
Agent capability and MCP tool selection
            |
            v
Action policy gate
            |
            +--> Read-only: execute autonomously
            +--> Consequential: approve / deny / edit
            |
            v
Tool evidence (Yahoo Finance / SEC EDGAR / AlphaChannel)
            |
            +--> Observation returns to Gemini when more evidence is needed
            |
            v
Gemini synthesis
            |
            v
Pydantic risk and action contracts
            |
            v
Slack Block Kit response
```

## Components

| Component | Responsibility |
|---|---|
| `app.py` | Socket Mode listeners, Real-Time Search, DM and thread history, Block Kit rendering, background workers, compliance delivery |
| `engine/core_router.py` | Gemini intent classification, asset resolution, iterative tool reasoning, structured synthesis, approval checkpoints |
| `engine/mcp_client.py` | Persistent MCP transport for normal tools, isolated SEC execution, deadlines, policies, typed observations |
| `engine/mcp_server.py` | Registered AlphaChannel tools and execution boundary |
| `engine/yahoo_finance_mcp_client.py` | Persistent client for the separate Yahoo Finance MCP server |
| `engine/yfinance_node.py` | Normalizes Yahoo Finance MCP fundamentals and handles explicit options requests |
| `engine/sec_node.py` | Direct SEC EDGAR retrieval by default, optional EdgarTools backend, filing-text risk extraction |
| `engine/trading_node.py` | Paper portfolio, alert skills, mitigation plans, approval checkpoints |
| `engine/portfolio_store.py` | SQLite cash and equity-position persistence |
| `skills/` | Stop-loss, take-profit, concentration, and portfolio-analysis policies |

## Agent Loop

1. Gemini classifies the request and resolves the requested company or ticker.
2. General educational questions are answered without invoking ticker-specific tools.
3. Financial assessments expose only approved read-only MCP evidence tools.
4. Every tool observation returns to Gemini, which decides whether to gather more evidence or synthesize.
5. Consequential tools stop at a Block Kit human-approval checkpoint.
6. Final risk output is validated against intent-specific Pydantic structured-output contracts.

The runtime confidence gate defaults to `0.70`. Lower-confidence classifications use a bounded deterministic intent fallback. Gemini remains responsible for planning, evidence selection, reasoning, and synthesis.

## Stateful Slack Workflows

- Slack mentions, direct messages, Agent View interactions, and Block Kit actions enter through Socket Mode.
- Thread memory is keyed by Slack channel and thread timestamp.
- Real-Time Search calls `assistant.search.context` to build the Internal Workspace Perception payload.
- Fact checking reads bounded thread or channel history, resolves a claim with Gemini, and verifies it against Yahoo Finance MCP statements.
- Portfolio actions prevent concurrent pending mutations for the same governed position.
- Approved paper trades update SQLite only after human confirmation.
- Progress animations and external calls run on daemon workers so Socket Mode remains responsive.

## MCP and Evidence Topology

AlphaChannel exposes its governed tools through the local FastMCP server configured in `mcp_servers.json`. The Gemini brain receives native function declarations derived from those MCP schemas.

- Yahoo Finance fundamentals are obtained from the separate `yahoo-finance-mcp` stdio server and normalized by AlphaChannel.
- SEC EDGAR is an evidence tool, not an external MCP server. It uses official SEC ticker, submissions, and filing archive endpoints by default.
- Portfolio, Git, system status, diagnostics, and dry-run transaction checkpoints are AlphaChannel-local MCP capabilities.
- Normal tools use a reusable persistent MCP session. SEC retrieval uses an isolated one-shot process so a stalled filing request cannot block later agent turns.

## Governance Boundary

Read-only evidence tools execute autonomously. Buy, sell, trim, hedge, and diagnostic actions pause for explicit approval. Users can approve, deny, or edit parameters. AlphaChannel does not submit live brokerage orders.

Trade-related Block Kit events are delivered to `#alphachannel-audit-logs` with UTC time, ticker, user ID, action type, delta details, redaction, and a unique event ID. Production deployments should configure `ALPHACHANNEL_AUDIT_CHANNEL_ID`. If Slack delivery is unavailable, the approved action remains valid and the sanitized event is retained in structured application logs for replay.

## Production Brokerage Integration

The default Hackathon implementation updates a persistent paper portfolio and never submits a live order. For production use, the governed transaction boundary can be extended with an authorized brokerage adapter, including the Webull API, a Robinhood MCP server, or another broker's official API or MCP integration.

Any live integration must preserve the existing control contract:

- Use separately managed, least-privilege brokerage credentials.
- Keep quote retrieval and portfolio reads autonomous only when they are read-only.
- Require explicit Slack approval for every consequential order or position change.
- Revalidate ticker, side, quantity or notional, reference price, and account before execution.
- Record the approval decision, broker response, order identifier, and resulting position delta in the compliance archive.
- Support idempotency so repeated Slack clicks cannot create duplicate orders.

## Data and Security

- Secrets load from `.env` and are never committed.
- SEC requests require an identifiable fair-access identity.
- MCP and provider calls have bounded deadlines.
- Audit details redact tokens, credentials, cookies, and authorization fields.
- Slack history and action payloads are bounded before model use.
- SQLite is appropriate for the single-instance Hackathon deployment; horizontally scaled production requires a managed transactional store.

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
