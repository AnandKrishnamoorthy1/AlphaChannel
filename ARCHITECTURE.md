# Architecture — Corporate Intelligence Engine (Qwen Edition)

## System Overview

A multi-layer agentic system with strict separation between the API surface, the orchestration graph, the agent logic, and the data/broker integrations.

```
┌──────────────────────────────────────────────────────────────────────┐
│  frontend.py  (Streamlit, port 8501)                                 │
│  • NDJSON stream consumer — renders live agent steps                 │
│  • Sidebar: portfolio panel, 📚 Sources citation panels             │
│  • Approve / reject trade UI                                         │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ HTTP  /  NDJSON stream
┌──────────────────────────────▼───────────────────────────────────────┐
│  backend.py  (FastAPI, port 8002)                                    │
│  • /api/analyze/stream  — background thread + queue → NDJSON        │
│  • /api/approve/{id}    — stores approval in PendingTradeStore       │
│  • /api/execute/{id}    — calls broker.place_order()                 │
│  • /api/portfolio       — AV-enriched live positions                 │
│  • /health                                                           │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│  orchestrator.py  (LangGraph StateGraph)                             │
│                                                                      │
│  GraphState (TypedDict)  ←─ mutable state passed between nodes      │
│                                                                      │
│  triage_node  ──►  route_after_triage()                             │
│                       │                                              │
│          ┌────────────┼──────────────┬──────────────┐              │
│          ▼            ▼              ▼              ▼              │
│    research_node  trading_node  portfolio_node  general_q_node      │
│          │            │              │              │              │
│          └────────────┴──────────────┴──────────────┘              │
│                               │                                      │
│                   approval_execution_node                            │
│                               │                                      │
│                       reporting_node ──► END                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Component Deep-Dives

### `orchestrator.py` — LangGraph State Machine

**State model:** `GraphState` (TypedDict) carries `user_input`, `routing_decision`, `current_target_ticker`, `research_data`, `report_markdown`, `pending_approval`, `approval_status`, `error_count`.

**Nodes:**

| Node | Responsibility |
|---|---|
| `triage_node` | Qwen structured JSON output → `routing_path` + `ticker` + `confidence` |
| `research_node` | 3-way parallel data fetch → `run_investment_committee()` |
| `trading_node` | Parses dollar/share amount → creates `ApprovalRequest` |
| `portfolio_node` | Broker account + live prices → HHI + Qwen risk analysis |
| `general_q_node` | Freeform Qwen Q&A |
| `approval_execution_node` | Calls `broker.place_order()` when `approval_status == "approved"` |
| `reporting_node` | Pass-through (report already built by analysis nodes) |

**Routing:** `route_after_triage()` reads `state["routing_decision"]` and returns the node name string. LangGraph evaluates this as a conditional edge.

---

### `app/agents/investment_committee.py` — Tri-Agent Adversarial Committee

**Design pattern:** Shared-evidence adversarial debate.

```
_format_evidence(ticker, market_data)
    └─► SHARED MARKET-DATA PAYLOAD  (the only facts any persona may cite)
            ==> AV quote, AV fundamentals, SEC 10-Q filings

InvestmentCommittee.deliberate(ticker, market_data)
    for round in 1..N:
        prior_transcript = render(turns_so_far)
        ThreadPoolExecutor(max_workers=2):
            bull_future = _bull_turn(round, evidence, prior_transcript)
            bear_future = _bear_turn(round, evidence, prior_transcript)
        turns.extend([bull.result(), bear.result()])
    verdict = _director_verdict(ticker, evidence, full_transcript)
