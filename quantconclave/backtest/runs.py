"""Persist backtest runs to the ``backtest_runs`` table.

The table schema is owned by ``web.results_store`` (in the canonical workspace
DB, ``results.db``) and is queried by ``web/history_chat.compare_backtests``.
The connection routes through :mod:`quantconclave.workspace.store`; the inline
``CREATE TABLE IF NOT EXISTS`` is an idempotent safety net for standalone use.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _get_conn() -> sqlite3.Connection:
    from quantconclave.workspace.store import get_connection, get_config
    return get_connection(get_config())


def save_backtest_run(
    ticker: str,
    strategy_name: str,
    strategy_id: str,
    start_date: str,
    end_date: str,
    parameters: dict,
    initial_capital: float,
    results: dict,
    equity_curve: list,
    trade_log: list,
) -> int:
    """Insert one backtest run. Returns the new row id."""
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy_name TEXT,
            strategy_id INTEGER,
            start_date TEXT,
            end_date TEXT,
            parameters_used TEXT,
            initial_capital REAL DEFAULT 100000,
            results TEXT,
            equity_curve TEXT,
            trade_log TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cur = conn.execute(
        """
        INSERT INTO backtest_runs
            (ticker, strategy_name, strategy_id, start_date, end_date,
             parameters_used, initial_capital, results, equity_curve, trade_log)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticker,
            strategy_name,
            strategy_id,
            start_date,
            end_date,
            json.dumps(parameters, ensure_ascii=False),
            initial_capital,
            json.dumps(results, ensure_ascii=False),
            json.dumps(equity_curve, ensure_ascii=False),
            json.dumps(trade_log, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_backtest_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent backtest runs for the compare tool."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return [dict(r) for r in rows]
