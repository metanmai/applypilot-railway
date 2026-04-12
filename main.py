"""
ApplyPilot Railway Service
Runs continuously using worker-based pipeline orchestration.

Pipeline workers:
1. DiscoverWorker - Find jobs from job boards
2. EnrichWorker - Get full job descriptions
3. ScoreWorker - Rate job fit (1-10)
4. TailorWorker - Generate tailored resumes for high-scoring jobs
5. CoverWorker - Write cover letters
6. ApplyWorker - Auto-submit applications
"""

import os
import logging
import threading
from datetime import datetime
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, render_template, send_from_directory

# ApplyPilot imports
from applypilot.config import load_profile, RESUME_PATH
from applypilot.database import get_connection

# Worker imports
from workers import (
    DiscoverWorker, EnrichWorker, ScoreWorker,
    TailorWorker, CoverWorker, ApplyWorker
)

# Configuration
DATA_DIR = Path(os.environ.get("APPLYPILOT_DATA_DIR", "/data"))
MIN_SCORE = int(os.environ.get("APPLYPILOT_MIN_SCORE", "7"))
PORT = int(os.environ.get("PORT", "8080"))

# Auto-apply toggle (set to false if you want to review before applying)
AUTO_APPLY = os.environ.get("AUTO_APPLY", "false").lower() == "true"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# Flask app for healthchecks
app = Flask(__name__)

# Global worker management
workers = []
worker_threads = []


def ensure_data_dir():
    """Ensure data directory exists and is writable."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Data directory: {DATA_DIR}")


def start_workers():
    """Start all pipeline workers."""
    global workers, worker_threads

    if workers:
        log.info("Workers already running")
        return {"status": "already running", "count": len(workers)}

    # Load searches.yaml for discovery worker
    searches_path = DATA_DIR / "searches.yaml"
    if searches_path.exists():
        with open(searches_path) as f:
            queries = yaml.safe_load(f).get('queries', [])
    else:
        # Fallback to default queries
        queries = [
            {"query": "Software Engineer II", "tier": 1},
            {"query": "SDE II", "tier": 2},
        ]
        log.info(f"Using default queries (no searches.yaml found at {searches_path})")

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
        if thread:
            worker_threads.append(thread)

    log.info(f"Started {len(workers)} pipeline workers")
    return {"status": "started", "workers": len(workers)}


def stop_workers():
    """Stop all pipeline workers."""
    global workers, worker_threads

    if not workers:
        log.info("No workers running")
        return {"status": "not running"}

    for worker in workers:
        worker.stop()

    # Wait for threads to finish
    for thread in worker_threads:
        if thread.is_alive():
            thread.join(timeout=5)

    workers = []
    worker_threads = []

    log.info("Stopped all pipeline workers")
    return {"status": "stopped"}


# Flask routes
@app.route('/health')
def health():
    """Health check endpoint for Railway."""
    return jsonify({
        "status": "healthy",
        "workers_running": len(workers),
        "worker_threads": len(worker_threads),
        "data_dir": str(DATA_DIR),
        "min_score": MIN_SCORE,
        "auto_apply": AUTO_APPLY,
    })


@app.route('/')
def dashboard():
    """Dashboard UI."""
    return render_template('dashboard.html')


@app.route('/api')
def api_index():
    """API index with status."""
    return jsonify({
        "service": "ApplyPilot Railway - Worker Pipeline",
        "status": "running",
        "workers": len(workers),
        "worker_names": [w.__class__.__name__ for w in workers],
        "auto_apply": AUTO_APPLY,
    })


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
        "workers": [w.__class__.__name__ for w in workers],
        "threads_alive": sum(1 for t in worker_threads if t.is_alive())
    })


@app.route('/db/stats')
def db_stats():
    """Get database statistics."""
    conn = get_connection()

    # Overall stats
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    scored = conn.execute("SELECT COUNT(fit_score) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
    with_desc = conn.execute("SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL").fetchone()[0]

    # Score distribution
    score_dist = conn.execute("""
        SELECT fit_score, COUNT(*) as count
        FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score
        ORDER BY fit_score DESC
    """).fetchall()

    # Jobs by site
    by_site = conn.execute("""
        SELECT site, COUNT(*) as count
        FROM jobs
        GROUP BY site
        ORDER BY count DESC
    """).fetchall()

    # High scoring jobs (7+)
    high_scores = conn.execute("""
        SELECT title, site, fit_score, score_reasoning
        FROM jobs
        WHERE fit_score >= 7
        ORDER BY fit_score DESC
        LIMIT 20
    """).fetchall()

    # Status distribution (for pipeline monitoring)
    status_dist = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM jobs
        GROUP BY status
        ORDER BY count DESC
    """).fetchall()

    return jsonify({
        "total_jobs": total,
        "scored": scored,
        "with_description": with_desc,
        "pending_score": total - scored,
        "score_distribution": [{"score": row[0], "count": row[1]} for row in score_dist],
        "by_site": [{"site": row[0], "count": row[1]} for row in by_site],
        "top_jobs": [{"title": row[0], "site": row[1], "score": row[2]} for row in high_scores],
        "status_distribution": [{"status": row[0], "count": row[1]} for row in status_dist]
    })


