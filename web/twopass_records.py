"""Persistence for two-pass (二次分析) watchlist records.

Every time the user runs a two-pass analysis in the eastmoney-wl (东方自选)
or index-stocks (指数选股) tab, the frontend saves the FULL set of analyzed
stocks + LLM conclusions as one append-only ``twopass_records`` row.

These records are:
  - shown on the left side of the Advisory Agent page (click → deep analysis),
  - exposed to the Advisory Agent as callable tools
    (``get_twopass_records`` / ``get_twopass_record_detail`` in history_chat.py).

Reuses ``results_store._get_conn`` / ``_get_db_path`` so WAL, row_factory and
the DB path (``~/.quantconclave/logs/results.db``) stay consistent with the rest
of the dashboard (same pattern as web/watchlist_store.py).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from web.results_store import _get_conn, _get_db_path

logger = logging.getLogger(__name__)

# Chinese labels used in record titles / frontend display
_TAB_LABELS = {"emwl": "东方自选", "idx": "指数选股"}


def init_twopass_store(config: dict) -> None:
    """Create the twopass_records table + index (idempotent)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS twopass_records (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                tab           TEXT DEFAULT 'emwl',
                model_count   INTEGER DEFAULT 1,
                stock_count   INTEGER DEFAULT 0,
                bullish_count INTEGER DEFAULT 0,
                title         TEXT DEFAULT '',
                stocks        TEXT DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_twopass_records_created
            ON twopass_records(created_at)
        """)
        conn.commit()
    except Exception:
        logger.exception("Failed to init twopass_records table")
        raise
    finally:
        conn.close()


def _tab_label(tab: str) -> str:
    return _TAB_LABELS.get(tab, tab or "自选")


def save_twopass_record(config: dict, tab: str, model_count: int,
                        stocks: list[dict]) -> int:
    """Persist one two-pass run. ``stocks`` is the full list of analyzed rows
    (each: code/name/price/change_pct/prevModel/prevVerdict/newModel/newVerdict/analysis).

    Returns the new record id.
    """
    stocks = stocks or []
    stock_count = len(stocks)
    bullish_count = sum(
        1 for s in stocks if (s.get("newVerdict") or "").strip() == "看多"
    )
    title = f"{_tab_label(tab)}·二次分析({max(1, model_count)}模型)·{stock_count}只复核"

    conn = _get_conn(config)
    try:
        cur = conn.execute(
            """
            INSERT INTO twopass_records
                (tab, model_count, stock_count, bullish_count, title, stocks)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tab, max(1, int(model_count)), stock_count, bullish_count,
             title, json.dumps(stocks, ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_twopass_records(config: dict, limit: int = 100) -> list[dict]:
    """Return records newest-first as lightweight summary dicts."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, tab, model_count, stock_count,
                   bullish_count, title
            FROM twopass_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_twopass_record(config: dict, record_id: int) -> Optional[dict]:
    """Return a single record with its full ``stocks`` list, or None."""
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM twopass_records WHERE id = ?", (int(record_id),)
        ).fetchone()
        if row is None:
            return None
        rec = dict(row)
        try:
            rec["stocks"] = json.loads(rec.get("stocks") or "[]")
        except (ValueError, TypeError):
            rec["stocks"] = []
        return rec
    finally:
        conn.close()


def delete_twopass_record(config: dict, record_id: int) -> bool:
    """Delete a record. Returns True if a row was removed."""
    conn = _get_conn(config)
    try:
        cur = conn.execute(
            "DELETE FROM twopass_records WHERE id = ?", (int(record_id),)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
