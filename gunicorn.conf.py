"""Gunicorn settings (loaded automatically from the working directory)."""
bind = "0.0.0.0:8000"
# One worker: Atrium session limits and the semester stats file assume a single process;
# threads serve concurrent requests and run the scrape jobs.
workers = 1
threads = 8
timeout = 120


def post_worker_init(worker):
    """Restart Wrapped runs the previous process left unfinished (deploy or crash)."""
    import app

    app.recover_jobs()