@app.route('/db/jobs')
def db_jobs():
    """Get jobs with optional filters."""
    conn = get_connection()

    # Get query params
    min_score = request.args.get('min_score', type=int)
    limit = request.args.get('limit', 20, type=int)
    site = request.args.get('site')

    query = "SELECT title, site, location, fit_score, url FROM jobs WHERE 1=1"
    params = []

    if min_score is not None:
        query += " AND fit_score >= ?"
        params.append(min_score)

    if site:
        query += " AND site = ?"
        params.append(site)

    query += " ORDER BY fit_score DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    # Convert to list of dicts
    columns = ['title', 'site', 'location', 'fit_score', 'url']
    jobs = [dict(zip(columns, row)) for row in rows]

    return jsonify({
        "count": len(jobs),
        "jobs": jobs
    })


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


@app.route('/migrate', methods=['POST'])
def run_migration():
    """Run database migration for pipelined architecture."""
    conn = get_connection()
    results = []

    try:
        # Check if status column exists
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'status' not in columns:
            conn.execute('ALTER TABLE jobs ADD COLUMN status TEXT')
            conn.execute("UPDATE jobs SET status = 'pending_discover'")
            results.append("Added status column")
        else:
            results.append("status column already exists")

        # Create index
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)')
        results.append("Created/verified idx_jobs_status")

        # Add other columns if they don't exist
        if 'updated_at' not in columns:
            conn.execute('ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP')
            conn.execute("UPDATE jobs SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
            results.append("Added updated_at column")

        if 'tailored_path' not in columns:
            conn.execute('ALTER TABLE jobs ADD COLUMN tailored_path TEXT')
            results.append("Added tailored_path column")

        if 'cover_path' not in columns:
            conn.execute('ALTER TABLE jobs ADD COLUMN cover_path TEXT')
            results.append("Added cover_path column")

        if 'applied_at' not in columns:
            conn.execute('ALTER TABLE jobs ADD COLUMN applied_at TIMESTAMP')
            results.append("Added applied_at column")

        # Create retry queue table
        conn.execute('''CREATE TABLE IF NOT EXISTS retry_queue (
            job_url TEXT PRIMARY KEY,
            stage TEXT,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            last_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        results.append("Created/verified retry_queue table")

        conn.commit()

        return jsonify({
            "status": "success",
            "results": results
        })
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@app.route('/migrate/repair', methods=['POST'])
def repair_job_statuses():
    """Update existing jobs to correct pipeline status based on their current state."""
    conn = get_connection()
    results = []

    try:
        MIN_SCORE = int(os.environ.get("APPLYPILOT_MIN_SCORE", "7"))

        # Jobs with fit_score >= 7 should go to pending_tailor
        # Jobs with fit_score < 7 should go to applied (end of pipeline for low scores)
        conn.execute(f"""
            UPDATE jobs
            SET status = CASE
                WHEN fit_score >= {MIN_SCORE} THEN 'pending_tailor'
                ELSE 'applied'
            END
            WHERE status = 'pending_discover' AND fit_score IS NOT NULL
        """)
        updated = conn.execute("SELECT changes()").fetchone()[0]
        results.append(f"Updated {updated} scored jobs to appropriate status")

        conn.commit()

        return jsonify({
            "status": "success",
            "results": results
        })
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


if __name__ == '__main__':
    # Ensure data directory exists
    ensure_data_dir()

    # Start workers automatically on startup
    start_workers()

    # Run Flask server
    log.info(f"Starting Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT)
# Deployment marker Sun Apr 12 17:21:35 UTC 2026
