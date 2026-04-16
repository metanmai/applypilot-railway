# Railway Chrome Setup Guide

This guide explains how to set up Chrome/Playwright for browser automation on Railway.

## Architecture

ApplyPilot uses **Playwright** for browser automation on Railway. This is different from traditional Selenium/Chrome setups because:

1. **No VNC Required**: Playwright runs headless by default
2. **Persistent Sessions**: Browser profiles are saved to `/data/browser-profile`
3. **Web-based Login**: Use the `/chrome/devtools` endpoint for login
4. **Automatic Retry**: Failed applications are queued for retry

## Setup Steps

### 1. Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and link
railway login
railway link

# Deploy
railway up
```

### 2. Set Environment Variables

In Railway dashboard, set these variables:

```
LLM_URL=https://api.z.ai/api/coding/paas/v4
LLM_API_KEY=your_key_here
LLM_MODEL=glm-4.5
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
ANTHROPIC_AUTH_TOKEN=your_key_here
AUTO_APPLY=false  # Set to true after testing
APPLYPILOT_MIN_SCORE=7
```

### 3. Log In to Job Sites

**Option A: Web Interface (Recommended)**

1. Get your Railway service URL from dashboard
2. Open: `https://your-service.railway.app/chrome/devtools`
3. Click "Log in to LinkedIn" button
4. Complete login in the new tab
5. Repeat for Indeed

**Option B: API Calls**

```bash
# Start browser
curl -X POST https://your-service.railway.app/chrome/start

# Navigate to LinkedIn
curl -X POST https://your-service.railway.app/chrome/navigate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.linkedin.com/login"}'

# Then use /chrome/devtools to complete login
```

### 4. Verify Session

```bash
curl https://your-service.railway.app/chrome/status
```

Should return:
```json
{
  "running": true,
  "browser_type": "playwright_chromium",
  "pages_open": 1
}
```

## API Endpoints

### Browser Management

- `POST /chrome/start` - Start Playwright browser
- `POST /chrome/stop` - Stop browser
- `GET /chrome/status` - Check browser status
- `GET /chrome/json` - List open pages (DevTools-style)

### Navigation & Login

- `POST /chrome/navigate?url=<url>` - Navigate to URL
- `GET /chrome/snapshot` - Get HTML snapshot
- `GET /chrome/devtools` - Web-based login interface
- `POST /chrome/login` - Navigate to login page

### Job Application

- `GET /db/stats` - Database statistics
- `GET /db/jobs?min_score=7` - List high-scoring jobs
- `PUT /db/jobs/<url>` - Update job status after local apply
- `GET /db/files/<url>` - Get tailored resume/cover letter URLs

## Monitoring

### Dashboard

Visit `https://your-service.railway.app/` for real-time dashboard showing:
- Jobs discovered
- Pipeline status
- High-scoring jobs
- Recently applied jobs

### Activity Log

```bash
curl https://your-service.railway.app/activity
```

### Queue Status

```bash
curl https://your-service.railway.app/queue/status
```

## Troubleshooting

### Browser Not Starting

1. Check logs: `railway logs`
2. Verify Playwright installed: Check Dockerfile includes `playwright install chromium`
3. Check data directory mounted: Verify PVC is attached

### Login Session Lost

1. Sessions saved to `/data/browser-profile`
2. Verify PVC is not empty: `railway shell` then `ls -la /data/`
3. Re-login if PVC was reset

### 403 Errors from Job Sites

1. ZipRecruiter may block automated requests
2. Use rotating proxies (configure in environment)
3. Reduce discovery frequency

### Chrome Dependencies Missing

If you see errors about missing libraries, ensure Dockerfile includes all Chrome dependencies listed in the base Dockerfile.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     Railway Service                      │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Flask Server (port 8080)              │ │
│  │  ┌──────────────────────────────────────────────┐  │ │
│  │  │          Pipeline Workers                    │  │ │
│  │  │  Discover → Enrich → Score → Tailor → Cover  │  │ │
│  │  │                                               │  │ │
│  │  │  ┌────────────────────────────────────────┐  │  │ │
│  │  │  │         ApplyWorker (Playwright)       │  │  │ │
│  │  │  │  - Reads login from /data/profile      │  │  │ │
│  │  │  │  - Navigates job sites                  │  │  │ │
│  │  │  │  - Submits applications                 │  │  │ │
│  │  │  └────────────────────────────────────────┘  │  │ │
│  │  └──────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────┘ │
│                                                           │
│  /data (PVC)                                             │
│  ├── browser-profile/  ← Persistent Chrome session       │
│  ├── applypilot.db     ← Job database                    │
│  ├── tailored_resumes/ ← Generated resumes               │
│  └── cover_letters/    ← Generated cover letters         │
└───────────────────────────────────────────────────────────┘
```

## Local Development

To run locally with the same setup:

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set environment
export APPLYPILOT_DATA_DIR=./local-data
export AUTO_APPLY=false

# Run
python main.py
```

Then visit `http://localhost:8080/chrome/devtools` for login.
