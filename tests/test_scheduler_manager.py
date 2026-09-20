"""SchedulerManager CRUD + manual-run tests against a temp SQLAlchemyJobStore.

No network / no real analysis: ``web.scheduler._execute_scheduled_analysis``
is monkeypatched to a no-op stub, so jobs only prove the plumbing
(add/list/pause/resume/delete/run-now + Asia/Shanghai trigger timezone).
"""

from __future__ import annotations

import threading

import pytest
from apscheduler.triggers.cron import CronTrigger

from web.scheduler import SchedulerManager, init_scheduler_run_store


@pytest.fixture()
def manager(tmp_path):
    cfg = {"results_dir": str(tmp_path)}
    init_scheduler_run_store(cfg)
    m = SchedulerManager(cfg)
    m.start()
    try:
        yield m
    finally:
        m.shutdown()


def _stub_analysis(monkeypatch):
    """Patch the scheduled-analysis entrypoint to capture calls."""
    from web import scheduler as sched_mod

    calls = []
    done = threading.Event()

    def stub(task_data):
        calls.append(task_data)
        done.set()

    monkeypatch.setattr(sched_mod, "_execute_scheduled_analysis", stub)
    return calls, done


def test_add_list_run_delete(manager, monkeypatch):
    task_data = {
        "job_id": "emwl-daily",
        "name": "东方自选每日",
        "task_type": "emwl_batch",
        "cron_expression": "30 9 * * *",
        "workers": [{"provider": "", "model": ""}],
        "excluded_boards": ["kcb"],
        "change_pct_min": 0.0,
        "two_pass": True,
        "two_pass_mode": "pack",
    }
    assert manager.add_task(task_data) == "emwl-daily"
    # add_task writes the real job id back into task_data so _execute_scheduled_analysis
    # records runs (and last_run lookup) under the actual job id.
    assert task_data["job_id"] == "emwl-daily"

    # Patch AFTER add_task: APScheduler pickles a "module:name" reference at
    # add time, so the real module-level function must be visible then. The
    # stub is only resolved at run time (when run_task fires the job).
    calls, done = _stub_analysis(monkeypatch)

    tasks = manager.list_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert t["job_id"] == "emwl-daily"
    assert t["name"] == "东方自选每日"
    assert t["task_type"] == "emwl_batch"
    assert t["enabled"] is True
    assert t["last_run"] is None

    # Trigger must be interpreted in Asia/Shanghai, not UTC.
    job = manager._scheduler.get_job("emwl-daily")
    assert job.trigger.timezone.key == "Asia/Shanghai"

    # Manual run fires the (stubbed) entrypoint with the stored task_data.
    assert manager.run_task("emwl-daily") is True
    assert done.wait(timeout=5), "manual run did not fire"
    assert calls == [task_data]

    assert manager.pause_task("emwl-daily") is True
    tasks = manager.list_tasks()
    assert tasks[0]["enabled"] is False

    assert manager.resume_task("emwl-daily") is True
    assert manager.delete_task("emwl-daily") is True
    assert manager.list_tasks() == []


def test_deep_task_legacy_shape(manager, monkeypatch):
    task_data = {
        "job_id": "deep-1",
        "name": "AAPL deep",
        "task_type": "deep",
        "ticker": "AAPL",
        "cron_expression": "30 9 * * 1-5",
        "analysts": ["market", "news"],
    }
    manager.add_task(task_data)
    _stub_analysis(monkeypatch)  # patch after add (see test above)
    t = manager.get_task("deep-1")
    assert t["task_type"] == "deep"
    assert t["ticker"] == "AAPL"
    assert t["enabled"] is True
    assert t["last_run"] is None


def test_add_task_writes_back_generated_job_id(manager):
    """Regression: when no job_id is supplied, APScheduler generates one — the
    writeback makes task_data["job_id"] match, fixing last_run linkage."""
    task_data = {
        "name": "无ID任务",
        "task_type": "deep",
        "ticker": "AAPL",
        "cron_expression": "0 8 * * *",
    }
    assert "job_id" not in task_data
    job_id = manager.add_task(task_data)
    assert job_id
    assert task_data["job_id"] == job_id
    t = manager.get_task(job_id)
    assert t is not None
    assert t["job_id"] == job_id


