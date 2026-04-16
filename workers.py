# workers.py
import logging
import time
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from applypilot.database import get_connection
from applypilot.config import load_profile
from activity_log import log_activity

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

        # Log activity
        log_activity("info", "ScoreWorker", f"Scored {result['score']}/10", job.get('title'))

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
        import re
        from pathlib import Path
        from applypilot.scoring.tailor import tailor_resume
        from applypilot.config import load_profile, RESUME_PATH, TAILORED_DIR

        # Create tailored resumes directory
        tailored_dir = Path(self.data_dir) / 'tailored_resumes'
        tailored_dir.mkdir(parents=True, exist_ok=True)

        # Generate safe filename
        safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50].strip().replace(" ", "_")
        safe_site = re.sub(r"[^\w\s-]", "", job["site"])[:20].strip().replace(" ", "_")
        filename = f"{safe_site}_{safe_title}"
        tailored_path = tailored_dir / f"{filename}.txt"

        # Check if file already exists
        if tailored_path.exists():
            db_path = f"data/tailored_resumes/{filename}.txt"
            log.info(f"Tailored resume already exists for {job.get('title')}")
            log_activity("info", "TailorWorker", "Using existing tailored resume", job.get('title'))
            self._save_result(job, 'pending_cover', tailored_path=db_path)
            return

        # Load profile and base resume
        profile = load_profile()
        resume_text = RESUME_PATH.read_text(encoding="utf-8")

        try:
            # Generate tailored resume
            tailored_text, report = tailor_resume(
                resume_text=resume_text,
                job=job,
                profile=profile,
                max_retries=3,
                validation_mode="lenient"  # Use lenient to avoid too many failures
            )

            # Save tailored resume
            tailored_path.write_text(tailored_text, encoding="utf-8")

            # Build relative path for database (from /data perspective)
            db_path = f"data/tailored_resumes/{filename}.txt"

            # Log activity
            log_activity("info", "TailorWorker",
                         f"Generated tailored resume ({report.get('status', 'unknown')})",
                         job.get('title'))

            # Move to next stage
            self._save_result(job, 'pending_cover', tailored_path=db_path)

        except Exception as e:
            log.error(f"Failed to tailor resume for {job.get('title')}: {e}")
            # Still mark as ready_to_apply so it doesn't block - will use base resume
            self._save_result(job, 'ready_to_apply')
            log_activity("error", "TailorWorker", f"Failed: {str(e)[:50]}", job.get('title'))


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
        import re
        from pathlib import Path
        from applypilot.llm import get_client
        from applypilot.config import load_profile

        # Create cover letters directory
        cover_dir = Path(self.data_dir) / 'cover_letters'
        cover_dir.mkdir(parents=True, exist_ok=True)

        # Generate safe filename
        safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50].strip().replace(" ", "_")
        safe_site = re.sub(r"[^\w\s-]", "", job["site"])[:20].strip().replace(" ", "_")
        filename = f"{safe_site}_{safe_title}"
        cover_path = cover_dir / f"{filename}.txt"

        # Check if file already exists
        if cover_path.exists():
            db_path = f"data/cover_letters/{filename}.txt"
            log.info(f"Cover letter already exists for {job.get('title')}")
            log_activity("info", "CoverWorker", "Using existing cover letter", job.get('title'))
            self._save_result(job, 'ready_to_apply', cover_path=db_path)
            return

        try:
            # Generate cover letter using LLM
            profile = load_profile()
            personal = profile.get('personal', {})
            resume_facts = profile.get('resume_facts', {})

            # Build cover letter prompt
            prompt = f"""Write a concise, professional cover letter for this job application.

JOB TITLE: {job['title']}
COMPANY: {job['site']}
LOCATION: {job.get('location', 'Remote')}

JOB DESCRIPTION:
{(job.get('full_description') or '')[:3000]}

CANDIDATE INFO:
- Name: {personal.get('full_name', 'Candidate')}
- Email: {personal.get('email', '')}
- Current Role: {resume_facts.get('preserved_companies', ['N/A'])[0] if resume_facts.get('preserved_companies') else 'Software Engineer'}

Requirements:
- Keep it under 200 words
- Focus on relevant skills and experience
- Be specific to this role
- Professional but conversational tone
- No fluff or generic phrases

Return ONLY the cover letter text, no preamble."""

            client = get_client()
            cover_letter = client.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.7
            )

            # Clean up the response
            cover_letter = cover_letter.strip()
            if cover_letter.startswith('"') and cover_letter.endswith('"'):
                cover_letter = cover_letter[1:-1]
            if cover_letter.startswith('```') and cover_letter.endswith('```'):
                cover_letter = cover_letter[3:-3].strip()
            if cover_letter.lower().startswith('here is'):
                lines = cover_letter.split('\n')
                cover_letter = '\n'.join(lines[1:]).strip()

            # Save cover letter
            cover_path.write_text(cover_letter, encoding="utf-8")

            # Build relative path for database
            db_path = f"data/cover_letters/{filename}.txt"

            # Log activity
            log_activity("info", "CoverWorker", "Generated cover letter", job.get('title'))

            # Mark as ready to apply
            self._save_result(job, 'ready_to_apply', cover_path=db_path)

        except Exception as e:
            log.error(f"Failed to generate cover letter for {job.get('title')}: {e}")
            # Still mark as ready_to_apply - cover letter is optional
            self._save_result(job, 'ready_to_apply')
            log_activity("error", "CoverWorker", f"Failed: {str(e)[:50]}", job.get('title'))


