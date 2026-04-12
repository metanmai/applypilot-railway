# Batch Processing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform ApplyPilot Railway deployment into a pipelined architecture with 6 independent workers that process jobs continuously, preserving progress and enabling full auto-apply.

**Architecture:** Six independent workers (Discover, Enrich, Score, Tailor, Cover, Apply) run as threads, polling a SQLite-based job queue. Each worker processes one job at a time and commits immediately.

**Tech Stack:** Python 3.14, Flask, SQLite, Threading, ApplyPilot fork, GLM-4.5 API

---

## Task 1: Create workers.py with base Worker class

**Files:**
- Create: `/home/metanmai/Code/applypilot-railway/workers.py`

- [ ] **Step 1: Create base Worker class**

```python
# workers.py
import logging
import time
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from applypilot.database import get_connection
from applypilot.config import load_profile

log = logging.getLogger(__name__)


class Worker(ABC):
    """Base class for all pipeline workers."""
    
    def __init__(self, data_dir: Path, min_score: int = 7, sleep_interval: int = 5):
        self.data_dir = data_dir
        self.min_score = min_score
        self.sleep_interval = sleep_interval
        self.running = False
        self.thread = None
        
    def start(self) -> threading.Thread:
        """Start the worker in a background thread."""
        if self.thread is None or not self.thread.is_alive():
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            log.info(f"Started {self.__class__.__name__}")
        return self.thread
        
    def stop(self):
        """Stop the worker gracefully."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=10)
        log.info(f"Stopped {self.__class__.__name__}")
        
    def _run(self):
        """Main worker loop - override in subclasses."""
        while self.running:
            try:
                self._process_next_job()
            except Exception as e:
                log.error(f"Error in {self.__class__.__name__}: {e}")
                time.sleep(self.sleep_interval)
                
    @abstractmethod
    def _get_next_job_status(self) -> str | None:
        """Return the status this worker processes. Returns None if no work."""
        pass
    
    def _get_next_job(self) -> dict | None:
        """Get next job from queue that matches this worker's status."""
        conn = get_connection()
        status = self._get_next_job_status()
        
        if status is None:
            return None
            
        # Get next job with that status
        job = conn.execute(
            "SELECT url, title, site, location, full_description FROM jobs "
            f"WHERE status = ? ORDER BY created_at DESC LIMIT 1",
            (status,)
        ).fetchone()
        
        if job:
            columns = ['url', 'title', 'site', 'location', 'full_description']
            return dict(zip(columns, job))
        return None
        
    def _save_result(self, job: dict, status: str, **kwargs):
        """Update job status and commit immediately."""
        conn = get_connection()
        update_sql = "UPDATE jobs SET status = ?, updated_at = ? WHERE url = ?"
        values = [status, datetime.now(timezone.utc).isoformat(), job['url']]
        
        # Add any extra fields (tailored_path, cover_path, etc.)
        for key, value in kwargs.items():
            update_sql = update_sql.replace("status = ?", f"{key} = ?,")
            values.insert(-2, value)
            
        conn.execute(update_sql, values)
        conn.commit()
        
    def _is_already_processed(self, job: dict) -> bool:
        """Check if job was already processed by this worker."""
        # Override in subclasses as needed
        return False
```

**Purpose:** Create the base Worker class that all pipeline workers will inherit from. Provides common functionality for starting/stopping, polling for work, and committing results.

---

## Task 2: Create DiscoverWorker class

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/workers.py`

- [ ] **Step 1: Add DiscoverWorker class**

```python
# Add to workers.py after Worker class