def test_jobs_allow_a_day_for_sleep_resume_misfires(manager):
    """A laptop wake-up should still run a daily evaluation missed earlier."""
    manager.add_task({
        "job_id": "resume-safe",
        "name": "恢复补跑",
        "task_type": "evaluation_settle",
        "cron_expression": "30 15 * * 1-5",
    })

    job = manager._scheduler.get_job("resume-safe")

    assert job.misfire_grace_time == 24 * 60 * 60


def test_run_task_missing_job(manager):
    assert manager.run_task("nope") is False
    assert manager.delete_task("nope") is False
    assert manager.get_task("nope") is None


def test_update_task_preserves_job_id_and_replaces_config(manager):
    """Editing keeps the job_id (run-history/report linkage) but swaps the
    trigger, name and stored task_data."""
    task_data = {
        "job_id": "edit-me",
        "name": "旧名字",
        "task_type": "idx_batch",
        "cron_expression": "54 14 * * *",
        "indexes": ["highdiv"],
    }
    manager.add_task(task_data)
    old_trigger = str(manager._scheduler.get_job("edit-me").trigger)

    new_td = {
        "name": "新名字",
        "task_type": "emwl_batch",
        "cron_expression": "30 15 * * 1-5",
        "workers": [
            {"provider": "ollama", "model": "qwen3.8:27b"},
            {"provider": "ollama2", "model": "qwen3.8:27b"},
        ],
        "excluded_boards": ["kcb"],
    }
    assert manager.update_task("edit-me", new_td) is True

    t = manager.get_task("edit-me")
    assert t["job_id"] == "edit-me"          # linkage preserved
    assert t["name"] == "新名字"
    assert t["task_type"] == "emwl_batch"
    assert t["enabled"] is True
    assert t["trigger"] != old_trigger        # schedule actually changed
    assert "1-5" in t["trigger"]

    # Stored task_data fully replaced, with job_id writeback still applied.
    stored = manager._scheduler.get_job("edit-me").kwargs["task_data"]
    assert stored["job_id"] == "edit-me"
    assert stored["name"] == "新名字"
    assert len(stored["workers"]) == 2
    assert stored["excluded_boards"] == ["kcb"]

    # get_task_config returns the full stored config for form prefill.
    cfg = manager.get_task_config("edit-me")
    assert cfg["name"] == "新名字"
    assert cfg["task_type"] == "emwl_batch"
    assert len(cfg["workers"]) == 2

    # Missing jobs are rejected, not silently created.
    assert manager.get_task_config("nope") is None
    assert manager.update_task("nope", new_td) is False


def test_update_task_keeps_paused_task_paused(manager):
    task_data = {
        "job_id": "pause-edit",
        "name": "暂停任务",
        "task_type": "deep",
        "ticker": "AAPL",
        "cron_expression": "0 9 * * *",
    }
    manager.add_task(task_data)
    assert manager.pause_task("pause-edit") is True
    assert manager.get_task("pause-edit")["enabled"] is False

    # Editing must not accidentally re-enable a paused task.
    assert manager.update_task("pause-edit", {
        "name": "暂停任务(改)",
        "task_type": "deep",
        "ticker": "AAPL",
        "cron_expression": "0 10 * * *",
    }) is True
    assert manager.get_task("pause-edit")["enabled"] is False
    # ...but the stored config did change.
    assert manager.get_task_config("pause-edit")["cron_expression"] == "0 10 * * *"


def test_reconcile_legacy_job_ids_backfills_orphan_run(manager):
    """Regression: tasks persisted BEFORE the add_task job_id write-back fix
    have no job_id in their stored task_data, so run records land under job_id=''
    and last_run/report lookup by the real job id finds nothing.

    Simulate the legacy shape by registering a job whose task_data lacks job_id
    directly on the scheduler (bypassing add_task), dropping a run record under
    job_id='', then re-running the reconcile that start() performs — it must
    write the real id into the persisted kwargs AND backfill the orphan record.
    """
    from web.scheduler import _execute_scheduled_analysis, save_scheduler_run

    legacy_td = {
        # deliberately NO job_id key — the pre-fix persisted shape
        "name": "旧任务",
        "task_type": "emwl_batch",
        "cron_expression": "30 9 * * *",
    }
    real_id = manager._scheduler.add_job(
        func=_execute_scheduled_analysis,
        trigger=CronTrigger.from_crontab("30 9 * * *", timezone="Asia/Shanghai"),
        kwargs={"task_data": legacy_td},
        name=legacy_td["name"],
    ).id

    # Orphaned run record under job_id='' — exactly what the old code produced.
    save_scheduler_run(manager.config, "", legacy_td["name"], "emwl_batch",
                       {"analyzed": 1})

    # last_run is invisible before reconcile.
    assert manager.get_task(real_id)["last_run"] is None

    manager._reconcile_legacy_job_ids()

    # task_data now carries the real job id (future runs save correctly)...
    job = manager._scheduler.get_job(real_id)
    assert job.kwargs["task_data"]["job_id"] == real_id
    # ...and the existing orphaned run record is matched by name and backfilled.
    last = manager.get_task(real_id)["last_run"]
    assert last is not None
    assert last["job_id"] == real_id
    assert last["summary"]["analyzed"] == 1

    # Idempotent: a second pass is a no-op (skips the already-reconciled job).
    manager._reconcile_legacy_job_ids()
    assert manager.get_task(real_id)["last_run"]["job_id"] == real_id


