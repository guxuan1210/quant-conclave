"""Tests for the scheduled-task operation audit log (web/task_audit.py).

Uses a temp results.db via ``config={"results_dir": str(tmp_path)}`` — no
network.
"""

from __future__ import annotations

import pytest

from web.task_audit import (
    init_task_audit_store,
    list_task_audit,
    log_task_action,
)


@pytest.fixture()
def config(tmp_path):
    return {"results_dir": str(tmp_path)}


def test_init_log_list_roundtrip(config):
    init_task_audit_store(config)
    rid = log_task_action(
        config, "job-1", "create", "东方自选每日", "emwl_batch",
        detail={"name": "东方自选每日", "cron_expression": "0 8 * * *"},
    )
    assert rid > 0

    entries = list_task_audit(config)
    assert len(entries) == 1
    e = entries[0]
    assert e["job_id"] == "job-1"
    assert e["action"] == "create"
    assert e["task_name"] == "东方自选每日"
    assert e["task_type"] == "emwl_batch"
    assert e["status"] == "ok"
    assert e["created_at"]  # non-empty localtime
    # detail JSON preserved
    assert e["detail"]["cron_expression"] == "0 8 * * *"


def test_all_actions_and_newest_first(config):
    init_task_audit_store(config)
    for action in ("create", "pause", "resume", "delete", "run"):
        log_task_action(config, "job-x", action, "任务X", "deep")
    entries = list_task_audit(config)
    assert [e["action"] for e in entries] == ["run", "delete", "resume", "pause", "create"]


def test_detail_defaults_empty(config):
    init_task_audit_store(config)
    log_task_action(config, "j", "delete", "t", "idx_batch")
    entries = list_task_audit(config)
    assert entries[0]["detail"] == {}
    assert entries[0]["status"] == "ok"


def test_limit_newest_only(config):
    init_task_audit_store(config)
    for i in range(5):
        log_task_action(config, "j", "run", "t", "deep", detail={"i": i})
    entries = list_task_audit(config, limit=2)
    assert len(entries) == 2
    assert entries[0]["detail"]["i"] == 4


def test_init_idempotent(config):
    init_task_audit_store(config)
    init_task_audit_store(config)  # no error on second call
    log_task_action(config, "j", "run", "t", "deep")
    assert len(list_task_audit(config)) == 1
