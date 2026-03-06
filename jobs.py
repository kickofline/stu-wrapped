import threading
import uuid
from datetime import datetime, timedelta

JOBS: dict = {}
_lock = threading.Lock()

def create_job(total_steps: int = 10, plus_address: str = None, job_id: str = None) -> str:
    """Create a new job. Returns job_id."""
    if job_id is None:
        job_id = str(uuid.uuid4())
    with _lock:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "step": 0,
            "total_steps": total_steps,
            "message": "Queued...",
            "error": None,
            "result": None,
            "metadata": None,
            "plus_address": plus_address,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def set_job_plus_address(job_id: str, plus_address: str) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["plus_address"] = plus_address


def update_job_progress(job_id: str, step: int, message: str) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["step"] = step
            JOBS[job_id]["message"] = message
            JOBS[job_id]["status"] = "running"


def complete_job(job_id: str, result: dict, metadata: dict) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["step"] = JOBS[job_id]["total_steps"]
            JOBS[job_id]["message"] = "Complete!"
            JOBS[job_id]["result"] = result
            JOBS[job_id]["metadata"] = metadata
            JOBS[job_id]["completed_at"] = datetime.now().isoformat()


def fail_job(job_id: str, error_message: str) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = f"Error: {error_message}"
            JOBS[job_id]["error"] = error_message


def cleanup_old_jobs(max_age_hours: int = 2) -> None:
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    with _lock:
        to_delete = [
            jid for jid, job in JOBS.items()
            if datetime.fromisoformat(job["created_at"]) < cutoff
        ]
        for jid in to_delete:
            del JOBS[jid]