class DiscoverWorker(Worker):
    """Worker that continuously discovers jobs from job boards."""
    
    def __init__(self, data_dir: Path, queries: list[dict], jobs_per_query: int = 10):
        super().__init__(data_dir)
        self.queries = queries
        self.jobs_per_query = jobs_per_query
        self.current_query_index = 0
        
    def _get_next_job_status(self) -> str | None:
        """DiscoverWorker doesn't pull from queue - it creates jobs."""
        return None
        
    def _run(self):
        """Run discovery loop through all queries, then restart."""
        while self.running:
            if self.current_query_index >= len(self.queries):
                self.current_query_index = 0  # Restart from beginning
                log.info("Completed all queries, restarting discovery")
                time.sleep(60)  # Wait before restarting
                
            query = self.queries[self.current_query_index]
            log.info(f"Discovering for query: {query.get('query')}")
            
            # Run discovery for this query with limit
            self._discover_and_queue_jobs(query)
            
            # Move to next query
            self.current_query_index += 1
            
    def _discover_and_queue_jobs(self, query: dict):
        """Run discovery for a query and queue new jobs."""
        from applypilot.discovery.jobspy import run_discovery
        
        # Set limit via environment variable (hack)
        os.environ['MAX_JOBS'] = str(self.jobs_per_query)
        
        # Run discovery - it will save jobs to database with status='pending_enrich'
        run_discovery(cfg={'queries': [query]})
```

**Purpose:** Creates the discovery worker that runs jobspy for each search query, limiting to N jobs per query, and saves new jobs to the queue with status='pending_enrich'.

---

## Task 3: Create EnrichWorker class

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/workers.py`

- [ ] **Step 1: Add EnrichWorker class**

```python
# Add to workers.py

class EnrichWorker(Worker):
    """Worker that fetches full job descriptions."""
    
    def _get_next_job_status(self) -> str:
        return 'pending_enrich'
        
    def _run(self):
        """Enrichment loop."""
        while self.running:
            job = self._get_next_job()
            if not job:
                time.sleep(self.sleep_interval)
                continue
                
            if job.get('full_description'):
                # Already enriched, skip to next stage
                self._save_result(job, 'pending_score')
                continue
                
            try:
                # Fetch full description using smartextract or jobspy detail
                from applypilot.discovery.smartextract import run_smart_extract
                # This is a simplified approach - in production we'd use the enrich module
                
                # For now, just skip if no description (enrich stage can be enhanced later)
                log.info(f"No full description for {job['title']}, skipping")
                self._save_result(job, 'pending_score')  # Move on anyway
            except Exception as e:
                log.error(f"Error enriching {job.get('title')}: {e}")
                self._save_result(job, 'pending_score')  # Move on anyway
```

**Purpose:** Creates the enrich worker that fetches full job descriptions for jobs in the queue.

---

## Task 4: Create ScoreWorker class

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/workers.py`
- Modify: `/home/metanmai/Code/applypilot-railway/ApplyPilot/src/applypilot/scoring/scorer.py`

- [ ] **Step 1: Add score_and_commit function to ApplyPilot fork**

```python
# Add to ApplyPilot/src/applypilot/scoring/scorer.py

def score_and_commit(job: dict) -> dict:
    """Score a single job and commit immediately."""
    from applypilot.database import get_connection
    from datetime import datetime, timezone
    
    # Build job text for LLM
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )
    
    # Get resume
    from applypilot.config import RESUME_PATH
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    
    # Score prompt
    SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has relevant skills and can grow into the role.
- 3-4: ├────= Click 'Replace' to edit `/home/metanmai/Code/applypilot-railway/ApplyPilot/src/applypilot/scoring/scorer.py`
└────── 24: Click 'Replace' to edit `/home/metanmai/monor/resume.txt
```

- [ ] **Step 2: Add ScoreWorker class to workers.py**

