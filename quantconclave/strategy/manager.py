"""Strategy CRUD -- manage strategy_templates and strategy_versions tables.

The database lives in the canonical workspace DB (``results.db``) via
:mod:`quantconclave.workspace.store`; schema is owned by ``web.results_store``.
"""
from __future__ import annotations
import json, sqlite3
from typing import Optional

_DB_PATH: Optional[str] = None


def _get_conn() -> sqlite3.Connection:
    if _DB_PATH is not None:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    from quantconclave.workspace.store import get_connection, get_config
    return get_connection(get_config())


def set_db_path(path: str) -> None:
    """Override the DB path (tests / legacy callers). ``None`` resets to the
    canonical workspace DB."""
    global _DB_PATH
    _DB_PATH = path


def create_strategy(name, description="", type="custom_llm", template_id="", parameters=None, code="", tags=""):
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO strategy_templates (name,description,type,template_id,parameters,code,tags) VALUES (?,?,?,?,?,?,?)",
        (name, description, type, template_id, json.dumps(parameters or {}), code, tags))
    conn.commit()
    return cur.lastrowid


def list_strategies(type_filter=""):
    conn = _get_conn()
    if type_filter:
        rows = conn.execute("SELECT * FROM strategy_templates WHERE type=? ORDER BY updated_at DESC", (type_filter,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM strategy_templates ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_strategy(sid):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM strategy_templates WHERE id=?", (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def search_strategies(query):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM strategy_templates WHERE name LIKE ? OR description LIKE ? OR tags LIKE ? ORDER BY updated_at DESC",
        (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_strategy(sid):
    conn = _get_conn()
    conn.execute("DELETE FROM strategy_templates WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return True


def add_version(strategy_id, parameters, code, notes=""):
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO strategy_versions (strategy_id,parameters,code,version_notes) VALUES (?,?,?,?)",
        (strategy_id, json.dumps(parameters), code, notes))
    conn.commit()
    return cur.lastrowid


def get_versions(strategy_id):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM strategy_versions WHERE strategy_id=? ORDER BY created_at DESC", (strategy_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
