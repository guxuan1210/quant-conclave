"""Persistence for the investment-effect evaluation subsystem.

Four record types, mirroring the plan: evaluation cases (the sampled stock),
prediction versions (full pipeline vs single model), precise-period returns
(5/20/60 trading days), and periodic reports. Reuses the shared connection
factory from ``web.results_store`` and the ``init_*_store`` convention.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

from web.results_store import _get_conn

logger = logging.getLogger(__name__)


def init_eval_store(config: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_cases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id         TEXT NOT NULL UNIQUE,
                week_key        TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                company_name    TEXT DEFAULT '',
                index_source    TEXT DEFAULT '',
                industry        TEXT DEFAULT '',
                selection_date  TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'weekly',
                status          TEXT NOT NULL DEFAULT 'pending',
                failure_reason  TEXT DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_week ON eval_cases(week_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_ticker ON eval_cases(ticker)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_predictions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id       TEXT NOT NULL UNIQUE,
                case_id             TEXT NOT NULL,
                variant             TEXT NOT NULL DEFAULT 'full',
                run_id              TEXT DEFAULT '',
                rating              TEXT NOT NULL DEFAULT 'Hold',
                confidence          TEXT DEFAULT '',
                smart_money_score   REAL,
                model_config_hash   TEXT DEFAULT '',
                input_snapshot_hash TEXT DEFAULT '',
                llm_calls           INTEGER DEFAULT 0,
                tool_calls          INTEGER DEFAULT 0,
                tokens_in           INTEGER DEFAULT 0,
                tokens_out          INTEGER DEFAULT 0,
                estimated_cost      REAL DEFAULT 0,
                elapsed_ms          INTEGER DEFAULT 0,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_pred_case ON eval_predictions(case_id)")
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_pred_case_variant "
                "ON eval_predictions(case_id, variant)"
            )
        except sqlite3.IntegrityError:
            # A pre-release database may contain duplicates from manual runs.
            # Runtime lookups and deterministic IDs still prevent new duplicates;
            # do not make application startup destructive just to add the index.
            logger.warning("existing duplicate evaluation variants; unique index deferred")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_input_snapshots (
                case_id        TEXT PRIMARY KEY,
                reports_json   TEXT NOT NULL,
                snapshot_hash  TEXT NOT NULL,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_returns (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id     TEXT NOT NULL,
                case_id           TEXT NOT NULL,
                horizon           INTEGER NOT NULL,
                entry_date        TEXT DEFAULT '',
                entry_price       REAL,
                exit_date         TEXT DEFAULT '',
                exit_price        REAL,
                gross_return      REAL,
                net_return        REAL,
                benchmark_return  REAL,
                excess_return     REAL,
                settled_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(prediction_id, horizon)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_ret_case ON eval_returns(case_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_reports (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                period             TEXT NOT NULL,
                period_key         TEXT NOT NULL,
                generated_at       TEXT NOT NULL,
                summary_json       TEXT DEFAULT '',
                report_json        TEXT DEFAULT '',
                coverage_pct       REAL,
                cum_excess_return  REAL,
                annualized_ir      REAL,
                max_drawdown_delta REAL,
                is_valid           INTEGER,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(period, period_key)
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def create_eval_case(config: dict, case: dict) -> str:
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR IGNORE INTO eval_cases
                (case_id, week_key, ticker, company_name, index_source, industry,
                 selection_date, source, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            case["case_id"], case["week_key"], case["ticker"],
            case.get("company_name", ""), case.get("index_source", ""),
            case.get("industry", ""), case["selection_date"],
            case.get("source", "weekly"), case.get("status", "pending"),
        ))
        conn.commit()
    finally:
        conn.close()
    return case["case_id"]


def create_eval_cases(config: dict, cases: list[dict]) -> None:
    """Persist a complete weekly slate in one transaction."""
    conn = _get_conn(config)
    try:
        conn.executemany("""
            INSERT OR IGNORE INTO eval_cases
                (case_id, week_key, ticker, company_name, index_source, industry,
                 selection_date, source, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(
            case["case_id"], case["week_key"], case["ticker"],
            case.get("company_name", ""), case.get("index_source", ""),
            case.get("industry", ""), case["selection_date"],
            case.get("source", "weekly"), case.get("status", "pending"),
        ) for case in cases])
        conn.commit()
    finally:
        conn.close()


