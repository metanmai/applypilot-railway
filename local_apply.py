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
VALID_STATUSES = ['actually_applied', 'captcha', 'expired', 'failed', 'login_required']


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
            except (json.JSONDecodeError, IOError) as e:
                log.warning(f"Could not load state file: {e}, starting fresh")
        return {
            'last_run': None,
            'total_attempted': 0,
            'stats': {
                'actually_applied': 0,
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
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except (IOError, OSError) as e:
            log.error(f"Failed to save state file: {e}")

    def is_attempted(self, url: str) -> bool:
        """Check if job was already attempted."""
        encoded_url = quote(url, safe='')
        return encoded_url in self.state['jobs']

    def mark_attempted(self, url: str, status: str, title: str, duration_ms: int = 0):
        """Mark job as attempted, allowing status updates for retries."""
        encoded_url = quote(url, safe='')

        # Check if already attempted
        is_new = encoded_url not in self.state['jobs']
        old_status = None

        if not is_new:
            old_status = self.state['jobs'][encoded_url].get('status')
            if old_status == status:
                log.debug(f"Job already attempted with same status: {url}")
                return
            log.info(f"Updating job status from {old_status} to {status}: {url}")

        # Update or create job entry
        self.state['jobs'][encoded_url] = {
            'status': status,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'duration_ms': duration_ms,
            'title': title
        }

        # Update counters
        if is_new:
            self.state['total_attempted'] += 1
        else:
            # Decrement old status counter
            if old_status in self.state['stats']:
                self.state['stats'][old_status] -= 1

        # Increment new status counter
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
                log.info(f"Updated Railway: {status}")
            except Exception as e:
                log.error(f"Failed to update Railway: {e}")

        # Update local state
        if not args.dry_run:
            state_mgr.mark_attempted(job['url'], status, job.get('title', ''), duration_ms)

        # Track results
        if status in results:
            results[status] += 1
        elif status != 'dry_run':
            log.warning(f"Unexpected status '{status}' - counting as failed")
            results['failed'] += 1

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


if __name__ == '__main__':
    main()
