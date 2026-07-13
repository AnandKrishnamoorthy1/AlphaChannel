<p align="center">
  <img src="assets/alpha-channel-banner.svg" alt="AlphaChannel: Institutional risk intelligence for Slack" width="100%" />
</p>

<h2 align="center">Catch the risk your deal room cannot see.</h2>

<p align="center">
  <strong>AlphaChannel</strong> is a multi-agent AI harness that turns Slack into a stateful institutional risk console. It contrasts internal conviction with live SEC EDGAR disclosures and Yahoo Finance MCP valuation and growth fundamentals, then routes consequential decisions through human approval.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" />
  <img alt="Slack Bolt and Socket Mode" src="https://img.shields.io/badge/Slack-Bolt%20%2B%20Socket%20Mode-4A154B?logo=slack&logoColor=white" />
  <img alt="Gemini" src="https://img.shields.io/badge/AI-Gemini-4285F4" />
  <img alt="Model Context Protocol" src="https://img.shields.io/badge/MCP-FastMCP-159570" />
  <img alt="Google Cloud VM" src="https://img.shields.io/badge/deployment-Google%20Cloud%20VM-4285F4?logo=googlecloud&logoColor=white" />
  <img alt="SEC EDGAR" src="https://img.shields.io/badge/data-SEC%20EDGAR-B31B1B" />
  <img alt="Human approval required" src="https://img.shields.io/badge/trades-human%20approval%20required-1F883D" />
</p>

<p align="center"><em>Built for the Slack Agent Builder Challenge.</em></p>

<p align="center">
  <a href="#the-problem">The problem</a> &middot;
  <a href="#see-it-in-action">See it in action</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#deployment">Deployment</a>
</p>

---

## Challenge technologies

AlphaChannel leverages all three qualifying technologies required by the Slack Agent Builder Challenge:

- **Slack AI assistant capabilities:** provide stateful agent interaction, multi-turn thread continuity, live progress updates, and human approval checkpoints.
- **Slack Real-Time Search API:** retrieves workspace-grounded messages, files, and conversation context to construct the Internal Workspace Perception evidence payload.
- **Model Context Protocol server integrations:** let the Gemini orchestration brain autonomously invoke SEC EDGAR, Yahoo Finance, portfolio, repository, and governance tools through typed MCP contracts.

## The problem

Investment and corporate strategy teams make consequential decisions inside fast-moving Slack threads. Confidence compounds quickly, while the evidence that should challenge it is scattered across filing footnotes, valuation metrics, growth data, and disconnected research systems.

That creates an **executive blind spot**: internal consensus can remain bullish while liquidity pressure, litigation exposure, disclosure changes, or volatility skew tells a different story.

AlphaChannel closes that gap where the decision already happens. It retrieves relevant workspace context, asks an LLM to plan the investigation, executes live research through MCP tools, and returns a concise governance scorecard to Slack.

## See it in action

Ask an open-ended question in a channel or the Agent View:

```text
@AlphaChannel Check if ServiceNow is a risky buy right now.
```

AlphaChannel then:

1. Retains the request as part of the Slack thread's conversation state.
2. Lets Gemini select the evidence it needs from registered MCP tool schemas.
3. Fetches live market fundamentals from Yahoo Finance MCP and the latest relevant filing from SEC EDGAR.
4. Streams each research step into one updating Slack message without blocking Socket Mode.
5. Cross-examines internal perception against external evidence and validates the result with Pydantic.
6. Presents any trade or diagnostic action as an Approve, Deny, or Edit checkpoint.

The **Portfolio Center** provides the same interaction model across institutional holdings:

```text
@AlphaChannel show my portfolio
@AlphaChannel deep audit NU
```

AlphaChannel can also resolve and verify a financial claim from the preceding Slack conversation:

```text
Analyst: Amazon revenue is amazing.
Analyst: The company generated about $180 billion for Q1 2026.
Analyst: @AlphaChannel fact check this
```

The Fact-Checking Engine retrieves the recent thread context, resolves Amazon to `AMZN`, extracts the revenue claim and reporting period, and compares it with evidence retrieved through Yahoo Finance MCP and SEC EDGAR before returning a sourced verification result in the same thread.

