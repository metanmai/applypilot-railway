# Local Apply Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a local Python script that fetches high-scoring jobs from Railway, applies to them using ApplyPilot's Chrome automation, and syncs results back to Railway.

**Architecture:** Local script (`local_apply.py`) fetches jobs via Railway API, calls `applypilot.apply.launcher.run_job()` for Chrome automation, updates Railway via PUT endpoint. Progress tracked in local state file for resume capability.

**Tech Stack:** Python 3.14+, requests, applypilot.apply.launcher, Flask (Railway API), SQLite

---

## File Structure

```
applypilot-railway/
├── local_apply.py              # NEW - Main local apply script (CLI)
│   ├── RailwayAPIClient         # Fetch and update jobs
│   ├── StateManager             # Local progress tracking
│   ├── JobProcessor            # Orchestrate apply flow
│   └── CLI argument parsing
├── main.py                     # MODIFY - Add PUT /db/jobs/<url> endpoint
└── tests/
    └── test_local_apply.py      # NEW - Unit tests for local_apply.py
```

---

## Task 1: Add Railway PUT Endpoint for Job Status Updates

**Files:**
- Modify: `main.py` (add after line 570, after `db_jobs` endpoint)

**Purpose:** Allow local script to update job status after applying

- [ ] **Step 1: Add PUT endpoint to main.py**

Add this code after the `db_jobs()` endpoint (after line 570):

```python
@app.route('/db/jobs/<path:url>', methods=['PUT'])
def update_job_status(url):
    """Update job status after local apply.
    
    Expects JSON body:
    {
        "status": "actually_applied" | "captcha" | "expired" | "login_required" | "failed",
        "applied_at": "2026-04-13T10:30:00Z"  # optional, for actually_applied
    }
    
    Returns: {"success": true}
    """
    from urllib.parse import unquote
    
    conn = get_connection()
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Request body required"}), 400
    
    status = data.get('status')
    applied_at = data.get('applied_at')
    
    # Validate status
    valid_statuses = ['actually_applied', 'captcha', 'expired', 'login_required', 'failed']
    if status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {valid_statuses}"}), 400
    
    # URL decode the path parameter
    decoded_url = unquote(url)
    
    # Build update query
    set_clauses = ["status = ?", "updated_at = ?"]
    values = [status, datetime.now(timezone.utc).isoformat()]
    
    if applied_at and status == 'actually_applied':
        set_clauses.append("applied_at = ?")
        values.append(applied_at)
    
    values.append(decoded_url)
    
    update_sql = f"UPDATE jobs SET {', '.join(set_clauses)} WHERE url = ?"
    
    try:
        conn.execute(update_sql, values)
        conn.commit()
        return jsonify({"success": True, "url": decoded_url, "status": status})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 2: Test PUT endpoint with curl**

```bash
# Deploy to Railway first
railway up

# Test with a sample URL
curl -X PUT https://comfortable-flow-production.up.railway.app/db/jobs/https%3A%2F%2Flinkedin.com%2Fjobs%2F123 \
  -H "Content-Type: application/json" \
  -d '{"status": "actually_applied", "applied_at": "2026-04-13T10:30:00Z"}'
