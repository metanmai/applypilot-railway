# Batch Processing Pipeline Design for ApplyPilot Railway Deployment

**Date:** 2026-04-12
**Author:** Claude (assisted by user requirements)
**Status:** Draft

## Overview

This document describes the redesign of the ApplyPilot Railway deployment to use a pipelined, worker-based architecture where each stage runs independently as a continuously-running process. Jobs flow through stages via a queue-based system, preserving progress and enabling true set-and-forget operation.

## Problem Statement

### Current Issues

1. **Monolithic batch processing**: All 842 jobs processed in one batch per stage
2. **Progress loss**: If interrupted, all in-progress work is lost (scores committed only at end)
3. **Blocking stages**: Each stage must complete before next starts
4. **No resumability**: Interruption during scoring requires restarting from job 1

### Requirements

1. **Small batches**: Process ~10 jobs at a time through entire pipeline
2. **Independent stages**: Each stage runs as separate process, never blocks others
3. **Progress preservation**: Work saved after each job completion
4. **Full auto-apply**: Jobs scoring ≥7 automatically applied (no manual approval)
5. **Query exhaustion**: Complete one search query before moving to next
6. **Deduplication**: Never re-process work already completed

## Architecture

### Worker-Based Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│ SQLite Job Queue (jobs table with status field)                      │
│ ┌─────────────────────────────────────────────────────────────┐│
│ │ Status flow: pending_discover → pending_enrich → pending_score ││
│ │            → pending_tailor → pending_cover → ready_to_apply    ││
│ │            → applied                                          ││
│ └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
        ┌────────────────────────────────────────────────────────┴─────┐
        │                  │                  │                  │        │
        ▼                  ▼                  ▼                  ▼        ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   ┌──────────────┐
│ DISCOVER     │    │ ENRICH       │    │ SCORE        │    │ TAILOR       │   │ COVER        │
│ WORKER       │    │ WORKER       │    │ WORKER       │    │ WORKER       │   │ WORKER       │
│              │    │              │    │              │    │              │   │              │
│ Polls queue   │    │ Polls queue   │    │ Polls queue   │    │ Polls queue   │   │ Polls queue   │
│ Processes     │    │ Processes     │    │ Processes     │    │ Processes     │   │ Processes     │
│              │    │              │    │              │    │              │   │              │
│ Updates       │    │ Updates       │    │ Updates       │    │ Updates       │   │ Updates       │
│ status       │    │ status       │    │ status       │    │ status       │   │ status       │
│              │    │              │    │              │    │              │   │              │
│ Runs forever │    │ Runs forever  │    │ Runs forever  │    │ Runs forever  │   │ Runs forever  │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘   └──────────────┘
```

### Key Design Decisions

1. **SQLite as queue**: Jobs table with `status` field acts as work queue
2. **Status enum**: `pending_discover`, `pending_enrich`, `pending_score`, `pending_tailor`, `pending_cover`, `ready_to_apply`, `applied`
3. **Independent workers**: Each stage is a thread in Flask app
4. **No batches**: Workers pull one job at a time, process immediately, commit, repeat
5. **Discovery throttling**: Process one search query at a time, exhaust before moving to next

## Components

### New File: `workers.py`

Contains worker classes for each pipeline stage:

```python
class DiscoverWorker:
    """Continuously discovers jobs from job boards."""
    def __init__(self, queries: List[Dict]):
        self.queries = queries
        self.current_query_index = 0
    
    def run(self):
        while self.running:
            query = self._get_next_query()
            jobs = self._discover_jobs_for_query(query, limit=10)
            for job in jobs:
                self._save_to_queue(job, status='pending_enrich')
                self._update_query_status(query, jobs_found=len(jobs))

class EnrichWorker:
    """Enriches jobs with full descriptions."""
    def run(self):
        while self.running:
            job = self._get_next_job('pending_enrich')
            if not job:
                time.sleep(5)
                continue
            full_description = fetch_full_description(job)
            self._save_result(job, status='pending_score')

class ScoreWorker:
    """Scores jobs and commits immediately."""
    def run(self):
        while self.running:
            job = self._get_next_job('pending_score')
            if not job or self._is_already_scored(job):
                time.sleep(5)
                continue
            result = score_job(job)
            self._commit_score(job, result)
            if result['score'] >= MIN_SCORE:
                self._save_result(job, status='pending_tailor')

class TailorWorker:
    """Generates tailored resumes."""
    def run(self):
        while self.running:
            job = self._get_next_job('pending_tailor')
            if not job:
                time.sleep(5)
                continue
            if self._has_tailored_resume(job):
                self._save_result(job, status='pending_cover')
                continue
            resume = generate_tailored_resume(job)
            self._save_result(job, status='pending_cover', tailored_path=resume)

class CoverWorker:
    """Generates cover letters."""
    def run(self):
        while self.running:
            job = self._self._get_next_job('pending_cover')
            if not job or self._has_cover_letter(job):
                time.sleep(5)
                continue
            cover_letter = generate_cover_letter(job)
            self._save_result(job, status='ready_to_apply', cover_path=cover_letter)