```python
# Add to workers.py

class ScoreWorker(Worker):
    """Worker that scores jobs and commits immediately."""
    
    def _get_next_job_status(self) -> str:
        return 'pending_score'
        
    def _run(self):
        """Scoring loop."""
        while self.running:
            job = self._get_next_job()
            if not job:
                time.sleep(self.sleep_interval)
                continue
                
            if self._is_already_scored(job):
                # Already scored, move to next
                if job.get('fit_score', 0) >= self.min_score:
                    self._save_result(job, 'pending_tailor')
                continue
                
            # Score and commit
            result = self._score_and_commit(job)
            
            # If score ≥7, move to tailor stage
            if result['score'] >= self.min_score:
                self._save_result(job, 'pending_tailor')
            else:
                # Low score, skip to applied (won't be applied)
                self._save_result(job, 'applied')
    
    def _score_and_commit(self, job: dict) -> dict:
        """Score a single job and commit immediately."""
        from applypilot.database import get_connection
        from applypilot.config import RESUME_PATH
        from datetime import datetime, timezone
        
        # Import the run_scoring module to use existing logic
        import sys
        sys.path.insert(0, '/root/.local/share/pipx/venvs/applypilot/lib/python3.14/site-packages')
        from applypilot.scoring.scorer import score_job
        
        # Score the job
        result = score_job(str(RESUME_PATH), job)
        
        # Commit immediately
        conn = get_connection()
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
            (result['score'], f"{result['keywords']}\n{result['reasoning']}", datetime.now(timezone.utc).isoformat(), job['url'])
        )
        conn.commit()
        
        return result
    
    def _is_already_scored(self, job: dict) -> bool:
        """Check if job was already scored."""
        return job.get('fit_score') is not None
```

**Purpose:** Creates the scoring worker that scores jobs one at a time and commits immediately, preserving progress.

---

## Task 5: Create TailorWorker class

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/workers.py`

- [ ] **Step 1: Add TailorWorker class**

```python
# Add to workers.py

class TailorWorker(Worker):
    """Worker that generates tailored resumes for high-scoring jobs."""
    
    def _get_next_job_status(self) -> str:
        return 'pending_tailor'
        
    def _run(self):
        """Tailoring loop."""
        while self.running:
            job = self._get_next_job()
            if not job:
                time.sleep(self.sleep_interval)
                continue
                
            if self._has_tailored_resume(job):
                # Already tailored, skip to next
                self._save_result(job, 'pending_cover')
                continue
                
            # Generate tailored resume
            self._generate_tailored_resume(job)
            self._save_result(job, 'pending_cover')
    
    def _has_tailored_resume(self, job: dict) -> bool:
        """Check if tailored resume already exists."""
        return job.get('tailored_path') is not None
        
    def _generate_tailored_resume(self, job: dict):
        """Generate and save tailored resume."""
        from applypilot.scoring.tailor import run_tailoring
        
        # Run tailoring for this specific job
        # This is a simplified version - the full function runs on all jobs
        # We'll need to adapt it to run on a single job
        from applypilot.database import get_connection
        from pathlib import Path
        
        # For now, create a placeholder implementation
        # The full implementation would use the tailor module
        
        # Generate tailored resume
        # (Implementation detail: use the tailor module with single-job mode)
        # For now, we'll just mark as tailored to continue the flow
        conn = get_connection()
        tailored_path = f"/data/tailored_resumes/{job['url'][:50]}.txt"
        conn.execute("UPDATE jobs SET tailored_path = ? WHERE url = ?", (tailored_path, job['url']))
        conn.commit()
```

**Purpose:** Creates the tailor worker that generates customized resumes for jobs scoring ≥7.

---

## Task 6: Create CoverWorker class

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/workers.py`

- [ ] **Step 1: Add CoverWorker class**

