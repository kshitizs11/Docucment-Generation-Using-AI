"""
In-memory job store for the async /generate flow.

SCOPE NOTE: this is intentionally in-memory, single-process. It does not survive a
restart and does not work across multiple uvicorn workers. That's the right amount of
infrastructure for proving the async flow works at all; swap this for a real queue
(Redis/Celery/etc.) when there's an actual multi-worker deployment to target -- don't
guess at that infrastructure before it's needed.
"""
import threading
import time
import uuid

_JOBS: dict = {}
_lock = threading.Lock()


def create_job(request_text: str) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _JOBS[job_id] = {
            "status": "pending",
            "current_step": None,
            "request": request_text,
            "result": None,
            "error": None,
            "created_at": time.time(),
            "finished_at": None,
        }
    return job_id


def update_job(job_id: str, **fields) -> None:
    with _lock:
        _JOBS[job_id].update(fields)


def get_job(job_id: str):
    with _lock:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


def list_jobs() -> dict:
    with _lock:
        return {job_id: dict(job) for job_id, job in _JOBS.items()}
