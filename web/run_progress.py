"""In-process registry of in-flight scheduled runs (thread-safe).

Scheduled batch runs live in the same uvicorn process as the web app, so a
module-level dict is readable by ``SchedulerManager.list_tasks`` while the
run is still in progress. APScheduler jobs have ``max_instances=1``, so a
given job_id is never updated by two concurrent runs.

Lifecycle:
    run_batch_task starts/updates phase + per-stock counters; the deep branch
    of ``_execute_scheduled_analysis`` starts its own entry; the finally there
    calls ``remove`` so no entry survives a finished (or crashed) run.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_RUNS: dict[str, dict] = {}


def start(job_id: str, phase: str = "analyzing", total: int = 0,
          current: str = "") -> None:
    """Record the start of a run for ``job_id`` (no-op for empty id)."""
    if not job_id:
        return
    with _lock:
        _RUNS[job_id] = {
            "status": "running",
            "phase": phase,
            "analyzed": 0,
            "total": total,
            "current": current,
            "started_at": time.time(),
        }


def update(job_id: str, **fields) -> None:
    """Patch fields on an active run; ignored if the run is not registered."""
    if not job_id:
        return
    with _lock:
        run = _RUNS.get(job_id)
        if run:
            run.update(fields)


def remove(job_id: str) -> None:
    """Drop the run for ``job_id`` (idempotent)."""
    with _lock:
        _RUNS.pop(job_id, None)


def get(job_id: str) -> dict | None:
    """Return a read-only snapshot (with ``elapsed_sec``) or None."""
    with _lock:
        run = _RUNS.get(job_id)
        if not run:
            return None
        return {**run, "elapsed_sec": int(time.time() - run["started_at"])}
