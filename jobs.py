"""In-memory job tracking.

Gunicorn runs a single worker, so this module-level dict is shared by every
request. A job starts "waiting" for its inbox's credentials, is claimed
("running") exactly once by whichever side has them first — /start when they
are already stored, /api/inbound when the invite arrives — then ends "done"
(with a share_id) or "error".
"""
import secrets
import threading
from datetime import datetime, timedelta

TOTAL_STEPS = 6
WAITING_MESSAGE = "Waiting for your Flex Bucks invite email..."

JOBS: dict[str, dict] = {}
_lock = threading.Lock()


def create_job(tag: str) -> str:
    job_id = secrets.token_urlsafe(9)
    with _lock:
        JOBS[job_id] = {
            "job_id": job_id,
            "tag": tag,
            "status": "waiting",
            "step": 0,
            "total_steps": TOTAL_STEPS,
            "message": WAITING_MESSAGE,
            "error": None,
            "share_id": None,
            "created_at": datetime.now(),
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def active_job_for(tag: str) -> dict | None:
    with _lock:
        for job in JOBS.values():
            if job["tag"] == tag and job["status"] in ("waiting", "running"):
                return job
    return None


def _mark_running(job: dict) -> None:
    job["status"] = "running"
    job["message"] = "Got your Flex Bucks invite!"


def claim(job_id: str) -> bool:
    """Move a waiting job to running. False if someone else already did."""
    with _lock:
        job = JOBS.get(job_id)
        if not job or job["status"] != "waiting":
            return False
        _mark_running(job)
        return True


def claim_waiting(tag: str) -> list[str]:
    """Move every waiting job for this inbox to running; return their ids."""
    with _lock:
        claimed = [j for j in JOBS.values() if j["tag"] == tag and j["status"] == "waiting"]
        for job in claimed:
            _mark_running(job)
        return [j["job_id"] for j in claimed]


def update_progress(job_id: str, step: int, message: str) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["step"] = step
            JOBS[job_id]["message"] = message


def finish(job_id: str, share_id: str) -> None:
    with _lock:
        if job_id in JOBS:
            job = JOBS[job_id]
            job["status"] = "done"
            job["step"] = job["total_steps"]
            job["message"] = "Complete!"
            job["share_id"] = share_id


def fail(job_id: str, error_message: str) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = error_message
            JOBS[job_id]["error"] = error_message


def cleanup_old_jobs(max_age_hours: int = 2) -> None:
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    with _lock:
        for job_id in [jid for jid, job in JOBS.items() if job["created_at"] < cutoff]:
            del JOBS[job_id]
