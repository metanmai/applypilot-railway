# Local Apply Script Design

**Date:** 2026-04-13
**Author:** Claude
**Status:** Draft

## Overview

A local Python script that fetches high-scoring jobs from Railway and uses ApplyPilot's existing Chrome automation to submit applications. Results are synced back to Railway via API updates.

**Problem:** Railway has 114 high-scoring jobs (≥7) stored in database, but no actual applications have been submitted. The current ApplyWorker is a placeholder.

**Solution:** Bridge Railway's job queue with ApplyPilot's existing apply automation, running locally with the user's Chrome session.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           local_apply.py                                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │ Railway API  │───→│ Job Queue   │───→│ run_job()    │              │
│  │ Client       │    │ (sorted)    │    │ (Chrome +    │              │
│  └──────────────┘    └──────────────┘    │  Claude)     │              │
│         ↑                                  └──────────────┘              │
│         │                                          │                   │
│         │                                  Job Result                   │
│         │                                  (applied/captcha/etc)       │
│         │                                          │                   │
│         └──────────── PUT /db/jobs/{url} ─────────┘                   │
│                                                                   │
│  Progress tracking: ~/.applypilot/local_apply_state.json           │
└───────────────────────────────────────────────────────────────────┘
```

## Components

### 1. local_apply.py (NEW)

**Location:** `/home/metanmai/Code/applypilot-railway/local_apply.py`

**Responsibilities:**
- Fetch jobs from Railway API
- Sort by score descending (9s → 8s → 7s)
- Track progress in state file
- Call `applypilot.apply.launcher.run_job()` for each job
- Update Railway with results
- Handle interruptions and resume

**Key functions:**
- `fetch_jobs(min_score=7, status="ready_to_apply")` - GET from Railway
- `apply_job(job)` - Wrapper around `run_job()`
- `update_railway(url, status, applied_at)` - PUT to Railway
- `save_state()` / `load_state()` - Persistence
- `main()` - CLI entry point

### 2. Railway API Addition

**Location:** `main.py` (modify existing)

**New endpoint:**
```python
@app.route('/db/jobs/<path:url>', methods=['PUT'])
def update_job_status(url):
    """Update job status after local apply."""
    # Accepts: {"status": "applied"|"captcha"|"expired"|"failed", "applied_at": "..."}
    # Updates jobs table
    # Returns: {"success": true}
```

**URL encoding:** URLs will be URL-encoded in the path parameter.

### 3. ApplyPilot Integration (EXISTING)

**Module:** `applypilot.apply.launcher`

**Function used:**
```python
run_job(job: dict, port: int, worker_id: int = 0,
        model: str = "sonnet", dry_run: bool = False)
    → Tuple[str, int]  # (status_string, duration_ms)
```

**Returns:** `'applied'`, `'captcha'`, `'expired'`, `'login_issue'`, `'failed:reason'`, or `'skipped'`

## Data Flow

```
1. START local_apply.py
   ↓
2. Load previous state (if any) from ~/.applypilot/local_apply_state.json
   ↓
3. GET /db/jobs?min_score=7&status=ready_to_apply
   ↓
4. Filter out already-attempted jobs (from state file)
   ↓
5. Sort by score DESC (9→8→7)
   ↓
6. For each job:
   │
   ├─→ Display: "[1/114] Applying: Software Engineer at Google [9/10]"
   │
   ├─→ run_job(job, port=9222, worker_id=0)
   │   ├─ Launch Chrome (headless=false for visibility)
   │   ├─ Start Claude Code with Playwright MCP
   │   ├─ Claude fills forms, uploads resume, submits
   │   └─ Returns: "applied" | "captcha" | "expired" | "failed"
   │
   ├─→ PUT /db/jobs/{url} with result
   │
   ├─→ Update local state file
   │
   └─→ Display: "✓ Applied in 45s" or "✗ CAPTCHA - retry later"
   ↓
7. Save final state
   ↓
