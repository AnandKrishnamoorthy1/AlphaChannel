# Deployment Guide — Alibaba Cloud Serverless

Quick deployment steps for Corporate Intelligence Engine on Alibaba Cloud Function Compute.

---

## 🚀 Option 1: Alibaba Cloud Web Console (Recommended - 3 minutes)

**Fastest and most secure — no CLI configuration needed.**

### Step 1: Prepare Your Code
```bash
# From project root, ensure all code is ready
# All Python files, requirements.txt, configs in place
```

### Step 2: Open Alibaba Cloud Console
- Go to: https://console.aliyun.com
- Login with your Alibaba Cloud account

### Step 3: Create Function
1. Search for **Function Compute** → Open
2. Click **Create Function**
3. Select region: **us-east-1** (Virginia) or your preferred region
4. Fill in:
   - **Function Name:** `corporate-intelligence-fastapi`
   - **Runtime:** Python 3.10
   - **Memory:** 1024 MB
   - **Timeout:** 300 seconds

### Step 4: Upload Code
1. Click **Upload Code** → **Upload Folder**
2. Select entire project directory (includes backend.py, requirements.txt, all dependencies)
3. Set **Handler:** `backend.handler`
4. Click **Create**

### Step 5: Add Environment Variables (After Deployment)
1. Go to **Configurations** tab
2. Click **Modify** next to "Environment Variables"
3. Add these 3 secrets:
   - `DASHSCOPE_API_KEY` = Your Qwen API key (from DashScope)
   - `ALPHA_VANTAGE_API_KEY` = Your Alpha Vantage key
   - `PROD_SERVERLESS_DB_URL` = PostgreSQL connection string (if using)
4. Click **Save**

✅ **Done!** Your backend is live. Get the HTTP Trigger URL from the console.

---

## 🚀 Option 2: Serverless Devs CLI (Advanced)

**For infrastructure-as-code and automation.**

### Step 1: Install CLI
```bash
npm install -g @serverless-devs/s
```

### Step 2: Configure Credentials
```bash
# Save to ~/.s/access.yaml (see access.yaml.template)
# On Windows: C:\Users\<YourUsername>\.s\access.yaml
```

Content of `~/.s/access.yaml`:
```yaml
alibaba:
  access_key_id: YOUR_ACCESS_KEY_ID
  access_key_secret: YOUR_ACCESS_KEY_SECRET
  region: us-east-1
```

### Step 3: Deploy
```bash
# From project root
s deploy
```

---

## 🌐 Frontend Deployment — Streamlit Community Cloud (Free)

**Deploy the Streamlit frontend for a beautiful public URL.**

### Step 1: Push to GitHub
- Commit all code to your GitHub repository
- Ensure `frontend.py` is in the root directory

### Step 2: Deploy to Streamlit Cloud
1. Go to: https://share.streamlit.io
2. Login with GitHub
3. Click **New app**
4. Select your repository and `frontend.py`
5. Click **Deploy**

### Step 3: Add Backend URL to Streamlit Secrets
1. In Streamlit cloud dashboard, click **⋮ (menu)** → **Settings**
2. Go to **Secrets**
3. Add:
   ```toml
   BACKEND_API_URL = "https://your-alibaba-function-url.com"
   ```
4. Save

✅ **Done!** Your frontend has a public URL like `https://corporate-intelligence.streamlit.app`

---

## 📊 Architecture

```
User Browser
    ↓
[Streamlit Community Cloud] (Frontend - FREE)
    ↓ (API calls)
[Alibaba Function Compute] (Backend - PAY-PER-USE)
    ↓
[DashScope Qwen API]
[Alpha Vantage]
[SEC EDGAR]
```

**Monthly cost: ~$5.91** (Qwen tokens + RDS storage)

---

## 🔍 Monitoring

### View Logs (CLI)
```bash
s logs -t 100
```

### Monitor Costs (Web Console)
- Alibaba Cloud Console → **Cost Center** → **Billing Details**
- Set budget alert to $10/month

---

## 🚨 Troubleshooting

| Error | Solution |
|---|---|
| `Code config is empty` | Ensure requirements.txt and all Python files are included |
| `handler not found` | Verify `backend.handler` is correct in s.yaml |
| `Timeout after 300s` | Increase timeout in s.yaml or optimize Qwen query |
| `Missing environment variable` | Add via console: Configurations → Environment Variables |

---

## 📚 Documentation

- [Alibaba Cloud Function Compute](https://www.alibabacloud.com/en/products/function-compute)
- [Serverless Devs CLI](https://www.serverless-devs.com/)
- [Streamlit Community Cloud](https://share.streamlit.io)
- Full guide: See `DEPLOYMENT_ALIBABA.md`