def mark_case_settled(config: dict, case_id: str) -> None:
    _update_case_status(config, case_id, "settled", "")


def mark_case_failed(config: dict, case_id: str, reason: str) -> None:
    _update_case_status(config, case_id, "failed", reason)


def mark_case_pending(config: dict, case_id: str) -> None:
    _update_case_status(config, case_id, "pending", "")


def _update_case_status(config: dict, case_id: str, status: str, reason: str) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("UPDATE eval_cases SET status = ?, failure_reason = ? WHERE case_id = ?",
                     (status, reason, case_id))
        conn.commit()
    finally:
        conn.close()


def has_week(config: dict, week_key: str) -> bool:
    conn = _get_conn(config)
    try:
        row = conn.execute("SELECT COUNT(*) FROM eval_cases WHERE week_key = ?", (week_key,)).fetchone()
        return bool(row and row[0] > 0)
    finally:
        conn.close()


def recent_tickers(config: dict, weeks: int) -> set[str]:
    conn = _get_conn(config)
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM eval_cases
            WHERE week_key IN (SELECT DISTINCT week_key FROM eval_cases ORDER BY week_key DESC LIMIT ?)
        """, (weeks,)).fetchall()
        return {r["ticker"] for r in rows}
    finally:
        conn.close()


def list_eval_cases(
    config: dict,
    week_key: str | None = None,
    ticker: str | None = None,
    status: str | None = None,
    month_key: str | None = None,
    source: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    conn = _get_conn(config)
    try:
        where, params = [], []
        if week_key:
            where.append("week_key = ?")
            params.append(week_key)
        if ticker:
            where.append("ticker = ?")
            params.append(ticker)
        if status:
            where.append("status = ?")
            params.append(status)
        if month_key:
            where.append("substr(selection_date, 1, 7) = ?")
            params.append(month_key)
        if source:
            where.append("source = ?")
            params.append(source)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM eval_cases{clause} ORDER BY selection_date DESC, id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

def create_eval_prediction(config: dict, prediction: dict) -> str:
    conn = _get_conn(config)
    try:
        existing = conn.execute(
            "SELECT prediction_id FROM eval_predictions WHERE case_id = ? AND variant = ?",
            (prediction["case_id"], prediction.get("variant", "full")),
        ).fetchone()
        if existing:
            return str(existing["prediction_id"])
        conn.execute("""
            INSERT OR IGNORE INTO eval_predictions
                (prediction_id, case_id, variant, run_id, rating, confidence,
                 smart_money_score, model_config_hash, input_snapshot_hash,
                 llm_calls, tool_calls, tokens_in, tokens_out, estimated_cost, elapsed_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            prediction["prediction_id"], prediction["case_id"],
            prediction.get("variant", "full"), prediction.get("run_id", ""),
            prediction.get("rating", "Hold"), prediction.get("confidence", ""),
            prediction.get("smart_money_score"), prediction.get("model_config_hash", ""),
            prediction.get("input_snapshot_hash", ""),
            prediction.get("llm_calls", 0), prediction.get("tool_calls", 0),
            prediction.get("tokens_in", 0), prediction.get("tokens_out", 0),
            prediction.get("estimated_cost", 0.0), prediction.get("elapsed_ms", 0),
        ))
        conn.commit()
    finally:
        conn.close()
    return prediction["prediction_id"]


