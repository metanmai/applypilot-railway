# ApplyPilot Railway Deployment

Continuously running job application bot deployed on Railway.

## Features

- 🔄 **Automatic pipeline runs** every N hours (default: 6)
- 💾 **Persistent storage** via Railway PVC
- 🏥 **Health checks** for Railway monitoring
- 🎯 **Configurable min score** for filtering jobs
- 🔧 **Manual trigger** via `/trigger` endpoint

## Local Development

```bash
# Copy environment variables
cp .env.example .env
# Edit .env with your API keys

# Run with Docker Compose
docker-compose up -d

# Check status
curl http://localhost:8080/health

# View logs
docker-compose logs -f applypilot

# Manually trigger a run
curl -X POST http://localhost:8080/trigger
```

## Railway Deployment

1. **Create new project** on Railway
2. **Add this repository** or deploy from CLI
3. **Set environment variables** in Railway dashboard:
   - `LLM_URL`, `LLM_API_KEY`, `LLM_MODEL`
   - `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`
   - `APPLYPILOT_INTERVAL_HOURS` (optional, default: 6)
   - `APPLYPILOT_MIN_SCORE` (optional, default: 7)
4. **Add PVC** named `applypilot-data` (configured in railway.toml)
5. **Deploy!**

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service status |
| `/health` | GET | Health check for Railway |
| `/trigger` | POST | Manually trigger pipeline run |

## Local Apply

To apply to jobs locally using your Chrome session:

```bash
# Dry run to see what would be applied
python local_apply.py --dry-run

# Apply to 5 jobs
python local_apply.py --limit 5

# Resume after interruption
python local_apply.py --resume

# Single job for testing
python local_apply.py --single-job <url>
```

## Notes

- Resume and profile data should be in `~/.applypilot/` locally
- On Railway, data persists to PVC mounted at `/data`
- Chrome/Chromium is installed for job board scraping
- Claude Code CLI is installed for auto-apply functionality
