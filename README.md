# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent

AlphaChannel is a production-oriented Slack agent for detecting corporate blind spots before they become governance failures. It transforms internal collaboration signals into a secure, stateful interaction layer, then cross-references workspace sentiment against external evidence from SEC EDGAR filing footnotes and live yFinance options-volatility skews.

The agent uses Model Context Protocol (MCP) handler boundaries to normalize research inputs, routes them through specialized risk nodes, and synthesizes a concise institutional assessment through a Qwen model exposed by an OpenAI-compatible completions API. Recommendations return to Slack as interactive Block Kit controls. No trade is executed automatically: allocation and hedge actions remain explicit human governance checkpoints.

## Why AlphaChannel

Internal teams can become bullish, complacent, or anchored to stale assumptions while external risk signals move in the opposite direction. AlphaChannel measures that divergence by combining:

- Slack Real-time Search context, including relevant messages, files, and surrounding discussion.
- SEC 10-K and 10-Q risk evidence, including footnoted liabilities and disclosure changes.
- yFinance option-chain indicators, including implied volatility and market-skew markers.
- Bull/Bear cross-examination that labels material perception-versus-reality gaps as executive blind spots.
- Human-approved mitigation checkpoints that never submit autonomous trades.

## System Architecture

```text
+---------------------------+
| User Event                |
| @AlphaChannel analyze NU  |
+-------------+-------------+
              |
              v
+---------------------------+
| Slack Socket Mode         |
| Bolt events and actions   |
+-------------+-------------+
              |
              v
+---------------------------+
| Orchestrator              |
| engine/core_router.py     |
+-------------+-------------+
              |
              v
+---------------------------------------------------+
| Specialized Parallel Sub-Nodes                    |
|                                                   |
|  +--------------------+  +----------------------+  |
|  | SEC Research Node  |  | yFinance Risk Node   |  |
|  | engine/sec_node.py |  | engine/yfinance_node |  |
|  +--------------------+  +----------------------+  |
+-------------------------+-------------------------+
                          |
                          v
+---------------------------------------------------+
| Token-Optimized Synthesis                         |
| Qwen via OpenAI-Compatible Completions Layer      |
+-------------------------+-------------------------+
                          |
                          v
+---------------------------------------------------+
| Interactive Block Kit Dynamic UI Updates          |
+-------------------------+-------------------------+
                          |
                          v
+---------------------------------------------------+
| Non-Blocking Background Thread Execution Callbacks|
+---------------------------------------------------+
```

Slack action payloads are acknowledged immediately. Long-running SEC, market-data, and LLM work continues in daemon worker threads that update one Slack message in place, preserving Socket Mode responsiveness.

## Core Features

### Multi-Threaded Progress UI

Deep-audit and allocation-trim actions acknowledge button clicks immediately, start isolated background workers, and stream node-level progress with `chat.update`. Qwen synthesis and Slack API failures are bounded by exception handling and deterministic fallback responses.

### Live Portfolio Monitoring Dashboard

The Portfolio Center presents institutional positions as Block Kit sections with allocation status and per-asset controls. Users can initiate a deep divergence audit or queue defensive trim logic directly from the dashboard.

### Human-in-the-Loop Governance Guardrails

The Trading Node maps buy, sell, and hedge recommendations into decision checkpoints. Every contract records the ticker, internal consensus, external risk markers, verdict, and target mitigation orders. AlphaChannel generates defensive logic but does not autonomously execute a transaction.

### Natural Language Intent Routing

AlphaChannel accepts flexible Slack phrasing while maintaining deterministic routing:

```text
@AlphaChannel evaluate NU
@AlphaChannel analyze SNOW
@AlphaChannel check NOW
@AlphaChannel risk assessment on NU
@AlphaChannel status for SNOW

@AlphaChannel portfolio
@AlphaChannel list all my positions
@AlphaChannel show holdings
@AlphaChannel positions
@AlphaChannel dashboard
```

## Repository Layout

```text
alpha-channel/
|-- app.py                   # Slack Bolt Socket Mode entry point
|-- manifest.json            # Slack events, interactivity, and OAuth scopes
|-- requirements.txt         # Runtime dependencies
|-- .env.example             # Local configuration template
|-- LICENSE                  # MIT license
`-- engine/
    |-- __init__.py
    |-- core_router.py       # Assessment orchestration and synthesis contract
    |-- sec_node.py          # SEC filing risk extraction
    |-- yfinance_node.py     # Options and implied-volatility analysis
    `-- trading_node.py      # Human decision checkpoints and portfolio state
```

`app.py` is the canonical application entry point. The Slack application does not require Streamlit or an HTTP request server.

## Local Setup

### 1. Create the environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

For macOS or Linux, activate with `source venv/bin/activate` and create the configuration with `cp .env.example .env`.

### 2. Configure `.env`

```dotenv
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-socket-mode-token

DASHSCOPE_API_KEY=sk-your-qwen-key
DASHSCOPE_ENDPOINT=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-flash
QWEN_TIMEOUT_SECONDS=30

LOG_LEVEL=INFO
ALPHACHANNEL_RTS_LIMIT=12
```

`python-dotenv` loads this file before Slack, engine, or Qwen clients are initialized. Existing process-level environment variables take precedence because `.env` loading uses `override=False`.

Never commit `.env`, Slack tokens, API keys, action tokens, or workspace message content.

### 3. Install the Slack manifest

Create or update the app from `manifest.json`, enable Socket Mode, and reinstall it whenever OAuth scopes change. The app requires an `xapp-` Socket Mode token and an `xoxb-` bot token with the manifest's granular Real-time Search scopes.

### 4. Run AlphaChannel

```powershell
python app.py
```

Mention the installed app in an authorized Slack channel:

```text
@AlphaChannel risk assessment on NU
@AlphaChannel show holdings
```

## Operational Safety

- Slack RTS action tokens are passed as ephemeral request parameters and are never used as bearer credentials.
- API keys and Slack tokens are read only from the process environment populated by `.env` or the deployment platform.
- Logs record routing metadata and error classes without printing credentials.
- Qwen requests use configured regional endpoints, bounded timeouts, and fallback summaries.
- Interactive trade controls create dry-run checkpoints; execution requires a separately governed broker integration.

## License and Attribution

AlphaChannel is available under the MIT License. The canonical open-source notice is:

```text
Copyright (c) 2026 Anand Krishnamoorthy
SPDX-License-Identifier: MIT

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, subject to the conditions in LICENSE.
```

Source modules use the following identity header:

```python
# AlphaChannel: Institutional Risk Synchronizer & Strategic Governance Agent
# Copyright (c) 2026 Anand Krishnamoorthy
# SPDX-License-Identifier: MIT
#
# Purpose-built for the 2026 Slack Hackathon. See LICENSE for terms.
```

See [LICENSE](LICENSE) for the complete license text.