```

**Persona temperature stratification:**
- Bull / Bear: `temperature=0.5` (balanced exploration with determinism)
- Director: `temperature=0.2` (highly deterministic verdicts)

**Director uses `call_qwen_with_structured_output`** with a strict JSON schema (`CommitteeVerdict`) — guarantees machine-readable verdict, confidence, bull/bear points, risks, dissent.

**Hallucination prevention:** `_format_evidence` builds the "SHARED MARKET-DATA PAYLOAD" header. All three persona system prompts contain the rule: *"Argue ONLY from the market-data payload you are given. Cite exact numbers."*

---

### `app/llm/qwen_integration.py` — Qwen API Wrapper

Three call types, each with a distinct purpose:

| Function | Used by | Notes |
|---|---|---|
| `call_qwen_with_structured_output(system, user, schema)` | Triage, Director verdict | JSON schema enforcement via brace-balanced extraction |
| `call_qwen_persona(system, user, temperature)` | Bull / Bear turns | Free-form markdown argument |
| `call_qwen_for_general_qa(user)` | `general_q_node`, portfolio analysis | Conversational response |

**Client:** `OpenAI(api_key=..., base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")` — uses the OpenAI SDK against the DashScope international endpoint. Model: `qwen3.7-plus`.

---

### `app/tools/external_tools.py` — Data Fetch Layer

`FinancialDataTools.get_enriched_stock_data(ticker)`:

```python
with ThreadPoolExecutor(max_workers=3) as pool:
    quote_future        = pool.submit(get_stock_data, ticker)        # GLOBAL_QUOTE
    fundamentals_future = pool.submit(get_fundamental_data, ticker)  # OVERVIEW
    sec_future          = pool.submit(get_sec_filings, ticker, 2)    # 10-Q XBRL
    # all three resolve concurrently
result = merge(quote, fundamentals, sec)
```

Alpha Vantage fields returned from OVERVIEW: P/E, forward P/E, EPS, analyst target, 52-week range, profit margin, earnings growth YoY, revenue growth YoY, beta, ROE, dividend yield, book value, sector, industry, market cap.

Graceful degradation: any fetch failure returns `{}` / mock data — the pipeline continues with partial data.

---

### `app/tools/sec_tools.py` — SEC EDGAR Integration

Uses [`edgartools`](https://github.com/dgunning/edgartools) to fetch the last N 10-Q filings via EDGAR XBRL. Parsed metrics per quarter: revenue, net income, gross profit, operating income, R&D expense, EPS (diluted), cash, long-term debt, total assets, shareholders' equity, operating cash flow, capex, free cash flow.

Falls silent on any error — SEC data is enrichment, not critical path.

---

### `app/trading/` — Broker Abstraction Layer

**Pattern:** Abstract `BaseBroker` interface with two concrete implementations.

```
BaseBroker (ABC)
    ├── MockSimulationEngine    ← default (BROKER_TYPE=simulation)
    │       • JSON ledger (simulation_ledger.json)
    │       • Real Alpha Vantage prices for fill execution
    │       • $10,000 starting balance
    │       • Tracks positions, cash, order history
    │
    └── RobinhoodMCPClient      ← production (BROKER_TYPE=robinhood)
            • OAuth 2.0 flow via oauth_handler.py
            • Robinhood Agentic Trading MCP endpoint
```

`broker_factory.py` reads `BROKER_TYPE` from env and returns the correct instance. The orchestrator never imports a concrete class — it always uses `get_broker_for_user()`.

**Execution flow (approved trade):**
1. `approval_execution_node` calls `asyncio.run(broker.place_order(account_id, ticker, side, amount_dollars=...))`
2. MockSimulationEngine fetches real AV price, computes shares, validates cash, writes to ledger
3. Returns `TradeOrder(order_id, filled_price, total_value, status=FILLED)`
4. Report is generated from real fill details — not hardcoded strings

---

### `backend.py` — NDJSON Streaming Architecture

The `/api/analyze/stream` endpoint bridges a synchronous LangGraph graph run with async HTTP:

```
async event_generator():
    thread = Thread(target=run_graph_sync, args=(container, log_queue, done_event))
    thread.start()

    while True:
        while not log_queue.empty():
            msg = log_queue.get_nowait()
            yield json.dumps({"type": "log", "message": msg}) + "\n"
        if done_event.is_set() and log_queue.empty():
            break
        await asyncio.sleep(0.15)

    yield json.dumps({"type": "result", "result": payload}) + "\n"

return StreamingResponse(event_generator(), media_type="application/x-ndjson")
```

Loguru sink in the background thread writes to `log_queue`. The async generator drains it every 150ms and yields NDJSON events.

The `result` payload includes a `sources` field (built by `_extract_sources()`) containing the raw AV and SEC data, enabling the frontend citation panels.

---

### `frontend.py` — Streaming Consumer + Citation Sidebar

**Stream consumption:**
```python
for line in response.iter_lines():
    event = json.loads(line)
    if event["type"] == "log":
        steps.append(_format_agent_step(...))
        log_placeholder.markdown(render_steps(steps))
    elif event["type"] == "result":
        final_result = event["result"]
```

**Citation system:** `st.session_state.last_sources` stores `{av_quote, av_fundamentals, sec_filings}` from the result. The "📚 Sources" sidebar section renders one `st.expander` per data source, showing the exact raw values fed to the LLM.

---

## Folder Structure (Actual)

```
corporate-intelligence-engine-qwen/
├── backend.py                  # FastAPI app + NDJSON streaming endpoint
├── frontend.py                 # Streamlit app + sidebar citation panels
├── orchestrator.py             # LangGraph state machine + all nodes
│
├── app/
│   ├── agents/
│   │   └── investment_committee.py
│   ├── llm/
│   │   └── qwen_integration.py
│   ├── tools/
│   │   ├── external_tools.py
│   │   └── sec_tools.py
│   ├── trading/
│   │   ├── broker_interface.py
│   │   ├── mock_simulation_engine.py
│   │   ├── robinhood_client.py
│   │   ├── broker_factory.py
│   │   └── oauth_handler.py
│   └── prompts/
│       └── system_prompts.py
│
├── config/
│   └── settings.py             # Pydantic BaseSettings (all env vars typed)
│
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.frontend
│   └── docker-compose.yml
│
└── tests/
    └── test_backend.py
```

---

## Key Design Decisions

**Why LangGraph instead of a plain function chain?**
State is immutable between nodes, routing is data-driven (not hardcoded), and the graph is inspectable. Adding a new node (e.g., `news_node`) requires zero changes to existing nodes.

**Why a shared evidence contract in the committee?**
Prevents persona drift — without it, a sufficiently capable LLM will confabulate data to strengthen its assigned position. The shared payload is the "single source of truth" that the Director can use to audit each persona's claims.

**Why parallel Bull/Bear per round instead of sequential?**
Sequential means each agent *reacts* to the other within the same round, creating sycophancy. Parallel means each agent draws from the *prior* round's transcript — the rebuttal dynamic is preserved but neither agent can simply agree with whatever the other just said.

**Why `temperature=0.4` for the Director?**
Lower temperature reduces creative "hallucination" of a verdict not supported by the transcript. The Director's job is adjudication, not creativity.

**Why MockSimulationEngine instead of always-live Robinhood?**
Risk management. A public demo should never accidentally execute real trades. The broker abstraction (`BaseBroker`) means switching to live trading is a one-line env var change: `BROKER_TYPE=robinhood`.


```
corporate-intelligence-engine/
│
├── 📄 Root Configuration Files
│   ├── .env                    # ⚠️ NEVER COMMIT - Local environment variables
│   ├── .env.example            # ✓ Safe template - version controlled
│   ├── requirements.txt        # Python dependencies
│   ├── .gitignore             # Prevents accidental commits
│   ├── README.md              # Project overview
│   ├── QUICKSTART.md          # Setup instructions
│   ├── DEPLOYMENT.md          # Production deployment guide
│   ├── ARCHITECTURE.md        # This file
│   └── LICENSE
│
├── 📁 config/                 # ⚙️ CONFIGURATION MANAGEMENT
│   ├── __init__.py           # Package init
│   ├── settings.py           # Pydantic settings with env var loading
│   └── README.md             # Configuration guide
│
├── 📁 app/                    # 🧠 MAIN APPLICATION CODE
│   ├── __init__.py           # Package init
│   │
│   ├── 📁 models/            # 📊 PYDANTIC MODELS & SCHEMAS
│   │   ├── __init__.py       # Exports all models
│   │   └── README.md         # Model documentation
│   │   # Future:
│   │   # ├── agent_state.py  # State machine models
│   │   # └── llm_schemas.py  # LLM structured output schemas
│   │
│   ├── 📁 api/               # 🌐 REST API ENDPOINTS
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI app initialization
│   │   ├── routes.py         # Endpoint definitions (future)
│   │   └── README.md
│   │   # Future:
│   │   # ├── health.py       # Health check endpoints
│   │   # ├── analysis.py     # Analysis endpoints
│   │   # └── webhooks.py     # Webhook endpoints
│   │
│   ├── 📁 orchestrator/      # 🤖 STATE MACHINE & AGENTS
│   │   ├── __init__.py
│   │   ├── graph.py          # LangGraph state machine
│   │   ├── nodes.py          # Node implementations
│   │   └── README.md
│   │   # Future:
│   │   # ├── triage_node.py  # Router node
│   │   # ├── research_node.py
│   │   # ├── general_q_node.py
│   │   # └── reporting_node.py
│   │
│   ├── 📁 prompts/           # 📝 LLM SYSTEM PROMPTS
│   │   ├── __init__.py
│   │   ├── system_prompts.py # System message templates
│   │   └── README.md
│   │   # Future:
│   │   # ├── triage.txt
│   │   # ├── research.txt
│   │   # └── general_qa.txt
│   │
│   └── 📁 utils/             # 🛠️ UTILITY FUNCTIONS (future)
│       ├── __init__.py
│       ├── logging.py        # Logging configuration
│       └── cache.py          # Caching utilities
│
├── 📁 docker/                # 🐳 CONTAINERIZATION
│   ├── Dockerfile            # Backend container
│   ├── Dockerfile.frontend   # Frontend container
│   ├── docker-compose.yml    # Multi-container orchestration
│   └── README.md             # Docker deployment guide
│
├── 📁 tests/                 # ✅ TESTING
│   ├── __init__.py
│   ├── test_backend.py       # Backend unit/integration tests
│   ├── test_frontend.py      # Frontend tests (future)
│   └── conftest.py           # Pytest fixtures (future)
│
├── 📁 logs/                  # 📋 APPLICATION LOGS (generated)
│   └── app.log              # Generated at runtime
│
├── 📄 Root Application Files (Entry Points)
│   ├── backend.py            # FastAPI application entry point
│   ├── frontend.py           # Streamlit application entry point
│   ├── orchestrator.py       # State machine (to be moved to app/orchestrator/)
│   └── test_backend.py       # Backend tests
│
└── 📁 docs/                  # 📚 DOCUMENTATION (future)
    ├── API.md                # API documentation
    ├── AGENTS.md             # Agent architecture
    └── CONTRIBUTING.md       # Contribution guidelines
```

---

## Folder Structure Explanation

### 1. **Root Level** 🏠
Files at the root for quick access to essential project information and entry points.

**What goes here:**
- Entry point scripts (`backend.py`, `frontend.py`)
- Main configuration files (`.env`, `requirements.txt`)
- Documentation (`README.md`, `QUICKSTART.md`)
- Project metadata (`LICENSE`, `.gitignore`)

**Why:**
- Quick identification of project type
- Easy setup for developers
- Standard Python project layout

---

### 2. **config/** ⚙️
**Purpose:** Centralized environment and configuration management.

**Current files:**
- `settings.py` - Pydantic BaseSettings for environment variables

**Best practices:**
- ✅ Uses Pydantic for validation
- ✅ Environment variables via `.env` file
- ✅ Defaults for development, overrides for production
- ✅ Never commit actual `.env` (only `.env.example`)
- ✅ Type-safe configuration access

**Usage:**
```python
from config import settings

print(settings.openai_api_key)  # Loaded from .env or env var
print(settings.backend_port)    # Loaded or uses default
```

---

### 3. **app/** 🧠
**Purpose:** Main application code organized by function.

#### **app/models/** 📊
Pydantic models for:
- Request/response validation
- Database schemas (future)
- LLM structured output schemas

```python
from app.models import AnalysisRequest, AnalysisResponse
```

#### **app/api/** 🌐
FastAPI routes and endpoints:
- Currently in `backend.py` (should move here)
- Health checks, analysis endpoints, webhooks
- Request validation and error handling

```python
from fastapi import FastAPI
app = FastAPI()

@app.post("/api/analyze")
def analyze(request: AnalysisRequest):
    ...
```

#### **app/orchestrator/** 🤖
LangGraph state machine and agents:
- `graph.py` - State machine definition
- `nodes.py` - Individual node implementations
- Each node is a pure function
- Deterministic execution flow

```python
from app.orchestrator import build_graph

graph = build_graph()
result = graph.invoke(initial_state)
```

#### **app/prompts/** 📝
System prompts for LLM agents:
- Triage/routing prompts
- Research analysis prompts
- General QA prompts
- System messages

```python
from app.prompts import TRIAGE_PROMPT, RESEARCH_PROMPT

prompt = TRIAGE_PROMPT.format(user_input="Analyze NVDA")
```

---

### 4. **docker/** 🐳
**Purpose:** Containerization for local and production deployment.

**Files:**
- `Dockerfile` - Backend container
- `Dockerfile.frontend` - Frontend container  
- `docker-compose.yml` - Multi-container orchestration
  - Backend service
  - Frontend service
  - Redis service (optional, for caching)

**Usage:**
```bash
# Local development
docker-compose up

# Production
docker-compose -f docker-compose.yml up -d
```

---

### 5. **tests/** ✅
**Purpose:** Test suite for all components.

**Current:**
- `test_backend.py` - Backend API tests

**Future:**
- `test_orchestrator.py` - State machine tests
- `test_frontend.py` - Frontend UI tests
- `conftest.py` - Pytest fixtures

---

### 6. **logs/** 📋
**Purpose:** Application logs (generated at runtime).

**Contents:**
- `app.log` - Main application logs
- Never committed to version control

---

## Environment Configuration Strategy

### Development (.env.local)
```bash
# .env.local (never commit)
ENVIRONMENT=development
DEBUG=true
OPENAI_API_KEY=sk_test_your_key
```

### Template (.env.example)
```bash
# .env.example (ALWAYS commit this)
ENVIRONMENT=development
DEBUG=false
OPENAI_API_KEY=your_key_here
```

### Production (.env in secret manager)
```bash
# Store in AWS Secrets Manager, Azure KeyVault, etc.
ENVIRONMENT=production
DEBUG=false
OPENAI_API_KEY=sk_live_your_key
```

---

## Import Patterns

### ✅ Recommended Imports

**From config:**
```python
from config import settings
```

**From models:**
```python
from app.models import AnalysisRequest, AnalysisResponse
```

**From orchestrator:**
```python
from app.orchestrator import build_graph
```

**From prompts:**
```python
from app.prompts import TRIAGE_PROMPT, RESEARCH_PROMPT
```

### ❌ Avoid

```python
# Don't do this
import sys
sys.path.insert(0, '/some/weird/path')
```

---

## Adding New Features

### Add a new agent node:
1. Create `app/orchestrator/my_node.py`
2. Define node function with state validation
3. Add to `app/orchestrator/__init__.py` exports
4. Add to graph in `app/orchestrator/graph.py`

### Add a new API endpoint:
1. Create `app/api/my_route.py`
2. Define request/response models in `app/models/`
3. Add route to `backend.py` or create route handler
4. Test in `tests/test_backend.py`

### Add new prompts:
1. Add to `app/prompts/system_prompts.py`
2. Export in `app/prompts/__init__.py`
3. Use in agent nodes

---

## Production Checklist

- [ ] `.env` file created from `.env.example`
- [ ] All required API keys configured
- [ ] Database URL configured (if using)
- [ ] Redis URL configured (if caching)
- [ ] Docker images built and tested
- [ ] Docker compose tested locally
- [ ] Logs directory writable
- [ ] CORS origins configured correctly
- [ ] SSL certificates configured (if HTTPS)
- [ ] Monitoring/alerting configured

---

## Security Best Practices

✅ **DO:**
- Store all secrets in `.env` (not committed)
- Use `config/settings.py` for all configuration
- Never log sensitive data
- Validate all inputs with Pydantic
- Use environment-specific configs
- Rotate API keys regularly
- Use HTTPS in production

❌ **DON'T:**
- Commit `.env` files to git
- Hardcode API keys
- Use default/weak secrets in production
- Log user data or financial information
- Expose internal error messages
- Trust user input without validation

---

## References

- [12-Factor App Methodology](https://12factor.net/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/api/pydantic_settings/)
- [FastAPI Best Practices](https://fastapi.tiangolo.com/deployment/)
- [Python Project Structure](https://docs.python-guide.org/writing/structure/)

---
