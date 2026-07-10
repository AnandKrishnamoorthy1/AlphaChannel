<p align="center">
  <img src="assets/alpha-channel-banner.svg" alt="AlphaChannel: Institutional risk intelligence for Slack" width="100%" />
</p>

<h2 align="center">Catch the risk your deal room cannot see.</h2>

<p align="center">
  <strong>AlphaChannel</strong> turns Slack into a stateful institutional risk console. It contrasts internal conviction with live SEC EDGAR disclosures and Yahoo Finance MCP options-market signals, then routes consequential decisions through human approval.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" />
  <img alt="Slack Bolt and Socket Mode" src="https://img.shields.io/badge/Slack-Bolt%20%2B%20Socket%20Mode-4A154B?logo=slack&logoColor=white" />
  <img alt="Qwen" src="https://img.shields.io/badge/AI-Qwen-615CED" />
  <img alt="Model Context Protocol" src="https://img.shields.io/badge/MCP-FastMCP-159570" />
  <img alt="SEC EDGAR" src="https://img.shields.io/badge/data-SEC%20EDGAR-B31B1B" />
  <img alt="Human approval required" src="https://img.shields.io/badge/trades-human%20approval%20required-1F883D" />
</p>

<p align="center"><em>Built for the Slack Agent Builder Challenge.</em></p>

<p align="center">
  <a href="#the-problem">The problem</a> &middot;
  <a href="#see-it-in-action">See it in action</a> &middot;
  <a href="#why-it-is-an-agent">Why it is an agent</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#quick-start">Quick start</a> &middot;
  <a href="#security-and-governance">Security</a>
</p>

---

## The problem

Investment and corporate strategy teams make consequential decisions inside fast-moving Slack threads. Confidence compounds quickly, while the evidence that should challenge it is scattered across filing footnotes, options chains, and disconnected research systems.

That creates an **executive blind spot**: internal consensus can remain bullish while liquidity pressure, litigation exposure, disclosure changes, or volatility skew tells a different story.

AlphaChannel closes that gap where the decision already happens. It retrieves relevant workspace context, asks an LLM to plan the investigation, executes live research through MCP tools, and returns a concise governance scorecard to Slack.

## See it in action

Ask an open-ended question in a channel or the Agent View:

```text
@AlphaChannel Check if ServiceNow is a risky buy right now.
```

AlphaChannel then:

1. Retains the request as part of the Slack thread's conversation state.
2. Lets Qwen select the evidence it needs from registered MCP tool schemas.
3. Fetches live market structure from yFinance and the latest relevant filing from SEC EDGAR.
4. Streams each research step into one updating Slack message without blocking Socket Mode.
5. Cross-examines internal perception against external evidence and validates the result with Pydantic.
6. Presents any trade or diagnostic action as an Approve, Deny, or Edit checkpoint.

The **Portfolio Center** provides the same interaction model across institutional holdings:

```text
@AlphaChannel show my portfolio
@AlphaChannel deep audit NU
```

## Why it is an agent

AlphaChannel is not a command-to-script switchboard. The Qwen planning loop is the central dispatcher.

- **Goal-oriented planning:** the model receives the user's goal, thread memory, workspace perception, and available MCP tool schemas.
- **Autonomous tool choice:** read-only research tools can run without a hardcoded execution graph.
- **Observation loop:** every tool result returns to the model, which decides whether to gather more evidence or synthesize an answer.
- **Multi-turn continuity:** conversation and pending actions are keyed to the Slack channel and thread timestamp.
- **Governed autonomy:** diagnostics and trade checkpoints pause the same reasoning continuation until a user approves, denies, or edits the parameters.
- **Bounded execution:** the loop has a six-iteration cap, typed contracts, tool policy enforcement, and deterministic failure handling.

## Architecture

```text
 Open-ended Slack request
            |
            v
 Slack Bolt + Socket Mode ---- Slack Real-Time Search context
            |
            v
 Qwen planning and reasoning loop <-------------------------+
            |                                                |
            v                                                |
 MCP tool selection                                          |
       +----+-------------------+                            |
       |                        |                            |
       v                        v                            |
 Safe research tools      Consequential tools               |
 yFinance / SEC EDGAR     Diagnostics / trade gate           |
       |                  Approve | Deny | Edit               |
       +------------------------+-----------------------------+
                                |
                                v
 Perception-vs-reality synthesis + Pydantic validation
                                |
                                v
 Slack Block Kit scorecard + non-blocking progress updates
```

