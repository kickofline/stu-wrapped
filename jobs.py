"""Job tracking, persisted in data/state.db so waiting pages and status links survive
restarts and deploys.

A job starts "waiting" for its inbox's credentials, is claimed ("running") exactly once
by whichever side has them first — /start when they are already stored, /api/inbound
when the invite arrives — then ends "done" (with a share_id) or "error". A job still
"running" when the process stopped is picked back up by app.recover_jobs() at boot.
"""
import secrets
import threading
from contextlib import closing
from datetime import datetime, timedelta

import state

TOTAL_STEPS = 6
WAITING_MESSAGE = "Waiting for your Flex Bucks invite…"
RUNNING_MESSAGE = "Got your Flex Bucks invite!"
MAX_JOBS = 5000
STALE_WAITING = timedelta(minutes=30)

# Gunicorn runs one worker; the lock keeps read-then-write steps atomic between its
# threads, and BEGIN IMMEDIATE does the same against other processes (tools).
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write(sql: str, args: tuple) -> None:
    with _lock, closing(state.connect()) as con:
        con.execute(sql, args)


def job_for(tag: str, owner_hash: str | None = None) -> str | None:
    """This inbox's waiting or running job, else a new waiting one. Atomic, so parallel
    taps on Generate share one job. When the table is full, finished and long-stale
    waiting jobs make room; None if it is still full."""
    with _lock, closing(state.connect()) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute("SELECT job_id FROM jobs WHERE tag = ? AND status IN ('waiting', 'running')",
                              (tag,)).fetchone()
            if row:
                con.execute("COMMIT")
                return row["job_id"]
            if con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
                cutoff = (datetime.now() - STALE_WAITING).isoformat(timespec="seconds")
                con.execute("DELETE FROM jobs WHERE status IN ('done', 'error') "
                            "OR (status = 'waiting' AND created_at < ?)", (cutoff,))
                if con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
                    con.execute("COMMIT")
                    return None
            job_id = secrets.token_urlsafe(9)
            con.execute(
                "INSERT INTO jobs (job_id, tag, status, step, total_steps, message, owner_hash, created_at) "
                "VALUES (?, ?, 'waiting', 0, ?, ?, ?, ?)",
                (job_id, tag, TOTAL_STEPS, WAITING_MESSAGE, owner_hash, _now()),
            )
            con.execute("COMMIT")
            return job_id
        except BaseException:
            con.execute("ROLLBACK")
            raise


def get_job(job_id: str) -> dict | None:
    with closing(state.connect()) as con:
        row = con.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def claim(job_id: str) -> bool:
    """Move a waiting job to running. False if someone else already did."""
    with _lock, closing(state.connect()) as con:
        cursor = con.execute("UPDATE jobs SET status = 'running', message = ? "
                             "WHERE job_id = ? AND status = 'waiting'", (RUNNING_MESSAGE, job_id))
        return cursor.rowcount == 1


def claim_waiting(tag: str) -> list[str]:
    """Move every waiting job for this inbox to running; return their ids."""
    with _lock, closing(state.connect()) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            ids = [row["job_id"] for row in con.execute(
                "SELECT job_id FROM jobs WHERE tag = ? AND status = 'waiting'", (tag,))]
            con.execute("UPDATE jobs SET status = 'running', message = ? WHERE tag = ? AND status = 'waiting'",
                        (RUNNING_MESSAGE, tag))
            con.execute("COMMIT")
            return ids
        except BaseException:
            con.execute("ROLLBACK")
            raise


def running_jobs() -> list[dict]:
    """Jobs left "running": at boot these are runs the previous process never finished."""
    with closing(state.connect()) as con:
        return [dict(row) for row in con.execute("SELECT * FROM jobs WHERE status = 'running'")]


def update_progress(job_id: str, step: int, message: str) -> None:
    _write("UPDATE jobs SET step = ?, message = ? WHERE job_id = ?", (step, message, job_id))


def finish(job_id: str, share_id: str) -> None:
    _write("UPDATE jobs SET status = 'done', step = total_steps, message = 'Complete!', share_id = ? "
           "WHERE job_id = ?", (share_id, job_id))


def fail(job_id: str, error_message: str) -> None:
    _write("UPDATE jobs SET status = 'error', message = ?, error = ? WHERE job_id = ?",
           (error_message, error_message, job_id))


def cleanup_old_jobs(max_age_hours: int = 2) -> None:
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    _write("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
