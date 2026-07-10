# Deployment & Configuration Guide

Complete instructions for running the Corporate Intelligence Engine locally, with Qwen API integration, Docker containerization, and cloud deployment.

## Deployment Modes

### 🚀 Production: Alibaba Cloud + Streamlit Cloud
**Recommended for hackathons and live demos**
- Frontend: Streamlit Community Cloud (free tier, auto-deployed)
- Backend: Alibaba Cloud Function Compute 3.0 (serverless, scales to zero)
- Cost: ~$0.05/month + Qwen token usage
- See [deployment/DEPLOYMENT_ALIBABA.md](./deployment/DEPLOYMENT_ALIBABA.md) for complete setup

### 💻 Local Development
**Recommended for development and testing**
- Both services run on your local machine
- Port 9000 (backend), Port 8501 (frontend)
- Full debugging and iteration capability
- See below for setup instructions

---

## Local Development Setup

```
┌──────────────────────────────────────────────────────────────────┐
│                    CORPORATE INTELLIGENCE ENGINE                  │
│                     Web Layer Architecture                         │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌─────────────────────────────┐    ┌─────────────────────────┐  │
│  │                             │    │                         │  │
│  │   STREAMLIT FRONTEND        │    │   FASTAPI BACKEND       │  │
│  │   (frontend.py)             │◄──►│   (backend.py)          │  │
│  │                             │    │                         │  │
│  │ • Chat Interface            │    │ • /api/analyze (POST)   │  │
│  │ • Real-time Log Display     │    │ • /health (GET)         │  │
│  │ • Report Rendering          │    │ • /api/routes (GET)     │  │
│  │                             │    │                         │  │
│  │ Port: 8501                  │    │ Port: 8002              │  │
│  │ (via streamlit run)          │    │ (via uvicorn)           │  │
│  └─────────────────────────────┘    └─────────────────────────┘  │
│                                              ▲                    │
│                                              │                    │
│                                    ┌─────────┴─────────┐         │
│                                    │                   │         │
│                        ┌───────────▼──────────┐       │         │
│                        │  ORCHESTRATOR (Graph)│       │         │
│                        │  orchestrator.py     │       │         │
│                        │                      │       │         │
│                        │ • Triage Node        │       │         │
│                        │ • Research Node      │◄──────┘         │
│                        │ • General Q Node     │                 │
│                        │ • Reporting Node     │                 │
│                        └──────────────────────┘                 │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘

Data Flow:
1. User enters query in Streamlit frontend
2. Frontend sends HTTP POST to /api/analyze
3. Backend receives request and builds state graph
4. Graph executes orchestrator logic
5. Backend captures all logs and execution results
6. Frontend receives response with logs and report
7. Frontend displays logs in real-time status container
8. Frontend renders final markdown report
```

## Installation

### Prerequisites

- Python 3.8 or higher
- pip or conda package manager
- Two terminal windows (one for backend, one for frontend)

### Step 1: Install Dependencies

```bash
# Navigate to project directory
cd c:\AI_Agents\corporate-intelligence-engine

# Install all required packages
pip install -r requirements.txt
```

**Required Packages:**
- `fastapi==0.109.0` - Web framework for backend
- `uvicorn==0.27.0` - ASGI server
- `streamlit==1.32.0` - Frontend framework
- `requests==2.31.0` - HTTP client for frontend
- `pydantic==2.6.1` - Data validation
- `langgraph==0.1.39` - State machine orchestration
- `python-multipart==0.0.6` - Form data handling

### Step 2: Verify Installation

```bash
# Check Python version
python --version

# Verify key packages
pip show fastapi streamlit uvicorn
```

## Running the Application

### Terminal 1: Start the FastAPI Backend

```bash
# Navigate to project directory
cd c:\AI_Agents\corporate-intelligence-engine

# Start the Uvicorn server with auto-reload (port 9000 for local dev)
uvicorn backend:app --reload --host 0.0.0.0 --port 9000
```