class ApplyWorker(Worker):
    """Worker that auto-submits job applications using Chrome automation."""

    def __init__(self, data_dir: Path, min_score: int = 7, sleep_interval: int = 5, auto_apply: bool = False):
        super().__init__(data_dir, min_score, sleep_interval)
        self.auto_apply = auto_apply
        self.chrome_port = 9222
        self.worker_id = 0

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
        if not self.auto_apply:
            log.info(f"Auto-apply disabled, skipping: {job['title']}")
            self._save_result(job, 'applied')
            return

        # Check if Chrome is available
        if not self._chrome_available():
            log.warning("Chrome not available, skipping application")
            # Move to failed status so it can be retried later
            self._save_result(job, 'ready_to_apply')
            time.sleep(30)  # Wait longer before retry
            return

        # Prepare job with file paths
        prepared_job = self._prepare_job(job)

        # Submit application
        try:
            status, duration_ms = self._submit_application(prepared_job)

            # Map status to database values
            status_map = {
                'applied': 'actually_applied',
                'captcha': 'captcha',
                'expired': 'expired',
                'login_issue': 'login_required',
            }

            mapped_status = status_map.get(status, status if ':' in status else 'failed')

            if mapped_status == 'actually_applied':
                self._save_result(job, 'applied', applied_at=datetime.now(timezone.utc).isoformat())
                log.info(f"Applied to: {job['title']} at {job['site']}")
                log_activity("success", "ApplyWorker", f"Applied to {job['site']}", job.get('title'))
            else:
                # For failures, mark as applied anyway to avoid infinite retries
                # but keep track of the reason
                self._save_result(job, 'applied')
                log.info(f"Application {mapped_status}: {job['title']} at {job['site']}")
                log_activity("info", "ApplyWorker", f"{mapped_status}", job.get('title'))

        except Exception as e:
            log.error(f"Failed to apply to {job['title']}: {e}")
            self._save_result(job, 'applied')
            log_activity("error", "ApplyWorker", f"Failed: {str(e)[:50]}", job.get('title'))

    def _chrome_available(self) -> bool:
        """Check if Chrome with remote debugging is available."""
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', self.chrome_port))
            sock.close()
            return result == 0
        except:
            return False

    def _prepare_job(self, job: dict) -> dict:
        """Prepare job with file paths for apply automation."""
        from pathlib import Path

        job = job.copy()
        data_path = Path(self.data_dir)

        # Add tailored resume path if exists
        if job.get('tailored_path'):
            tailored_path = data_path / job['tailored_path'].replace('data/', '')
            if tailored_path.exists():
                job['tailored_resume_path'] = str(tailored_path)

        # Add cover letter path if exists
        if job.get('cover_path'):
            cover_path = data_path / job['cover_path'].replace('data/', '')
            if cover_path.exists():
                job['cover_letter_path'] = str(cover_path)

        # Add base resume path as fallback
        from applypilot.config import RESUME_PATH
        if 'tailored_resume_path' not in job:
            job['tailored_resume_path'] = str(RESUME_PATH)

        return job

    def _submit_application(self, job: dict) -> tuple[str, int]:
        """Submit job application via Chrome automation.

        Returns:
            Tuple of (status_string, duration_ms)
        """
        from applypilot.apply.launcher import run_job

        log.info(f"Applying to: {job.get('title')} at {job.get('site')} [{job.get('fit_score')}/10]")

        status, duration_ms = run_job(
            job=job,
            port=self.chrome_port,
            worker_id=self.worker_id,
            model='sonnet',
            dry_run=False
        )

        log.info(f"Result: {status} ({duration_ms/1000:.1f}s)")
        return status, duration_ms


class DiscoverWorker(Worker):
    """Worker that continuously discovers jobs from job boards."""

    def __init__(self, data_dir: Path, queries: list[dict], locations: list[dict] | None = None,
                 search_defaults: dict | None = None, jobs_per_query: int = 10):
        super().__init__(data_dir)
        self.queries = queries
        self.locations = locations or []
        self.search_defaults = search_defaults or {}
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

        # Build discovery config with queries, locations, and defaults
        cfg = {
            'queries': [query],
            'locations': self.locations,
        }

        # Merge defaults - jobs_per_query from constructor takes precedence
        cfg['defaults'] = {
            'results_per_site': self.jobs_per_query,
            **self.search_defaults,  # Override with yaml defaults (hours_old, etc)
        }

        # Run discovery
        stats = run_discovery(cfg=cfg)

        new_count = stats.get('new', 0)
        if new_count > 0:
            log_activity("info", "DiscoverWorker", f"Found {new_count} new jobs", query.get('query'))

        return new_count

    def _process_job(self, job: dict) -> None:
        """DiscoverWorker doesn't process jobs from queue."""
        pass


class EnrichWorker(Worker):
    """Worker that fetches full job descriptions for jobs in the queue."""

    def _get_next_job_status(self) -> str:
        return 'pending_discover'

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
                # Log activity
                log_activity("info", "EnrichWorker", f"Fetched {len(full_description)} chars", job.get('title'))
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
