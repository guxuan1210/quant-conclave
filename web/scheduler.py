"""Task scheduler for the QuantConclave web dashboard.

Uses APScheduler with SQLAlchemyJobStore to persist scheduled tasks across
server restarts. Runs the analysis pipeline in background threads.
"""

import json
import logging
import re
import traceback
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger

from web import run_progress

logger = logging.getLogger(__name__)


def _build_scheduler(config: dict) -> BackgroundScheduler:
    results_dir = config.get("results_dir", "")
    jobstore_url = f"sqlite:///{results_dir}/scheduler_jobs.db"

    jobstores = {"default": SQLAlchemyJobStore(url=jobstore_url)}
    executors = {"default": ThreadPoolExecutor(max_workers=3)}
    job_defaults = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 24 * 60 * 60,
    }

    return BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone="Asia/Shanghai",
    )


def init_scheduler_run_store(config: dict) -> None:
    """Create the scheduled_run_log table (idempotent) in results.db."""
    from web.results_store import _get_conn
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_run_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id     TEXT,
                task_name  TEXT,
                task_type  TEXT,
                run_at     TEXT,
                status     TEXT DEFAULT 'done',
                summary    TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sched_run_job
            ON scheduled_run_log(job_id, id)
        """)
        conn.commit()
    finally:
        conn.close()


def save_scheduler_run(config: dict, job_id: str, task_name: str,
                       task_type: str, summary: dict) -> int:
    """Append one scheduled-task execution record. Returns the new row id."""
    from web.results_store import _get_conn
    conn = _get_conn(config)
    try:
        cur = conn.execute(
            """
            INSERT INTO scheduled_run_log
                (job_id, task_name, task_type, run_at, status, summary)
            VALUES (?, ?, ?, ?, 'done', ?)
            """,
            (job_id, task_name, task_type,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             json.dumps(summary or {}, ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_last_scheduler_run(config: dict, job_id: str) -> Optional[dict]:
    """Return the most recent run for a job (with parsed summary), or None."""
    from web.results_store import _get_conn
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM scheduled_run_log WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        rec = dict(row)
        try:
            rec["summary"] = json.loads(rec.get("summary") or "{}")
        except (ValueError, TypeError):
            rec["summary"] = {}
        return rec
    finally:
        conn.close()


def get_scheduler_runs(config: dict, job_id: str, limit: int = 50) -> list[dict]:
    """All recorded runs for a job, newest first (parsed summaries).

    Returns up to ``limit`` ``scheduled_run_log`` rows so callers can show a
    per-run history (and rebuild any single run's report via ``run_id``).
    """
    from web.results_store import _get_conn
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_run_log WHERE job_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (job_id, max(1, int(limit))),
        ).fetchall()
        out = []
        for row in rows:
            rec = dict(row)
            try:
                rec["summary"] = json.loads(rec.get("summary") or "{}")
            except (ValueError, TypeError):
                rec["summary"] = {}
            out.append(rec)
        return out
    finally:
        conn.close()


def get_scheduler_run(config: dict, run_id: int,
                      expected_job_id: str | None = None) -> Optional[dict]:
    """One run record by its ``scheduled_run_log`` row id, or None.

    When ``expected_job_id`` is given, the row must ALSO belong to that job —
    this scopes ``?run_id=`` lookups to the requested task so a caller cannot
    read another job's run by guessing its row id (IDOR).
    """
    from web.results_store import _get_conn
    conn = _get_conn(config)
    try:
        sql = "SELECT * FROM scheduled_run_log WHERE id = ?"
        params: list = [int(run_id)]
        if expected_job_id is not None:
            sql += " AND job_id = ?"
            params.append(expected_job_id)
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        rec = dict(row)
        try:
            rec["summary"] = json.loads(rec.get("summary") or "{}")
        except (ValueError, TypeError):
            rec["summary"] = {}
        return rec
    finally:
        conn.close()


class SchedulerManager:
    def __init__(self, config: dict):
        self.config = config
        self._scheduler: Optional[BackgroundScheduler] = None

    def start(self) -> None:
        self._scheduler = _build_scheduler(self.config)
        self._scheduler.start()
        self._reconcile_legacy_job_ids()
        logger.info("Scheduler started (jobstore sqlite, 3 workers)")

    def _reconcile_legacy_job_ids(self) -> None:
        """Heal tasks persisted before the add_task job_id write-back fix.

        Old tasks were stored in the jobstore with a task_data that has no
        job_id key, so _execute_scheduled_analysis saved run records under
        job_id='' and get_last_scheduler_run(real_id) never matched them —
        last_run stayed None and the report endpoint / frontend auto-display
        saw nothing. Write the real id back into the persisted kwargs (and
        backfill any orphaned run records matched by name) so future runs
        resolve correctly. Idempotent: jobs already reconciled are skipped.
        """
        from web.results_store import _get_conn
        conn = _get_conn(self.config)
        try:
            for job in self._scheduler.get_jobs():
                kwargs = dict(job.kwargs)
                task_data = dict(kwargs.get("task_data") or {})
                if task_data.get("job_id") == job.id:
                    continue
                task_data["job_id"] = job.id
                kwargs["task_data"] = task_data
                self._scheduler.modify_job(job.id, kwargs=kwargs)
                conn.execute(
                    "UPDATE scheduled_run_log SET job_id = ? "
                    "WHERE job_id = '' AND task_name = ?",
                    (job.id, task_data.get("name", "")),
                )
                conn.commit()
                logger.info("Reconciled legacy job %s task_data.job_id", job.id)
        finally:
            conn.close()

    def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=True)
            logger.info("Scheduler shut down")

    def add_task(self, task_data: dict) -> str:
        if not self._scheduler:
            raise RuntimeError("Scheduler not started")

        trigger = CronTrigger.from_crontab(
            task_data["cron_expression"],
            timezone=task_data.get("timezone", "Asia/Shanghai"),
        )
        job = self._scheduler.add_job(
            func=_execute_scheduled_analysis,
            trigger=trigger,
            kwargs={"task_data": task_data},
            id=task_data.get("job_id"),
            name=task_data.get("name", f"{task_data.get('ticker', '')} analysis"),
            replace_existing=True,
        )
        # Persist the real job id back into task_data so _execute_scheduled_analysis
        # saves run records (and the report endpoint looks them up) under the
        # actual job id — previously this stayed "" and last_run was never found.
        task_data["job_id"] = job.id
        logger.info("Added scheduled task %s: %s", job.id, job.name)
        return job.id

    def list_tasks(self) -> list[dict]:
        if not self._scheduler:
            return []
        tasks = []
        for job in self._scheduler.get_jobs():
            task_data = job.kwargs.get("task_data", {})
            last = get_last_scheduler_run(self.config, job.id)
            prog = run_progress.get(job.id)
            tasks.append({
                "job_id": job.id,
                "name": job.name,
                "ticker": task_data.get("ticker", "") or job.name.split(" ")[0],
                "task_type": task_data.get("task_type", "deep"),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
                "enabled": job.next_run_time is not None,
                "last_run": last,
                "running": bool(prog and prog.get("status") == "running"),
                "progress": prog,
            })
        return tasks

    def get_task(self, job_id: str) -> Optional[dict]:
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(job_id)
        if not job:
            return None
        task_data = job.kwargs.get("task_data", {})
        prog = run_progress.get(job_id)
        return {
            "job_id": job.id,
            "name": job.name,
            "ticker": task_data.get("ticker", "") or job.name.split(" ")[0],
            "task_type": task_data.get("task_type", "deep"),
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
            "enabled": job.next_run_time is not None,
            "last_run": get_last_scheduler_run(self.config, job.id),
            "running": bool(prog and prog.get("status") == "running"),
            "progress": prog,
        }

    def pause_task(self, job_id: str) -> bool:
        if not self._scheduler:
            return False
        try:
            self._scheduler.pause_job(job_id)
            return True
        except Exception:
            logger.exception("Failed to pause job %s", job_id)
            return False

    def resume_task(self, job_id: str) -> bool:
        if not self._scheduler:
            return False
        try:
            self._scheduler.resume_job(job_id)
            return True
        except Exception:
            logger.exception("Failed to resume job %s", job_id)
            return False

    def delete_task(self, job_id: str) -> bool:
        if not self._scheduler:
            return False
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            logger.exception("Failed to delete job %s", job_id)
            return False

    def update_task(self, job_id: str, task_data: dict) -> bool:
        """Replace a task's schedule/name/params in place, keeping the same
        job_id so run records and the report endpoint stay linked to it."""
        if not self._scheduler:
            return False
        job = self._scheduler.get_job(job_id)
        if not job:
            return False
        task_data["job_id"] = job_id
        trigger = CronTrigger.from_crontab(
            task_data["cron_expression"],
            timezone=task_data.get("timezone", "Asia/Shanghai"),
        )
        was_paused = job.next_run_time is None
        self._scheduler.modify_job(
            job_id, name=task_data.get("name", job.name),
            trigger=trigger, kwargs={"task_data": task_data})
        # modify_job recomputes next_run_time from the new trigger; a task that
        # was paused must stay paused (guard against accidental re-enable).
        if was_paused:
            try:
                self._scheduler.pause_job(job_id)
            except Exception:
                logger.exception("Failed to re-pause job %s after edit", job_id)
        return True

    def get_task_config(self, job_id: str) -> Optional[dict]:
        """The full stored task_data for a job (form prefill on edit)."""
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(job_id)
        if not job:
            return None
        return job.kwargs.get("task_data", {})

    def run_task(self, job_id: str) -> bool:
        """Trigger a task immediately (manual run) without altering its schedule."""
        if not self._scheduler:
            return False
        job = self._scheduler.get_job(job_id)
        if not job:
            return False
        import threading
        threading.Thread(target=job.func, kwargs=job.kwargs, daemon=True).start()
        return True


def _execute_scheduled_analysis(task_data: dict) -> None:
    """Called by APScheduler when a scheduled task fires.

    Runs the task and clears its in-flight progress marker (``run_progress``)
    in a finally so no "running" entry survives a finished or crashed run.
    """
    job_id = task_data.get("job_id", "")
    try:
        _execute_scheduled_analysis_dispatch(task_data)
    finally:
        run_progress.remove(job_id)


def _execute_scheduled_analysis_dispatch(task_data: dict) -> None:
    """Run one scheduled task; batch progress is published by
    ``run_batch_task`` itself, deep progress is started here."""
    job_id = task_data.get("job_id", "")
    ttype = task_data.get("task_type", "deep")
    if ttype in ("evaluation_weekly", "evaluation_settle", "evaluation_monthly"):
        from web.eval_scheduler import run_evaluation_task
        from quantconclave.default_config import DEFAULT_CONFIG
        summary = run_evaluation_task(ttype, DEFAULT_CONFIG)
        save_scheduler_run(DEFAULT_CONFIG, job_id, task_data.get("name", ""), ttype, summary)
        return
    if ttype in ("emwl_batch", "idx_batch"):
        from quantconclave.default_config import DEFAULT_CONFIG
        from web.scheduled_batch import run_batch_task
        from web.wecom_push import push_run_result
        try:
            summary = run_batch_task(DEFAULT_CONFIG, task_data)
        except Exception:
            # A crashed batch must not be silent on WeChat: push a failure card
            # (same channel resolution as the success path), then re-raise so
            # APScheduler still records the job as failed.
            logger.exception("Scheduled batch task failed: %s",
                             task_data.get("name", ""))
            push_run_result(task_data, {"error": traceback.format_exc()},
                            "batch_failure")
            raise
        save_scheduler_run(DEFAULT_CONFIG, task_data.get("job_id", ""),
                           task_data.get("name", ""), ttype, summary)
        _push_res = push_run_result(task_data, summary, ttype)
        _log_push_result(task_data, ttype, _push_res)
        return
    from quantconclave.graph.trading_graph import QuantConclaveGraph
    from quantconclave.default_config import DEFAULT_CONFIG
    from quantconclave.agents.utils.rating import parse_rating
    from quantconclave.llm_clients import resolve_role_llm
    from web.results_store import save_result

    ticker = task_data["ticker"]
    date_str = task_data.get("date_str", "")
    if task_data.get("use_current_date", True) or not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    cfg = DEFAULT_CONFIG.copy()
    if task_data.get("provider"):
        cfg["llm_provider"] = task_data["provider"]
    if task_data.get("deep_provider"):
        cfg["deep_think_provider"] = task_data["deep_provider"]
    if task_data.get("quick_provider"):
        cfg["quick_think_provider"] = task_data["quick_provider"]
    if task_data.get("deep_model"):
        cfg["deep_think_llm"] = task_data["deep_model"]
    if task_data.get("quick_model"):
        cfg["quick_think_llm"] = task_data["quick_model"]
    lang = task_data.get("language", "English")
    cfg["output_language"] = lang

    analysts = task_data.get("analysts", [])
    if not analysts:
        analysts = ["market", "news", "fundamentals"]

    logger.info(
        "Scheduled analysis: ticker=%s date=%s provider=%s analysts=%s",
        ticker, date_str, cfg["llm_provider"], analysts,
    )

    run_progress.start(job_id, phase="deep", total=0)

    ta = QuantConclaveGraph(
        selected_analysts=analysts,
        debug=False,
        config=cfg,
    )

    try:
        state_dict, signal_str = ta.propagate(ticker, date_str)
    except Exception:
        logger.exception("Scheduled analysis failed for %s on %s", ticker, date_str)
        # Push a failure card so a crashed deep run is never silent on WeChat.
        from web.wecom_push import push_run_result
        push_run_result(task_data, {
            "ticker": ticker,
            "date": date_str,
            "company_name": "",
            "error": traceback.format_exc(),
        }, "deep_failure")
        return

    final_decision = state_dict.get("final_trade_decision", "")
    rating = parse_rating(final_decision)

    results_dir = cfg.get("results_dir", "")
    ticker_safe = ticker.upper()
    json_rel = f"{ticker_safe}/QuantConclaveStrategy_logs/full_states_log_{date_str}.json"

    from web.ticker_utils import resolve_company_name
    ticker_norm, company_name = resolve_company_name(ticker)

    save_result(cfg, {
        "ticker": ticker_norm or ticker,
        "date": date_str,
        "company_name": company_name,
        "rating": rating,
        "signal": signal_str,
        "analysts": ",".join(analysts),
        "provider": cfg.get("llm_provider", ""),
        "deep_model": cfg.get("deep_think_llm", ""),
        "quick_model": cfg.get("quick_think_llm", ""),
        "language": lang,
        "run_type": "scheduled",
        "scheduled_job_id": task_data.get("job_id", ""),
        "json_path": json_rel,
        "deep_provider": resolve_role_llm(cfg, "deep")[0],
        "quick_provider": resolve_role_llm(cfg, "quick")[0],
    })
    # Also append a scheduled_run_log row so deep runs are stored like batch
    # runs: the task list shows last_run, the "new run" auto-open fires, and the
    # run-history query (/runs + ?run_id=) can rebuild any single deep report.
    run_summary = {
        "ticker": ticker_norm or ticker,
        "company_name": company_name,
        "date": date_str,
        "rating": rating,
        "signal": signal_str,
        "analysts": ",".join(analysts),
        "json_path": json_rel,
    }
    save_scheduler_run(DEFAULT_CONFIG, task_data.get("job_id", ""),
                       task_data.get("name", ""), "deep", run_summary)
    from web.wecom_push import push_run_result
    _push_res = push_run_result(task_data, run_summary, "deep")
    _log_push_result(task_data, "deep", _push_res)
    logger.info("Scheduled analysis complete: %s %s = %s", ticker, date_str, rating)


_URL_RE = re.compile(r"https?://\S+")


def _redact_urls(text: str) -> str:
    """Strip any URL from a log payload — a webhook URL carries its key in the
    query string, so it must never reach the log even via a third-party errmsg."""
    return _URL_RE.sub("<redacted-url>", text or "")


def _log_push_result(task_data: dict, ttype: str, res: dict) -> None:
    """Surface the push outcome at a level matching severity.

    ``channel is None`` means the task has no delivery channel configured — that
    is intentional configuration, not a failure, so it logs at INFO. A configured
    channel that failed to deliver logs at WARNING so a silent WeChat is
    greppable in the server log.
    """
    res = res or {}  # callers may stub push_run_result to None
    if res.get("channel") and not res.get("ok"):
        logger.warning("Scheduled WeChat push failed (%s, %s): %s",
                       task_data.get("name", ""), ttype,
                       _redact_urls(res.get("errmsg")))
    else:
        logger.info("Scheduled WeChat push (%s, %s) channel=%s ok=%s",
                    task_data.get("name", ""), ttype,
                    res.get("channel"), res.get("ok"))
