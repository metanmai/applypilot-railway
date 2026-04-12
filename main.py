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
from flask import Flask, jsonify, request

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


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ApplyPilot Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #334155;
        }
        h1 { font-size: 24px; font-weight: 600; }
        .status-badge {
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 500;
        }
        .status-healthy { background: #059669; color: white; }
        .status-error { background: #dc2626; color: white; }
        .last-update { font-size: 13px; color: #94a3b8; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card {
            background: #1e293b;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #334155;
        }
        .card h2 {
            font-size: 14px;
            font-weight: 600;
            color: #94a3b8;
            margin-bottom: 15px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #334155;
        }
        .stat-row:last-child { border-bottom: none; }
        .stat-label { color: #94a3b8; }
        .stat-value { font-weight: 600; }
        .worker-list { display: flex; flex-wrap: wrap; gap: 8px; }
        .worker-badge {
            padding: 6px 12px;
            background: #334155;
            border-radius: 6px;
            font-size: 12px;
        }
        .worker-badge.running { background: #059669; }
        .pipeline-bar {
            height: 40px;
            background: #334155;
            border-radius: 8px;
            display: flex;
            overflow: hidden;
        }
        .pipeline-stage {
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 600;
            transition: width 0.5s ease;
        }
        .stage-discover { background: #3b82f6; }
        .stage-enrich { background: #8b5cf6; }
        .stage-score { background: #f59e0b; }
        .stage-tailor { background: #10b981; }
        .stage-cover { background: #06b6d4; }
        .stage-ready { background: #f97316; }
        .stage-applied { background: #059669; }
        .job-list { max-height: 400px; overflow-y: auto; }
        .job-item {
            padding: 12px;
            border-bottom: 1px solid #334155;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .job-item:last-child { border-bottom: none; }
        .score-badge {
            min-width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 13px;
        }
        .score-9 { background: #059669; color: white; }
        .score-8 { background: #10b981; color: white; }
        .score-7 { background: #f59e0b; color: white; }
        .job-info { flex: 1; }
        .job-title { font-weight: 500; margin-bottom: 4px; }
        .job-meta { font-size: 12px; color: #94a3b8; }
        .site-badge {
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            background: #334155;
        }
        .logs-container {
            background: #0f172a;
            border-radius: 8px;
            padding: 15px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 12px;
        }
        .log-entry { padding: 4px 0; color: #94a3b8; }
        .log-entry.info { color: #60a5fa; }
        .log-entry.error { color: #f87171; }
        .log-entry.success { color: #34d399; }
        .score-dist { display: flex; flex-direction: column; gap: 8px; }
        .score-bar {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .score-bar-label { width: 30px; font-weight: 600; }
        .score-bar-track { flex: 1; height: 24px; background: #334155; border-radius: 4px; overflow: hidden; }
        .score-bar-fill { height: 100%; background: linear-gradient(90deg, #f97316, #059669); transition: width 0.5s ease; }
        .score-bar-count { width: 40px; text-align: right; font-size: 12px; color: #94a3b8; }
        .refresh-btn {
            padding: 8px 16px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
        }
        .refresh-btn:hover { background: #2563eb; }
        .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .auto-refresh {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 13px;
            color: #94a3b8;
        }
        .toggle-switch {
            position: relative;
            width: 40px;
            height: 22px;
            background: #334155;
            border-radius: 11px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .toggle-switch.active { background: #059669; }
        .toggle-switch::after {
            content: '';
            position: absolute;
            width: 18px;
            height: 18px;
            background: white;
            border-radius: 50%;
            top: 2px;
            left: 2px;
            transition: transform 0.3s;
        }
        .toggle-switch.active::after { transform: translateX(18px); }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #1e293b; }
        ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>ApplyPilot Dashboard</h1>
                <p class="last-update">Last updated: <span id="lastUpdate">-</span></p>
            </div>
            <div style="display: flex; align-items: center; gap: 15px;">
                <div class="auto-refresh">
                    <span>Auto-refresh</span>
                    <div class="toggle-switch active" id="autoRefreshToggle"></div>
                </div>
                <button class="refresh-btn" id="refreshBtn">Refresh Now</button>
                <span class="status-badge status-healthy" id="healthStatus">● Healthy</span>
            </div>
        </header>

        <div class="grid">
            <div class="card">
                <h2>Workers</h2>
                <div class="stat-row">
                    <span class="stat-label">Running</span>
                    <span class="stat-value" id="workersRunning">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Threads Alive</span>
                    <span class="stat-value" id="threadsAlive">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Auto-Apply</span>
                    <span class="stat-value" id="autoApply">-</span>
                </div>
                <div style="margin-top: 15px;">
                    <span class="stat-label" style="font-size: 12px;">Active Workers:</span>
                    <div class="worker-list" id="workerList" style="margin-top: 10px;"></div>
                </div>
            </div>

            <div class="card">
                <h2>Pipeline Queue</h2>
                <div class="pipeline-bar" id="pipelineBar"></div>
                <div style="margin-top: 15px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; font-size: 11px;">
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #3b82f6; border-radius: 2px;"></span> Discover</div>
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #8b5cf6; border-radius: 2px;"></span> Enrich</div>
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #f59e0b; border-radius: 2px;"></span> Score</div>
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #10b981; border-radius: 2px;"></span> Tailor</div>
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #06b6d4; border-radius: 2px;"></span> Cover</div>
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #f97316; border-radius: 2px;"></span> Ready</div>
                    <div style="display: flex; align-items: center; gap: 5px;"><span style="width: 10px; height: 10px; background: #059669; border-radius: 2px;"></span> Applied</div>
                </div>
            </div>

            <div class="card">
                <h2>Job Statistics</h2>
                <div class="stat-row">
                    <span class="stat-label">Total Jobs</span>
                    <span class="stat-value" id="totalJobs">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Scored (≥7)</span>
                    <span class="stat-value" id="highScoreJobs">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">With Description</span>
                    <span class="stat-value" id="withDesc">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Min Score Threshold</span>
                    <span class="stat-value" id="minScore">-</span>
                </div>
            </div>
        </div>

        <div class="grid">
            <div class="card" style="grid-column: span 2;">
                <h2>Top High-Scoring Jobs (Score ≥7)</h2>
                <div class="job-list" id="topJobs"></div>
            </div>

            <div class="card">
                <h2>Score Distribution</h2>
                <div class="score-dist" id="scoreDist"></div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Jobs by Site</h2>
                <div id="bySite"></div>
            </div>

            <div class="card">
                <h2>System Logs</h2>
                <div class="logs-container" id="logs">
                    <div class="log-entry">Loading logs...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = window.location.origin;
        let autoRefresh = true;
        let refreshInterval;

        async function fetchJSON(endpoint) {
            try {
                const res = await fetch(`${API_BASE}${endpoint}`);
                return await res.json();
            } catch (e) {
                console.error(`Failed to fetch ${endpoint}:`, e);
                return null;
            }
        }

        function updateLastUpdate() {
            document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
        }

        async function updateWorkers() {
            const data = await fetchJSON('/workers/status');
            if (!data) return;

            document.getElementById('workersRunning').textContent = data.count;
            document.getElementById('threadsAlive').textContent = data.threads_alive;

            const workerList = document.getElementById('workerList');
            workerList.innerHTML = data.workers.map(w =>
                `<span class="worker-badge running">${w}</span>`
            ).join('');
        }

        async function updateHealth() {
            const data = await fetchJSON('/health');
            if (!data) return;

            const badge = document.getElementById('healthStatus');
            if (data.status === 'healthy') {
                badge.className = 'status-badge status-healthy';
                badge.textContent = '● Healthy';
            } else {
                badge.className = 'status-badge status-error';
                badge.textContent = '● Error';
            }

            document.getElementById('autoApply').textContent = data.auto_apply ? 'Enabled' : 'Disabled';
            document.getElementById('minScore').textContent = data.min_score;
        }

        async function updateQueue() {
            const data = await fetchJSON('/queue/status');
            if (!data) return;

            const stageMap = {
                'pending_discover': { label: 'Discover', class: 'stage-discover', count: 0 },
                'pending_enrich': { label: 'Enrich', class: 'stage-enrich', count: 0 },
                'pending_score': { label: 'Score', class: 'stage-score', count: 0 },
                'pending_tailor': { label: 'Tailor', class: 'stage-tailor', count: 0 },
                'pending_cover': { label: 'Cover', class: 'stage-cover', count: 0 },
                'ready_to_apply': { label: 'Ready', class: 'stage-ready', count: 0 },
                'applied': { label: 'Applied', class: 'stage-applied', count: 0 }
            };

            data.queue.forEach(q => {
                if (stageMap[q.status]) {
                    stageMap[q.status].count = q.count;
                }
            });

            const total = Object.values(stageMap).reduce((sum, s) => sum + s.count, 0);
            const bar = document.getElementById('pipelineBar');
            bar.innerHTML = Object.values(stageMap).map(stage => {
                if (stage.count === 0) return '';
                const width = (stage.count / total * 100).toFixed(1);
                return `<div class="pipeline-stage ${stage.class}" style="width: ${width}%" title="${stage.label}: ${stage.count}">${stage.count}</div>`;
            }).join('');
        }

        async function updateStats() {
            const data = await fetchJSON('/db/stats');
            if (!data) return;

            document.getElementById('totalJobs').textContent = data.total_jobs;
            document.getElementById('withDesc').textContent = data.with_description;

            const highScore = data.score_distribution.filter(s => s.score >= 7).reduce((sum, s) => sum + s.count, 0);
            document.getElementById('highScoreJobs').textContent = highScore;

            const topJobs = document.getElementById('topJobs');
            topJobs.innerHTML = data.top_jobs.map(job => `
                <div class="job-item">
                    <span class="score-badge score-${job.score}">${job.score}</span>
                    <div class="job-info">
                        <div class="job-title">${job.title}</div>
                        <div class="job-meta">
                            <span class="site-badge">${job.site}</span>
                        </div>
                    </div>
                </div>
            `).join('');

            const scoreDist = document.getElementById('scoreDist');
            const maxCount = Math.max(...data.score_distribution.map(s => s.count), 1);
            scoreDist.innerHTML = data.score_distribution.map(s => `
                <div class="score-bar">
                    <span class="score-bar-label">${s.score}</span>
                    <div class="score-bar-track">
                        <div class="score-bar-fill" style="width: ${(s.count / maxCount * 100)}%"></div>
                    </div>
                    <span class="score-bar-count">${s.count}</span>
                </div>
            `).join('');

            const bySite = document.getElementById('bySite');
            const total = data.by_site.reduce((sum, s) => sum + s.count, 0);
            bySite.innerHTML = data.by_site.map(s => `
                <div class="stat-row">
                    <span class="stat-label">${s.site}</span>
                    <span class="stat-value">${s.count} (${(s.count / total * 100).toFixed(1)}%)</span>
                </div>
            `).join('');
        }

        async function updateAll() {
            await Promise.all([
                updateHealth(),
                updateWorkers(),
                updateQueue(),
                updateStats()
            ]);
            updateLastUpdate();
        }

        function startAutoRefresh() {
            if (refreshInterval) clearInterval(refreshInterval);
            if (autoRefresh) {
                refreshInterval = setInterval(updateAll, 5000);
            }
        }

        document.getElementById('refreshBtn').addEventListener('click', async () => {
            const btn = document.getElementById('refreshBtn');
            btn.disabled = true;
            btn.textContent = 'Refreshing...';
            await updateAll();
            btn.disabled = false;
            btn.textContent = 'Refresh Now';
        });

        document.getElementById('autoRefreshToggle').addEventListener('click', function() {
            autoRefresh = !autoRefresh;
            this.classList.toggle('active', autoRefresh);
            startAutoRefresh();
        });

        updateAll();
        startAutoRefresh();
    </script>
</body>
</html>"""

@app.route('/')
def dashboard():
    """Dashboard UI."""
    return DASHBOARD_HTML


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
