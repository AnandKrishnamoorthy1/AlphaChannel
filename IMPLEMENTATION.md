# IMPLEMENTATION NOTES

## Architecture at a Glance

```
Query → Triage → [research | direct_trade | portfolio | general_q]
                       ↓
              approval_execution
                       ↓
                   reporting → END
```

All nodes are pure functions operating on a shared `GraphState` TypedDict. LangGraph compiles the graph once at startup and reuses it for every request.

---

## 1. Qwen Integration (`app/llm/qwen_integration.py`)

**Client:** OpenAI SDK pointed at DashScope international endpoint.

```python
_qwen_client = OpenAI(
    api_key=settings.dashscope_api_key,
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
)
```

**Model:** `qwen3.7-plus` — configured via `QWEN_MODEL` env var.

Three distinct call types:

| Function | Schema enforcement | Temperature | Use |
|---|---|---|---|
| `call_qwen_with_structured_output` | Yes — brace-balanced JSON extraction | configurable | Triage routing, Director verdict |
| `call_qwen_persona` | No — free-form markdown | configurable | Bull / Bear committee turns |
| `call_qwen_for_general_qa` | No — conversational | 0.7 | General Q&A, portfolio narrative |

**Structured output parsing:** The parser scans the response for the first `{` and last `}`, validates brace balance, then calls `json.loads()`. Partial responses are truncated to the last complete JSON object.

---

## 2. 4-Path Triage (`triage_node`)

Qwen returns a `RouterDecision` JSON object:
```json
{
  "ticker": "NVDA",
  "routing_path": "research",
  "trade_action": null,
  "confidence": 0.94,
  "reasoning": "User asked for earnings analysis on a named ticker."
}
```

`route_after_triage()` maps the `routing_path` string to the LangGraph node name. `direct_trade` additionally carries `trade_action` ("BUY" / "SELL") forward in state.

---

## 3. Tri-Agent Investment Committee (`app/agents/investment_committee.py`)

### Shared Evidence Contract

`_format_evidence(ticker, market_data)` renders a fixed-format text block:
```
SHARED MARKET-DATA PAYLOAD (the ONLY facts any persona may cite)

=== PRICE / QUOTE ===
- Ticker: NVDA
- Current Price: $200.09
...

=== FUNDAMENTALS ===
- Sector: Technology
- P/E Ratio (TTM): 45.2
...

=== SEC 10-Q FILINGS ===
- Q-1 (2026-01-26): Revenue $39.33B | Net Income $22.09B ...
```

Every persona system prompt contains: *"Argue ONLY from the market-data payload you are given."* The Director's system prompt adds: *"Reward arguments grounded in the data; discount unsupported claims."*

### Debate Loop

```python
for r in range(1, rounds + 1):
    prior = render(turns_so_far)           # snapshot before this round
    with ThreadPoolExecutor(max_workers=2):
        bull = _bull_turn(r, evidence, prior)   # parallel
        bear = _bear_turn(r, evidence, prior)   # parallel
    turns.extend([bull, bear])
verdict = _director_verdict(ticker, evidence, render(turns))
```

- Bull prompt: *"Rebut the Bear Auditor's single strongest point using the data."*
- Bear prompt: *"Dismantle the Bull Analyst's strongest claim using the data."*
- Each turn is capped at ~180 words (in system prompt) to control cost/latency.

### Verdict Schema

```python
class CommitteeVerdict(BaseModel):
    ticker: str
    verdict: Literal["BUY", "HOLD", "SELL"]
    confidence: float          # 0.0–1.0; close debate → lower
    thesis: str
    bull_points: List[str]
    bear_points: List[str]
    key_risks: List[str]
    dissent: str               # unresolved disagreement, if any
```

---

## 4. Data Fetch Layer (`app/tools/`)

### `external_tools.py` — Alpha Vantage

`get_enriched_stock_data(ticker)` runs three threads concurrently:

1. **GLOBAL_QUOTE** → price, change, change%, volume, trading day
2. **OVERVIEW** → 18 fundamental fields (P/E, EPS, analyst target, margins, beta, sector, …)
3. **SEC 10-Q** (via `sec_tools.py`) → last 2 quarters of audited financials

Any individual failure returns `{}` / mock data. The merge always produces a usable dict.

### `sec_tools.py` — SEC EDGAR 10-Q

Uses `edgartools` to fetch XBRL-parsed quarterly filings. Extracted per quarter: revenue, net income, gross profit, operating income, R&D expense, EPS (diluted), cash, long-term debt, total assets, shareholders' equity, operating cash flow, capex, free cash flow.

Identity header sent to EDGAR: `corporate-intelligence-engine@hackathon.demo` (required by SEC fair-use policy).