For a consequential portfolio request, ask:

```text
@AlphaChannel Buy $500 of Amazon stock
```

AlphaChannel resolves Amazon to `AMZN`, gathers the reference market price, and pauses at an Approve, Deny, or Edit checkpoint. Approval updates the persistent paper portfolio and compliance audit trail; it does not submit an automatic live brokerage order.

## Agentic Architecture

`GeminiOrchestrationBrain` is AlphaChannel's central reasoning and execution dispatcher. It interprets user goals, selects governed MCP tools, evaluates returned evidence, and continues reasoning until it can produce a validated response or must pause for human approval.

- **Goal-oriented planning:** Gemini receives the user's goal, thread memory, workspace perception, and registered MCP tool schemas.
- **Dynamic tool selection:** Gemini autonomously selects read-only research tools without relying on a fixed execution graph.
- **Observation loop:** every tool result returns to Gemini, which decides whether to gather more evidence or synthesize the final response.
- **Multi-turn continuity:** conversation state and pending actions are retained by Slack channel and thread timestamp.
- **Governed autonomy:** consequential diagnostics and transaction actions pause for users to approve, deny, or edit their parameters.
- **Bounded execution:** iteration limits, typed contracts, tool policies, timeouts, and deterministic fallbacks constrain the agent's behavior.

## Architecture

![AlphaChannel architecture showing Slack interaction, Gemini orchestration, governed MCP tools, policy controls, validation, and Block Kit responses](assets/Slack_Architecture.png)

| Layer | Responsibility |
| --- | --- |
| [`app.py`](app.py) | Slack events, Agent View, Real-Time Search, Block Kit, and background callbacks |
| [`engine/core_router.py`](engine/core_router.py) | Gemini-native planning, MCP tool-call loop, thread memory, action continuations, and typed final synthesis |
| [`engine/mcp_client.py`](engine/mcp_client.py) | MCP transport, schema exposure, and execution policy enforcement |
| [`engine/mcp_server.py`](engine/mcp_server.py) | Registered finance, filing, repository, diagnostic, and checkpoint tools |
| [`engine/sec_node.py`](engine/sec_node.py) | Live SEC EDGAR resolution, filing retrieval, parsing, and risk extraction |
| [`engine/yfinance_node.py`](engine/yfinance_node.py) | Investment fundamentals plus optional options and implied-volatility signals |
| [`engine/trading_node.py`](engine/trading_node.py) | Portfolio state and human-governed mitigation checkpoints |

The runtime brain is `GeminiOrchestrationBrain` in [`engine/core_router.py`](engine/core_router.py). It dynamically selects registered MCP tools, receives their results, continues multi-turn reasoning, and emits validated structured synthesis.

For deeper implementation notes, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Core capabilities

### Live perception-vs-reality analysis

Slack Real-Time Search provides relevant messages, files, and surrounding context as the **Internal Workspace Perception**. Gemini cross-examines that material against live **External Reality** evidence from SEC filings and Yahoo Finance MCP fundamentals, explicitly flagging material divergence.

### Multi-threaded progress UI

Slack interactions are acknowledged immediately. SEC, yFinance, and LLM work runs in background threads while `chat.update` streams compact status changes into a single message. The Socket Mode event loop stays responsive throughout the investigation.

### Portfolio monitoring

The Portfolio Center renders institutional holdings as Block Kit layouts with allocation state, Deep Audit, and Trim controls. A deep audit re-enters the same agentic evidence loop rather than returning a static portfolio message.

The Portfolio Monitoring skill evaluates each refreshed snapshot against explicit policy limits: stop-loss below `-20%` versus average cost, profit alert above `+50%` versus average cost, single-stock concentration above `15%` of portfolio equity, and sector or stock-group concentration above `30%`. Alerts produce recommendations only; any sell or rebalance remains an approval-gated dry-run checkpoint.

### Human-in-the-loop guardrails

AlphaChannel does **not** place autonomous trades. Buy, sell, hedge, test, and diagnostic recommendations become explicit checkpoints with editable parameters and a retained audit trail.