**Expected Output:**
```
INFO:     Uvicorn running on http://0.0.0.0:8002
INFO:     Application startup complete
================================================================================
CORPORATE INTELLIGENCE ENGINE - FASTAPI BACKEND
================================================================================
Server started at: 2026-06-13T14:30:00.000000
Available endpoints:
  - POST /api/analyze
  - GET /health
  - GET /api/routes
  - GET /docs (API documentation)
================================================================================
```

**What to do next:** Keep this terminal running and open a new terminal for the frontend.

### Terminal 2: Start the Streamlit Frontend

```bash
# Open a NEW terminal window
# Navigate to project directory
cd c:\AI_Agents\corporate-intelligence-engine

# Start the Streamlit app
streamlit run frontend.py
```

**Expected Output:**
```
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://192.168.x.x:8501

  For better performance, install the Watchdog module:
    pip install watchdog
```

**The Streamlit app will automatically open in your default browser** at `http://localhost:8501`

---

## Using the Application

### 1. Check Backend Status

When you first load the frontend, check the sidebar to verify the backend is online:
- ✓ **Green checkmark** = Backend is running and healthy
- ✗ **Red X** = Backend is not accessible (start it in Terminal 1)

### 2. Enter a Query

In the chat input box at the bottom, type one of the following:

**Research Queries:**
```
Analyze the latest earnings for NVDA
Show me Tesla stock forecast
What's the current price of AAPL?
```

**General Questions:**
```
What are the top machine learning frameworks in 2024?
Explain quantitative analysis
What is a stock dividend?
```

### 3. View Real-Time Agent Activity

When you submit a query, you'll see:
1. **Your message** displayed in a chat bubble
2. **Status container** showing "Orchestrating AI Agents..."
3. **Agent execution trace** with logs from each step:
   - [ENTERING TRIAGE NODE]
   - LLM routing decision
   - [CONDITIONAL EDGE] path selection
   - [ENTERING RESEARCH/GENERAL_Q NODE]
   - Processing steps
4. **Status updates** to "Analysis Complete!"
5. **Final report** rendered in Markdown format

### 4. View Execution Metrics

Above the final report, you'll see:
- **Routing Decision**: Whether it was routed to Research or General Q
- **Execution Time**: Total time in milliseconds
- **Log Entries**: Number of captured log lines

---

## API Endpoints Documentation

### 1. Health Check

**Endpoint:** `GET /health`

**Description:** Check if the backend is running

**Response:**
```json
{
  "status": "healthy",
  "service": "corporate-intelligence-engine",
  "timestamp": "2026-06-13T14:30:00.000000"
}
```

---

### 2. Analyze (Main Endpoint)

**Endpoint:** `POST /api/analyze`

**Description:** Execute the orchestrator graph with user input

**Request Body:**
```json
{
  "user_input": "Analyze the latest earnings for NVDA"
}
```

**Response:**
```json
{
  "status": "success",
  "routing_decision": "research",
  "logs": [
    "[INFO] [ENTERING TRIAGE NODE]",
    "[INFO] User Input: Analyze the latest earnings for NVDA",
    "[INFO] Invoking LLM for structured routing decision...",
    "[INFO] LLM Response: {\"ticker\": \"NVDA\", ...}",
    "[INFO] [CONDITIONAL ROUTING] Decision: research",
    "[INFO] [CONDITIONAL EDGE] Taking RESEARCH path",
    "[INFO] [ENTERING RESEARCH NODE]",
    "[INFO] Research complete. 14 data points collected.",
    "[INFO] [REPORTING NODE COMPLETE]"
  ],
  "report_markdown": "# Financial Research Report: NVDA\n\n## Company Overview\n...",
  "error_message": "",
  "execution_time_ms": 245.3
}
```

---

### 3. Get Available Routes

**Endpoint:** `GET /api/routes`

**Description:** Get information about available routing paths

**Response:**
```json
{
  "routes": [
    {
      "path": "research",
      "description": "Stock research and financial analysis",
      "triggers": ["ticker", "earnings", "stock price", "analyst rating"]
    },
    {
      "path": "general_q",
      "description": "General knowledge questions",
      "triggers": ["frameworks", "tutorial", "how to", "what is"]
    }
  ]
}
```

---

### 4. API Documentation

