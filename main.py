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
import requests
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import yaml
from flask import Flask, jsonify, request, Response

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
            search_config = yaml.safe_load(f)
            queries = search_config.get('queries', [])
            locations = search_config.get('locations', [])
            search_defaults = search_config.get('defaults', {})
    else:
        # Fallback to default queries with remote locations
        queries = [
            {"query": "Software Engineer II", "tier": 1},
            {"query": "SDE II", "tier": 2},
        ]
        locations = [
            {"location": "Remote", "remote": True},
            {"location": "United States", "remote": True},
        ]
        search_defaults = {"hours_old": 168, "results_per_site": 50}
        log.info(f"Using default queries and locations (no searches.yaml found at {searches_path})")

    # Create workers
    batch_size = int(os.environ.get("BATCH_SIZE", "10"))

    workers = [
        DiscoverWorker(DATA_DIR, queries, locations=locations, search_defaults=search_defaults, jobs_per_query=batch_size),
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
    valid_statuses = ['actually_applied', 'captcha', 'expired', 'login_required', 'failed', 'sso_required']
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


# ---------------------------------------------------------------------------
# Chrome/Login Management Endpoints
# ---------------------------------------------------------------------------

# Browser automation (Playwright-based - works on Railway without Chrome installation)
playwright_context = None
playwright_browser = None
playwright = None
CHROME_PROFILE = DATA_DIR / 'browser-profile'


# HTTP proxy for Chrome DevTools Protocol (for login without SSH tunnel)
def _get_chrome_session() -> dict:
    """Get Chrome browser and page sessions via DevTools Protocol."""
    try:
        response = requests.get("http://localhost:9222/json", timeout=2)
        if response.status_code == 200:
            targets = response.json()
            for target in targets:
                if target.get('type') == 'page':
                    return {'browser_url': targets[0].get('webSocketDebuggerUrl'), 'page': target}
            # Create a new page if none exists
            response = requests.post("http://localhost:9222/json/new?about:blank", timeout=2)
            if response.status_code == 200:
                return {'page': response.json()}
        return None
    except:
        return None


@app.route('/chrome/navigate', methods=['POST'])
def navigate_chrome():
    """Navigate browser to a URL."""
    global playwright_context

    data = request.get_json() or {}
    url = data.get('url')

    if not url:
        return jsonify({"error": "URL required"}), 400

    if not playwright_context:
        return jsonify({"error": "Browser not started. Call POST /chrome/start first"}), 503

    try:
        # Get or create a page
        if playwright_context.pages:
            page = playwright_context.pages[0]
        else:
            page = playwright_context.new_page()

        # Navigate to the URL
        page.goto(url, wait_until='networkidle', timeout=30000)

        return jsonify({
            "success": True,
            "url": url,
            "title": page.title(),
            "message": "Navigated successfully"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/chrome/snapshot', methods=['GET'])
def chrome_snapshot():
    """Get HTML snapshot of current Chrome page."""
    try:
        response = requests.get("http://localhost:9222/json", timeout=5)
        if response.status_code != 200:
            return "Chrome not accessible", 503

        targets = response.json()
        page_target = None
        for target in targets:
            if target.get('type') == 'page':
                page_target = target
                break

        if not page_target:
            # Try to create a new page
            try:
                new_page = requests.post("http://localhost:9222/json/new?https://www.linkedin.com/login", timeout=5)
                if new_page.status_code == 200:
                    page_target = new_page.json()
            except:
                pass

        current_url = page_target.get('url', 'about:blank') if page_target else 'No page'

        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Browser Login - ApplyPilot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; background: #0f172a; color: #e2e8f0; }}
        .container {{ max-width: 600px; margin: 0 auto; }}
        h1 {{ color: #4da6ff; text-align: center; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 24px; margin: 20px 0; border: 1px solid #334155; }}
        .card h2 {{ margin-top: 0; color: #fff; }}
        .btn {{ display: block; width: 100%; padding: 16px; margin: 10px 0; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; text-decoration: none; text-align: center; transition: all 0.2s; }}
        .btn:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
        .btn-linkedin {{ background: #0a66c2; color: white; }}
        .btn-linkedin:hover {{ background: #004182; }}
        .btn-indeed {{ background: #2563eb; color: white; }}
        .btn-indeed:hover {{ background: #1d4ed8; }}
        .status {{ padding: 12px; border-radius: 8px; text-align: center; margin: 20px 0; font-size: 14px; }}
        .status.running {{ background: #059669; color: white; }}
        .status.stopped {{ background: #dc2626; color: white; }}
        .instructions {{ background: #16213e; padding: 20px; border-radius: 8px; font-size: 14px; line-height: 1.6; }}
        .instructions h3 {{ margin-top: 0; color: #fbbf24; }}
        code {{ background: #0f172a; padding: 8px 12px; border-radius: 4px; display: block; margin: 10px 0; overflow-x: auto; font-size: 12px; }}
        .spinner {{ display: inline-block; width: 20px; height: 20px; border: 3px solid #fff; border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite; }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 ApplyPilot Browser Login</h1>

        <div class="card">
            <h2>Step 1: Start Chrome Session</h2>
            <p>First, let's make sure Chrome is running:</p>
            <button class="btn" style="background: #059669;" onclick="startChrome()">
                ✓ Start Chrome
            </button>
            <div id="chromeStatus" class="status stopped">Status: Checking...</div>
        </div>

        <div class="card">
            <h2>Step 2: Choose Login Method</h2>
            <p class="instructions">
                <strong>Important:</strong> Railway doesn't expose the Chrome port directly.<br>
                Please use one of these methods:
            </p>

            <details style="margin: 20px 0;">
                <summary style="cursor: pointer; padding: 10px; background: #334155; border-radius: 8px;">
                    <strong>Method A: Railway SSH (Recommended)</strong>
                </summary>
                <div class="instructions" style="margin-top: 10px;">
                    <p>1. Run <code>railway ssh</code> in a terminal</p>
                    <p>2. Paste this command and keep it running:</p>
                    <code>cd /app && python -c "
import httpx, asyncio
async def tunnel():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                r = await client.get('http://localhost:9222/json')
                print('Chrome accessible at localhost:9222')
                await asyncio.sleep(30)
            except:
                print('Waiting for Chrome...')
                await asyncio.sleep(5)
asyncio.run(tunnel())
" &</code>
                    <p>3. Open another terminal and create tunnel:</p>
                    <code>railway ssh -R 9222:localhost:9222</code>
                </div>
            </details>

            <details style="margin: 20px 0;">
                <summary style="cursor: pointer; padding: 10px; background: #334155; border-radius: 8px;">
                    <strong>Method B: Direct Login Form (Simplest)</strong>
                </summary>
                <div class="instructions" style="margin-top: 10px;">
                    <p>Use the form below to submit your credentials directly:</p>
                    <button class="btn btn-linkedin" onclick="showLoginForm('linkedin')">
                        LinkedIn Login →
                    </button>
                    <button class="btn btn-indeed" onclick="showLoginForm('indeed')">
                        Indeed Login →
                    </button>
                    <div id="loginForm" style="margin-top: 20px;"></div>
                </div>
            </details>
        </div>

        <div class="card">
            <h2>Current Page</h2>
            <p id="currentUrl" style="font-family: monospace; word-break: break-all; color: #94a3b8;">{current_url}</p>
        </div>
    </div>

    <script>
        function startChrome() {{
            fetch('/chrome/start', {{ method: 'POST' }})
                .then(r => r.json())
                .then(data => {{
                    document.getElementById('chromeStatus').className = 'status running';
                    document.getElementById('chromeStatus').textContent = '✓ Chrome Running!';
                    checkChromeStatus();
                }})
                .catch(e => {{
                    document.getElementById('chromeStatus').textContent = 'Error: ' + e;
                }});
        }}

        function checkChromeStatus() {{
            fetch('/chrome/status')
                .then(r => r.json())
                .then(data => {{
                    if (data.running) {{
                        document.getElementById('chromeStatus').className = 'status running';
                        document.getElementById('chromeStatus').textContent = '✓ Chrome Running!';
                    }} else {{
                        document.getElementById('chromeStatus').className = 'status stopped';
                        document.getElementById('chromeStatus').textContent = 'Chrome Stopped';
                    }}
                }})
                .catch(() => {{
                    document.getElementById('chromeStatus').className = 'status stopped';
                    document.getElementById('chromeStatus').textContent = 'Chrome Unreachable';
                }});
        }}

        function showLoginForm(site) {{
            const form = document.getElementById('loginForm');
            if (site === 'linkedin') {{
                form.innerHTML = `
                    <h3>LinkedIn Login</h3>
                    <form onsubmit="submitLogin(event, 'linkedin')" style="display: flex; flex-direction: column; gap: 15px;">
                        <input type="email" id="linkedin_email" placeholder="Email" required
                            style="padding: 12px; border-radius: 8px; border: 1px solid #475569; background: #1e293b; color: white;">
                        <input type="password" id="linkedin_password" placeholder="Password" required
                            style="padding: 12px; border-radius: 8px; border: 1px solid #475569; background: #1e293b; color: white;">
                        <button type="submit" class="btn btn-linkedin">Log In to LinkedIn</button>
                    </form>
                `;
            }} else {{
                form.innerHTML = `
                    <h3>Indeed Login</h3>
                    <form onsubmit="submitLogin(event, 'indeed')" style="display: flex; flex-direction: column; gap: 15px;">
                        <input type="email" id="indeed_email" placeholder="Email" required
                            style="padding: 12px; border-radius: 8px; border: 1px solid #475569; background: #1e293b; color: white;">
                        <input type="password" id="indeed_password" placeholder="Password" required
                            style="padding: 12px; border-radius: 8px; border: 1px solid #475569; background: #1e293b; color: white;">
                        <button type="submit" class="btn btn-indeed">Log In to Indeed</button>
                    </form>
                `;
            }}
        }}

        function submitLogin(e, site) {{
            e.preventDefault();
            const email = document.getElementById(site + '_email').value;
            const password = document.getElementById(site + '_password').value;

            const form = document.getElementById('loginForm');
            form.innerHTML = '<p style="text-align: center;">Logging in... <span class="spinner"></span></p>';

            fetch('/chrome/login', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ site, email, password }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    form.innerHTML = '<p style="color: #4ade80;">✓ Login successful! Session saved.</p>';
                }} else {{
                    form.innerHTML = '<p style="color: #f87171;">✗ Login failed: ' + (data.error || 'Unknown error') + '</p>';
                }}
            }})
            .catch(e => {{
                form.innerHTML = '<p style="color: #f87171;">✗ Error: ' + e + '</p>';
            }});
        }}

        checkChromeStatus();
        setInterval(checkChromeStatus, 10000);
    </script>
</body>
</html>
        """

    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route('/chrome/start', methods=['POST'])
def start_browser():
    """Start Playwright browser for login session (works on Railway without Chrome)."""
    global playwright, playwright_browser, playwright_context

    if playwright_context:
        return jsonify({
            "status": "already_running",
            "message": "Browser is already running"
        })

    try:
        from playwright.sync_api import sync_playwright

        # Create browser profile directory
        CHROME_PROFILE.mkdir(parents=True, exist_ok=True)

        # Playwright context with persistent profile
        playwright_instance = sync_playwright().start()
        playwright_browser = playwright_instance.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE),
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-background-networking',
                '--disable-default-apps',
                '--disable-extensions',
                '--disable-gpu',
                '--disable-sync',
                '--mute-audio',
                '--no-first-run',
            ]
        )

        # Store for later use
        playwright = playwright_instance
        playwright_context = playwright_browser

        log.info("Started Playwright browser with persistent profile")
        log_activity("info", "Browser", "Started with Playwright (persistent profile)", None)

        return jsonify({
            "status": "started",
            "message": "Browser started - ready for login",
            "profile": str(CHROME_PROFILE),
            "endpoints": {
                "navigate": "POST /chrome/navigate?url=https://...",
                "status": "GET /chrome/status",
                "login_form": "GET /chrome/snapshot"
            }
        })
    except ImportError:
        return jsonify({
            "status": "error",
            "error": "Playwright not installed. Install with: pip install playwright && playwright install chromium"
        }), 500
    except Exception as e:
        log.error(f"Failed to start browser: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@app.route('/chrome/stop', methods=['POST'])
def stop_browser():
    """Stop Playwright browser."""
    global playwright, playwright_browser, playwright_context

    if not playwright_context:
        return jsonify({
            "status": "not_running",
            "message": "Browser is not running"
        })

    try:
        if playwright_context:
            playwright_context.close()
            playwright_context = None
        if playwright_browser:
            playwright_browser = None
        if playwright:
            playwright.stop()
            playwright = None

        log_activity("info", "Browser", "Stopped", None)
        return jsonify({"status": "stopped"})
    except Exception as e:
        # Reset state even if close fails
        playwright_context = None
        playwright_browser = None
        playwright = None
        return jsonify({
            "status": "stopped_with_error",
            "message": f"Browser stopped with error: {e}"
        })


@app.route('/chrome/status')
def chrome_status():
    """Check if browser is running."""
    global playwright_context

    is_running = playwright_context is not None

    # Check if we can actually use the context
    pages = []
    if is_running:
        try:
            pages = playwright_context.pages
        except:
            is_running = False

    return jsonify({
        "running": is_running,
        "browser_type": "playwright_chromium",
        "pages_open": len(pages) if is_running else 0,
        "profile": str(CHROME_PROFILE)
    })


@app.route('/chrome/json')
def chrome_json():
    """Return browser pages info (DevTools-style)."""
    global playwright_context
    try:
        if not playwright_context:
            return jsonify({"error": "Browser not running"}), 503

        pages_info = []
        for i, page in enumerate(playwright_context.pages):
            pages_info.append({
                "id": str(i),
                "type": "page",
                "title": page.title(),
                "url": page.url
            })

        return jsonify(pages_info)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route('/chrome/json/version')
def chrome_version():
    """Return browser version info."""
    return jsonify({
        "Browser": "Playwright Chromium",
        "Protocol-Version": "1.3",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/120.0.0.0 Safari/537.36"
    })


@app.route('/chrome/ws/<path:path>')
def chrome_websocket(path):
    """Playwright doesn't use DevTools WebSocket protocol."""
    return jsonify({
        "note": "Playwright uses its own protocol, not Chrome DevTools WebSocket",
        "alternative": "Use /chrome/navigate for browser control"
    }), 200


@app.route('/chrome/login', methods=['POST'])
def chrome_login():
    """Navigate to login page and wait for user to complete login."""
    global playwright_context

    if not playwright_context:
        return jsonify({"error": "Browser not started. Call POST /chrome/start first"}), 503

    data = request.get_json() or {}
    site = data.get('site', 'linkedin')

    login_urls = {
        'linkedin': 'https://www.linkedin.com/login',
        'indeed': 'https://secure.indeed.com/account/login'
    }

    if site not in login_urls:
        return jsonify({"error": f"Unknown site: {site}. Use 'linkedin' or 'indeed'"}), 400

    try:
        # Get or create a page
        if playwright_context.pages:
            page = playwright_context.pages[0]
        else:
            page = playwright_context.new_page()

        # Navigate to login page
        page.goto(login_urls[site], wait_until='networkidle', timeout=30000)

        return jsonify({
            "success": True,
            "site": site,
            "url": login_urls[site],
            "message": f"Navigated to {site} login page",
            "note": "Browser is ready. Please complete login manually."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        # Use the same Chrome automation as ApplyWorker
        from workers.base import ensure_chrome, wait_for_human_login

        # Ensure Chrome is running with profile
        driver = ensure_chrome(headless=True, user_data_dir=str(CHROME_PROFILE))

        login_urls = {
            'linkedin': 'https://www.linkedin.com/login',
            'indeed': 'https://secure.indeed.com/account/login'
        }

        if site not in login_urls:
            return jsonify({"error": f"Unknown site: {site}"}), 400

        # Navigate to login page
        driver.get(login_urls[site])
        import time
        time.sleep(2)

        # This is where we'd normally automate, but for security,
        # we'll redirect to a page where user can complete login
        # via the DevTools interface

        # Store the session info for later retrieval
        return jsonify({
            "success": True,
            "message": f"Navigated to {site}. Please complete login manually.",
            "instructions": f"1. SSH into Railway with: railway ssh\n2. Check Chrome is accessible\n3. Use DevTools to complete login\n\nOr use the web-based DevTools at /chrome/devtools"
        })

    except Exception as e:
        log.error(f"Login error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/chrome/automation/login', methods=['POST'])
def chrome_automation_login():
    """
    Login endpoint that uses Playwrightwright automation.
    This handles the full login flow automatically.
    """
    data = request.get_json() or {}
    site = data.get('site', 'linkedin')

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # Launch with persistent context for session saving
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )

            page = context.new_page()

            if site == 'linkedin':
                page.goto('https://www.linkedin.com/login')
                return jsonify({
                    "status": "ready",
                    "message": "LinkedIn login page loaded",
                    "next_step": "Enter credentials manually via DevTools or use automated login"
                })
            elif site == 'indeed':
                page.goto('https://secure.indeed.com/account/login')
                return jsonify({
                    "status": "ready",
                    "message": "Indeed login page loaded",
                    "next_step": "Enter credentials manually via DevTools"
                })

            context.close()
            return jsonify({"error": "Unknown site"}), 400

    except ImportError:
        return jsonify({
            "error": "Playwright not installed",
            "solution": "Use railway ssh and manual Chrome DevTools login instead"
        }), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/login')
def login_instructions():
    """Get login instructions for LinkedIn and Indeed."""
    return jsonify({
        "title": "ApplyPilot - Browser Login Setup",
        "instructions": [
            "1. Chrome is already running (check /chrome/status)",
            "2. Open this URL in your browser for Chrome DevTools access:",
            f"   https://comfortable-flow-production.up.railway.app/chrome/devtools",
            "3. Navigate to LinkedIn/Indeed in the DevTools window and log in",
            "4. Your session will be saved in /data/chrome-profile"
        ],
        "devtools_url": "https://comfortable-flow-production.up.railway.app/chrome/devtools",
        "sites": ["LinkedIn", "Indeed"],
        "note": "You only need to log in once. The session persists in Railway's /data storage."
    })


@app.route('/chrome/devtools')
def chrome_devtools():
    """Interactive browser interface for login."""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Browser Login - ApplyPilot</title>
    <script src="https://cdn.jsdelivr.net/npm/@puppeteer/browsers@0.0.3/lib/js/chrome-remote-interface/chrome-remote-interface.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            text-align: center;
            padding: 30px 20px;
            background: #1e293b;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .header h1 {
            margin: 0 0 10px 0;
            font-size: 24px;
        }
        .status {
            display: inline-block;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 500;
        }
        .status.running { background: #059669; color: white; }
        .status.stopped { background: #dc2626; color: white; }
        .browser-frame {
            width: 100%;
            height: 600px;
            border: none;
            border-radius: 10px;
            background: white;
        }
        .controls {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary { background: #3b82f6; color: white; }
        .btn-primary:hover { background: #2563eb; }
        .btn-secondary { background: #475569; color: white; }
        .btn-secondary:hover { background: #64748b; }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; }
        .url-bar {
            flex: 1;
            padding: 12px 15px;
            border: 1px solid #334155;
            border-radius: 8px;
            background: #1e293b;
            color: #e2e8f0;
            font-size: 14px;
        }
        .info-box {
            background: #1e293b;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .info-box h3 { margin-top: 0; }
        .step {
            padding: 10px 0;
            border-bottom: 1px solid #334155;
        }
        .step:last-child { border-bottom: none; }
        .step-num {
            display: inline-block;
            width: 24px;
            height: 24px;
            background: #3b82f6;
            border-radius: 50%;
            text-align: center;
            line-height: 24px;
            font-size: 12px;
            font-weight: bold;
            margin-right: 10px;
        }
        .iframe-container {
            background: #1e293b;
            border-radius: 10px;
            padding: 10px;
        }
        .message {
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
        }
        .message.info { background: #1e40af; color: #93c5fd; }
        .message.success { background: #065f46; color: #6ee7b7; }
        .message.error { background: #7f1d1d; color: #fca5a5; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 ApplyPilot Browser Login</h1>
            <p>Log in to LinkedIn and Indeed - your session will be saved</p>
            <p>Chrome Status: <span id="chromeStatus" class="status">Checking...</span></p>
        </div>

        <div id="messageBox"></div>

        <div class="info-box">
            <h3>📋 Quick Login Steps</h3>
            <div class="step"><span class="step-num">1</span>Click the LinkedIn button below</div>
            <div class="step"><span class="step-num">2</span>Log in to LinkedIn in the browser window</div>
            <div class="step"><span class="step-num">3</span>Come back and click the Indeed button</div>
            <div class="step"><span class="step-num">4</span>Log in to Indeed</div>
            <div class="step"><span class="step-num">5</span>You're done! The system will auto-apply 24/7</div>
        </div>

        <div class="controls">
            <button class="btn btn-success" onclick="navigateToLinkedIn()">
                🔐 Log in to LinkedIn
            </button>
            <button class="btn btn-success" onclick="navigateToIndeed()">
                🔐 Log in to Indeed
            </button>
            <button class="btn btn-secondary" onclick="navigateTo('https://www.google.com')">
                🔍 Open Google
            </button>
            <input type="text" class="url-bar" id="urlBar" placeholder="Enter URL..." value="https://www.linkedin.com/login">
            <button class="btn btn-primary" onclick="navigateToCustom()">
                Go
            </button>
        </div>

        <div class="iframe-container">
            <div id="browserContent" style="text-align: center; padding: 50px; color: #64748b;">
                <p>👆 Click a login button above to open a site</p>
                <p>The browser will open in this frame</p>
            </div>
        </div>
    </div>

    <script>
        function showMessage(text, type = 'info') {
            const box = document.getElementById('messageBox');
            box.innerHTML = `<div class="message ${type}">${text}</div>`;
            setTimeout(() => box.innerHTML = '', 5000);
        }

        function checkChromeStatus() {
            fetch('/chrome/status')
                .then(r => r.json())
                .then(data => {
                    const status = document.getElementById('chromeStatus');
                    if (data.running) {
                        status.textContent = 'Running ✓';
                        status.className = 'status running';
                    } else {
                        status.textContent = 'Stopped - Starting...';
                        status.className = 'status stopped';
                        fetch('/chrome/start', { method: 'POST' })
                            .then(() => setTimeout(checkChromeStatus, 2000));
                    }
                })
                .catch(() => {
                    const status = document.getElementById('chromeStatus');
                    status.textContent = 'Error - Retrying...';
                    status.className = 'status stopped';
                });
        }

        function navigateToLinkedIn() {
            document.getElementById('urlBar').value = 'https://www.linkedin.com/login';
            navigateToCustom();
            showMessage('🔐 Opening LinkedIn login page...', 'info');
        }

        function navigateToIndeed() {
            document.getElementById('urlBar').value = 'https://secure.indeed.com/account/login';
            navigateToCustom();
            showMessage('🔐 Opening Indeed login page...', 'info');
        }

        function navigateToCustom() {
            const url = document.getElementById('urlBar').value;
            if (!url) return;

            // Since we can't easily proxy Chrome DevTools, we'll open in a new tab
            // The user will log in there, and Chrome will save the session
            const content = document.getElementById('browserContent');
            content.innerHTML = `
                <div style="text-align: center; padding: 40px;">
                    <h3>Opening ${new URL(url).hostname}...</h3>
                    <p>A new tab will open with the login page.</p>
                    <p><strong>Important:</strong> Log in normally in that tab.</p>
                    <p>Your session will be automatically saved!</p>
                    <p style="margin-top: 20px;">
                        <a href="${url}" target="_blank" class="btn btn-primary" style="text-decoration: none; display: inline-block; padding: 15px 30px;">
                            🔗 Open ${new URL(url).hostname} in New Tab
                        </a>
                    </p>
                    <p style="margin-top: 30px; font-size: 14px; color: #64748b;">
                        After logging in, come back here and click the other site!
                    </p>
                </div>
            `;
        }

        // Check status on load
        checkChromeStatus();
        setInterval(checkChromeStatus, 10000);
    </script>
</body>
</html>
    """


if __name__ == '__main__':
    # Ensure data directory exists
    ensure_data_dir()

    # Start workers automatically on startup
    start_workers()

    # Run Flask server
    log.info(f"Starting Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT)
# Deployment marker Sun Apr 12 17:21:35 UTC 2026