def test_list_tasks_surfaces_running_progress(manager):
    """While a run is in flight, list_tasks/get_task carry running=True and the
    progress snapshot; once the marker is cleared they fall back to None."""
    from web import run_progress

    manager.add_task({
        "job_id": "prog-1",
        "name": "东方自选",
        "task_type": "emwl_batch",
        "cron_expression": "30 9 * * *",
        "workers": [{"provider": "", "model": ""}],
    })
    idle = manager.list_tasks()[0]
    assert idle["running"] is False
    assert idle["progress"] is None
    assert manager.get_task("prog-1")["running"] is False

    run_progress.start("prog-1", phase="analyzing", total=50)
    run_progress.update("prog-1", analyzed=10, current="600036")

    t = manager.list_tasks()[0]
    assert t["running"] is True
    assert t["progress"]["phase"] == "analyzing"
    assert t["progress"]["analyzed"] == 10
    assert t["progress"]["total"] == 50
    assert t["progress"]["current"] == "600036"
    assert "elapsed_sec" in t["progress"]

    assert manager.get_task("prog-1")["running"] is True

    run_progress.remove("prog-1")
    idle2 = manager.list_tasks()[0]
    assert idle2["running"] is False
    assert idle2["progress"] is None


def test_get_scheduler_runs_returns_full_history(manager):
    """Every run is appended to scheduled_run_log; get_scheduler_runs returns
    them newest-first and get_scheduler_run fetches one by row id — so each
    run's report can be rebuilt on demand."""
    from web.scheduler import (get_last_scheduler_run, get_scheduler_run,
                               get_scheduler_runs, save_scheduler_run)

    assert get_scheduler_runs(manager.config, "hist-1") == []

    id1 = save_scheduler_run(manager.config, "hist-1", "历史任务",
                             "emwl_batch", {"analyzed": 3, "bullish": 2})
    id2 = save_scheduler_run(manager.config, "hist-1", "历史任务",
                             "emwl_batch", {"analyzed": 5, "bullish": 4})

    runs = get_scheduler_runs(manager.config, "hist-1")
    assert [r["id"] for r in runs] == [id2, id1]           # newest first
    assert runs[0]["summary"]["bullish"] == 4
    assert runs[1]["summary"]["bullish"] == 2

    # get_last_scheduler_run is still just the newest row.
    assert get_last_scheduler_run(manager.config, "hist-1")["id"] == id2

    # Individual run lookup by row id (what ?run_id= uses).
    assert get_scheduler_run(manager.config, id1)["summary"]["analyzed"] == 3
    assert get_scheduler_run(manager.config, id2)["summary"]["analyzed"] == 5
    assert get_scheduler_run(manager.config, 999999) is None

    # IDOR scoping: a run must belong to the requested job, or it's invisible
    # to a ?run_id= lookup under a different job's report.
    assert get_scheduler_run(manager.config, id1, expected_job_id="hist-1")["id"] == id1
    assert get_scheduler_run(manager.config, id1, expected_job_id="OTHER-JOB") is None

    # Other jobs' runs are not mixed in.
    assert get_scheduler_runs(manager.config, "hist-2") == []


def test_get_scheduler_runs_honors_limit(manager):
    from web.scheduler import get_scheduler_runs, save_scheduler_run

    for i in range(5):
        save_scheduler_run(manager.config, "lim-1", "限量", "deep",
                           {"rating": "buy", "analyzed": i})
    assert len(get_scheduler_runs(manager.config, "lim-1")) == 5
    assert len(get_scheduler_runs(manager.config, "lim-1", limit=2)) == 2
