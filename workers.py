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

    # Whitelist of valid column names for _save_result
    _VALID_RESULT_COLUMNS = {'tailored_path', 'cover_path', 'fit_score', 'applied_at', 'full_description'}

    def __init__(self, data_dir: Path, min_score: int = 7, sleep_interval: int = 5):
        self.data_dir = data_dir
        self.min_score = min_score
        self.sleep_interval = sleep_interval
        self.running = False
        self.thread = None
        self._start_lock = threading.Lock()

    def start(self) -> threading.Thread:
        """Start the worker in a background thread."""
        with self._start_lock:
            if self.thread is None or not self.thread.is_alive():
                self.running = True
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
                log.info(f"Started {self.__class__.__name__}")
        return self.thread

    def stop(self):
        """Stop the worker gracefully."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        log.info(f"Stopped {self.__class__.__name__}")

    def _run(self):
        """Main worker loop - override in subclasses."""
        while self.running:
            try:
                job = self._get_next_job()
                if job is None:
                    # No work available, sleep before checking again
                    time.sleep(self.sleep_interval)
                    continue

                # Check if already processed
                if self._is_already_processed(job):
                    continue

                # Process the job - subclasses implement actual processing
                self._process_job(job)
            except Exception as e:
                log.error(f"Error in {self.__class__.__name__}: {e}")
                time.sleep(self.sleep_interval)

    @abstractmethod
    def _process_job(self, job: dict) -> None:
        """Process a single job. Must be implemented by subclasses."""
        pass

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
            f"WHERE status = ? ORDER BY discovered_at DESC LIMIT 1",
            (status,)
        ).fetchone()

        if job:
            columns = ['url', 'title', 'site', 'location', 'full_description']
            return dict(zip(columns, job))
        return None

    def _save_result(self, job: dict, status: str, **kwargs):
        """Update job status and commit immediately."""
        conn = get_connection()

        # Build SQL dynamically based on what fields we're updating
        set_clauses = ["status = ?"]
        values = [status]

        # Add updated_at
        set_clauses.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())

        # Add any extra fields (tailored_path, cover_path, etc.)
        # Validate column names against whitelist to prevent SQL injection
        for key, value in kwargs.items():
            if key not in self._VALID_RESULT_COLUMNS:
                raise ValueError(f"Invalid column for result: {key}. "
                                 f"Valid columns: {self._VALID_RESULT_COLUMNS}")
            set_clauses.append(f"{key} = ?")
            values.append(value)

        # Add url for WHERE clause
        values.append(job['url'])

        update_sql = f"UPDATE jobs SET {', '.join(set_clauses)} WHERE url = ?"

        conn.execute(update_sql, values)
        conn.commit()

    def _is_already_processed(self, job: dict) -> bool:
        """Check if job was already processed by this worker."""
        # Override in subclasses as needed
        return False

    @property
    def is_running(self) -> bool:
        """Check if the worker is currently running."""
        return self.running and self.thread is not None and self.thread.is_alive()


class ScoreWorker(Worker):
    """Worker that scores jobs and commits immediately."""

    def _get_next_job_status(self) -> str:
        return 'pending_score'

    def _get_next_job(self) -> dict | None:
        """Get next job from queue that matches this worker's status."""
        conn = get_connection()
        status = self._get_next_job_status()

        if status is None:
            return None

        # Get next job with that status, including fit_score for already-scored check
        job = conn.execute(
            "SELECT url, title, site, location, full_description, fit_score FROM jobs "
            f"WHERE status = ? ORDER BY discovered_at DESC LIMIT 1",
            (status,)
        ).fetchone()

        if job:
            columns = ['url', 'title', 'site', 'location', 'full_description', 'fit_score']
            return dict(zip(columns, job))
        return None

    def _process_job(self, job: dict) -> None:
        """Score a single job and commit immediately."""
        # Check if already scored
        if job.get('fit_score') is not None:
            # Already scored, move to next stage based on score
            if job.get('fit_score', 0) >= self.min_score:
                self._save_result(job, 'pending_tailor')
            else:
                # Low score, skip to applied (won't be applied)
                self._save_result(job, 'applied')
            return

        # Score and commit
        from applypilot.scoring.scorer import score_and_commit
        result = score_and_commit(job)

        # If score >= min_score, move to tailor stage
        if result['score'] >= self.min_score:
            self._save_result(job, 'pending_tailor')
        else:
            # Low score, skip to applied (won't be applied)
            self._save_result(job, 'applied')