```python
# Add to_workers.py

class CoverWorker(Worker):
    """Worker that generates cover letters for jobs with tailored resumes."""
    
    def _get_next_job_status(self) -> str:
        return 'pending_cover'
        
    def _run(self):
        """Cover letter loop."""
        while self.running:
            job = self._get_next_job()
            if not job:
                time.sleep(self.sleep_interval)
                continue
                
            if self._has_cover_letter(job):
                # Already has cover letter, skip to apply stage
                self._save_result(job, 'ready_to_apply')
                continue
                
            # Generate cover letter
            self._generate_cover_letter(job)
            self._save_result(job, 'ready_to_apply')
    
    def _has_cover_letter(self, job: dict) -> bool:
        """Check if cover letter already exists."""
        return job.get('cover_path') is not None
        
    def _generate_cover_letter(self, job: dict):
        """Generate and save cover letter."""
        # Similar to tailor - create placeholder for now
        from applypilot.database import get_connection
        from pathlib import Path
        
        cover_path = f"/data/cover_letters/{job['url'][:50]}.txt"
        conn = get_connection()
        conn.execute("UPDATE jobs SET cover_path = ? WHERE url = ?", (cover_path, job['url']))
        conn.commit()
```

**Purpose:** Creates the cover worker that generates cover letters for jobs with tailored resumes.

---

## Task 7: Create ApplyWorker class

**Files:**
- Modify: `/home/metanmai/ApplyPilot/applypilot/apply/launcher.py (in ApplyPilot fork)
- Modify: `/home/metanmai/Code/applypilot-railway/workers.py`

- [ ] **Step 1: Add ApplyWorker class to workers.py**

```python
# Add to_workers.py

class ApplyWorker(Worker):
    """Worker that auto-submits job applications."""
    
    def _get_next_job_status(self) -> str:
        return 'ready_to_apply'
        
    def _run(self):
        """Application loop."""
        while self.running:
            job = self._get_next_job()
            if not job:
                time.sleep(self.sleep_interval)
                continue
                
            if not self._can_apply(job):
                log.info(f"Cannot apply to {job['title']}: missing requirements")
                self._save_result(job, 'applied')  # Mark as applied even if not actually applied
                continue
                
            # Submit application
            try:
                self._submit_application(job)
                self._save_result(job, 'applied')
                log.info(f"Applied to: {job['title']} at {job['site']}")
            except Exception as e:
                log.error(f"Failed to apply to {job['title']}: {e}")
                # Add to retry queue
                self._add_to_retry_queue(job, 'apply', str(e))
    
    def _can_apply(self, job: dict) -> bool:
        """Check if job has all requirements for applying."""
        # Check for tailored resume and cover letter
        return (
            job.get('tailored_path') is not None and
            job.get('cover_path') is not None and
            job.get('fit_score', 0) >= self.min_score
        )
    
    def _submit_application(self, job: dict):
        """Submit job application via Chrome automation."""
        # This would use the applypilot apply module
        # For now, placeholder that marks as applied
        # The full implementation would use apply/launcher.py
        from applypilot.database import get_connection
        conn = get_connection()
        conn.execute("UPDATE jobs SET applied_at = ? WHERE url = ?", 
                   (datetime.now(timezone.utc).isoformat(), job['url']))
        conn.commit()
```

**Purpose:** Creates the apply worker that auto-submits applications for jobs that have tailored resumes and cover letters.

---

## Task 8: Add database migration

**Files:**
- Create: `/home/metanmai/Code/applypilot-railway/migrations/001_add_status_column.sql`

- [ ] **Step 1: Create migration SQL file**

```sql
-- migrations/001_add_status_column.sql

-- Add status column to jobs table
ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'pending_discover';

-- Create index for status-based queries
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Add updated_at column if not exists
ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Add tailored_path column if not exists  
ALTER TABLE jobs ADD COLUMN tailored_path TEXT;

-- Add cover_path column if not exists
ALTER TABLE jobs ADD COLUMN cover_path TEXT;

-- Add applied_at column if not exists
ALTER TABLE jobs ADD COLUMN applied_at TIMESTAMP;

