"""Tests for the advisor agent's scheduled-task tools (web/advisor_task_tools.py).

The tools let the advisor (driven from the WeCom bot bridge) list / start /
pause / resume scheduled tasks and push a run's report to WeChat. The real
scheduler manager is swapped for a fake via ``att._scheduler_manager``; the
push path monkeypatches the scheduler/push modules the tool bodies import
lazily (so no real scheduler or network is touched).
"""

from __future__ import annotations

import web.advisor_task_tools as att


def _task(job_id, name, ttype="emwl_batch", enabled=True, running=False,
          last=None, progress=None):
    return {
        "job_id": job_id, "name": name, "task_type": ttype,
        "enabled": enabled, "running": running, "last_run": last,
        "progress": progress,
    }


class _FakeMgr:
    """Records the scheduler calls the tools make."""
    def __init__(self, tasks):
        self._tasks = tasks
        self.runs = []
        self.pauses = []
        self.resumes = []

    def list_tasks(self):
        return list(self._tasks)

    def run_task(self, job_id):
        self.runs.append(job_id)
        return True

    def pause_task(self, job_id):
        self.pauses.append(job_id)
        return True

    def resume_task(self, job_id):
        self.resumes.append(job_id)
        return True

    def get_task_config(self, job_id):
        for t in self._tasks:
            if t["job_id"] == job_id:
                return {"job_id": job_id, "name": t["name"]}
        return None


class _FakeMgrRunFail:
    def __init__(self, tasks):
        self._tasks = tasks
        self.runs = []

    def list_tasks(self):
        return list(self._tasks)

    def run_task(self, job_id):
        self.runs.append(job_id)
        return False


def _use_mgr(monkeypatch, mgr):
    monkeypatch.setattr(att, "_scheduler_manager", lambda: mgr)
    return mgr


def _no_scheduler(monkeypatch):
    monkeypatch.setattr(att, "_scheduler_manager", lambda: None)


# ---- resolve_task -----------------------------------------------------------


def test_resolve_task_exact():
    t = _task("j1", "东方自选")
    task, cand = att.resolve_task("东方自选", [t])
    assert task["job_id"] == "j1"
    assert cand == []


def test_resolve_task_unique_substring():
    t1 = _task("j1", "东方自选批量")
    t2 = _task("j2", "指数选股")
    task, cand = att.resolve_task("东方", [t1, t2])
    assert task["job_id"] == "j1"
    assert cand == []


def test_resolve_task_ambiguous_substring():
    t1 = _task("j1", "东方自选")
    t2 = _task("j2", "东方精选")
    task, cand = att.resolve_task("东方", [t1, t2])
    assert task is None
    assert cand == ["东方自选", "东方精选"]


def test_resolve_task_unknown():
    task, cand = att.resolve_task("不存在", [_task("j1", "东方自选")])
    assert task is None
    assert cand == []


# ---- list / start / pause / resume -----------------------------------------


def test_list_scheduled_tasks_formats_rows(monkeypatch):
    last = {"run_at": "2026-08-25 19:59:00", "status": "done"}
    mgr = _use_mgr(monkeypatch, _FakeMgr([
        _task("j1", "东方自选", ttype="emwl_batch", enabled=True, running=True,
              last=last),
        _task("j2", "银行深度", ttype="deep", enabled=False),
    ]))
    res = att.list_scheduled_tasks.invoke({})
    assert "东方自选" in res and "银行深度" in res
    assert "🟢 启用" in res and "⏸ 暂停" in res
    assert "🔄 运行中" in res
    assert "2026-08-25 19:59:00" in res


def test_list_scheduled_tasks_no_scheduler(monkeypatch):
    _no_scheduler(monkeypatch)
    assert "尚未启动" in att.list_scheduled_tasks.invoke({})


def test_list_scheduled_tasks_empty(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([]))
    assert "还没有配置" in att.list_scheduled_tasks.invoke({})


def test_start_scheduled_task_calls_run_task(monkeypatch):
    mgr = _use_mgr(monkeypatch, _FakeMgr([
        _task("j1", "东方自选", ttype="emwl_batch")]))
    res = att.start_scheduled_task.invoke({"task_name": "东方自选"})
    assert "已启动" in res and "东方自选" in res
    assert mgr.runs == ["j1"]