class TailorWorker(Worker):
    """Worker that generates tailored resumes for high-scoring jobs."""

    def _get_next_job_status(self) -> str:
        return 'pending_tailor'

    def _get_next_job(self) -> dict | None:
        """Get next job from queue that matches this worker's status."""
        conn = get_connection()
        status = self._get_next_job_status()

        if status is None:
            return None

        # Get next job with that status, including tailored_path for already-tailored check
        job = conn.execute(
            "SELECT url, title, site, location, full_description, tailored_path FROM jobs "
            f"WHERE status = ? ORDER BY discovered_at DESC LIMIT 1",
            (status,)
        ).fetchone()

        if job:
            columns = ['url', 'title', 'site', 'location', 'full_description', 'tailored_path']
            return dict(zip(columns, job))
        return None

    def _is_already_processed(self, job: dict) -> bool:
        """Check if tailored resume already exists."""
        return job.get('tailored_path') is not None

    def _process_job(self, job: dict) -> None:
        """Generate and save tailored resume."""
        import uuid

        # Generate a unique path for the tailored resume
        # Use URL prefix and random suffix for uniqueness
        safe_url_prefix = job['url'][:50].replace('/', '_').replace(':', '_')
        tailored_path = f"data/tailored_resumes/{safe_url_prefix}_{uuid.uuid4().hex[:8]}.txt"

        # Placeholder: In production, this would call run_tailoring for this specific job
        # Note: run_tailoring processes ALL jobs, not single jobs
        # We'd need to adapt it or create a new single-job function
        log.info(f"Placeholder: generating tailored resume for {job.get('title', 'unknown')} -> {tailored_path}")

        # Mark as tailored with the path and move to cover stage
        self._save_result(job, 'pending_cover', tailored_path=tailored_path)


class CoverWorker(Worker):
    """Worker that generates cover letters for jobs with tailored resumes."""

    def _get_next_job_status(self) -> str:
        return 'pending_cover'

    def _get_next_job(self) -> dict | None:
        """Get next job from queue that matches this worker's status."""
        conn = get_connection()
        status = self._get_next_job_status()

        if status is None:
            return None

        # Get next job with that status, including cover_path for already-generated check
        job = conn.execute(
            "SELECT url, title, site, location, full_description, cover_path FROM jobs "
            f"WHERE status = ? ORDER BY discovered_at DESC LIMIT 1",
            (status,)
        ).fetchone()

        if job:
            columns = ['url', 'title', 'site', 'location', 'full_description', 'cover_path']
            return dict(zip(columns, job))
        return None

    def _is_already_processed(self, job: dict) -> bool:
        """Check if cover letter already exists."""
        return job.get('cover_path') is not None

    def _process_job(self, job: dict) -> None:
        """Generate and save cover letter."""
        import uuid

        # Generate a unique path for the cover letter
        # Use URL prefix and random suffix for uniqueness
        safe_url_prefix = job['url'][:50].replace('/', '_').replace(':', '_')
        cover_path = f"data/cover_letters/{safe_url_prefix}_{uuid.uuid4().hex[:8]}.txt"

        # Placeholder: In production, this would generate an actual cover letter
        # using LLM or template-based approach
        log.info(f"Placeholder: generating cover letter for {job.get('title', 'unknown')} -> {cover_path}")

        # Mark as ready to apply with the cover letter path
        self._save_result(job, 'ready_to_apply', cover_path=cover_path)


