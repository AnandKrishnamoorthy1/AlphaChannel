# Installation & Setup Guide

Complete setup instructions for the Corporate Intelligence Engine with Qwen LLM.

## Deployment Options

### Option A: Production Deployment (Recommended)
- **Frontend:** Streamlit Community Cloud (free, auto-deploys from GitHub)
- **Backend:** Alibaba Cloud Function Compute 3.0 (serverless, ~$0.05/month)
- See [DEPLOYMENT_ALIBABA.md](./deployment/DEPLOYMENT_ALIBABA.md) for full instructions

### Option B: Local Development
- Both backend and frontend run on your machine
- Perfect for development and testing
- Follow the instructions below

---

## Local Development Setup

### Prerequisites

- Python 3.12
- pip or conda package manager
- Internet connection (for Qwen API calls)
- **Qwen API Key** - Get it from [Alibaba DashScope](https://dashscope.aliyun.com/)

---

## Getting Your Qwen API Key

### Step 1: Sign Up
1. Visit [https://dashscope.aliyun.com/](https://dashscope.aliyun.com/)
2. Create account or log in with your Alibaba ID
3. Complete verification if required

### Step 2: Generate API Key
1. Navigate to API Keys section
2. Click "Create New API Key"
3. Copy your API key (keep it secret!)
4. Add to `.env` file (see below)

---

## Installation

### Option 1: Quick Setup (Recommended)

```bash
# Clone repository
cd corporate-intelligence-engine-qwen

# Create virtual environment
python -m venv venv
source venv/bin/activate          # macOS/Linux
# OR
venv\Scripts\activate             # Windows

# Install dependencies
pip install -r requirements.txt

# Create environment file
cp .env.example .env

# Edit .env and add your Qwen API key
# QWEN_API_KEY=sk-xxxxxxxxxxxx
```

### Option 2: Conda Environment

```bash
# Create conda environment
conda create -n qwen-engine python=3.10
conda activate qwen-engine

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your QWEN_API_KEY
```

---

## Environment Configuration

### Create `.env` File

```bash
cp .env.example .env
```

Edit `.env` with these required and optional settings:

```bash
# ============================================================================
# REQUIRED: Qwen LLM (Alibaba DashScope International)
# ============================================================================

# Get from: https://dashscope-intl.aliyuncs.com/
DASHSCOPE_API_KEY=your_dashscope_api_key_here

# Model — qwen3.7-plus is recommended for this project
QWEN_MODEL=qwen3.7-plus
DASHSCOPE_ENDPOINT=https://dashscope-intl.aliyuncs.com/compatible-mode/v1

# Model sampling parameters
QWEN_TEMPERATURE=0.3
QWEN_TOP_P=0.65

# ============================================================================
# REQUIRED: Financial Data
# ============================================================================

# Get free key from: https://www.alphavantage.co/support/#api-key
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key_here

# ============================================================================
# Backend Configuration
# ============================================================================

BACKEND_HOST=0.0.0.0
BACKEND_PORT=8002
BACKEND_LOG_LEVEL=info

# ============================================================================
# Frontend Configuration
# ============================================================================

FRONTEND_HOST=localhost
FRONTEND_PORT=8501

# ============================================================================
# Optional: Broker Mode
# ============================================================================

# simulation = paper trading with $10k balance (default, safe for demos)
# robinhood  = live Robinhood trading (requires OAuth credentials below)
BROKER_TYPE=simulation

# Required only when BROKER_TYPE=robinhood
# ROBINHOOD_CLIENT_ID=your_client_id
# ROBINHOOD_CLIENT_SECRET=your_client_secret
# ROBINHOOD_ACCOUNT_ID=your_account_id

# ============================================================================
# Development Settings
# ============================================================================

DEBUG=true
ENVIRONMENT=development
CORS_ORIGINS=http://localhost:8501,http://localhost:3000

# ============================================================================
# Optional: Financial APIs (for future integration)
# ============================================================================

# ALPHA_VANTAGE_API_KEY=your_key_here
# FINNHUB_API_KEY=your_key_here
```

---

## Starting the Application

### Terminal 1: Backend Server

```bash
python backend.py
```

Expected output:
```
[INFO] Qwen initialized with model: qwen-max
[INFO] Starting server on http://0.0.0.0:8002
```

### Terminal 2: Frontend UI

```bash
streamlit run frontend.py
```

Expected output:
```
  You can now view your Streamlit app in your browser.
  URL: http://localhost:8501
```

### Access Application

Open browser → `http://localhost:8501`

---

## Qwen Models

| Model | Notes |
|---|---|
| **`qwen3.7-plus`** | **Used by this project** — strong reasoning, good cost/quality balance |
| `qwen-turbo` | Fastest, cheapest — suitable for quick testing |
| `qwen-plus` | Mid-tier quality |
| `qwen-max` | Highest quality, higher cost |

Set in `.env`:
```bash
QWEN_MODEL=qwen3.7-plus   # default for this project
```

---

## API Configuration Options

### Temperature (Creativity)

`QWEN_TEMPERATURE=0.3` (range: 0.0 - 2.0, lower = more deterministic)

- **0.0** = Deterministic, factual responses
- **0.7** = Balanced (default)
- **2.0** = Very creative, varied responses

For financial analysis, use lower values (0.3-0.7).

### Top P (Diversity)

`QWEN_TOP_P=0.65` (range: 0.0 - 1.0, lower = more deterministic)

- **0.85** = Default (balanced diversity)
- **0.5** = More focused
- **0.95** = More diverse

---

## Verifying Installation

### Test Backend

```bash
curl http://localhost:8002/health
```

Expected response:
```json
{"status": "ok"}
```

### Test Qwen Integration

```bash
curl -X POST http://localhost:8002/api/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"user_input": "What is a PE ratio?"}'
```

Expected: JSON response with analysis.

### Test Frontend

Open `http://localhost:8501` and submit a query. You should see:
1. Query routed to Research or General Q
2. Qwen processing indicator
3. AI-generated response

---

## Troubleshooting

### "QWEN_API_KEY not set"

**Problem:** Missing API key in environment

**Solutions:**
1. Check `.env` file exists in project root
2. Verify `QWEN_API_KEY=xxx` is added
3. Restart backend after editing `.env`
4. Try: `echo $QWEN_API_KEY` to verify it's loaded

### "Qwen API error: 401"

**Problem:** Invalid or expired API key

**Solutions:**
1. Check API key is correct in `.env`
2. Verify key in DashScope dashboard
3. Check if key has remaining quota
4. Generate a new key if expired

### "Connection timeout"

**Problem:** Can't reach Qwen API

**Solutions:**
1. Check internet connection
2. Verify DashScope isn't blocked by firewall
3. Try different Qwen model (some regions vary)
4. Check DashScope service status

### "JSON parsing error"

**Problem:** Qwen response couldn't be parsed

**Solutions:**
1. Check logs for raw response
2. Verify Qwen API is working: test in DashScope console
3. Try with different query (may be too complex)
4. Reduce `QWEN_TEMPERATURE` for more consistent format

### "Rate limit exceeded"

**Problem:** Too many API calls

**Solutions:**
1. Check DashScope dashboard for quota
2. Wait a moment before retrying
3. Upgrade plan if needed
4. Cache responses for common queries

### Backend won't start

**Problem:** Port already in use or import error

**Solutions:**
```bash
# Check if port 8002 is in use
netstat -tuln | grep 8000  # macOS/Linux
netstat -ano | findstr :8002  # Windows

# Use different port
BACKEND_PORT=8001 python backend.py

# Reinstall dependencies
pip install --force-reinstall -r requirements.txt
```

### Frontend shows "Connection refused"

**Problem:** Backend not running

**Solutions:**
1. Start backend first: `python backend.py`
2. Verify backend is on `http://localhost:8002`
3. Check `BACKEND_PORT` matches in `.env`
4. Look for backend error messages in Terminal 1

---

## Development Tips

### Enable Debug Logging

```bash
BACKEND_LOG_LEVEL=debug python backend.py
```

This shows detailed Qwen API calls and responses.

### Test Individual Nodes

```python
from orchestrator import build_graph

graph = build_graph()
result = graph.invoke({
    "user_input": "Analyze Tesla",
    "current_target_ticker": "",
    "routing_decision": "",
    "research_data": {},
    "report_markdown": "",
    "error_count": 0,
})

print(result["report_markdown"])
```

### Monitor Qwen Usage

In DashScope console:
1. Check tokens consumed
2. Monitor API calls
3. Track costs
4. View error logs

---

## Cost Estimation

Approximate costs per query:

| Operation | Tokens | Cost |
|-----------|--------|------|
| Triage (routing) | 100-200 | ~$0.0002 |
| Research analysis | 1500-2500 | ~$0.003 |
| General Q&A | 800-1500 | ~$0.002 |

**Example:** 100 queries/day ≈ $0.50/day

Check [DashScope pricing](https://dashscope.aliyun.com/pricing) for current rates.

---

## Next Steps

1. **Test the application** - Try some queries to verify Qwen is working
2. **Integrate financial APIs** - Add Alpha Vantage or Finnhub for real data
3. **Customize prompts** - Edit `app/prompts/system_prompts.py` for your needs
4. **Deploy to production** - See `DEPLOYMENT.md`

---

## Support Resources

- **Qwen Docs:** [https://qwen.aliyun.com](https://qwen.aliyun.com)
- **DashScope API:** [https://dashscope.aliyun.com/api-details/public-docs](https://dashscope.aliyun.com/api-details/public-docs)
- **LangGraph:** [https://langchain-ai.github.io/langgraph/](https://langchain-ai.github.io/langgraph/)
- **FastAPI:** [https://fastapi.tiangolo.com](https://fastapi.tiangolo.com)
- **Streamlit:** [https://docs.streamlit.io](https://docs.streamlit.io)
# OPTIONAL: LLM INTEGRATION (for real LLM, uncomment and fill in)
# ============================================================================
# OPENAI_API_KEY=sk_test_...
# OPENAI_MODEL=gpt-4-turbo

# ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-3-opus-20240229

# ============================================================================
# OPTIONAL: DATABASE (for persistence)
# ============================================================================
# DATABASE_URL=postgresql://user:password@localhost:5432/corporate_intelligence
# REDIS_URL=redis://localhost:6379/0

# ============================================================================
# OPTIONAL: AWS (for cloud deployment)
# ============================================================================
# AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# S3_BUCKET_NAME=corporate-intelligence-reports

# ============================================================================
# OPTIONAL: MONITORING (for production)
# ============================================================================
# LANGSMITH_API_KEY=...
# LANGSMITH_PROJECT=corporate-intelligence-engine
# SENTRY_DSN=...
```

### ⚠️ Important Security Notes

1. **Never commit `.env`** - It's in `.gitignore`
2. **Always use `.env.example`** as template
3. **Never hardcode secrets** - Use environment variables
4. **For production**, use proper secret management:
   - AWS Secrets Manager
   - Azure Key Vault
   - HashiCorp Vault
   - Kubernetes Secrets

---

## Using Configuration in Code

### Access Configuration

```python
from config import settings

# Access any setting
print(settings.backend_port)        # 8000
print(settings.openai_api_key)      # None (if not set)
print(settings.environment)         # "development"
print(settings.is_development)      # True
print(settings.is_production)       # False
```

### Check if Optional Services are Configured

```python
from config import settings

if settings.has_openai:
    # Use OpenAI
    pass

if settings.has_database:
    # Connect to database
    pass

if settings.has_redis:
    # Use Redis cache
    pass
```

---

## Development Workflow

### 1. Making Changes

The backend automatically reloads on code changes (when `BACKEND_RELOAD=true`):

```bash
# Edit orchestrator.py
# Save the file
# Uvicorn automatically reloads
```

Streamlit also auto-reloads:

```bash
# Edit frontend.py
# Save the file
# Streamlit automatically reruns
```

### 2. Testing Changes

```bash
# Test backend
python test_backend.py

# Test specific endpoint
curl http://localhost:8002/health

```bash
# Tail log file
tail -f logs/app.log

# Or view in terminal where uvicorn is running
```

---

## Docker Development

### Build Images

```bash
# Build backend image
docker build -f docker/Dockerfile -t corporate-intelligence-backend .

# Build frontend image  
docker build -f docker/Dockerfile.frontend -t corporate-intelligence-frontend .
```

### Run with Docker Compose

```bash
# Start all services
docker-compose -f docker/docker-compose.yml up

# Start in background
docker-compose -f docker/docker-compose.yml up -d

# View logs
docker-compose logs -f backend
docker-compose logs -f frontend

# Stop services
docker-compose down
```

---

## Adding LLM Integration

### OpenAI Setup

1. Get API key from https://platform.openai.com/api-keys
2. Add to `.env`:
   ```bash
   OPENAI_API_KEY=sk_test_your_key_here
   OPENAI_MODEL=gpt-4-turbo
   ```
3. In code:
   ```python
   from config import settings
   from openai import OpenAI
   
   client = OpenAI(api_key=settings.openai_api_key)
   ```

### Anthropic Setup

1. Get API key from https://console.anthropic.com
2. Add to `.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-your_key_here
   ```
3. In code:
   ```python
   from config import settings
   from anthropic import Anthropic
   
   client = Anthropic(api_key=settings.anthropic_api_key)
   ```

---

## Database Setup (Optional)

### PostgreSQL

```bash
# Install (macOS)
brew install postgresql

# Install (Ubuntu)
sudo apt-get install postgresql postgresql-contrib

# Start service
psql postgres

# Create database
CREATE DATABASE corporate_intelligence;
```

Add to `.env`:
```bash
DATABASE_URL=postgresql://postgres:password@localhost:5432/corporate_intelligence
```

### Redis

```bash
# Install (macOS)
brew install redis

# Install (Ubuntu)
sudo apt-get install redis-server

# Start service
redis-server

# Test connection
redis-cli ping  # Should return PONG
```

Add to `.env`:
```bash
REDIS_URL=redis://localhost:6379/0
```

---

## VS Code Configuration

### Recommended Extensions

```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.vscode-pylance",
    "charliermarsh.ruff",
    "ms-python.black-formatter",
    "ms-python.debugpy",
    "httpyac.httpyac"
  ]
}
```

### Launch Configuration (.vscode/launch.json)

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: Backend",
      "type": "python",
      "request": "launch",
      "module": "uvicorn",
      "args": ["backend:app", "--reload"],
      "jinja": true,
      "justMyCode": true
    },
    {
      "name": "Python: Tests",
      "type": "python",
      "request": "launch",
      "module": "pytest",
      "args": ["tests/", "-v"],
      "jinja": true
    }
  ]
}
```

---

## Common Issues & Solutions

### Issue: Import Error - No module named 'config'

**Solution:**
```bash
# Ensure you're in the project root
cd c:\AI_Agents\corporate-intelligence-engine

# Install pydantic-settings
pip install pydantic-settings

# Verify PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Issue: port 8002 already in use

**Solution:**
```bash
# Kill process using port 8002
lsof -ti:8002 | xargs kill -9  # macOS/Linux

# Windows
netstat -ano | findstr :8002
taskkill /PID <PID> /F

# Use different port
uvicorn backend:app --port 8001
```

### Issue: `.env` file not loading

**Solution:**
```bash
# Verify .env exists in project root
ls -la .env

# Restart backend/frontend after creating .env
# Stop uvicorn and streamlit
# Start them again
```

### Issue: Streamlit can't connect to backend

**Solution:**
1. Verify backend is running: `curl http://localhost:8002/health`
2. Check `CORS_ORIGINS` in `.env` includes `http://localhost:8501`
3. Reload frontend page

---

## Performance Tips

1. **Use PyPy** for faster Python execution
2. **Enable Redis** for caching queries
3. **Use PostgreSQL** instead of SQLite for production
4. **Profile with py-spy**: `pip install py-spy`
5. **Monitor with New Relic/DataDog** in production

---

## Next Steps

1. ✅ Create `.env` from `.env.example`
2. ✅ Install dependencies: `pip install -r requirements.txt`
3. ✅ Start backend: `uvicorn backend:app --reload`
4. ✅ Start frontend: `streamlit run frontend.py`
5. ✅ Test at `http://localhost:8501`
6. Make changes and see hot-reload in action
7. Add real LLM integration when ready
8. Set up database if needed
9. Deploy using Docker

---

## Getting Help

- Check [ARCHITECTURE.md](./ARCHITECTURE.md) for folder structure
- See [DEPLOYMENT.md](./DEPLOYMENT.md) for production setup
- Review [QUICKSTART.md](./QUICKSTART.md) for API usage
- Read configuration source: [config/settings.py](./config/settings.py)

---

**Status:** ✅ Development environment ready  
**Last Updated:** 2026-06-14
