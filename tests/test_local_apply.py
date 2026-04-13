"""Tests for local_apply.py"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest


class TestRailwayAPIClient:
    """Test Railway API client."""

    def test_fetch_jobs(self):
        """Test fetching jobs from Railway."""
        from local_apply import RailwayAPIClient

        client = RailwayAPIClient('https://test.railway.app')

        with patch('local_apply.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                'jobs': [
                    {'url': 'https://test.com/1', 'title': 'Job 1', 'fit_score': 8, 'site': 'Test Site'}
                ]
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            jobs = client.fetch_jobs(min_score=7)

            assert len(jobs) == 1
            assert jobs[0]['title'] == 'Job 1'
            assert jobs[0]['fit_score'] == 8
            mock_get.assert_called_once()

    def test_update_job_status(self):
        """Test updating job status."""
        from local_apply import RailwayAPIClient

        client = RailwayAPIClient('https://test.railway.app')

        with patch('local_apply.requests.put') as mock_put:
            mock_response = Mock()
            mock_response.json.return_value = {'success': True}
            mock_response.raise_for_status = Mock()
            mock_put.return_value = mock_response

            result = client.update_job_status('https://test.com/1', 'actually_applied')

            assert result is True
            mock_put.assert_called_once()

    def test_update_job_status_with_applied_at(self):
        """Test updating job status with applied_at timestamp."""
        from local_apply import RailwayAPIClient

        client = RailwayAPIClient('https://test.railway.app')

        with patch('local_apply.requests.put') as mock_put:
            mock_response = Mock()
            mock_response.json.return_value = {'success': True}
            mock_response.raise_for_status = Mock()
            mock_put.return_value = mock_response

            result = client.update_job_status(
                'https://test.com/1',
                'actually_applied',
                applied_at='2024-01-01T00:00:00Z'
            )

            assert result is True
            # Verify the payload includes applied_at
            call_args = mock_put.call_args
            assert call_args[1]['json']['applied_at'] == '2024-01-01T00:00:00Z'

    def test_fetch_jobs_empty_response(self):
        """Test fetching jobs when none are available."""
        from local_apply import RailwayAPIClient

        client = RailwayAPIClient('https://test.railway.app')

        with patch('local_apply.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {'jobs': []}
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            jobs = client.fetch_jobs(min_score=7)

            assert len(jobs) == 0

    def test_update_job_status_failure(self):
        """Test updating job status when API returns failure."""
        from local_apply import RailwayAPIClient

        client = RailwayAPIClient('https://test.railway.app')

        with patch('local_apply.requests.put') as mock_put:
            mock_response = Mock()
            mock_response.json.return_value = {'success': False}
            mock_response.raise_for_status = Mock()
            mock_put.return_value = mock_response

            result = client.update_job_status('https://test.com/1', 'actually_applied')

            assert result is False


class TestStateManager:
    """Test state management."""

    def test_create_state_file(self):
        """Test state file creation."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            # State file is created when save() is called
            mgr.save()

            assert state_file.exists()
            assert 'jobs' in mgr.state
            assert 'stats' in mgr.state
            assert 'last_run' in mgr.state
            assert 'total_attempted' in mgr.state

    def test_state_file_initial_stats(self):
        """Test initial state statistics are zero."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            assert mgr.state['stats']['actually_applied'] == 0
            assert mgr.state['stats']['captcha'] == 0
            assert mgr.state['stats']['expired'] == 0
            assert mgr.state['stats']['failed'] == 0
            assert mgr.state['stats']['login_required'] == 0

    def test_mark_attempted(self):
        """Test marking job as attempted."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            mgr.mark_attempted('https://test.com/1', 'actually_applied', 'Job 1', 45000)

            assert mgr.is_attempted('https://test.com/1')
            assert mgr.state['stats']['actually_applied'] == 1
            assert mgr.state['total_attempted'] == 1

    def test_mark_attempted_with_duration(self):
        """Test marking job as attempted with duration."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            mgr.mark_attempted('https://test.com/1', 'captcha', 'Job 1', 30000)

            encoded_url = 'https%3A%2F%2Ftest.com%2F1'
            assert encoded_url in mgr.state['jobs']
            assert mgr.state['jobs'][encoded_url]['duration_ms'] == 30000
            assert mgr.state['jobs'][encoded_url]['title'] == 'Job 1'

    def test_mark_attempted_status_update(self):
        """Test updating status of an already attempted job."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            # First mark as captcha
            mgr.mark_attempted('https://test.com/1', 'captcha', 'Job 1')
            assert mgr.state['stats']['captcha'] == 1
            assert mgr.state['stats']['actually_applied'] == 0

            # Update to actually_applied
            mgr.mark_attempted('https://test.com/1', 'actually_applied', 'Job 1')
            assert mgr.state['stats']['captcha'] == 0
            assert mgr.state['stats']['actually_applied'] == 1
            assert mgr.state['total_attempted'] == 1  # Should not increment again

    def test_mark_attempted_same_status_idempotent(self):
        """Test that marking with same status doesn't change stats."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            mgr.mark_attempted('https://test.com/1', 'captcha', 'Job 1')
            first_total = mgr.state['stats']['captcha']

            mgr.mark_attempted('https://test.com/1', 'captcha', 'Job 1')
            assert mgr.state['stats']['captcha'] == first_total

    def test_is_attempted(self):
        """Test checking if job was attempted."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            assert not mgr.is_attempted('https://test.com/1')

            mgr.mark_attempted('https://test.com/1', 'actually_applied', 'Job 1')

            assert mgr.is_attempted('https://test.com/1')
            assert not mgr.is_attempted('https://test.com/2')

    def test_get_jobs_to_retry(self):
        """Test filtering attempted jobs by status."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            mgr.mark_attempted('https://test.com/1', 'actually_applied', 'Job 1')
            mgr.mark_attempted('https://test.com/2', 'captcha', 'Job 2')
            mgr.mark_attempted('https://test.com/3', 'captcha', 'Job 3')

            captcha_jobs = mgr.get_jobs_to_retry('captcha')

            assert len(captcha_jobs) == 2
            assert 'https://test.com/2' in captcha_jobs
            assert 'https://test.com/3' in captcha_jobs
            assert 'https://test.com/1' not in captcha_jobs

    def test_get_jobs_to_retry_empty_filter(self):
        """Test get_jobs_to_retry with no status filter returns empty set."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            mgr.mark_attempted('https://test.com/1', 'captcha', 'Job 1')

            result = mgr.get_jobs_to_retry()
            assert result == set()

    def test_save_updates_last_run(self):
        """Test that save updates last_run timestamp."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'
            mgr = StateManager(state_file)

            mgr.save()

            assert mgr.state['last_run'] is not None
            assert 'T' in mgr.state['last_run']  # ISO format should have T

    def test_load_existing_state(self):
        """Test loading existing state from file."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'

            # Create initial state
            initial_state = {
                'last_run': '2024-01-01T00:00:00Z',
                'total_attempted': 5,
                'stats': {
                    'actually_applied': 3,
                    'captcha': 2,
                    'expired': 0,
                    'failed': 0,
                    'login_required': 0
                },
                'jobs': {}
            }

            with open(state_file, 'w') as f:
                json.dump(initial_state, f)

            mgr = StateManager(state_file)

            assert mgr.state['total_attempted'] == 5
            assert mgr.state['stats']['actually_applied'] == 3
            assert mgr.state['last_run'] == '2024-01-01T00:00:00Z'

    def test_load_corrupted_state_fallback(self):
        """Test loading corrupted state falls back to defaults."""
        from local_apply import StateManager

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / 'state.json'

            # Write corrupted JSON
            with open(state_file, 'w') as f:
                f.write('{ invalid json }')

            mgr = StateManager(state_file)

            # Should have default values
            assert mgr.state['total_attempted'] == 0
            assert 'jobs' in mgr.state
            assert 'stats' in mgr.state