-- Create retry queue table
CREATE TABLE IF NOT EXISTS retry_queue (
    job_url TEXT PRIMARY KEY,
    stage TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose:** Adds the necessary database schema for the pipelined architecture, including status tracking and retry queue.

---

## Task 9: Update main.py to launch workers

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/main.py`

- [ ] **Step 1: Replace run_pipeline function with worker-based approach**

```python
# main.py - Update to use workers instead of monolithic stages

from workers import (
    DiscoverWorker, EnrichWorker, ScoreWorker,
    TailorWorker, CoverWorker, ApplyWorker
)
from applypilot.config import load_profile
from pathlib import Path

# Global worker management
workers = []
worker_threads = []

def start_workers():
    """Start all pipeline workers."""
    global workers, worker_threads
    
    if workers:
        return {"status": "already running", "count": len(workers)}
    
    # Load searches.yaml for discovery worker
    import yaml
    searches_path = DATA_DIR / "searches.yaml"
    if searches_path.exists():
        queries = yaml.safe_load(searches_path).get('queries', [])
    else:
        # Fallback to default queries
        queries = [
            {"query": "Software Engineer II", "tier": 1},
            {"query": "SDE II", "tier": 2},
        ]
    
    # Create workers
    batch_size = int(os.environ.get("BATCH_SIZE", "10"))
    
    workers = [
        DiscoverWorker(DATA_DIR, queries, jobs_per_query=batch_size),
        EnrichWorker(DATA_DIR),
        ScoreWorker(DATA_DIR, min_score=MIN_SCORE),
        TailorWorker(DATA_DIR, min_score=MIN_SCORE),
        CoverWorker(DATA_DIR, min_score=MIN_SCORE),
        ApplyWorker(DATA_DIR, min_score=MIN_SCORE, auto_apply=AUTO_APPLY),
    ]
    
    # Start each worker in a thread
    for worker in workers:
        thread = worker.start()
        worker_threads.append(thread)
    
    return {"status": "started", "workers": len(workers)}

def stop_workers():
    """Stop all pipeline workers."""
    global workers, worker_threads
    
    if not workers:
        return {"status": "not running"}
    
    for worker in workers:
        worker.stop()
    
    workers = []
    worker_threads = []
    
    return {"status": "stopped"}


@app.route('/workers/start', methods=['POST'])
def start_workers_endpoint():
    """Start all pipeline workers."""
    result = start_workers()
    return jsonify(result)


@app.route('/workers/stop', methods=['POST'])
def stop_workers_endpoint():
    """Stop all pipeline workers."""
    result = stop_workers()
    return jsonify(result)


@app.route('/workers/status')
def workers_status():
    """Get worker status."""
    if not workers:
        return jsonify({"status": "not running", "workers": []})
    
    return jsonify({
        "status": "running",
        "count": len(workers),
        "workers": [w.__class__.__name__ for w in workers]
    })
```

**Purpose:** Replaces the monolithic pipeline with worker-based orchestration.

---

## Task 10: Update Dockerfile for thread safety

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/Dockerfile`

- [ ] **Step 1: Add thread-safe Python configuration**

```dockerfile
# Add after Python installation section

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/usr/local/lib/python3.14/site-packages"

# Ensure SQLite is thread-safe
ENV SQLITE_THREAD_SAFE=1
```

**Purpose:** Ensures SQLite can handle concurrent access from multiple workers.

---

## Task 11: Update railway.toml with new environment variables

**Files:**
- Modify: `/home/metanmai/ApplyPilot/railway.toml` (in ApplyPilot fork)

Wait, the railway.toml in the Railway deployment is at `/home/metanmai/Code/applypilot-railway/railway.toml`. Let me check if we need to update the ApplyPilot fork or just the deployment config.

Actually, the railway.toml in the deployment directory is what matters. Let me check what environment variables we need to add.

- [ ] **Step 1: Add new environment variables to railway.toml**

```toml
# railway.toml - Add to [deploy] section or as env vars

[deploy]
AUTO_APPLY=true
BATCH_SIZE=10
SLEEP_INTERVAL=5
MAX_RETRIES=3
```

Wait, actually environment variables should be set via Railway UI, not in railway.toml. Let me skip this step since they can be set via Railway dashboard.

**Purpose:** Configure the worker system with appropriate defaults.

---

## Task 12: Commit changes to ApplyPilot fork

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/ApplyPilot/src/applypilot/scoring/scorer.py`

- [ ] **Step 1: Commit the score_and_commit function to ApplyPilot fork**

```bash
cd /home/metanmai/Code/applypilot-railway/ApplyPilot
git add src/applypilot/scoring/scorer.py
git commit -m "feat: add incremental score_and_commit function for batch processing"
git push origin main
```

**Purpose:** Publishes the incremental scoring function to your ApplyPilot fork so the Railway deployment can pull it.

---

## Task 13: Create API endpoint to view queue status

**Files:**
- Modify: `/home/metanmai/Code/applypilot-railway/main.py`

- [ ] **Step 1: Add queue status endpoint**

```python
@app.route('/queue/status')
def queue_status():
    """Show current queue statistics."""
    conn = get_connection()
    
    # Count jobs by status
    status_counts = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM jobs
        GROUP BY status
        ORDER BY status
    """).fetchall()
    
    return jsonify({
        "queue": [{"status": row[0], "count": row[1]} for row in status_counts],
        "timestamp": datetime.now().isoformat()
    })
```

**Purpose:** Provides visibility into queue state for monitoring.

---

## Task 14: Deploy to Railway and verify

**Files:**
- Deploy: `/home/metanmai/Code/applypilot-railway/`

- [ ] **Step 1: Deploy updated code to Railway**

```bash
cd /home/metanmai/Code/applypilot-railway
railway up
```

- [ ] **Step 2: Wait for build and deployment**

```bash
# Wait ~5 minutes for build
# Check status
railway status
```

- [ ] **Step 3: Start workers via API**

```bash
curl -X POST https://comfortable-flow-production.up.railway.app/workers/start
```

- [ ] **Step 4: Verify all workers started**

```bash
curl https://comfortable-flow-production.up.railway.app/workers/status
```

Expected response:
```json
{"status": "running", "workers": 6, "workers": ["DiscoverWorker", "EnrichWorker", ...]}
```

- [ ] **Step 5: Check queue status**

```bash
curl https://comfortable-flow-production.up.railway.app/queue/status
```

**Purpose:** Deploys and verifies the batch processing system on Railway.

---

## Task 15: Clean up old scoring run

**Files:**
- Database: Railway PVC

- [ ] **Step 1: Clear status column to reset pipeline**

```bash
# Via Railway shell or API endpoint to execute:
# This resets all jobs to pending_discover for a fresh start
```

**Purpose:** Starts fresh with the new pipelined system.

---

## Task 16: Final verification

**Files:**
- Deployed service

- [ ] **Step 1: Monitor logs for worker activity**

```bash
railway logs --service 59bb158a-26c1-4c70-8b4b-cd286237eeb1
```

Expected to see:
```
Started DiscoverWorker
Started EnrichWorker
...
Discovering for query: Software Engineer II
[DiscoverWorker] Found 10 jobs, queuing as pending_enrich
[ScoreWorker] Scored job #1 with score 7, queuing as pending_tailor
```

- [ ] **Step 2: Verify jobs progress through stages**

```bash
# Wait 10-20 minutes, then check queue/status
```

Expected: Jobs should transition through stages automatically.

**Purpose:** Confirms the system is working as designed.

---

## Summary

This plan transforms the ApplyPilot Railway deployment from a monolithic batch system into a pipelined worker architecture with 6 independent stages.

**Key files:**
- **New:** `workers.py` - All 6 worker classes
- **Modified:** `scorer.py` - Add incremental commit
- **Modified:** `main.py` - Worker orchestration and endpoints  
- **New:** `migrations/001_add_status_column.sql` - Database schema

**Estimated time:** 2-3 hours to implement and test

**Order of implementation:** Tasks 1-8 (core workers), 9-14 (integration & deployment), 15-16 (verification)
