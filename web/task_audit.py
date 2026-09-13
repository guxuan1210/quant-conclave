"""Persistence for scheduled-task operation audit log (定时任务·操作记录).

Every lifecycle mutation of a scheduled task — create / pause / resume /
delete / run — is appended as an immutable row so the user can review what
happened to a task and when. The ``detail`` column holds a JSON snapshot of
the operation (e.g. the task_data for a create, ``{"enabled": ...}`` for a
pause/resume).

Reuses ``results_store._get_conn`` / ``_get_db_path`` — same pattern as
``web/stage3_records.py`` and ``web/twopass_records.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from web.results_store import _get_conn

logger = logging.getLogger(__name__)

# action -> Chinese label used by the frontend badge (kept here so the i18n
# layer can fall back to a sane default even if a key is missing)
_ACTION_LABELS = {
    "create": "新增",
    "delete": "删除",
    "pause": "暂停",
    "resume": "恢复",
    "run": "运行",
}


def init_task_audit_store(config: dict) -> None:
    """Create the task_audit_log table + indexes (idempotent)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                job_id     TEXT,
                action     TEXT,      -- create | pause | resume | delete | run
                task_name  TEXT,
                task_type  TEXT,
                detail     TEXT,      -- JSON snapshot of the operation
                status     TEXT DEFAULT 'ok'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_audit_created
            ON task_audit_log(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_audit_job
            ON task_audit_log(job_id)
        """)
        conn.commit()
    except Exception:
        logger.exception("Failed to init task_audit_log table")
        raise
    finally:
        conn.close()


def log_task_action(config: dict, job_id: str, action: str, task_name: str,
                    task_type: str = "", detail: Optional[dict] = None,
                    status: str = "ok") -> int:
    """Append one audit entry. Returns the new row id.

    ``detail`` is JSON-serialized (any dict with JSON-able values; task_data
    snapshots for creates, ``{"enabled": ...}`` for pause/resume).
    """
    conn = _get_conn(config)
    try:
        cur = conn.execute(
            """
            INSERT INTO task_audit_log
                (job_id, action, task_name, task_type, detail, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id or "", action or "", task_name or "", task_type or "",
             json.dumps(detail or {}, ensure_ascii=False) if detail else "",
             status or "ok"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_task_audit(config: dict, limit: int = 200) -> list[dict]:
    """Return audit entries newest-first, each with rehydrated ``detail``."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, job_id, action, task_name, task_type,
                   detail, status
            FROM task_audit_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        out = []
        for r in rows:
            rec = dict(r)
            try:
                rec["detail"] = json.loads(rec.get("detail") or "{}")
            except (ValueError, TypeError):
                rec["detail"] = {}
            out.append(rec)
        return out
    finally:
        conn.close()
