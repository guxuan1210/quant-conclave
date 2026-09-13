"""Position/portfolio tracking — manual entry + MXAPI sync.

Stores user positions in the shared ``results.db``:

- ``positions`` — one row per holding (ticker + name + shares + cost + date)
  with current-price caching and P&L computation.

Reuses ``results_store._get_conn`` / ``_get_db_path`` so WAL, row_factory, and
the DB path stay consistent with the rest of the dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from web.results_store import _get_conn

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_position_store(config: dict) -> None:
    """Create the positions table (idempotent)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT NOT NULL,
                name          TEXT NOT NULL DEFAULT '',
                shares        INTEGER NOT NULL DEFAULT 0,
                cost_price    REAL NOT NULL DEFAULT 0,
                buy_date      TEXT NOT NULL DEFAULT '',
                current_price REAL NOT NULL DEFAULT 0,
                updated_at    TEXT NOT NULL DEFAULT '',
                notes         TEXT DEFAULT '',
                source        TEXT DEFAULT 'manual',
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_ticker
            ON positions(ticker)
        """)
        conn.commit()
    finally:
        conn.close()


def _fetch_price(code: str) -> float:
    """Fetch real-time price from Tencent. Returns 0 on failure."""
    try:
        from quantconclave.dataflows.tencent_realtime import _normalize_symbol
        import requests
        norm = _normalize_symbol(code)
        resp = requests.get(f"http://qt.gtimg.cn/q={norm}", timeout=5)
        resp.encoding = "gbk"
        if '="' in resp.text:
            fld = resp.text.split('="')[1].rstrip('";\n').split("~")
            if len(fld) > 3:
                return float(fld[3]) if fld[3] else 0
    except Exception:
        pass
    return 0


def add_position(config: dict, ticker: str, name: str = "",
                 shares: int = 0, cost_price: float = 0,
                 buy_date: str = "", notes: str = "", source: str = "manual") -> dict:
    """Add or update a position. Returns the row as a dict."""
    conn = _get_conn(config)
    now = _now()
    current_price = _fetch_price(ticker)
    try:
        existing = conn.execute(
            "SELECT id, shares, cost_price FROM positions WHERE ticker=?",
            (ticker,)
        ).fetchone()
        if existing:
            # Update: average down/up the cost basis, add shares
            old_shares = existing["shares"]
            old_cost = existing["cost_price"]
            total_shares = old_shares + shares
            avg_cost = ((old_shares * old_cost) + (shares * cost_price)) / total_shares if total_shares > 0 else 0
            conn.execute(
                "UPDATE positions SET shares=?, cost_price=?, current_price=?,"
                " updated_at=?, notes=? WHERE ticker=?",
                (total_shares, round(avg_cost, 4), current_price, now, notes, ticker),
            )
        else:
            conn.execute(
                "INSERT INTO positions (ticker, name, shares, cost_price, buy_date,"
                " current_price, updated_at, notes, source)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ticker, name, shares, cost_price, buy_date or now[:10],
                 current_price, now, notes, source),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM positions WHERE ticker=?", (ticker,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_position(dict(row)) if row else {}


def remove_position(config: dict, ticker: str) -> bool:
    """Delete a position by ticker. Returns True if deleted."""
    conn = _get_conn(config)
    try:
        cur = conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_positions(config: dict, refresh_prices: bool = True) -> list[dict]:
    """Return all positions with computed P&L. Optionally refresh current prices."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM positions ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        r = dict(row)
        if refresh_prices:
            new_price = _fetch_price(r["ticker"])
            if new_price > 0:
                r["current_price"] = new_price
                # Persist updated price
                c2 = _get_conn(config)
                try:
                    c2.execute(
                        "UPDATE positions SET current_price=?, updated_at=? WHERE id=?",
                        (new_price, _now(), r["id"]),
                    )
                    c2.commit()
                finally:
                    c2.close()
        results.append(_row_to_position(r))
    return results


def get_position(config: dict, ticker: str) -> Optional[dict]:
    """Get a single position by ticker."""
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM positions WHERE ticker=?", (ticker,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_position(dict(row)) if row else None


def _row_to_position(row: dict) -> dict:
    """Enrich a DB row with computed P&L fields."""
    shares = row.get("shares", 0) or 0
    cost = row.get("cost_price", 0) or 0
    current = row.get("current_price", 0) or 0
    cost_total = shares * cost
    market_value = shares * current
    pnl = market_value - cost_total
    pnl_pct = (pnl / cost_total * 100) if cost_total > 0 else 0
    return {
        "id": row.get("id"),
        "ticker": row.get("ticker", ""),
        "name": row.get("name", ""),
        "shares": shares,
        "cost_price": round(cost, 4),
        "buy_date": row.get("buy_date", ""),
        "current_price": round(current, 4),
        "cost_total": round(cost_total, 2),
        "market_value": round(market_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "updated_at": row.get("updated_at", ""),
        "notes": row.get("notes", ""),
        "source": row.get("source", "manual"),
        "created_at": row.get("created_at", ""),
    }