| Layer | Responsibility |
| --- | --- |
| [`app.py`](app.py) | Slack events, Agent View, Real-Time Search, Block Kit, and background callbacks |
| [`engine/agent_brain.py`](engine/agent_brain.py) | Qwen planning, MCP tool-call loop, and typed final synthesis |
| [`engine/core_router.py`](engine/core_router.py) | Thread memory, action continuations, and orchestration contracts |
| [`engine/mcp_client.py`](engine/mcp_client.py) | MCP transport, schema exposure, and execution policy enforcement |
| [`engine/mcp_server.py`](engine/mcp_server.py) | Registered finance, filing, repository, diagnostic, and checkpoint tools |
| [`engine/sec_node.py`](engine/sec_node.py) | Live SEC EDGAR resolution, filing retrieval, parsing, and risk extraction |
| [`engine/yfinance_node.py`](engine/yfinance_node.py) | Price, options-chain, implied-volatility, and skew signals |
| [`engine/trading_node.py`](engine/trading_node.py) | Portfolio state and human-governed mitigation checkpoints |

For deeper implementation notes, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Core capabilities

### Live perception-vs-reality analysis

Slack Real-Time Search provides relevant messages, files, and surrounding context as the **Internal Workspace Perception**. Qwen cross-examines that material against live **External Reality** evidence from SEC filings and market structure, explicitly flagging material divergence.

### Multi-threaded progress UI

Slack interactions are acknowledged immediately. SEC, yFinance, and LLM work runs in background threads while `chat.update` streams compact status changes into a single message. The Socket Mode event loop stays responsive throughout the investigation.

### Portfolio monitoring

The Portfolio Center renders institutional holdings as Block Kit layouts with allocation state, Deep Audit, and Trim controls. A deep audit re-enters the same agentic evidence loop rather than returning a static portfolio message.

### Human-in-the-loop guardrails

AlphaChannel does **not** place autonomous trades. Buy, sell, hedge, test, and diagnostic recommendations become explicit checkpoints with editable parameters and a retained audit trail.

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

DASHSCOPE_API_KEY=sk-your-qwen-key
DASHSCOPE_ENDPOINT=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-flash
QWEN_TIMEOUT_SECONDS=30

# SEC requires an identifiable organization and monitored contact address.
SEC_EDGAR_USER_AGENT=AlphaChannel your-email@example.com

LOG_LEVEL=INFO
ALPHACHANNEL_RTS_LIMIT=12
```

`python-dotenv` loads `.env` before Slack or model clients initialize. Process-level environment variables take precedence. Never commit `.env`, access tokens, API keys, action tokens, or workspace content.

### 3. Install and run

Create the Slack app from [`manifest.json`](manifest.json), enable Socket Mode, reinstall after any scope change, then start the agent:

```powershell
python app.py
```

Validate the complete live-data path before a demo:

```powershell
python scripts/golden_path_smoke_test.py
```

The smoke test succeeds only when Qwen autonomously invokes both `yfinance_risk_lookup` and `sec_risk_lookup`, receives valid MCP observations, and returns a validated synthesis.

## Security and governance

- Slack action tokens remain ephemeral request parameters and are never treated as bearer credentials.
- Secrets are read from `.env` or the deployment environment and excluded from structured logs.
- SEC requests use a declared identity, bounded request frequency, and official EDGAR endpoints.
- Qwen calls use configured regional endpoints, timeouts, typed output validation, and clean fallbacks.
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
|-- scripts/
|   `-- golden_path_smoke_test.py
`-- engine/
    |-- agent_brain.py
    |-- core_router.py
    |-- mcp_client.py
    |-- mcp_server.py
    |-- sec_node.py
    |-- yfinance_node.py
    `-- trading_node.py
```

## License

AlphaChannel is released under the [MIT License](LICENSE).

Copyright (c) 2026 Anand Krishnamoorthy.