**Access Interactive Docs:**
- Swagger UI: http://localhost:8002/docs
- ReDoc: http://localhost:8002/redoc

---

## Testing the API with curl

### Test 1: Health Check

```bash
curl http://localhost:8002/health
```

### Test 2: Analysis Request

```bash
curl -X POST http://localhost:8002/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"user_input\": \"Analyze NVDA earnings\"}"
```

### Test 3: Get Routes

```bash
curl http://localhost:8002/api/routes
```

---

## Troubleshooting

### Issue: "Backend Connection Error" in Streamlit

**Solution:**
1. Ensure the FastAPI backend is running in Terminal 1
2. Verify it's running on `http://localhost:9000`
3. Reload the Streamlit page (Ctrl+R or Command+R)

### Issue: Backend won't start (port 9000 already in use)

**Solution:**
```bash
# Find process using port 9000
netstat -ano | findstr :9000

# Kill the process (replace PID)
taskkill /PID <PID> /F

# Restart backend
uvicorn backend:app --reload
```

### Issue: Streamlit won't start (Port 8501 already in use)

**Solution:**
```bash
# Start on a different port
streamlit run frontend.py --server.port 8502
```

### Issue: "ModuleNotFoundError: No module named 'orchestrator'"

**Solution:**
Ensure you're running the commands from the project directory:
```bash
cd c:\AI_Agents\corporate-intelligence-engine
```

### Issue: Slow or hanging requests

**Solution:**
1. The orchestrator logs many messages; this is normal
2. Increase timeout in `frontend.py` if needed (currently 60 seconds)
3. Check that your system has sufficient resources

---

## Performance Metrics

### Typical Execution Times

| Component | Time (ms) |
|-----------|-----------|
| Triage Node | 5-10 |
| Research Node | 10-20 |
| Reporting Node | 5-10 |
| **Total (Research Path)** | **30-50** |
| **Total (General Q Path)** | **20-40** |

### Log Volume

- **Triage Node:** 10-15 log lines
- **Research Node:** 15-20 log lines
- **Reporting Node:** 5-10 log lines
- **Total:** 30-45 log lines per request

---

## Production Deployment

For production deployment, follow these steps:

### 1. Update Backend Configuration

**backend.py** changes:
```python
# Change CORS to restrict to your Streamlit domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Change reload to False
run(
    app,
    reload=False,  # Disable auto-reload in production
    log_level="info",
)
```

### 2. Update Frontend Configuration

**frontend.py** changes:
```python
# Change API URL to your production domain
API_BASE_URL = "https://api.yourdomain.com"
```

### 3. Deploy Backend

Using Gunicorn + Uvicorn:
```bash
pip install gunicorn
gunicorn backend:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8002
```

### 4. Deploy Frontend

Using Streamlit Cloud:
```bash
# Push your repo to GitHub
git push origin main

# Deploy via Streamlit Cloud (streamlit.io/cloud)
# Connect your GitHub repo
```

Or deploy to AWS/Azure/GCP using Docker:

**Dockerfile for Backend:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Development Workflow

### Hot Reload

Both FastAPI and Streamlit support hot reload:
- **Backend:** Changes to `backend.py` auto-reload (via `--reload`)
- **Frontend:** Changes to `frontend.py` auto-reload

Just save your changes and refresh the browser.

### Debugging

#### View Backend Logs

```bash
# Tail logs from the backend terminal
# All logs with [INFO], [ERROR], etc. are visible
```

#### View Frontend Logs

```bash
# Browser console (F12 or right-click → Inspect)
# Check for JavaScript errors

# Terminal where you ran streamlit:
# Streamlit debug messages
```

#### Enable Debug Mode

**backend.py:**
```python
run(
    app,
    log_level="debug",  # More verbose logging
)
```

---

## Docker Deployment

### Build and Run with Docker Compose

```bash
# Build all images
docker-compose build

# Start all services
docker-compose up

# Access:
# - Backend: http://localhost:8002
# - Frontend: http://localhost:8501
# - API Docs: http://localhost:8002/docs
```

### Services Included

**docker-compose.yml** includes:

| Service | Port | Description |
|---------|------|-------------|
| **backend** | 8000 | FastAPI application |
| **frontend** | 8501 | Streamlit application |
| **redis** | 6379 | Cache & queue (optional) |

### Production Docker

For production:

```bash
# Use production compose file
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# View logs
docker-compose logs -f backend
docker-compose logs -f frontend

# Stop services
docker-compose down
```

---

## Configuration Management

### Environment Variables

The application uses Pydantic `BaseSettings` to load configuration from `.env` files.

**Key Settings:**

```bash
# ============================================================================
# Required: Qwen LLM
# ============================================================================
QWEN_API_KEY=sk-xxxxxxxxxxxxxxxx
QWEN_MODEL=qwen-max
QWEN_TEMPERATURE=0.3
QWEN_TOP_P=0.85

# ============================================================================
# Backend
# ============================================================================
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_RELOAD=false           # Always false in production
BACKEND_LOG_LEVEL=info

# ============================================================================
# Frontend
# ============================================================================
FRONTEND_HOST=localhost
FRONTEND_PORT=8501

# ============================================================================
# Environment
# ============================================================================
ENVIRONMENT=production         # development or production
DEBUG=false

# ============================================================================
# Optional: Financial APIs (for integration)
# ============================================================================
ALPHA_VANTAGE_API_KEY=your_key
FINNHUB_API_KEY=your_key

# ============================================================================
# Optional: Monitoring
# ============================================================================
LANGSMITH_API_KEY=your_key
SENTRY_DSN=your_dsn
```

### Three-Tier Configuration

| Layer | File | Committed | Purpose |
|-------|------|-----------|---------|
| **Template** | `.env.example` | ✓ YES | Safe defaults |
| **Local** | `.env` | ✗ NO | Local secrets |
| **Production** | AWS Secrets Manager | - | Production secrets |

### Usage in Code

```python
from config import settings

# Access configuration
print(settings.qwen_api_key)
print(settings.backend_port)
print(settings.is_production)  # Computed property
```

---

## Monitoring & Observability

### Application Logs

**Backend Logs** (terminal output):
```
[INFO] [ENTERING TRIAGE NODE]
[INFO] Invoking Qwen LLM for structured routing decision...
[INFO] Qwen Response: {...}
[INFO] [ENTERING RESEARCH NODE]
[INFO] Research complete. 14 data points collected.
```

**Enable Debug Logging:**
```bash
BACKEND_LOG_LEVEL=debug python backend.py
```

### Health Checks

**Docker Health Check:**
```bash
curl http://localhost:8002/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "service": "corporate-intelligence-engine"
}
```

### Performance Monitoring

**Track these metrics:**
- Request duration (ms)
- Token usage per request
- Qwen API costs
- Error rate
- Uptime

---

## Scaling Considerations

### Horizontal Scaling

```bash
# Use Gunicorn with multiple workers
gunicorn backend:app -w 4 -k uvicorn.workers.UvicornWorker

# Or use load balancer (Nginx, AWS ALB)
# upstream backend {
#   server backend1:8002;
#   server backend2:8002;
# }
```

### Vertical Scaling

**For high-volume:**
- Increase Qwen model to `qwen-max-longcontext`
- Add caching layer (Redis)
- Implement request queuing
- Use async I/O throughout

### Cost Optimization

1. **Model Selection:**
   - Use `qwen-turbo` for simple queries
   - Use `qwen-plus` for balanced needs
   - Use `qwen-max` only when needed

2. **Caching:**
   - Cache triage decisions for similar queries
   - Cache research data by ticker

3. **Rate Limiting:**
   - Implement per-user rate limits
   - Use sliding window algorithm

---

## Troubleshooting

### Backend Won't Start

```bash
# Check if port is in use
lsof -i :8002  # macOS/Linux
netstat -ano | findstr :8002  # Windows

# Use different port
BACKEND_PORT=8001 python backend.py
```

### Frontend Can't Connect to Backend

```bash
# Check backend is running
curl http://localhost:9000/health

# Check firewall
# Ensure port 9000 is open
```

### Qwen API Errors

