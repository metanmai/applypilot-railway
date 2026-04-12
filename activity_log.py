"""
Shared activity logging for dashboard.
"""
import threading
from datetime import datetime
from typing import Optional

# Thread-safe activity log
recent_activity = []
MAX_ACTIVITY = 50
activity_lock = threading.Lock()


def log_activity(level: str, worker: str, message: str, job_title: Optional[str] = None):
    """Log activity for dashboard display."""
    activity = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "worker": worker,
        "message": message,
        "job_title": job_title
    }
    with activity_lock:
        recent_activity.append(activity)
        if len(recent_activity) > MAX_ACTIVITY:
            recent_activity.pop(0)


def get_activity():
    """Get all recent activity."""
    with activity_lock:
        return list(recent_activity)


def clear_activity():
    """Clear all activity."""
    with activity_lock:
        recent_activity.clear()