8. Summary: "Applied: 95, Failed: 12, CAPTCHA: 7"
```

## Status Definitions

| Status | Meaning | Railway DB |
|--------|---------|------------|
| `actually_applied` | Successfully submitted application | NEW status |
| `captcha` | CAPTCHA blocked, requires manual retry | NEW status |
| `expired` | Job no longer accepting applications | NEW status |
| `login_required` | User needs to log into job site | NEW status |
| `failed` | Other error (network, Chrome crash, etc.) | NEW status |

## Configuration

**Environment variables:**
```bash
RAILWAY_URL=https://comfortable-flow-production.up.railway.app
APPLYPILOT_PROFILE=~/.applypilot/profile.json
CHROME_HEADLESS=false  # Show Chrome for debugging
```

**Command-line options:**
```bash
--dry-run          # List jobs without applying
--limit N          # Apply to N jobs only
--single-job URL   # Apply to single job (for testing)
--resume           # Skip already-attempted jobs
--verbose          # Detailed output
--ignore-captcha   # Retry jobs marked as captcha
```

## File Structure

```
applypilot-railway/
├── local_apply.py              # NEW - Main local apply script
├── main.py                     # MODIFY - Add PUT /db/jobs/<url>
├── workers.py                  # Unchanged
├── requirements.txt            # Unchanged
├── Railway (deployed)
│   └── Database with 842 jobs (114 high-score)
└── ApplyPilot/ (existing)
    └── src/applypilot/apply/
        └── launcher.py         # Used by local_apply.py
```

## State File

**Location:** `~/.applypilot/local_apply_state.json`

**Structure:**
```json
{
  "last_run": "2026-04-13T10:30:00Z",
  "total_attempted": 30,
  "stats": {
    "applied": 25,
    "captcha": 3,
    "expired": 2,
    "failed": 0
  },
  "jobs": {
    "https%3A%2F%2Flinkedin.com%2Fjobs%2Fview%2F123": {
      "status": "applied",
      "timestamp": "2026-04-13T10:32:15Z",
      "duration_ms": 45000,
      "title": "Software Engineer"
    },
    "https%3A%2F%2Findeed.com%2Fjobs%3Fjk=456": {
      "status": "captcha",
      "timestamp": "2026-04-13T10:35:00Z",
      "title": "Frontend Developer"
    }
  }
}
```

**URL encoding:** URLs used as object keys are URL-encoded to handle special characters.

## Error Handling

| Error | Action | Railway Status |
|-------|--------|----------------|
| CAPTCHA detected | Pause, notify user | `captcha` |
| Job expired | Skip, log | `expired` |
| Login required | Prompt user to login | `login_required` |
| Network timeout | Retry 3x, then fail | `failed` |
| Chrome crash | Restart Chrome, retry 1x | `failed` |
| Railway API down | Cache result locally, retry later | N/A |
| Claude Code not found | Error message, exit | N/A |

## Testing Strategy

### TEST 1: Dry Run
```bash
python local_apply.py --dry-run --limit 3
# Expected: Lists 3 jobs with scores, shows what would happen, exits
# Verify: No actual applications made
```

### TEST 2: Single Job Apply
```bash
python local_apply.py --single-job "https://linkedin.com/jobs/view/123"
# Expected: Opens Chrome, Claude applies to one job
# Verify: Railway status updated to "actually_applied"
# Verify: Confirmation email arrives
```

### TEST 3: Resume After Interruption
```bash
python local_apply.py --limit 2  # Applies 2 jobs
# Ctrl+C after 1st job
python local_apply.py --resume      # Skips completed job
# Expected: Only applies remaining jobs
```

### TEST 4: CAPTCHA Handling
```bash
# Trigger a CAPTCHA during apply
# Verify: Job marked as "captcha" in state file
# Verify: Job marked as "captcha" in Railway
# Re-run: python local_apply.py --ignore-captcha
# Verify: CAPTCHA jobs are retried
```

### TEST 5: Railway Sync
```bash
python local_apply.py --limit 1
curl https://comfortable-flow-production.up.railway.app/db/stats
# Verify: "actually_applied" count increases
```

### TEST 6: Score Ordering
```bash
python local_apply.py --dry-run --limit 10
# Verify: Jobs are listed 9/10 first, then 8/10, then 7/10
```

## Migration to Railway (Future)

Phase 1: Local (current design)
- Script runs on user's machine
- User's Chrome with existing logins
- User handles CAPTCHAs immediately

Phase 2: Railway with login persistence
- Run apply module in Railway container
- Persist Chrome profile to PVC
- User logs in once via Railway-provided mechanism

Phase 3: Full Railway automation
- Handle CAPTCHAs via pause/notification
- Add Telegram/email notification for manual intervention
- Fully unattended operation

## Success Criteria

1. ✅ Can fetch jobs from Railway API
2. ✅ Applies to jobs using existing ApplyPilot automation
3. ✅ Updates Railway with apply results
4. ✅ Resumes after interruption without duplicates
5. ✅ Confirmation emails received for successful applies
6. ✅ Testable end-to-end with dry-run and single-job modes