def get_eval_predictions(
    config: dict,
    case_id: str | None = None,
    variant: str | None = None,
) -> list[dict]:
    conn = _get_conn(config)
    try:
        where, params = [], []
        if case_id:
            where.append("case_id = ?")
            params.append(case_id)
        if variant:
            where.append("variant = ?")
            params.append(variant)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(f"SELECT * FROM eval_predictions{clause} ORDER BY id", params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_full_prediction(config: dict, case_id: str) -> dict | None:
    rows = get_eval_predictions(config, case_id=case_id, variant="full")
    return rows[0] if rows else None


def load_frozen_reports(config: dict, case_id: str) -> dict:
    """Load immutable analyst reports, with a legacy run-file fallback."""
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT reports_json FROM eval_input_snapshots WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row:
            return json.loads(row["reports_json"] or "{}")
    finally:
        conn.close()

    pred = get_full_prediction(config, case_id)
    if not pred or not pred.get("run_id"):
        return {}


def save_frozen_reports(
    config: dict, case_id: str, reports: dict, snapshot_hash: str
) -> str:
    """Freeze reports until the Full prediction exists, then make them immutable."""
    keys = ("market_report", "sentiment_report", "news_report",
            "fundamentals_report", "capital_flow_report",
            "competitor_report", "partner_report")
    frozen = {key: reports.get(key, "") for key in keys}
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT INTO eval_input_snapshots
                (case_id, reports_json, snapshot_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                reports_json = excluded.reports_json,
                snapshot_hash = excluded.snapshot_hash
            WHERE NOT EXISTS (
                SELECT 1 FROM eval_predictions
                WHERE case_id = excluded.case_id AND variant = 'full'
            )
        """, (case_id, json.dumps(frozen, ensure_ascii=False), snapshot_hash))
        conn.commit()
        row = conn.execute(
            "SELECT snapshot_hash FROM eval_input_snapshots WHERE case_id = ?", (case_id,)
        ).fetchone()
        return str(row["snapshot_hash"])
    finally:
        conn.close()
    try:
        from web.results_store import load_full_state
        state = load_full_state(config, pred["run_id"]) or {}
        keys = ("market_report", "sentiment_report", "news_report",
                "fundamentals_report", "capital_flow_report",
                "competitor_report", "partner_report")
        return {k: state.get(k, "") for k in keys}
    except Exception as e:
        logger.warning("load_frozen_reports failed for %s: %s", case_id, e)
        return {}


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

def save_eval_return(config: dict, ret: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO eval_returns
                (prediction_id, case_id, horizon, entry_date, entry_price,
                 exit_date, exit_price, gross_return, net_return, benchmark_return, excess_return)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ret["prediction_id"], ret["case_id"], ret["horizon"],
            ret.get("entry_date", ""), ret.get("entry_price"),
            ret.get("exit_date", ""), ret.get("exit_price"),
            ret.get("gross_return"), ret.get("net_return"),
            ret.get("benchmark_return"), ret.get("excess_return"),
        ))
        conn.commit()
    finally:
        conn.close()


def get_eval_returns(
    config: dict,
    prediction_id: str | None = None,
    case_id: str | None = None,
    horizon: int | None = None,
) -> list[dict]:
    conn = _get_conn(config)
    try:
        where, params = [], []
        if prediction_id:
            where.append("prediction_id = ?")
            params.append(prediction_id)
        if case_id:
            where.append("case_id = ?")
            params.append(case_id)
        if horizon:
            where.append("horizon = ?")
            params.append(horizon)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(f"SELECT * FROM eval_returns{clause} ORDER BY id", params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_eval_metrics_rows(config: dict) -> list[dict]:
    """Joined rows for summary: case + full prediction + returns (one per horizon)."""
    conn = _get_conn(config)
    try:
        rows = conn.execute("""
            SELECT c.case_id, c.week_key, c.ticker, c.selection_date, c.status,
                   p.variant, p.rating, p.confidence, p.smart_money_score,
                   r.horizon, r.gross_return, r.net_return, r.benchmark_return,
                   r.excess_return
            FROM eval_cases c
            JOIN eval_predictions p ON p.case_id = c.case_id
            LEFT JOIN eval_returns r ON r.prediction_id = p.prediction_id
            ORDER BY c.week_key, c.id, p.id, r.horizon
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def upsert_eval_report(config: dict, report: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO eval_reports
                (period, period_key, generated_at, summary_json, report_json,
                 coverage_pct, cum_excess_return, annualized_ir, max_drawdown_delta, is_valid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report["period"], report["period_key"], report["generated_at"],
            json.dumps(report.get("summary", {}), ensure_ascii=False),
            json.dumps(report.get("report", {}), ensure_ascii=False),
            report.get("coverage_pct"), report.get("cum_excess_return"),
            report.get("annualized_ir"), report.get("max_drawdown_delta"),
            report.get("is_valid"),
        ))
        conn.commit()
    finally:
        conn.close()


def get_eval_report(config: dict, period: str, period_key: str) -> dict | None:
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM eval_reports WHERE period = ? AND period_key = ?",
            (period, period_key),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["summary"] = json.loads(d.pop("summary_json") or "{}")
        d["report"] = json.loads(d.pop("report_json") or "{}")
        return d
    finally:
        conn.close()