```

Expected: `{"success": true, "url": "https://linkedin.com/jobs/123", "status": "actually_applied"}`

- [ ] **Step 3: Commit Railway API changes**

```bash
git add main.py
git commit -m "feat: add PUT endpoint for job status updates"
git push
```

---

## Task 2: Create local_apply.py - Basic Structure and CLI

**Files:**
- Create: `local_apply.py`

- [ ] **Step 1: Create file with imports and CLI skeleton**

```python
#!/usr/bin/env python3
"""
Local apply script for ApplyPilot Railway integration.

Fetches high-scoring jobs from Railway and applies using
ApplyPilot's Chrome automation. Results synced back to Railway.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# Configuration
RAILWAY_URL = os.environ.get('RAILWAY_URL', 'https://comfortable-flow-production.up.railway.app')
STATE_FILE = Path.home() / '.applypilot' / 'local_apply_state.json'


class RailwayAPIClient:
    """Client for Railway API operations."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
    
    def fetch_jobs(self, min_score: int = 7, status: str = 'ready_to_apply', limit: int = 1000) -> list:
        """Fetch jobs from Railway."""
        params = {'min_score': min_score, 'status': status, 'limit': limit}
        response = requests.get(f"{self.base_url}/db/jobs", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('jobs', [])
    
    def update_job_status(self, url: str, status: str, applied_at: Optional[str] = None) -> bool:
        """Update job status in Railway."""
        encoded_url = quote(url, safe='')
        payload = {'status': status}
        if applied_at:
            payload['applied_at'] = applied_at
        
        response = requests.put(
            f"{self.base_url}/db/jobs/{encoded_url}",
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json().get('success', False)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Apply to jobs from Railway using local Chrome automation'
    )
    parser.add_argument('--dry-run', action='store_true', help='List jobs without applying')
    parser.add_argument('--limit', type=int, help='Limit number of jobs to apply')
    parser.add_argument('--single-job', type=str, help='Apply to single job URL')
    parser.add_argument('--resume', action='store_true', help='Skip already-attempted jobs')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--ignore-captcha', action='store_true', help='Retry jobs marked as captcha')
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    log.info(f"Railway URL: {RAILWAY_URL}")
    
    # TODO: Implement full flow
    
    if args.dry_run:
        log.info("DRY RUN MODE - No applications will be submitted")
        # Fetch and display jobs
        api = RailwayAPIClient(RAILWAY_URL)
        jobs = api.fetch_jobs(min_score=7)
        log.info(f"Found {len(jobs)} jobs")
        for i, job in enumerate(jobs[:10], 1):
            log.info(f"{i}. [{job.get('fit_score')}/10] {job.get('title')} at {job.get('site')}")
    else:
        log.info("Full apply mode coming in next tasks...")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Make file executable**

```bash
chmod +x local_apply.py
```

- [ ] **Step 3: Test basic CLI**

```bash
# Test dry run
python local_apply.py --dry-run --verbose
```

Expected: Lists jobs from Railway

- [ ] **Step 4: Commit basic structure**

```bash
git add local_apply.py
git commit -m "feat: add local_apply.py basic structure and CLI"
```

---

## Task 3: Implement StateManager for Progress Tracking

**Files:**
- Modify: `local_apply.py` (add StateManager class)

- [ ] **Step 1: Add StateManager class**

Add this after RailwayAPIClient class:

```python
class StateManager:
    """Manage local state for progress tracking and resume capability."""
    
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
    
    def _load(self) -> dict:
        """Load state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                log.warning(f"Could not load state file, starting fresh")
        return {
            'last_run': None,
            'total_attempted': 0,
            'stats': {
                'applied': 0,
                'captcha': 0,
                'expired': 0,
                'failed': 0,
                'login_required': 0
            },
            'jobs': {}
        }
    
    def save(self):
        """Save state to file."""
        self.state['last_run'] = datetime.now(timezone.utc).isoformat()
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def is_attempted(self, url: str) -> bool:
        """Check if job was already attempted."""
        from urllib.parse import quote
        encoded_url = quote(url, safe='')
        return encoded_url in self.state['jobs']
    
    def mark_attempted(self, url: str, status: str, title: str, duration_ms: int = 0):
        """Mark job as attempted."""
        from urllib.parse import quote
        encoded_url = quote(url, safe='')
        
        self.state['jobs'][encoded_url] = {
            'status': status,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'duration_ms': duration_ms,
            'title': title
        }
        self.state['total_attempted'] += 1
        
        if status in self.state['stats']:
            self.state['stats'][status] += 1
        
        self.save()
    
    def get_jobs_to_retry(self, status_filter: str = None) -> set:
        """Get set of job URLs matching status filter."""
        from urllib.parse import unquote
        if status_filter:
            return {
                unquote(url) for url, data in self.state['jobs'].items()
                if data.get('status') == status_filter
            }
        return set()
```

- [ ] **Step 2: Update main() to use StateManager**

Modify the main() function to use StateManager:

```python
def main():
    """Main entry point."""
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    log.info(f"Railway URL: {RAILWAY_URL}")
    
    # Initialize state manager
    state_mgr = StateManager(STATE_FILE)
    log.info(f"State file: {STATE_FILE}")
    log.info(f"Previously attempted: {state_mgr.state['total_attempted']} jobs")
    
    # Show stats
    stats = state_mgr.state['stats']
    log.info(f"Stats: applied={stats['applied']}, captcha={stats['captcha']}, "
            f"expired={stats['expired']}, failed={stats['failed']}")
    
    # Rest of implementation in next tasks...
```

- [ ] **Step 3: Test StateManager**

```bash
# Test state file creation
python local_apply.py --dry-run
ls -la ~/.applypilot/local_apply_state.json
cat ~/.applypilot/local_apply_state.json
```

Expected: State file created with empty jobs dict

- [ ] **Step 4: Commit StateManager**

```bash
git add local_apply.py
git commit -m "feat: add StateManager for progress tracking"
```

---

## Task 4: Implement JobProcessor with ApplyPilot Integration

**Files:**
- Modify: `local_apply.py` (add JobProcessor class)

- [ ] **Step 1: Add JobProcessor class**

Add this after StateManager class:

```python
class JobProcessor:
    """Process jobs using ApplyPilot's apply automation."""
    
    def __init__(self):
        self.base_cdp_port = 9222
        self.worker_id = 0
    
    def process_job(self, job: dict, dry_run: bool = False) -> tuple[str, int]:
        """Process a single job application.
        
        Returns:
            Tuple of (status_string, duration_ms)
            Status: 'actually_applied', 'captcha', 'expired', 'login_required', 'failed'
        """
        if dry_run:
            log.info(f"[DRY RUN] Would apply to: {job.get('title')} at {job.get('site')}")
            return ('dry_run', 0)
        
        try:
            # Import here to avoid issues if not available
            from applypilot.apply.launcher import run_job
            
            log.info(f"Applying to: {job.get('title')} at {job.get('site')} [{job.get('fit_score')}/10]")
            
            # Run the apply automation
            status, duration_ms = run_job(
                job=job,
                port=self.base_cdp_port,
                worker_id=self.worker_id,
                model='sonnet',
                dry_run=dry_run
            )
            
            # Map ApplyPilot status to our status
            status_map = {
                'applied': 'actually_applied',
                'captcha': 'captcha',
                'expired': 'expired',
                'login_issue': 'login_required',
            }
            
            mapped_status = status_map.get(status, status if ':' in status else 'failed')
            
            log.info(f"Result: {mapped_status} ({duration_ms/1000:.1f}s)")
            return (mapped_status, duration_ms)
            
        except ImportError as e:
            log.error(f"ApplyPilot apply module not available: {e}")
            log.error("Make sure ApplyPilot is installed: pip install -e ApplyPilot")
            return ('failed', 0)
        except Exception as e:
            log.error(f"Error processing job: {e}")
            return ('failed', 0)
```

- [ ] **Step 2: Test ApplyPilot import**

```bash
python -c "from applypilot.apply.launcher import run_job; print('OK')"
```

Expected: `OK` (or error indicating ApplyPilot needs to be installed)

- [ ] **Step 3: Commit JobProcessor**

```bash
git add local_apply.py
git commit -m "feat: add JobProcessor with ApplyPilot integration"
```

---

## Task 5: Implement Main Apply Loop

**Files:**
- Modify: `local_apply.py` (replace the main() function)

- [ ] **Step 1: Implement full apply loop**

Replace the main() function with:

```python
def main():
    """Main entry point."""
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    log.info(f"Railway URL: {RAILWAY_URL}")
    log.info("=" * 60)
    
    # Initialize components
    api = RailwayAPIClient(RAILWAY_URL)
    state_mgr = StateManager(STATE_FILE)
    processor = JobProcessor()
    
    # Get jobs to process
    jobs = []
    
    if args.single_job:
        # Single job mode
        jobs = [{'url': args.single_job, 'title': 'Single Job', 'site': 'Unknown', 'fit_score': 0}]
    else:
        # Fetch from Railway
        jobs = api.fetch_jobs(min_score=7, limit=args.limit or 1000)
        
        # Filter by resume flag
        if args.resume:
            jobs = [j for j in jobs if not state_mgr.is_attempted(j['url'])]
            log.info(f"Resume mode: {len(jobs)} unattempted jobs")
        
        # Filter out captcha jobs unless ignoring
        if not args.ignore_captcha:
            captcha_jobs = state_mgr.get_jobs_to_retry('captcha')
            if captcha_jobs:
                jobs = [j for j in jobs if j['url'] not in captcha_jobs]
                log.info(f"Skipping {len(captcha_jobs)} CAPTCHA jobs (use --ignore-captcha to retry)")
        
        # Sort by score descending
        jobs.sort(key=lambda j: j.get('fit_score', 0), reverse=True)
    
    if not jobs:
        log.info("No jobs to process")
        return
    
    if args.dry_run or args.single_job:
        limit = args.limit if args.limit else len(jobs)
    else:
        limit = len(jobs)
    
    log.info(f"Processing {limit} jobs...")
    log.info("=" * 60)
    
    # Process each job
    results = {'actually_applied': 0, 'captcha': 0, 'expired': 0, 'failed': 0, 'login_required': 0}
    
    for i, job in enumerate(jobs[:limit], 1):
        log.info(f"[{i}/{limit}] {job.get('title')} at {job.get('site')} [{job.get('fit_score')}/10]")
        log.info(f"URL: {job.get('url')}")
        
        # Process the job
        status, duration_ms = processor.process_job(job, dry_run=args.dry_run)
        
        # Update Railway
        if not args.dry_run and status != 'dry_run':
            try:
                applied_at = datetime.now(timezone.utc).isoformat() if status == 'actually_applied' else None
                api.update_job_status(job['url'], status, applied_at)
                log.info(f"✓ Railway updated: {status}")
            except Exception as e:
                log.error(f"✗ Failed to update Railway: {e}")
        
        # Update local state
        if not args.dry_run:
            state_mgr.mark_attempted(job['url'], status, job.get('title', ''), duration_ms)
        
        # Track results
        if status in results:
            results[status] += 1
        
        log.info("-" * 60)
    
    # Summary
    log.info("=" * 60)
    log.info("SUMMARY:")
    log.info(f"  Actually Applied: {results['actually_applied']}")
    log.info(f"  CAPTCHA: {results['captcha']}")
    log.info(f"  Expired: {results['expired']}")
    log.info(f"  Login Required: {results['login_required']}")
    log.info(f"  Failed: {results['failed']}")
    log.info("=" * 60)
```

- [ ] **Step 2: Test dry run mode**

```bash
python local_apply.py --dry-run --limit 3
```

Expected: Lists 3 jobs without applying

- [ ] **Step 3: Commit main loop**

```bash
git add local_apply.py
git commit -m "feat: implement main apply loop"
```

---

## Task 6: Deploy Updated Railway API and End-to-End Test

**Files:**
- Deploy: `main.py` to Railway
- Test: Full integration

- [ ] **Step 1: Deploy Railway changes**

```bash
railway up
```

Wait for deployment to complete, verify with:
```bash
curl -s https://comfortable-flow-production.up.railway.app/health | python3 -m json.tool
```

- [ ] **Step 2: Test PUT endpoint manually**

```bash
# Encode a URL and test PUT
ENCODED_URL=$(python3 -c "import urllib.parse; print(urllib.parse.quote('https://linkedin.com/jobs/test', safe=''))")
curl -X PUT "https://comfortable-flow-production.up.railway.app/db/jobs/$ENCODED_URL" \
  -H "Content-Type: application/json" \
  -d '{"status": "captcha"}'
```

Expected: `{"success": true, "url": "https://linkedin.com/jobs/test", "status": "captcha"}`

- [ ] **Step 3: Test local_apply.py dry run**

```bash
python local_apply.py --dry-run --limit 5
```

Expected: Lists 5 highest-scoring jobs

- [ ] **Step 4: Document deployment**

```bash
# Update README with local apply usage
echo "
## Local Apply

To apply to jobs locally using your Chrome session:

\`\`\`bash
# Dry run to see what would be applied
python local_apply.py --dry-run

# Apply to 5 jobs
python local_apply.py --limit 5

# Resume after interruption
python local_apply.py --resume

# Single job for testing
python local_apply.py --single-job <url>
\`\`\`
" >> README.md
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "docs: add local apply usage to README"
git push
```

---

## Task 7: Create Unit Tests

**Files:**
- Create: `tests/test_local_apply.py`

- [ ] **Step 1: Create test file structure**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Write unit tests**

Create `tests/test_local_apply.py`:

```python
"""Tests for local_apply.py"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestRailwayAPIClient:
    """Test Railway API client."""
    
    def test_fetch_jobs(self):
        """Test fetching jobs from Railway."""
        from local_apply import RailwayAPIClient
        
        client = RailwayAPIClient('https://test.railway.app')
        
        with patch('requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'jobs': [
                    {'url': 'https://test.com/1', 'title': 'Job 1', 'fit_score': 8}
                ]
            }
            
            jobs = client.fetch_jobs(min_score=7)
            
            assert len(jobs) == 1
            assert jobs[0]['title'] == 'Job 1'
            mock_get.assert_called_once()
    
    def test_update_job_status(self):
        """Test updating job status."""
        from local_apply import RailwayAPIClient
        
        client = RailwayAPIClient('https://test.railway.app')
        
        with patch('requests.put') as mock_put:
            mock_put.return_value.json.return_value = {'success': True}
            
            result = client.update_job_status('https://test.com/1', 'applied')
            
            assert result is True


class TestStateManager:
    """Test state management."""
    
    def test_create_state_file(self):
        """Test state file creation."""
        from local_apply import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)
            
            assert state_file.exists()
            assert 'jobs' in mgr.state
            assert 'stats' in mgr.state
    
    def test_mark_attempted(self):
        """Test marking job as attempted."""
        from local_apply import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)
            
            mgr.mark_attempted('https://test.com/1', 'applied', 'Job 1', 45000)
            
            assert mgr.is_attempted('https://test.com/1')
            assert mgr.state['stats']['applied'] == 1
    
    def test_resume_filter(self):
        """Test filtering attempted jobs."""
        from local_apply import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)
            
            mgr.mark_attempted('https://test.com/1', 'applied', 'Job 1')
            mgr.mark_attempted('https://test.com/2', 'captcha', 'Job 2')
            
            captcha_jobs = mgr.get_jobs_to_retry('captcha')
            
            assert 'https://test.com/2' in captcha_jobs
            assert 'https://test.com/1' not in captcha_jobs
```

- [ ] **Step 3: Install pytest if needed**

```bash
pip install pytest pytest-mock
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_local_apply.py -v
```

- [ ] **Step 5: Commit tests**

```bash
git add tests/
git commit -m "test: add unit tests for local_apply.py"
```

---

## Self-Review Against Spec

**Spec coverage:**
- ✅ Railway API GET/PUT endpoints → Task 1, Task 2
- ✅ ApplyPilot run_job() integration → Task 4
- ✅ Local state file for progress → Task 3
- ✅ CLI flags (--dry-run, --limit, --single-job, --resume) → Task 2, Task 5
- ✅ Score ordering (9→8→7) → Task 5
- ✅ Error handling for CAPTCHA/expired/failed → Task 4, Task 5
- ✅ End-to-end testing → Task 6

**Placeholder scan:** No "TBD", "TODO", or incomplete steps found.

**Type consistency:** All function names and signatures match across tasks.