def test_start_scheduled_task_unknown(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选")]))
    res = att.start_scheduled_task.invoke({"task_name": "不存在"})
    assert "未找到" in res


def test_start_scheduled_task_ambiguous(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([
        _task("j1", "东方自选"), _task("j2", "东方精选")]))
    res = att.start_scheduled_task.invoke({"task_name": "东方"})
    assert "匹配到多个任务" in res


def test_start_scheduled_task_already_running(monkeypatch):
    mgr = _use_mgr(monkeypatch, _FakeMgr([
        _task("j1", "东方自选", running=True)]))
    res = att.start_scheduled_task.invoke({"task_name": "东方自选"})
    assert "正在运行中" in res
    assert mgr.runs == []


def test_start_scheduled_task_run_failure(monkeypatch):
    mgr = _use_mgr(monkeypatch, _FakeMgrRunFail([_task("j1", "东方自选")]))
    res = att.start_scheduled_task.invoke({"task_name": "东方自选"})
    assert "失败" in res
    assert mgr.runs == ["j1"]


def test_pause_and_resume(monkeypatch):
    mgr = _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选", enabled=True)]))
    res = att.pause_scheduled_task.invoke({"task_name": "东方自选"})
    assert "已暂停" in res
    assert mgr.pauses == ["j1"]

    mgr2 = _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选", enabled=False)]))
    res = att.resume_scheduled_task.invoke({"task_name": "东方自选"})
    assert "已恢复" in res
    assert mgr2.resumes == ["j1"]


def test_pause_already_paused(monkeypatch):
    mgr = _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选", enabled=False)]))
    res = att.pause_scheduled_task.invoke({"task_name": "东方自选"})
    assert "已经是暂停状态" in res
    assert mgr.pauses == []


def test_resume_already_enabled(monkeypatch):
    mgr = _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选", enabled=True)]))
    res = att.resume_scheduled_task.invoke({"task_name": "东方自选"})
    assert "已经是启用状态" in res
    assert mgr.resumes == []


# ---- get_task_status --------------------------------------------------------


def test_get_task_status_shows_state_and_summary(monkeypatch):
    last = {
        "run_at": "2026-08-25 19:59:00", "status": "done",
        "summary": {"pool": 100, "bullish": 8, "bearish": 3, "watch": 89},
    }
    _use_mgr(monkeypatch, _FakeMgr([
        _task("j1", "东方自选", ttype="emwl_batch", running=True,
              last=last, progress={"phase": "analyzing", "analyzed": 42,
                                   "total": 100, "elapsed_sec": 120})]))
    res = att.get_task_status.invoke({"task_name": "东方自选"})
    assert "🟢 启用" in res and "🔄" in res
    assert "42/100" in res
    assert "看多 8" in res


# ---- push_task_report -------------------------------------------------------


def test_push_task_report_pushes_latest(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选", ttype="emwl_batch")]))
    pushes = []
    monkeypatch.setattr(
        "web.wecom_push.push_run_result",
        lambda td, summary, ttype: pushes.append((td, summary, ttype))
        or {"ok": True, "channel": "bot", "errmsg": ""})
    monkeypatch.setattr(
        "web.scheduler.get_last_scheduler_run",
        lambda cfg, job_id: {"summary": {"pool": 100},
                             "task_type": "emwl_batch"})

    res = att.push_task_report.invoke({"task_name": "东方自选"})
    assert "已把" in res and "东方自选" in res and "智能机器人" in res
    assert pushes and pushes[0][0]["job_id"] == "j1"
    assert pushes[0][1] == {"pool": 100}
    assert pushes[0][2] == "emwl_batch"


def test_push_task_report_by_run_id(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选", ttype="emwl_batch")]))
    pushes = []
    monkeypatch.setattr(
        "web.wecom_push.push_run_result",
        lambda td, summary, ttype: pushes.append(td) or {"ok": True,
                                                         "channel": "webhook",
                                                         "errmsg": ""})
    monkeypatch.setattr(
        "web.scheduler.get_scheduler_run",
        lambda cfg, run_id, expected_job_id=None:
            {"summary": {"pool": 5}, "task_type": "emwl_batch"})
    res = att.push_task_report.invoke({"task_name": "东方自选", "run_id": 7})
    assert "记录#7" in res and "群机器人" in res


def test_push_task_report_no_runs(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选")]))
    monkeypatch.setattr("web.scheduler.get_last_scheduler_run",
                        lambda cfg, job_id: None)
    res = att.push_task_report.invoke({"task_name": "东方自选"})
    assert "还没有运行记录" in res


def test_push_task_report_run_id_missing(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选")]))
    monkeypatch.setattr("web.scheduler.get_scheduler_run",
                        lambda cfg, run_id, expected_job_id=None: None)
    res = att.push_task_report.invoke({"task_name": "东方自选", "run_id": 9})
    assert "没有记录 ID 为 9 的运行" in res


def test_push_task_report_push_failure(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选")]))
    monkeypatch.setattr(
        "web.wecom_push.push_run_result",
        lambda td, summary, ttype: {"ok": False, "channel": None,
                                    "errmsg": "未配置微信推送渠道"})
    monkeypatch.setattr(
        "web.scheduler.get_last_scheduler_run",
        lambda cfg, job_id: {"summary": {}, "task_type": "emwl_batch"})
    res = att.push_task_report.invoke({"task_name": "东方自选"})
    assert "推送失败" in res and "未配置微信推送渠道" in res


def test_push_task_report_unknown_task(monkeypatch):
    _use_mgr(monkeypatch, _FakeMgr([_task("j1", "东方自选")]))
    res = att.push_task_report.invoke({"task_name": "不存在"})
    assert "未找到" in res
