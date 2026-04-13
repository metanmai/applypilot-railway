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
        encoded_url = quote(url, safe='')
        return encoded_url in self.state['jobs']

    def mark_attempted(self, url: str, status: str, title: str, duration_ms: int = 0):
        """Mark job as attempted."""
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

    # Initialize state manager
    state_mgr = StateManager(STATE_FILE)
    log.info(f"State file: {STATE_FILE}")
    log.info(f"Previously attempted: {state_mgr.state['total_attempted']} jobs")

    # Show stats
    stats = state_mgr.state['stats']
    log.info(f"Stats: applied={stats['applied']}, captcha={stats['captcha']}, "
             f"expired={stats['expired']}, failed={stats['failed']}")

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