class ApplyWorker(Worker):
    """Worker that auto-submits job applications."""

    def __init__(self, data_dir: Path, min_score: int = 7, sleep_interval: int = 5, auto_apply: bool = False):
        super().__init__(data_dir, min_score, sleep_interval)
        self.auto_apply = auto_apply

    def _get_next_job_status(self) -> str:
        return 'ready_to_apply'

    def _get_next_job(self) -> dict | None:
        """Get next job from queue that matches this worker's status."""
        conn = get_connection()
        status = self._get_next_job_status()

        if status is None:
            return None

        # Get next job with that status, including columns needed for applying
        job = conn.execute(
            "SELECT url, title, site, location, full_description, tailored_path, cover_path, fit_score FROM jobs "
            f"WHERE status = ? ORDER BY discovered_at DESC LIMIT 1",
            (status,)
        ).fetchone()

        if job:
            columns = ['url', 'title', 'site', 'location', 'full_description', 'tailored_path', 'cover_path', 'fit_score']
            return dict(zip(columns, job))
        return None

    def _is_already_processed(self, job: dict) -> bool:
        """Check if job was already applied."""
        return job.get('applied_at') is not None

    def _process_job(self, job: dict) -> None:
        """Process job application."""
        # Check if we can apply (has all requirements)
        if not self._can_apply(job):
            log.info(f"Cannot apply to {job['title']}: missing requirements")
            # Mark as applied even if not actually applied (keeps pipeline moving)
            self._save_result(job, 'applied')
            return

        # Submit application
        try:
            self._submit_application(job)
            self._save_result(job, 'applied', applied_at=datetime.now(timezone.utc).isoformat())
            log.info(f"Applied to: {job['title']} at {job['site']}")
        except Exception as e:
            log.error(f"Failed to apply to {job['title']}: {e}")
            # Mark as applied anyway to avoid infinite retries
            self._save_result(job, 'applied')

    def _can_apply(self, job: dict) -> bool:
        """Check if job has all requirements for applying."""
        return (
            job.get('tailored_path') is not None and
            job.get('cover_path') is not None and
            job.get('fit_score', 0) >= self.min_score
        )

    def _submit_application(self, job: dict):
        """Submit job application via Chrome automation.

        For now, placeholder that just marks as applied.
        In production, this would integrate with applypilot apply module.
        """
        if not self.auto_apply:
            log.info(f"Auto-apply disabled, would apply to: {job['title']}")
            return

        # Placeholder: Chrome automation integration
        # This would use the applypilot apply module when available
        log.info(f"Placeholder: submitting application for {job['title']}")


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
            try:
                if self.current_query_index >= len(self.queries):
                    self.current_query_index = 0  # Restart from beginning
                    log.info("Completed all queries, restarting discovery")
                    time.sleep(60)  # Wait before restarting

                query = self.queries[self.current_query_index]
                log.info(f"Discovering for query: {query.get('query')}")

                # Run discovery for this query with limit
                jobs_found = self._discover_and_queue_jobs(query)
                log.info(f"Found {jobs_found} jobs for query: {query.get('query')}")

                # Move to next query
                self.current_query_index += 1
            except Exception as e:
                log.error(f"Error in {self.__class__.__name__}: {e}")
                time.sleep(self.sleep_interval)

    def _discover_and_queue_jobs(self, query: dict) -> int:
        """Run discovery for a query and queue new jobs.

        Returns:
            Number of jobs discovered and queued.
        """
        from applypilot.discovery.jobspy import run_discovery

        # Run discovery with proper limit via config dict
        # run_discovery reads 'results_per_site' from cfg['defaults']
        stats = run_discovery(
            cfg={
                'queries': [query],
                'defaults': {'results_per_site': self.jobs_per_query}
            }
        )
        return stats.get('new', 0)

    def _process_job(self, job: dict) -> None:
        """DiscoverWorker doesn't process jobs from queue."""
        pass


class EnrichWorker(Worker):
    """Worker that fetches full job descriptions for jobs in the queue."""

    def _get_next_job_status(self) -> str:
        return 'pending_enrich'

    def _is_already_processed(self, job: dict) -> bool:
        """Skip jobs that already have a full description."""
        return bool(job.get('full_description'))

    def _process_job(self, job: dict) -> None:
        """Fetch full description for a job and move to scoring stage."""
        url = job.get('url')
        if not url:
            log.warning(f"Job has no URL, skipping enrichment")
            self._save_result(job, 'pending_score')
            return

        try:
            full_description = self._fetch_description(url)
            if full_description and len(full_description) > 100:
                log.info(f"Fetched {len(full_description)} chars description for {job.get('title', 'unknown')}")
                # Update with full description and move to scoring
                self._save_result(job, 'pending_score', full_description=full_description)
            else:
                log.info(f"No description found for {job.get('title', 'unknown')}, moving to scoring anyway")
                self._save_result(job, 'pending_score')
        except Exception as e:
            log.error(f"Error enriching {job.get('title', 'unknown')}: {e}")
            # Move to scoring anyway - be forgiving
            self._save_result(job, 'pending_score')

    def _fetch_description(self, url: str) -> str | None:
        """Fetch full job description from URL.

        Uses simple HTTP scraping with fallback strategies.
        In production, this could use site-specific extractors or jobspy detail fetch.
        """
        import httpx
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Remove scripts, styles, and other non-content elements
            for element in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                element.decompose()

            # Try to find the job description using common selectors
            # This is a generic approach - site-specific extractors would be better
            description_selectors = [
                # Generic job posting selectors
                'div[class*="description"]',
                'div[class*="job-description"]',
                'section[class*="description"]',
                'div[data-testid="job-description"]',
                'div[id*="job-description"]',
                'div[id*="description"]',
                # Fallback to main content area
                'main',
                'article',
                'div[class*="content"]',
            ]

            for selector in description_selectors:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(separator='\n', strip=True)
                    if len(text) > 200:  # Only use if substantial content
                        return text

            # Final fallback: get text from body
            body_text = soup.get_text(separator='\n', strip=True)
            # Take first substantial chunk (job descriptions usually come first)
            lines = [line.strip() for line in body_text.split('\n') if line.strip()]
            if lines and len(lines[0]) > 100:
                return '\n'.join(lines[:10])  # First 10 meaningful lines

            return None

        except httpx.HTTPError as e:
            log.warning(f"HTTP error fetching {url}: {e}")
            return None
        except Exception as e:
            log.warning(f"Error fetching description from {url}: {e}")
            return None
