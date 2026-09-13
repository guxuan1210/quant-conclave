
"""SQLite CRUD for advisory experiences.

The database lives in the canonical workspace DB (``results.db``) via
:mod:`capitalradar.workspace.store`; schema is owned by ``web.results_store``.
The inline ``CREATE TABLE IF NOT EXISTS`` below is an idempotent safety net so
the module stays usable standalone (it now targets the same DB, so it is a
no-op after ``init_db`` runs).
"""
from __future__ import annotations
import sqlite3
import os
from typing import Optional

_DB_PATH: Optional[str] = None


def _get_conn() -> sqlite3.Connection:
    if _DB_PATH is not None:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
    else:
        from capitalradar.workspace.store import get_connection, get_config
        conn = get_connection(get_config())
    conn.execute("CREATE TABLE IF NOT EXISTS advisory_experiences (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, source_ticker TEXT, source_date TEXT, outcome TEXT, raw_return REAL, category TEXT DEFAULT 'other', status TEXT DEFAULT 'pending_review', lesson_abstract TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS advisory_experience_log (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, experience_ids TEXT NOT NULL, injected_at TEXT)")
    conn.commit()
    return conn


def set_db_path(path: str) -> None:
    """Override the DB path (tests / legacy callers). ``None`` resets to the
    canonical workspace DB."""
    global _DB_PATH
    _DB_PATH = path


def create_experience(
    content: str, source_ticker: str = "", source_date: str = "",
    outcome: str = "", raw_return: float = None,
    category: str = "other", lesson_abstract: str = "",
) -> int:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO advisory_experiences "
        "(content,source_ticker,source_date,outcome,raw_return,category,status,lesson_abstract) "
        "VALUES (?,?,?,?,?,?,'pending_review',?)",
        (content, source_ticker, source_date, outcome, raw_return, category, lesson_abstract),
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id


def list_experiences(status: str = "") -> list[dict]:
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM advisory_experiences WHERE status=? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM advisory_experiences ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_experience(eid: int) -> bool:
    conn = _get_conn()
    conn.execute(
        "UPDATE advisory_experiences SET status='active' WHERE id=? AND status='pending_review'", (eid,)
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()

    return affected > 0


def archive_experience(eid: int) -> bool:
    conn = _get_conn()
    conn.execute("UPDATE advisory_experiences SET status='archived' WHERE id=?", (eid,))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def reactivate_experience(eid: int) -> bool:
    conn = _get_conn()
    conn.execute(
        "UPDATE advisory_experiences SET status='active' WHERE id=? AND status='archived'", (eid,)
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def update_experience(
    eid: int, content: str = None, category: str = None, lesson_abstract: str = None,
) -> bool:
    conn = _get_conn()
    fields = []
    vals = []
    if content is not None:
        fields.append("content=?")
        vals.append(content)
    if category is not None:
        fields.append("category=?")
        vals.append(category)
    if lesson_abstract is not None:
        fields.append("lesson_abstract=?")
        vals.append(lesson_abstract)
    if not fields:
        conn.close()
        return False
    vals.append(eid)
    conn.execute(
        f"UPDATE advisory_experiences SET {', '.join(fields)} WHERE id=?", tuple(vals)
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def get_active_experiences() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM advisory_experiences WHERE status='active' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_injection(thread_id: str, experience_ids: list[int]) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO advisory_experience_log (thread_id, experience_ids) VALUES (?, ?)",
        (thread_id, ",".join(str(e) for e in experience_ids)),
    )
    conn.commit()
    conn.close()


def get_injection_log(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM advisory_experience_log ORDER BY injected_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
