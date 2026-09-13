"""Unit tests for the in-process run-progress registry (web/run_progress.py).

The registry is a process-wide singleton shared across tests, so an autouse
fixture clears it between tests to leave no residue.
"""

from __future__ import annotations

import time

import pytest

import web.run_progress as rp


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    with rp._lock:
        rp._RUNS.clear()


def test_start_get_update_remove():
    rp.start("job-1", phase="analyzing", total=50)
    p = rp.get("job-1")
    assert p is not None
    assert p["status"] == "running"
    assert p["phase"] == "analyzing"
    assert p["analyzed"] == 0
    assert p["total"] == 50
    assert p["current"] == ""
    assert "started_at" in p
    assert p["elapsed_sec"] >= 0

    rp.update("job-1", analyzed=12, current="600036")
    p = rp.get("job-1")
    assert p["analyzed"] == 12
    assert p["current"] == "600036"
    assert p["total"] == 50           # untouched fields preserved

    rp.remove("job-1")
    assert rp.get("job-1") is None
    rp.remove("job-1")                # idempotent


def test_get_returns_snapshot_copy():
    """get() must hand back a copy — mutating it must not leak into the registry."""
    rp.start("snap", total=5)
    p1 = rp.get("snap")
    p1["analyzed"] = 999
    p2 = rp.get("snap")
    assert p2["analyzed"] == 0
    assert rp.get("snap")["analyzed"] == 0


def test_unknown_job_is_none():
    assert rp.get("never-started") is None


def test_empty_job_id_is_noop():
    """Callers outside the scheduler pass job_id='' and must see zero side effects."""
    rp.start("", total=3)
    rp.update("", analyzed=1, current="x")
    rp.remove("")
    assert rp.get("") is None
    assert rp.get("anything") is None


def test_update_ignores_unregistered_run():
    """update() on a never-started job is a silent no-op (no KeyError)."""
    rp.update("ghost", analyzed=5)
    assert rp.get("ghost") is None


def test_start_replaces_existing_run():
    rp.start("reuse", phase="analyzing", total=10)
    rp.update("reuse", analyzed=4)
    rp.start("reuse", phase="deep", total=0)
    p = rp.get("reuse")
    assert p["phase"] == "deep"
    assert p["analyzed"] == 0          # fresh counters, not the old 4
    assert p["total"] == 0


def test_elapsed_sec_tracks_time():
    rp.start("timer")
    t0 = rp.get("timer")["elapsed_sec"]
    time.sleep(0.05)
    t1 = rp.get("timer")["elapsed_sec"]
    assert t1 >= t0