```bash
# Verify API key is set
echo $QWEN_API_KEY

# Check DashScope dashboard for:
# - Valid API key
# - Remaining quota
# - Service status
```

### High Memory Usage

```bash
# Monitor process
ps aux | grep python  # macOS/Linux
tasklist | findstr python  # Windows

# Reduce Streamlit cache size
# Increase workers (use async)
```

---

## Next Steps

1. **Test locally** - Follow "Running the Application" above
2. **Deploy to Docker** - Use docker-compose for consistent environments
3. **Monitor in production** - Set up logging and alerting
4. **Scale as needed** - Implement caching, async, load balancing
5. **Secure** - Add authentication, API rate limiting, HTTPS

---

## Support Resources

- **FastAPI:** https://fastapi.tiangolo.com
- **Streamlit:** https://docs.streamlit.io
- **LangGraph:** https://langchain-ai.github.io/langgraph/
- **Docker:** https://docs.docker.com
- **Qwen:** https://qwen.aliyun.com
- **DashScope:** https://dashscope.aliyun.com
- `LogCapture` - Custom logging handler to capture logs
- `/api/analyze` - Main analysis endpoint
- `build_graph()` - Imports and executes orchestrator

**Data Flow:**
```
HTTP POST /api/analyze
    ↓
Parse + Validate AnalysisRequest
    ↓
Build state graph from orchestrator.py
    ↓
Execute graph with user_input
    ↓
Capture all logs during execution
    ↓
Return AnalysisResponse with logs + report
    ↓
HTTP 200 + JSON response
```

### Frontend (Streamlit)

**File:** `frontend.py`

**Key Components:**
- Chat message display
- `st.chat_input()` - User query input
- `st.status()` - Real-time execution container
- `requests.post()` - API communication
- Markdown report rendering

**Data Flow:**
```
User types in chat input
    ↓
Display user message in chat
    ↓
Send POST request to /api/analyze
    ↓
Show status container "Orchestrating..."
    ↓
For each log line in response:
    Display log in status container
    ↓
Close status container with ✅
    ↓
Render final report_markdown
```

### Orchestrator (LangGraph)

**File:** `orchestrator.py`

**State Machine:**
```
START
  ↓
[Triage Node]
  ├─ Extracts ticker
  └─ Decides: research or general_q
  ↓
[Conditional Router]
  ├─→ [Research Node] → [Reporting Node] → END
  └─→ [General Q Node] → [Reporting Node] → END
```

---

## Next Steps & Enhancements

### Short Term

1. **Add file upload support**
   ```python
   # Allow users to upload CSV/PDF files for analysis
   uploaded_file = st.file_uploader("Upload data")
   ```

2. **Add export functionality**
   ```python
   # Export reports as PDF, DOCX
   st.download_button("Download Report", report_data)
   ```

3. **Add conversation history**
   ```python
   # Store analysis history in session
   # Allow multi-turn conversations
   ```

### Medium Term

4. **Real LLM integration**
   ```python
   # Replace mock routing with GPT-4
   # Use Anthropic structured output
   ```

5. **Financial data APIs**
   ```python
   # Connect to real APIs: yfinance, Alpha Vantage
   # Cache results with Redis
   ```

6. **Database persistence**
   ```python
   # Store analysis history
   # Track ticker analysis trends
   ```

### Long Term

7. **Multi-user support**
   ```python
   # User authentication
   # Separate workspaces per user
   ```

8. **Advanced analytics**
   ```python
   # Comparative analysis
   # Time-series forecasting
   # Sentiment analysis
   ```

9. **Monitoring & observability**
   ```python
   # LangSmith integration
   # Datadog metrics
   # Error tracking (Sentry)
   ```

---

## Support & Documentation

- **FastAPI Docs:** https://fastapi.tiangolo.com
- **Streamlit Docs:** https://docs.streamlit.io
- **LangGraph Docs:** https://python.langchain.com/en/latest/modules/agents/agent_executors/langgraph_document
- **Pydantic Docs:** https://docs.pydantic.dev

---

**Status:** ✅ Web Layer Complete  
**Built with:** FastAPI + Streamlit + LangGraph  
**Last Updated:** 2026-06-13