class TestJobProcessor:
    """Test job processing."""

    def test_process_job_dry_run(self):
        """Test processing job in dry run mode."""
        from local_apply import JobProcessor

        processor = JobProcessor()
        job = {'title': 'Test Job', 'site': 'Test Site', 'fit_score': 8}

        status, duration_ms = processor.process_job(job, dry_run=True)

        assert status == 'dry_run'
        assert duration_ms == 0

    def test_process_job_import_error(self):
        """Test processing job when ApplyPilot is not available."""
        from local_apply import JobProcessor

        processor = JobProcessor()
        job = {'title': 'Test Job', 'site': 'Test Site', 'fit_score': 8}

        # Patch the builtins.__import__ to simulate ImportError when importing applypilot
        with patch('builtins.__import__', side_effect=ImportError("No module")):
            status, duration_ms = processor.process_job(job, dry_run=False)

            assert status == 'failed'
            assert duration_ms == 0

    def test_process_job_exception_handling(self):
        """Test processing job handles exceptions gracefully."""
        from local_apply import JobProcessor

        processor = JobProcessor()
        job = {'title': 'Test Job', 'site': 'Test Site', 'fit_score': 8}

        # Patch the import to raise an exception after import
        def mock_import(name, *args, **kwargs):
            if name == 'applypilot.apply.launcher':
                raise Exception("Test error")
            return __import__(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            status, duration_ms = processor.process_job(job, dry_run=False)

            assert status == 'failed'
            assert duration_ms == 0

    def test_process_job_successful_apply(self):
        """Test processing job that succeeds."""
        from local_apply import JobProcessor

        processor = JobProcessor()
        job = {'title': 'Test Job', 'site': 'Test Site', 'fit_score': 8}

        # Create a mock module for applypilot.apply.launcher
        mock_run_job = Mock(return_value=('applied', 45000))
        mock_launcher = Mock()
        mock_launcher.run_job = mock_run_job

        mock_apply = Mock()
        mock_apply.launcher = mock_launcher

        mock_applypilot = Mock()
        mock_applypilot.apply = mock_apply

        with patch.dict('sys.modules', {'applypilot': mock_applypilot, 'applypilot.apply': mock_apply, 'applypilot.apply.launcher': mock_launcher}):
            status, duration_ms = processor.process_job(job, dry_run=False)

            assert status == 'actually_applied'
            assert duration_ms == 45000


class TestParseArgs:
    """Test command line argument parsing."""

    def test_parse_args_defaults(self):
        """Test parsing args with defaults."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py']):
            args = parse_args()

            assert args.dry_run is False
            assert args.limit is None
            assert args.single_job is None
            assert args.resume is False
            assert args.verbose is False
            assert args.ignore_captcha is False

    def test_parse_args_with_dry_run(self):
        """Test parsing --dry-run flag."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py', '--dry-run']):
            args = parse_args()

            assert args.dry_run is True

    def test_parse_args_with_limit(self):
        """Test parsing --limit argument."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py', '--limit', '10']):
            args = parse_args()

            assert args.limit == 10

    def test_parse_args_with_single_job(self):
        """Test parsing --single-job argument."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py', '--single-job', 'https://example.com/job']):
            args = parse_args()

            assert args.single_job == 'https://example.com/job'

    def test_parse_args_with_resume(self):
        """Test parsing --resume flag."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py', '--resume']):
            args = parse_args()

            assert args.resume is True

    def test_parse_args_with_verbose(self):
        """Test parsing --verbose flag."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py', '--verbose']):
            args = parse_args()

            assert args.verbose is True

    def test_parse_args_with_ignore_captcha(self):
        """Test parsing --ignore-captcha flag."""
        from local_apply import parse_args

        with patch('sys.argv', ['local_apply.py', '--ignore-captcha']):
            args = parse_args()

            assert args.ignore_captcha is True