### Brokerage integrations

Users can connect a separately governed brokerage platform such as Robinhood, Webull, or another supported trading service through its official API or an MCP server. AlphaChannel's default implementation remains a dry-run checkpoint: broker credentials are not required, no live order is submitted, and any future API/MCP execution adapter must preserve explicit human approval, scoped permissions, and an auditable order trail.

### Portfolio persistence and live prices

The Portfolio Center stores account cash and equity positions in SQLite at `data/portfolio.db` by default. The file is ignored by Git and should live on a persistent application volume in a single-instance deployment. Each dashboard refresh attempts to replace stored reference prices with Yahoo Finance MCP prices and labels the source in Slack. For multi-instance or high-write production deployments, use a managed transactional repository instead of sharing a SQLite file across containers.

## Deployment

AlphaChannel is deployed on a Google Cloud VM as a persistent Slack Socket Mode service. The VM hosts the Slack Bolt application, Gemini orchestration runtime, governed MCP processes, direct SEC EDGAR retrieval, Yahoo Finance MCP client, approval callbacks, and SQLite paper-portfolio state.

Socket Mode establishes an outbound connection to Slack, so AlphaChannel does not require a public inbound Slack webhook endpoint. Deployment credentials remain outside the repository and are loaded from the VM environment. The current SQLite and process-local conversation state are appropriate for this single-instance Hackathon deployment; a horizontally scaled production topology should use managed transactional storage and a shared conversation-state service.

## Quick start

### 1. Create the environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS or Linux, use `source venv/bin/activate` and `cp .env.example .env`.

### 2. Configure credentials

```dotenv
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-socket-mode-token

GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-flash-latest

# SEC fair-access policy requires an identifiable organization and monitored contact address.
EDGAR_IDENTITY=AlphaChannel your-email@example.com
SEC_EDGAR_BACKEND=raw

LOG_LEVEL=INFO
ALPHACHANNEL_RTS_LIMIT=12
```

`python-dotenv` loads `.env` before Slack or model clients initialize. Process-level environment variables take precedence. Never commit `.env`, access tokens, API keys, action tokens, or workspace content.

### 3. Install and run

Create the Slack app from [`manifest.json`](manifest.json), enable Socket Mode, reinstall after any scope change, then start the agent:

```powershell
python app.py
```

Validate the complete live-data path before Hackathon judging:

```powershell
python scripts/golden_path_smoke_test.py
```

The smoke test succeeds only when Gemini autonomously invokes `yfinance_fundamental_lookup` and `sec_risk_lookup`, receives valid MCP observations, and returns a validated synthesis.

## Security and governance

- Slack action tokens remain ephemeral request parameters and are never treated as bearer credentials.
- Secrets are read from `.env` or the deployment environment and excluded from structured logs.
- SEC filings are retrieved from official EDGAR ticker, submissions, and filing archive endpoints with a declared identity, bounded request frequency, and isolated execution. EdgarTools remains an optional backend.
- Gemini calls use configured model settings, timeouts, typed output validation, and clean fallbacks.
- MCP policies distinguish autonomous read-only tools from consequential tools that require approval.
- Trading controls are dry-run governance checkpoints; a separately governed broker integration is required for execution.
- Thread state is process-local for the hackathon runtime. A multi-instance deployment should use Redis or another shared checkpoint store.

## Repository map

```text
AlphaChannel/
|-- app.py
|-- manifest.json
|-- mcp_servers.json
|-- requirements.txt
|-- .env.example
|-- assets/
|-- scripts/
|   `-- golden_path_smoke_test.py
|-- skills/
`-- engine/
    |-- agent_brain.py
    |-- core_router.py
    |-- mcp_client.py
    |-- mcp_server.py
    |-- portfolio_store.py
    |-- sec_node.py
    |-- yahoo_finance_mcp_client.py
    |-- yfinance_node.py
    `-- trading_node.py
```

## License

AlphaChannel is released under the [MIT License](LICENSE).

Copyright (c) 2026 Anand Krishnamoorthy.
