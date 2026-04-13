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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

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
from activity_log import log_activity, get_activity, clear_activity

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
def index():
    """Dashboard HTML page."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ApplyPilot Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 15px; border-bottom: 1px solid #334155; }
        h1 { font-size: 22px; font-weight: 600; }
        .status-badge { padding: 5px 12px; border-radius: 15px; font-size: 12px; font-weight: 500; }
        .status-healthy { background: #059669; color: white; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .card { background: #1e293b; border-radius: 10px; padding: 15px; border: 1px solid #334155; }
        .card h2 { font-size: 12px; font-weight: 600; color: #94a3b8; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #334155; font-size: 13px; }
        .stat-row:last-child { border-bottom: none; }
        .stat-label { color: #94a3b8; }
        .stat-value { font-weight: 600; }
        .stat-value.highlight { color: #10b981; }
        .pipeline-bar { height: 30px; background: #334155; border-radius: 6px; display: flex; overflow: hidden; font-size: 11px; }
        .pipeline-stage { display: flex; align-items: center; justify-content: center; font-weight: 600; }
        .stage-discover { background: #3b82f6; }
        .stage-enrich { background: #8b5cf6; }
        .stage-score { background: #f59e0b; }
        .stage-tailor { background: #10b981; }
        .stage-cover { background: #06b6d4; }
        .stage-ready { background: #f97316; }
        .stage-applied { background: #059669; }
        .activity-feed { max-height: 350px; overflow-y: auto; font-size: 12px; }
        .activity-item { padding: 10px; border-bottom: 1px solid #334155; display: flex; gap: 10px; }
        .activity-item:last-child { border-bottom: none; }
        .activity-time { color: #64748b; min-width: 60px; font-size: 11px; }
        .activity-content { flex: 1; }
        .activity-worker { font-weight: 600; color: #60a5fa; }
        .activity-message { color: #94a3b8; margin-top: 2px; }
        .activity-item.success .activity-worker { color: #10b981; }
        .activity-item.info .activity-worker { color: #60a5fa; }
        .activity-item.apply .activity-worker { color: #f97316; }
        .job-list { max-height: 300px; overflow-y: auto; font-size: 12px; }
        .job-item { padding: 8px; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 10px; }
        .job-item:last-child { border-bottom: none; }
        .score-badge { min-width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 11px; }
        .score-9, .score-8 { background: #059669; color: white; }
        .score-7 { background: #f59e0b; color: white; }
        .job-info { flex: 1; }
        .job-title { font-weight: 500; margin-bottom: 2px; }
        .job-meta { font-size: 11px; color: #94a3b8; }
        .site-badge { padding: 2px 8px; border-radius: 3px; font-size: 10px; background: #334155; }
        .applied-list { max-height: 250px; overflow-y: auto; }
        .applied-item { padding: 8px; border-bottom: 1px solid #334155; font-size: 12px; }
        .applied-item:last-child { border-bottom: none; }
        .applied-title { font-weight: 500; margin-bottom: 2px; }
        .applied-time { font-size: 11px; color: #10b981; }
        .refresh-btn { padding: 6px 14px; background: #3b82f6; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 12px; }
        .refresh-btn:hover { background: #2563eb; }
        .last-update { font-size: 12px; color: #94a3b8; }
        .worker-list { display: flex; flex-wrap: wrap; gap: 6px; }
        .worker-badge { padding: 4px 10px; background: #334155; border-radius: 5px; font-size: 11px; }
        .worker-badge.running { background: #059669; }
        .count-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 15px; }
        .count-card { background: #1e293b; border-radius: 8px; padding: 12px; border: 1px solid #334155; text-align: center; }
        .count-number { font-size: 24px; font-weight: 700; color: #e2e8f0; }
        .count-label { font-size: 11px; color: #94a3b8; margin-top: 4px; }
        .count-card.applied .count-number { color: #10b981; }
        .count-card.ready .count-number { color: #f97316; }
        .count-card.high .count-number { color: #059669; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #1e293b; }
        ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>ApplyPilot Dashboard</h1>
                <p class="last-update">Last updated: <span id="lastUpdate">-</span></p>
            </div>
            <button class="refresh-btn" onclick="updateAll()">Refresh Now</button>
        </header>

        <div class="count-cards">
            <div class="count-card applied">
                <div class="count-number" id="appliedCount">-</div>
                <div class="count-label">Applications Submitted</div>
            </div>
            <div class="count-card ready">
                <div class="count-number" id="readyCount">-</div>
                <div class="count-label">Ready to Apply</div>
            </div>
            <div class="count-card high">
                <div class="count-number" id="highScoreCount">-</div>
                <div class="count-label">High Score (≥7)</div>
            </div>
            <div class="count-card">
                <div class="count-number" id="totalCount">-</div>
                <div class="count-label">Total Jobs</div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Real-Time Activity</h2>
                <div class="activity-feed" id="activityFeed">
                    <div style="padding: 20px; text-align: center; color: #64748b;">Loading activity...</div>
                </div>
            </div>

            <div class="card">
                <h2>Recently Applied</h2>
                <div class="applied-list" id="appliedList">
                    <div style="padding: 20px; text-align: center; color: #64748b;">Loading...</div>
                </div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Pipeline Queue</h2>
                <div class="pipeline-bar" id="pipelineBar"></div>
                <div style="margin-top: 10px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; font-size: 10px;">
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #3b82f6; border-radius: 2px;"></span> Discover</div>
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #8b5cf6; border-radius: 2px;"></span> Enrich</div>
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #f59e0b; border-radius: 2px;"></span> Score</div>
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #10b981; border-radius: 2px;"></span> Tailor</div>
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #06b6d4; border-radius: 2px;"></span> Cover</div>
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #f97316; border-radius: 2px;"></span> Ready</div>
                    <div style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #059669; border-radius: 2px;"></span> Applied</div>
                </div>
            </div>

            <div class="card">
                <h2>Workers</h2>
                <div class="stat-row"><span class="stat-label">Running</span><span class="stat-value" id="workersRunning">-</span></div>
                <div class="stat-row"><span class="stat-label">Auto-Apply</span><span class="stat-value" id="autoApply">-</span></div>
                <div class="worker-list" id="workerList" style="margin-top: 10px;"></div>
            </div>

            <div class="card">
                <h2>High-Scoring Jobs (≥7)</h2>
                <div class="job-list" id="topJobs"></div>
            </div>
        </div>
    </div>

    <script>
        const API_BASE = window.location.origin;
        let activityData = [];

        async function fetchJSON(endpoint) {
            try {
                const res = await fetch(`${API_BASE}${endpoint}`);
                return await res.json();
            } catch (e) {
                console.error(`Failed to fetch ${endpoint}:`, e);
                return null;
            }
        }

        function formatTime(isoString) {
            const date = new Date(isoString);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }

        function formatRelative(isoString) {
            const date = new Date(isoString);
            const now = new Date();
            const diff = Math.floor((now - date) / 1000);
            if (diff < 60) return 'just now';
            if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
            if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
            return Math.floor(diff / 86400) + 'd ago';
        }

        function updateLastUpdate() {
            document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
        }

        async function updateActivity() {
            const data = await fetchJSON('/activity');
            if (!data || !data.activity) return;

            activityData = data.activity;
            const feed = document.getElementById('activityFeed');

            if (activityData.length === 0) {
                feed.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b;">No activity yet. Workers are starting up...</div>';
                return;
            }

            feed.innerHTML = activityData.slice().reverse().map(a => {
                const typeClass = a.worker === 'ApplyWorker' ? 'apply' : (a.level === 'error' ? 'error' : 'info');
                return `
                    <div class="activity-item ${typeClass}">
                        <div class="activity-time">${formatRelative(a.timestamp)}</div>
                        <div class="activity-content">
                            <div class="activity-worker">${a.worker}</div>
                            <div class="activity-message">${a.message}${a.job_title ? ': ' + a.job_title : ''}</div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function updateQueue() {
            const data = await fetchJSON('/queue/status');
            if (!data) return;

            const counts = {};
            data.queue.forEach(q => counts[q.status] = q.count);

            document.getElementById('appliedCount').textContent = counts.applied || 0;
            document.getElementById('readyCount').textContent = counts.ready_to_apply || 0;
            document.getElementById('totalCount').textContent = Object.values(counts).reduce((a, b) => a + b, 0);

            const stageMap = {
                'pending_discover': { class: 'stage-discover', count: 0 },
                'pending_enrich': { class: 'stage-enrich', count: 0 },
                'pending_score': { class: 'stage-score', count: 0 },
                'pending_tailor': { class: 'stage-tailor', count: 0 },
                'pending_cover': { class: 'stage-cover', count: 0 },
                'ready_to_apply': { class: 'stage-ready', count: 0 },
                'applied': { class: 'stage-applied', count: 0 }
            };
            data.queue.forEach(q => { if (stageMap[q.status]) stageMap[q.status].count = q.count; });
            const total = Object.values(stageMap).reduce((sum, s) => sum + s.count, 0);
            document.getElementById('pipelineBar').innerHTML = Object.entries(stageMap)
                .filter(([_, s]) => s.count > 0)
                .map(([_, stage]) => `<div class="pipeline-stage ${stage.class}" style="width: ${(stage.count / total * 100).toFixed(1)}%">${stage.count}</div>`)
                .join('');
        }

        async function updateWorkers() {
            const data = await fetchJSON('/workers/status');
            if (!data) return;
            document.getElementById('workersRunning').textContent = data.count;
            document.getElementById('workerList').innerHTML = data.workers.map(w => `<span class="worker-badge running">${w}</span>`).join('');
        }

        async function updateHealth() {
            const data = await fetchJSON('/health');
            if (!data) return;
            document.getElementById('autoApply').textContent = data.auto_apply ? 'Enabled' : 'Disabled';
        }

        async function updateStats() {
            const data = await fetchJSON('/db/stats');
            if (!data) return;

            const highScore = data.score_distribution.filter(s => s.score >= 7).reduce((sum, s) => sum + s.count, 0);
            document.getElementById('highScoreCount').textContent = highScore;

            document.getElementById('topJobs').innerHTML = data.top_jobs.slice(0, 10).map(job => `
                <div class="job-item">
                    <span class="score-badge score-${Math.min(job.score, 9)}">${job.score}</span>
                    <div class="job-info">
                        <div class="job-title">${job.title}</div>
                        <div class="job-meta"><span class="site-badge">${job.site}</span></div>
                    </div>
                </div>
            `).join('');

            // Get applied jobs with timestamps
            const appliedData = await fetchJSON('/db/jobs?limit=20');
            if (appliedData && appliedData.jobs) {
                const applied = appliedData.jobs.filter(j => j.applied_at || (j.fit_score && j.fit_score >= 7));
                document.getElementById('appliedList').innerHTML = applied.slice(0, 15).map(job => `
                    <div class="applied-item">
                        <div class="applied-title">${job.title}</div>
                        <div class="job-meta">
                            <span class="site-badge">${job.site}</span>
                            ${job.fit_score ? `<span style="color: ${job.fit_score >= 7 ? '#10b981' : '#94a3b8'}; margin-left: 6px;">Score: ${job.fit_score}</span>` : ''}
                        </div>
                    </div>
                `).join('');
            }
        }

        async function updateAll() {
            await Promise.all([updateHealth(), updateWorkers(), updateQueue(), updateStats(), updateActivity()]);
            updateLastUpdate();
        }

        updateAll();
        setInterval(updateAll, 5000);
    </script>
</body>
</html>
    """


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

    query = "SELECT title, site, location, fit_score, url, tailored_path, cover_path FROM jobs WHERE 1=1"
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
    columns = ['title', 'site', 'location', 'fit_score', 'url', 'tailored_path', 'cover_path']
    jobs = [dict(zip(columns, row)) for row in rows]

    return jsonify({
        "count": len(jobs),
        "jobs": jobs
    })


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
        cursor = conn.execute(update_sql, values)
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Job URL not found"}), 404
        conn.commit()
        return jsonify({"success": True, "url": decoded_url, "status": status})
    except Exception as e:
        conn.rollback()
        log.error(f"Error updating job status: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/db/files/tailored/<path:filename>')
def get_tailored_resume(filename):
    """Download a tailored resume by filename."""
    from flask import send_file
    file_path = Path('/data') / 'tailored_resumes' / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True, download_name=filename)
    return jsonify({"error": "File not found"}), 404


@app.route('/db/files/cover/<path:filename>')
def get_cover_letter(filename):
    """Download a cover letter by filename."""
    from flask import send_file
    file_path = Path('/data') / 'cover_letters' / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True, download_name=filename)
    return jsonify({"error": "File not found"}), 404


@app.route('/db/files/<path:url>')
def get_job_files(url):
    """Get download URLs for all files associated with a job."""
    from urllib.parse import unquote
    conn = get_connection()
    decoded_url = unquote(url)

    row = conn.execute(
        "SELECT tailored_path, cover_path FROM jobs WHERE url = ?",
        (decoded_url,)
    ).fetchone()

    if not row:
        return jsonify({"error": "Job not found"}), 404

    tailored_path, cover_path = row
    base_url = request.host_url.rstrip('/')

    result = {"url": decoded_url}
    if tailored_path:
        filename = Path(tailored_path).name
        result["tailored_url"] = f"{base_url}/db/files/tailored/{filename}"
    if cover_path:
        filename = Path(cover_path).name
        result["cover_url"] = f"{base_url}/db/files/cover/{filename}"

    return jsonify(result)


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


@app.route('/activity')
def fetch_activity():
    """Get recent activity log."""
    return jsonify({"activity": get_activity()})


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
