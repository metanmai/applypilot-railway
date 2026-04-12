# Deploy to Railway

## Quick Start

1. **Update seed data** with your local ApplyPilot data:
   ```bash
   cp ~/.applypilot/applypilot.db seed-data/
   cp ~/.applypilot/profile.json seed-data/
   cp ~/.applypilot/searches.yaml seed-data/
   cp ~/.applypilot/resume.txt seed-data/
   cp ~/.applypilot/.env seed-data/
   ```

2. **Install Railway CLI:**
   ```bash
   npm install -g @railway/cli
   ```

3. **Login:**
   ```bash
   railway login
   ```

4. **Initialize project:**
   ```bash
   cd /home/metanmai/Code/applypilot-railway
   railway init
   railway up
   ```

5. **Set environment variables** in Railway dashboard:
   - `LLM_URL` = `https://api.z.ai/api/coding/paas/v4`
   - `LLM_API_KEY` = your ZAI key
   - `LLM_MODEL` = `glm-4.5`
   - `ANTHROPIC_BASE_URL` = `https://api.z.ai/api/anthropic`
   - `ANTHROPIC_AUTH_TOKEN` = your Claude key
   - `APPLYPILOT_INTERVAL_HOURS` = `6` (optional)

6. **Add PVC** (in Railway UI):
   - Go to project → Variables → New Variable
   - Type: PVC
   - Name: `applypilot-data`
   - Mount path: `/data`

7. **Deploy!**

## Data Sync

The deployment will:
1. Copy `seed-data/*` to the PVC on first run
2. Use the PVC for all subsequent operations
3. Persist new jobs, scores, and applications across restarts

**To sync local changes to Railway:**
```bash
# Update seed data and redeploy
cp ~/.applypilot/applypilot.db seed-data/
railway up
```

**To pull Railway data back to local:**
```bash
# Via Railway shell
railway shell
# Then: cp /data/applypilot.db /dev/stdout > ~/local-applypilot.db
```

## Monitoring

- Health check: `https://your-app.railway.app/health`
- Manual trigger: `curl -X POST https://your-app.railway.app/trigger`
- Logs: Railway dashboard → Logs tab