class ApplyWorker:
    """Auto-submits applications."""
    def run(self):
        while self.running:
            job = self._get_next_job('ready_to_apply')
            if not job or not self._can_apply(job):
                time.sleep(5)
                continue
            submit_application(job)
            self._save_result(job, status='applied')
```

### Modified File: `scorer.py` (in ApplyPilot fork)

Add incremental commit function:

```python
def score_and_commit(job: dict) -> dict:
    """Score a single job and commit immediately."""
    result = score_job(job)  # existing function
    conn = get_connection()
    conn.execute(
        "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
        (result['score'], f"{result['keywords']}\n{result['reasoning']}", datetime.now(timezone.utc).isoformat(), job['url'])
    )
    conn.commit()
    return result
```

### Modified File: `main.py`

Update to launch all workers:

```python
@app.route('/start', methods=['POST'])
def start_workers():
    """Start all pipeline workers."""
    global workers
    if workers:
        return jsonify({"status": "already running"})
    
    workers = []
    
    # Start each worker in a thread
    for WorkerClass in [DiscoverWorker, EnrichWorker, ScoreWorker, TailorWorker, CoverWorker, ApplyWorker]:
        thread = threading.Thread(target=WorkerClass(...).run, daemon=True)
        thread.start()
        workers.append(thread)
    
    return jsonify({"status": "started", "workers": len(workers)})
```

### Database Schema Updates

Add status column to jobs table:

```sql
ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'pending_discover';
CREATE INDEX idx_jobs_status ON jobs(status);
```

Add query exhaustion tracking:

```sql
ALTER TABLE searches ADD COLUMN exhausted INTEGER DEFAULT 0;
```

## Data Flow

1. **System starts**: All 6 workers begin polling their respective queues
2. **DiscoverWorker**: Runs jobspy for current query, saves jobs with status='pending_enrich'
3. **EnrichWorker**: Picks up jobs with status='pending_enrich', fetches full descriptions, updates to status='pending_score'
4. **ScoreWorker**: Picks up jobs with status='pending_score', scores and commits, if score≥7 updates to status='pending_tailor'
5. **TailorWorker**: Picks up jobs with status='pending_tailor', generates resume, updates to status='pending_cover'
6. **CoverWorker**: Picks up jobs with status='pending_cover', generates letter, updates to status='ready_to_apply'
7. **ApplyWorker**: Picks up jobs with status='ready_to_apply', submits application, updates to status='applied'

Each worker:
- Polls for work every 5 seconds when queue is empty
- Processes one job at a time
- Commits immediately after processing
- Never blocks other workers
- Runs continuously (until stopped)

## Error Handling

### Worker-level Errors

**Database errors:**
- Connection pool retries with exponential backoff
- Deadlock detection with automatic retry
- Query timeouts (30s) prevent hanging

**API errors:**
- Rate limits (429): wait 60s, retry job
- Token limits: split requests
- Context length: truncate and retry with summary
- Other API errors: mark job for retry queue

### Retry Queue

Failed jobs are tracked in `retry_queue` table:

```sql
CREATE TABLE retry_queue (
    job_url TEXT PRIMARY KEY,
    stage TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_retry_at TIMESTAMP
)
```

### Recovery Mechanism

- Workers auto-retry on transient errors
- Jobs move to retry_queue after max retries
- Cron job re-processes failed jobs hourly
- Workers mark themselves as stopped on shutdown

## Configuration

### Environment Variables

```bash
BATCH_SIZE=10              # Jobs per discovery query (not used in worker model)
MIN_SCORE=7                # Minimum score for tailor/cover/apply
AUTO_APPLY=true            # Enable auto-apply
SLEEP_INTERVAL=5            # Poll interval when queue empty (seconds)
MAX_RETRIES=3              # Retry attempts before giving up
```

### API Endpoints

- `POST /start` - Start all workers
- `POST /stop` - Stop all workers
- `GET /workers` - Check worker status
- `GET /queue` - View queue statistics
- `GET /retry` - View retry queue

## Testing Plan

### Unit Tests

- Worker polling behavior when queue empty
- Single job processing through all stages
- Deduplication prevents reprocessing
- Incremental commits preserve data

### Integration Tests

- Full pipeline with 10 test jobs
- Worker independence (one worker doesn't block another)
- Error recovery (failed job gets retried)

### Deployment Tests

- Verify all 6 workers started on Railway
- Monitor queue status via `/db/stats`
- Verify jobs progress through stages independently
- Check logs for worker activity

## Migration Path

1. Deploy to Railway (current run will be lost)
2. Run database migration (add status column)
3. Start workers via `/start` endpoint
4. Monitor via `/workers` and `/queue` endpoints

## Success Criteria

- All 6 workers run independently
- Jobs progress through stages without blocking
- Progress preserved if deployment interrupted
- Auto-apply works for jobs scoring ≥7
- No duplicate work across stages
