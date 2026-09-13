"""Persistence layer for the Hot Tracker module (PR1).

Two tables live in results.db alongside stock_picks:
    hot_tracker_pool   — daily candidate snapshots with factor breakdowns
    hot_tracker_picks  — actual recommendations with settlement tracking

Table DDL is owned by ``web.results_store.init_db`` (the single migration
hub); this module only reads/writes them, mirroring pick_tracker's pattern
of calling into ``web.results_store``.
"""

import json
import logging
from datetime import datetime

from web.results_store import _get_conn

from quantconclave.hot_tracker.settlement import PENDING, append_track_entry

logger = logging.getLogger(__name__)


# ── hot_tracker_pool ──

def insert_pool_snapshot(config: dict, trade_date: str, rows: list[dict]) -> int:
    """Bulk upsert a daily candidate snapshot (UNIQUE on trade_date+ts_code)."""
    conn = _get_conn(config)
    try:
        for r in rows:
            conn.execute("""
                INSERT OR REPLACE INTO hot_tracker_pool (
                    trade_date, ts_code, name,
                    score_theme, score_momentum, score_fund, score_ml,
                    score_position, score_fresh, total_score,
                    theme_name, theme_rank, theme_pct_chg, theme_flow_3d,
                    fund_net_3d, fund_ddx_3d, sms_score,
                    dist_low_20d, dist_high_20d, ml_up_prob_5d,
                    vol_ratio, turnover_rate, market_cap, pe, pb,
                    guardrail_ok, guardrail_note, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade_date, r.get("ts_code", ""), r.get("name", ""),
                r.get("score_theme"), r.get("score_momentum"), r.get("score_fund"),
                r.get("score_ml"), r.get("score_position"), r.get("score_fresh"),
                r.get("total_score"), r.get("theme_name"), r.get("theme_rank"),
                r.get("theme_pct_chg"), r.get("theme_flow_3d"), r.get("fund_net_3d"),
                r.get("fund_ddx_3d"), r.get("sms_score"), r.get("dist_low_20d"),
                r.get("dist_high_20d"), r.get("ml_up_prob_5d"), r.get("vol_ratio"),
                r.get("turnover_rate"), r.get("market_cap"), r.get("pe"), r.get("pb"),
                1 if r.get("guardrail_ok", True) else 0, r.get("guardrail_note", ""),
                r.get("status", "ACTIVE"),
            ))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def get_pool_snapshot(config: dict, trade_date: str | None = None) -> list[dict]:
    """Return a pool snapshot, newest date by default, ranked by total_score."""
    conn = _get_conn(config)
    try:
        if trade_date:
            rows = conn.execute(
                "SELECT * FROM hot_tracker_pool WHERE trade_date = ? "
                "ORDER BY total_score DESC", (trade_date,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hot_tracker_pool WHERE trade_date = "
                "(SELECT MAX(trade_date) FROM hot_tracker_pool) "
                "ORDER BY total_score DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── hot_tracker_picks ──

def insert_pick(config: dict, pick_data: dict) -> str:
    """Record a recommendation for tracking. Returns ts_code (pick key part)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO hot_tracker_picks (
                pick_date, ts_code, name, pick_price, total_score, sms_score,
                settle_date, stop_loss_pct, take_profit_pct, status,
                latest_price, latest_date, peak_price, max_return_pct, return_pct,
                daily_track_json, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pick_data.get("pick_date", ""), pick_data.get("ts_code", ""),
            pick_data.get("name", ""), pick_data.get("pick_price", 0),
            pick_data.get("total_score", 0), pick_data.get("sms_score", 0),
            pick_data.get("settle_date", ""), pick_data.get("stop_loss_pct", -8.0),
            pick_data.get("take_profit_pct", 20.0), pick_data.get("status", PENDING),
            pick_data.get("latest_price", 0), pick_data.get("latest_date", ""),
            pick_data.get("peak_price", 0), pick_data.get("max_return_pct"),
            pick_data.get("return_pct"), pick_data.get("daily_track_json"),
            pick_data.get("notes", ""),
        ))
        conn.commit()
    finally:
        conn.close()
    return pick_data.get("ts_code", "")


def get_pending_picks(config: dict) -> list[dict]:
    """All PENDING tracked picks, oldest first."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM hot_tracker_picks WHERE status = ? ORDER BY pick_date ASC",
            (PENDING,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_pick_track(config: dict, ts_code: str, pick_date: str,
                      latest_price: float, latest_date: str, track_entry: dict) -> bool:
    """Update a PENDING pick's latest price, peak, return and daily snapshot."""
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM hot_tracker_picks WHERE ts_code = ? AND pick_date = ?",
            (ts_code, pick_date)).fetchone()
        if not row:
            return False
        pick_price = row["pick_price"] or 0
        ret_pct = (latest_price - pick_price) / pick_price * 100 if pick_price > 0 else None
        peak = row["peak_price"] or 0
        new_peak = max(peak, latest_price) if peak else latest_price
        max_ret = (new_peak - pick_price) / pick_price * 100 if pick_price > 0 else None
        conn.execute("""
            UPDATE hot_tracker_picks SET
                latest_price = ?, latest_date = ?, peak_price = ?,
                max_return_pct = ?, return_pct = ?, daily_track_json = ?
            WHERE ts_code = ? AND pick_date = ?
        """, (latest_price, latest_date, new_peak, max_ret, ret_pct,
              append_track_entry(row["daily_track_json"], track_entry),
              ts_code, pick_date))
        conn.commit()
        return True
    finally:
        conn.close()


def settle_pick(config: dict, ts_code: str, pick_date: str, status: str,
                return_pct: float, note: str = "") -> None:
    """Mark a pick terminal (SETTLED / STOPPED / PROFIT_TAKEN)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            UPDATE hot_tracker_picks SET status = ?, return_pct = ?,
                settled_at = ?,
                notes = CASE WHEN notes = '' THEN ? ELSE notes || ' | ' || ? END
            WHERE ts_code = ? AND pick_date = ?
        """, (status, return_pct, datetime.now().strftime("%Y-%m-%d %H:%M"),
              note, note, ts_code, pick_date))
        conn.commit()
    finally:
        conn.close()


# ── dashboard (minimal) ──

def get_hot_tracker_dashboard(config: dict, trade_date: str | None = None) -> dict:
    """Minimal dashboard query: theme aggregation + active picks + alerts.

    The full rendering (markdown tables, staleness flags) lands in PR3; this
    returns raw JSON so the endpoint exists and the loop is visibly closed.
    """
    pool = get_pool_snapshot(config, trade_date)
    conn = _get_conn(config)
    try:
        picks = [dict(r) for r in conn.execute(
            "SELECT * FROM hot_tracker_picks ORDER BY pick_date DESC, status DESC"
        ).fetchall()]
    finally:
        conn.close()
    alerts = [p for p in picks if p["status"] not in (PENDING,)]
    return {
        "themes": _aggregate_themes(pool),
        "picks": picks,
        "alerts": alerts,
        "stale_data": False,
    }


def _aggregate_themes(pool: list[dict]) -> list[dict]:
    """Group a pool snapshot by theme for the dashboard's theme table."""
    themes: dict[str, dict] = {}
    for r in pool:
        name = r.get("theme_name") or "—"
        if name not in themes:
            themes[name] = {
                "name": name,
                "rank": r.get("theme_rank"),
                "pct_chg": r.get("theme_pct_chg"),
                "flow_3d": r.get("theme_flow_3d"),
                "stocks": 0,
            }
        themes[name]["stocks"] += 1
    return sorted(themes.values(), key=lambda t: (t["rank"] is not None, t["rank"] or 999))
