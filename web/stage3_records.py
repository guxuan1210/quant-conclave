"""Persistence for stage-3 (阶段三·顾问综合报告) records.

The scheduled three-phase batch pipeline writes each phase to its own table:
  ① batch per-stock analysis  -> web.watchlist_store.save_watchlist_analysis
  ② two-pass re-analysis      -> web.twopass_records.save_twopass_record
  ③ advisory composite report -> this module (``stage3_records``)

Stage 3 runs one headless advisory conversation against the 看多 subset of a
two-pass record and persists the composite report (per-stock conclusion +
overall thesis) plus the input stock set as one append-only row.

Reuses ``results_store._get_conn`` / ``_get_db_path`` — same pattern as
``web/twopass_records.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from web.results_store import _get_conn

logger = logging.getLogger(__name__)

# Chinese labels used in record titles / frontend display
_TAB_LABELS = {"emwl": "东方自选", "idx": "指数选股"}


def init_stage3_store(config: dict) -> None:
    """Create the stage3_records table + index (idempotent)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stage3_records (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at        TEXT DEFAULT (datetime('now','localtime')),
                tab               TEXT DEFAULT 'emwl',
                twopass_record_id INTEGER,
                thread_id         TEXT DEFAULT '',
                provider          TEXT DEFAULT '',
                model             TEXT DEFAULT '',
                stock_count       INTEGER DEFAULT 0,
                title             TEXT DEFAULT '',
                report            TEXT DEFAULT '',
                stocks            TEXT DEFAULT '[]',
                status            TEXT DEFAULT 'done'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stage3_records_created
            ON stage3_records(created_at)
        """)
        conn.commit()
    except Exception:
        logger.exception("Failed to init stage3_records table")
        raise
    finally:
        conn.close()


def _tab_label(tab: str) -> str:
    return _TAB_LABELS.get(tab, tab or "自选")


def save_stage3_record(config: dict, tab: str, twopass_record_id: int,
                       thread_id: str, provider: str, model: str,
                       stock_count: int, report: str,
                       stocks: list[dict], title: str = "") -> int:
    """Persist one stage-3 composite report. Returns the new record id."""
    stocks = stocks or []
    if not title:
        title = f"{_tab_label(tab)}·顾问综合报告·{len(stocks)}只看多"

    conn = _get_conn(config)
    try:
        cur = conn.execute(
            """
            INSERT INTO stage3_records
                (tab, twopass_record_id, thread_id, provider, model,
                 stock_count, title, report, stocks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tab, int(twopass_record_id), thread_id or "", provider or "",
             model or "", int(stock_count), title, report or "",
             json.dumps(stocks, ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_stage3_records(config: dict, limit: int = 100) -> list[dict]:
    """Return records newest-first as lightweight summary dicts."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, tab, twopass_record_id, provider, model,
                   stock_count, title, status
            FROM stage3_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stage3_record(config: dict, record_id: int) -> Optional[dict]:
    """Return a single record with ``report`` + rehydrated ``stocks``, or None."""
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM stage3_records WHERE id = ?", (int(record_id),)
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
