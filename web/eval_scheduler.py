"""System scheduler tasks for the evaluation subsystem.

Three recurring jobs drive the forward-testing lifecycle:
- ``evaluation_weekly`` (Mon–Fri 16:30, fires on the first trading day of the
  week) → sample + run the full pipeline, save predictions (no settlement).
- ``evaluation_settle`` (Mon–Fri 15:30) → progressively settle whatever entry /
  5/20/60-day prices are now available, then push due period reports.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

WEEKLY_JOB_ID = "evaluation_weekly"
SETTLE_JOB_ID = "evaluation_settle"
MONTHLY_JOB_ID = "evaluation_monthly"


def register_evaluation_tasks(manager, config) -> None:
    if not (config or {}).get("evaluation", {}).get("enabled", True):
        return
    manager.add_task({
        "job_id": WEEKLY_JOB_ID,
        "name": "评测·每周选样",
        "task_type": "evaluation_weekly",
        "cron_expression": "30 16 * * 1-5",
        "timezone": "Asia/Shanghai",
    })
    manager.add_task({
        "job_id": SETTLE_JOB_ID,
        "name": "评测·每日结算",
        "task_type": "evaluation_settle",
        "cron_expression": "30 15 * * 1-5",
        "timezone": "Asia/Shanghai",
    })
    manager.add_task({
        "job_id": MONTHLY_JOB_ID,
        "name": "评测·月度消融",
        "task_type": "evaluation_monthly",
        "cron_expression": "0 17 * * 1-5",
        "timezone": "Asia/Shanghai",
    })
    logger.info("Registered evaluation system tasks")


def run_evaluation_task(ttype: str, config: dict) -> dict:
    if ttype == "evaluation_weekly":
        return _run_weekly(config)
    if ttype == "evaluation_settle":
        return _run_settle(config)
    if ttype == "evaluation_monthly":
        return _run_monthly(config)
    return {"skipped": True, "reason": f"unknown evaluation task type {ttype}"}


def _run_weekly(config) -> dict:
    from web.trade_cal import is_first_trading_day_of_week
    if not is_first_trading_day_of_week():
        return {"skipped": True, "reason": "not first trading day of week"}
    from quantconclave.evaluation.pipeline import run_weekly_evaluation
    return run_weekly_evaluation(config)


def _run_settle(config) -> dict:
    from datetime import datetime
    from web.trade_cal import get_open_days
    if datetime.now().strftime("%Y%m%d") not in get_open_days():
        return {"skipped": True, "reason": "not a trading day"}
    from quantconclave.evaluation.pipeline import settle_pending_cases
    summary = settle_pending_cases(config)
    reports = _push_due_reports(config)
    return {**summary, "reports_pushed": len(reports)}


def _run_monthly(config) -> dict:
    """Idempotently fill the previous calendar month's ablation sample."""
    from datetime import datetime, timedelta
    from quantconclave.evaluation.pipeline import run_monthly_ablation
    first_this_month = datetime.now().replace(day=1)
    previous_month = (first_this_month - timedelta(days=1)).strftime("%Y-%m")
    return run_monthly_ablation(config, previous_month)


_HORIZON_FOR_PERIOD = {"week": 5, "month": 20, "quarter": 60}


def _period_bucket_ready(config: dict, cases: list[dict], period: str) -> bool:
    """Return true only after every non-failed case has the period horizon."""
    from web import eval_store
    horizon = _HORIZON_FOR_PERIOD[period]
    active_cases = 0
    for case in cases:
        if case.get("status") == "failed":
            continue
        active_cases += 1
        full = eval_store.get_eval_predictions(
            config, case_id=case["case_id"], variant="full"
        )
        if not full:
            return False
        returns = eval_store.get_eval_returns(
            config, prediction_id=full[0]["prediction_id"], horizon=horizon
        )
        if not returns or returns[0].get("net_return") is None:
            return False
    return active_cases > 0


def _push_due_reports(config) -> list[tuple[str, str]]:
    """Push a report for any period with no pending cases and no existing report."""
    from web import eval_store
    from web import eval_report

    cases = eval_store.list_eval_cases(config, limit=100000)
    groups = {"week": {}, "month": {}, "quarter": {}}
    for c in cases:
        wk = c["week_key"]
        mk = c["selection_date"][:7]
        qk = _quarter_key(c["selection_date"])
        groups["week"].setdefault(wk, []).append(c)
        groups["month"].setdefault(mk, []).append(c)
        groups["quarter"].setdefault(qk, []).append(c)

    pushed = []
    for period, buckets in groups.items():
        for key, cs in buckets.items():
            if not _period_bucket_ready(config, cs, period):
                continue
            if eval_store.get_eval_report(config, period, key):
                continue
            try:
                eval_report.push_eval_report(config, period, key)
                pushed.append((period, key))
            except Exception as e:
                logger.warning("report push failed for %s %s: %s", period, key, e)
    return pushed


def _quarter_key(date_str: str) -> str:
    y, m = int(date_str[:4]), int(date_str[5:7])
    q = (m - 1) // 3 + 1
    return f"{y}-Q{q}"