---

## 5. Broker Abstraction Layer (`app/trading/`)

### Interface

```python
class BaseBroker(ABC):
    async def get_account_info(self, account_id: str) -> AccountInfo: ...
    async def place_order(self, account_id, ticker, side, quantity=None,
                          order_type=MARKET, price=None, amount_dollars=None) -> TradeOrder: ...
```

### MockSimulationEngine

- Persists state in `simulation_ledger.json` (JSON file, simple and auditable)
- Fetches real Alpha Vantage price at fill time — quantity = `amount_dollars / live_price`
- Validates buying power before accepting BUY orders
- Returns `TradeOrder(status=FILLED, filled_price=..., total_value=...)` on success

### RobinhoodMCPClient

- OAuth 2.0 via `oauth_handler.py`
- Calls Robinhood's Agentic Trading MCP endpoint
- Same `BaseBroker` interface — orchestrator is unaware of which implementation is active

**Selection:** `broker_factory.py` reads `BROKER_TYPE` env var → returns the right instance.

---

## 6. NDJSON Streaming (`backend.py`)

The `/api/analyze/stream` endpoint bridges sync LangGraph with async FastAPI:

```python
log_queue = queue.Queue()
done_event = threading.Event()

def run_graph():
    sink_id = logger.add(lambda m: log_queue.put(m.record["message"]), level="INFO")
    try:
        result = graph.invoke(initial_state)
        container["final_state"] = result
    finally:
        logger.remove(sink_id)
        done_event.set()

thread = threading.Thread(target=run_graph, daemon=True)
thread.start()

async def event_generator():
    while True:
        while not log_queue.empty():
            yield json.dumps({"type": "log", "message": log_queue.get_nowait()}) + "\n"
        if done_event.is_set() and log_queue.empty():
            break
        await asyncio.sleep(0.15)
    yield json.dumps({"type": "result", "result": payload}) + "\n"
```

The `result` payload includes `sources` (raw AV + SEC data) enabling the frontend citation panels.

---

## 7. Portfolio Risk — HHI (`orchestrator.py`, `portfolio_node`)

The portfolio node computes the **Herfindahl-Hirschman Index** for position concentration:

```python
hhi = sum((position_market_value / total_portfolio_value) ** 2 for each position)
```

- `hhi = 1.0` → single stock (maximum concentration)
- `hhi ≤ 0.18` → conventionally diversified (6+ equal positions)
- `0.18 < hhi ≤ 0.5` → moderate concentration
- `hhi > 0.5` → HIGH risk

The HHI value and label are passed to Qwen in the portfolio analysis prompt so the AI narrative reflects the actual quantitative risk.

---

## 8. Citation System

**Backend (`_extract_sources`):** Filters `research_data` to only verifiable raw-API fields. Strips all LLM-generated content. Organises into `{av_quote, av_fundamentals, sec_filings}`.

**Committee report inline markers:** `[1]` on the price line, `[2]` on the "Fundamental Data" header, `[3]`/`[4]` on each SEC 10-Q quarter line.

**Frontend sidebar:** One `st.expander` per source showing every raw field value from the API response — the exact data the LLM was given.

---

## 9. Error Handling Strategy

| Layer | Strategy |
|---|---|
| Alpha Vantage fetch | Returns mock data on any exception; logs warning |
| SEC EDGAR fetch | Returns `{"available": False}` silently; pipeline continues |
| Qwen API call | Raises `RuntimeError` → propagates to `error_count` |
| Broker `place_order` | Returns `TradeOrder(status=FAILED, error_message=...)` |
| Graph node | Increments `state["error_count"]`; builds fallback report; never crashes |
| NDJSON stream | Background thread exception caught → `{"type":"result","status":"error"}` |

---

## 10. Key Libraries

| Library | Version | Purpose |
|---|---|---|
| `langgraph` | ≥0.1 | State machine graph orchestration |
| `pydantic` | v2 | State models, structured output schemas |
| `fastapi` | ≥0.109 | REST + NDJSON streaming backend |
| `streamlit` | ≥1.32 | Frontend UI |
| `openai` | ≥1.0 | Qwen API client (OpenAI-compatible) |
| `loguru` | ≥0.7 | Structured logging with sink injection |
| `edgartools` | ≥2.0 | SEC EDGAR XBRL 10-Q parsing |
| `httpx` | ≥0.25 | Async HTTP for Robinhood MCP client |

---

**Status:** Production-ready agentic system  
**LLM:** Alibaba Qwen `qwen3.7-plus` via DashScope international  
**Trading:** Paper simulation (real AV prices) with live Robinhood MCP option
