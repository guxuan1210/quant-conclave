"""Advisor-agent tools for managing scheduled tasks via chat.

These tools are appended to ``build_tool_set`` in ``web/history_chat.py`` so the
advisor agent can list / start / pause / resume scheduled tasks and push a
run's report to WeChat — the remote-control capability that powers the WeCom
bot bridge (``web/bot_advisor_bridge.py``).

Design rules (mirroring the codebase's tool pattern):
- Each tool is a ``@tool`` (langchain_core.tools); the agent loop dispatches by
  name via ``tool_map``, so adding a tool here is additive and never changes
  existing tools.
- Cross-module references (the scheduler manager, the push module, the config)
  are imported lazily INSIDE tool bodies — no import-time side effects and no
  circular imports at module load.
- Tools only act on EXISTING tasks by name — there is deliberately no create /
  edit tool: a chat form is too error-prone. The web UI remains the place to
  define tasks.
- Tasks are resolved by name (exact → unique substring → ambiguous hint) via
  ``resolve_task``, so the user can say 「东方自选」 without quoting a job id.
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_TASK_TYPE_LABELS = {
    "deep": "深度分析",
    "emwl_batch": "东方自选批量",
    "idx_batch": "指数选股批量",
}


def _task_type_label(ttype: str | None) -> str:
    return _TASK_TYPE_LABELS.get(ttype or "deep", ttype or "深度分析")


def _scheduler_manager():
    """The running SchedulerManager (web.app module global)."""
    from web.app import _scheduler_manager
    return _scheduler_manager


def _default_config() -> dict:
    from quantconclave.default_config import DEFAULT_CONFIG
    return DEFAULT_CONFIG


def resolve_task(task_name: str, tasks: list[dict]) -> tuple[dict | None, list[str]]:
    """Resolve a user-supplied name to exactly one task.

    Exact name match wins; otherwise a unique case-insensitive substring match.
    Returns ``(task, [])`` on a hit, ``(None, candidates)`` when ambiguous, or
    ``(None, [])`` when nothing matched.
    """
    name = (task_name or "").strip()
    if not name:
        return None, []
    by_name: dict[str, list[dict]] = {}
    for t in tasks:
        by_name.setdefault(t.get("name", ""), []).append(t)
    exact = by_name.get(name)
    if exact and len(exact) == 1:
        return exact[0], []
    lowered = name.lower()
    matches = [t for t in tasks if lowered in (t.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, [t.get("name", "?") for t in matches]
    return None, []


def _ambiguous(name: str, candidates: list[str]) -> str:
    return (f"「{name}」匹配到多个任务: {', '.join(candidates)}。"
            f"请说得更具体一些。")


def _not_found(name: str) -> str:
    return (f"未找到名为「{name}」的定时任务。可先发「我的任务」查看全部任务。")


def _load_tasks(mgr) -> tuple[list[dict] | None, str]:
    """Return (tasks, "") or (None, errmsg)."""
    if mgr is None:
        return None, "定时任务调度器尚未启动。"
    try:
        return mgr.list_tasks(), ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_tasks failed in advisor tool")
        return None, f"读取定时任务失败: {exc}"


@tool
def list_scheduled_tasks() -> str:
    """List all scheduled analysis tasks with their type, enabled state,
    most recent run, and whether one is currently running. Call this FIRST
    when the user asks about 定时任务/我的任务/有哪些任务."""
    tasks, err = _load_tasks(_scheduler_manager())
    if err:
        return err
    if not tasks:
        return "还没有配置任何定时任务。可在网页「定时任务」中新建。"
    lines = ["# 📋 定时任务列表", ""]
    for t in tasks:
        lr = t.get("last_run")
        lr_str = "无"
        if lr:
            lr_str = f"{lr.get('run_at', '')}"
        state = "🟢 启用" if t.get("enabled") else "⏸ 暂停"
        if t.get("running"):
            state += " · 🔄 运行中"
        lines.append(f"- **{t.get('name', '?')}** "
                     f"[{_task_type_label(t.get('task_type'))}] {state}")
        lines.append(f"  最近运行: {lr_str}")
    lines.append("")
    lines.append("可直接说：启动/暂停/恢复某任务，或推送某任务最近一次结果。")
    return "\n".join(lines)


@tool
def start_scheduled_task(
    task_name: Annotated[str, "定时任务名称，如「东方自选」"],
) -> str:
    """立即启动一个定时任务（手动运行一次，不改变原有排程）。任务完成后的
    结果会自动推送到该任务配置的微信推送渠道。"""
    mgr = _scheduler_manager()
    tasks, err = _load_tasks(mgr)
    if err:
        return err
    task, candidates = resolve_task(task_name, tasks)
    if task is None:
        return _ambiguous(task_name, candidates) if candidates \
            else _not_found(task_name)
    if task.get("running"):
        return f"「{task['name']}」正在运行中，不能重复启动。"
    try:
        ok = mgr.run_task(task["job_id"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_task failed in advisor tool")
        return f"启动「{task['name']}」失败: {exc}"
    if not ok:
        return f"启动「{task['name']}」失败，任务不存在或调度器未就绪。"
    return (f"✅ 已启动「{task['name']}」"
            f"（{_task_type_label(task.get('task_type'))}）。\n"
            f"运行完成后结果会自动推送微信，我也可以帮你查看进度。")


@tool
def pause_scheduled_task(
    task_name: Annotated[str, "定时任务名称"],
) -> str:
    """暂停一个定时任务：停止按 cron 自动触发，不删除任务，不影响「立即
    运行」。"""
    mgr = _scheduler_manager()
    tasks, err = _load_tasks(mgr)
    if err:
        return err
    task, candidates = resolve_task(task_name, tasks)
    if task is None:
        return _ambiguous(task_name, candidates) if candidates \
            else _not_found(task_name)
    if not task.get("enabled"):
        return f"「{task['name']}」已经是暂停状态。"
    try:
        ok = mgr.pause_task(task["job_id"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("pause_task failed in advisor tool")
        return f"暂停「{task['name']}」失败: {exc}"
    return f"✅ 已暂停「{task['name']}」。" if ok \
        else f"暂停「{task['name']}」失败。"


@tool
def resume_scheduled_task(
    task_name: Annotated[str, "定时任务名称"],
) -> str:
    """恢复一个被暂停的定时任务：重新按 cron 自动触发。"""
    mgr = _scheduler_manager()
    tasks, err = _load_tasks(mgr)
    if err:
        return err
    task, candidates = resolve_task(task_name, tasks)
    if task is None:
        return _ambiguous(task_name, candidates) if candidates \
            else _not_found(task_name)
    if task.get("enabled"):
        return f"「{task['name']}」已经是启用状态。"
    try:
        ok = mgr.resume_task(task["job_id"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("resume_task failed in advisor tool")
        return f"恢复「{task['name']}」失败: {exc}"
    return f"✅ 已恢复「{task['name']}」。" if ok \
        else f"恢复「{task['name']}」失败。"


@tool
def get_task_status(
    task_name: Annotated[str, "定时任务名称"],
) -> str:
    """查看一个定时任务的状态：是否启用、是否运行中、当前进度、最近一次
    运行的结果概要。"""
    mgr = _scheduler_manager()
    tasks, err = _load_tasks(mgr)
    if err:
        return err
    task, candidates = resolve_task(task_name, tasks)
    if task is None:
        return _ambiguous(task_name, candidates) if candidates \
            else _not_found(task_name)
    name = task.get("name", "?")
    lines = [
        f"# {name}",
        f"- 类型: {_task_type_label(task.get('task_type'))}",
        f"- 状态: {'🟢 启用' if task.get('enabled') else '⏸ 暂停'}",
    ]
    if task.get("running"):
        prog = task.get("progress") or {}
        phase = prog.get("phase") or "运行中"
        lines.append(f"- 运行中: 🔄 {phase}")
        if prog.get("analyzed") is not None:
            lines.append(f"  进度: {prog.get('analyzed')}/{prog.get('total', 0)}"
                         f" · 已用 {prog.get('elapsed_sec', 0)}s")
    lr = task.get("last_run")
    if lr:
        lines.append(f"- 最近运行: {lr.get('run_at', '')} · {lr.get('status', 'done')}")
        summary = lr.get("summary") or {}
        if task.get("task_type") in ("emwl_batch", "idx_batch"):
            lines.append(
                f"  股票池 {summary.get('pool', 0)} · "
                f"看多 {summary.get('bullish', 0)} / "
                f"看空 {summary.get('bearish', 0)} / 观望 {summary.get('watch', 0)}")
        else:
            lines.append(f"  {summary.get('company_name', '')} "
                         f"({summary.get('ticker', '')}) 评级 {summary.get('rating', '')}")
    else:
        lines.append("- 最近运行: 无")
    return "\n".join(lines)


@tool
def push_task_report(
    task_name: Annotated[str, "定时任务名称"],
    run_id: Annotated[int, "可选：某次运行的记录 ID，留空推最近一次"] = 0,
) -> str:
    """把某定时任务的一次运行结果完整推送到该任务配置的微信推送渠道
    （群机器人 webhook 或智能机器人）。不指定 run_id 时推送最近一次运行。
    与网页上「推送微信」按钮完全一致。"""
    from web.wecom_push import push_run_result
    from web.scheduler import get_last_scheduler_run, get_scheduler_run

    mgr = _scheduler_manager()
    tasks, err = _load_tasks(mgr)
    if err:
        return err
    task, candidates = resolve_task(task_name, tasks)
    if task is None:
        return _ambiguous(task_name, candidates) if candidates \
            else _not_found(task_name)
    cfg = _default_config()
    job_id = task["job_id"]
    if run_id:
        run = get_scheduler_run(cfg, run_id, expected_job_id=job_id)
        if run is None:
            return f"任务「{task['name']}」没有记录 ID 为 {run_id} 的运行。"
    else:
        run = get_last_scheduler_run(cfg, job_id)
        if run is None:
            return f"「{task['name']}」还没有运行记录，无法推送。"
    task_data = mgr.get_task_config(job_id) or {"name": task["name"]}
    ttype = run.get("task_type") or task_data.get("task_type") or "deep"
    res = push_run_result(task_data, run.get("summary") or {}, ttype)
    if not res.get("ok"):
        return f"推送失败: {res.get('errmsg') or '未知错误'}"
    channel = "群机器人" if res.get("channel") == "webhook" else "智能机器人"
    return (f"✅ 已把「{task['name']}」"
            f"{('记录#' + str(run_id)) if run_id else '最近一次运行'}"
            f"结果推送到微信（{channel}）。")
