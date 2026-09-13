"""Results persistence layer for the QuantConclave web dashboard.

Stores analysis run metadata in SQLite at {results_dir}/results.db, with
full state JSONs referenced by relative path (reusing the existing JSON
log files written by QuantConclaveGraph._log_state()).

The database path, connection factory, and legacy-DB migration are owned by
:mod:`quantconclave.workspace.store` — this module owns only the results
schema and the domain CRUD that reads/writes it.
"""

import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from quantconclave.workspace.store import get_connection, get_db_path

logger = logging.getLogger(__name__)

_RESULTS_DB_NAME = "results.db"


def _get_db_path(config: dict) -> Path:
    return get_db_path(config)


def _get_conn(config: dict) -> sqlite3.Connection:
    return get_connection(config)


def init_db(config: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS result_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT NOT NULL UNIQUE,
                ticker          TEXT NOT NULL,
                date            TEXT NOT NULL,
                company_name    TEXT DEFAULT '',
                rating          TEXT DEFAULT 'Hold',
                signal          TEXT DEFAULT '',
                analysts        TEXT DEFAULT '',
                provider        TEXT DEFAULT '',
                deep_model      TEXT DEFAULT '',
                quick_model     TEXT DEFAULT '',
                language        TEXT DEFAULT 'English',
                total_elapsed_ms INTEGER DEFAULT 0,
                run_type        TEXT DEFAULT 'manual',
                scheduled_job_id TEXT DEFAULT '',
                json_path       TEXT DEFAULT '',
                risk_level      TEXT DEFAULT '',
                next_analysis_date TEXT DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_result_runs_ticker
            ON result_runs(ticker)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_result_runs_date
            ON result_runs(date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_result_runs_created
            ON result_runs(created_at)
        """)
        # Migration: add next_analysis_date if missing
        try:
            conn.execute("ALTER TABLE result_runs ADD COLUMN next_analysis_date TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migration: per-role provider columns (resolved at save time)
        try:
            conn.execute("ALTER TABLE result_runs ADD COLUMN deep_provider TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE result_runs ADD COLUMN quick_provider TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists

        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_picks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pick_id      TEXT NOT NULL UNIQUE,
                ticker       TEXT NOT NULL,
                pick_date    TEXT NOT NULL,
                source       TEXT DEFAULT 'advisory',
                strategy     TEXT DEFAULT '',
                smart_money_score REAL DEFAULT 0,
                pick_price   REAL DEFAULT 0,
                reason       TEXT DEFAULT '',
                return_5d    REAL,
                return_20d   REAL,
                return_60d   REAL,
                resolved_at  TEXT,
                latest_price REAL DEFAULT 0,
                latest_date  TEXT DEFAULT '',
                notes        TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_ticker ON stock_picks(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_date ON stock_picks(pick_date)")
        # Migration: latest-price tracking columns (fix 0.00 latest price display)
        for col, ddl in (("latest_price", "REAL DEFAULT 0"),
                         ("latest_date", "TEXT DEFAULT ''")):
            try:
                conn.execute(f"ALTER TABLE stock_picks ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Hot Tracker tables (PR1): daily candidate snapshots + tracked picks ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hot_tracker_pool (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date      TEXT    NOT NULL,
                ts_code         TEXT    NOT NULL,
                name            TEXT    NOT NULL,
                score_theme     REAL, score_momentum  REAL, score_fund    REAL,
                score_ml        REAL, score_position  REAL, score_fresh   REAL,
                total_score     REAL,
                theme_name      TEXT, theme_rank      INTEGER,
                theme_pct_chg   REAL, theme_flow_3d   REAL,
                fund_net_3d     REAL, fund_ddx_3d     REAL, sms_score     REAL,
                dist_low_20d    REAL, dist_high_20d   REAL, ml_up_prob_5d REAL,
                vol_ratio       REAL, turnover_rate   REAL, market_cap    REAL,
                pe              REAL, pb              REAL,
                guardrail_ok    INTEGER DEFAULT 1,
                guardrail_note  TEXT,
                status          TEXT DEFAULT 'ACTIVE',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, ts_code)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hot_pool_date ON hot_tracker_pool(trade_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hot_pool_status ON hot_tracker_pool(status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hot_tracker_picks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pick_date       TEXT    NOT NULL,
                ts_code         TEXT    NOT NULL,
                name            TEXT    NOT NULL,
                pick_price      REAL    NOT NULL,
                total_score     REAL,
                sms_score       REAL,
                settle_date     TEXT    NOT NULL,
                stop_loss_pct   REAL DEFAULT -8.0,
                take_profit_pct REAL DEFAULT 20.0,
                status          TEXT DEFAULT 'PENDING',
                latest_price    REAL DEFAULT 0,
                latest_date     TEXT DEFAULT '',
                peak_price      REAL DEFAULT 0,
                max_return_pct  REAL,
                return_pct      REAL,
                settled_at      TEXT,
                daily_track_json TEXT,
                notes           TEXT DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pick_date, ts_code)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hot_picks_status ON hot_tracker_picks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hot_picks_date ON hot_tracker_picks(pick_date)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisory_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source_ticker TEXT,
                source_date TEXT,
                outcome TEXT,
                raw_return REAL,
                category TEXT DEFAULT 'other',
                status TEXT DEFAULT 'pending_review'
                    CHECK(status IN ('pending_review','active','archived')),
                lesson_abstract TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisory_experience_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                experience_ids TEXT NOT NULL,
                injected_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
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
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                type TEXT DEFAULT 'custom_llm',
                template_id TEXT,
                parameters TEXT DEFAULT '{}',
                code TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                is_favorite INTEGER DEFAULT 0,
                parent_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER,
                parameters TEXT,
                code TEXT,
                version_notes TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                trigger TEXT,
                total_pending INTEGER DEFAULT 0,
                resolved_count INTEGER DEFAULT 0,
                stats TEXT,
                proposals TEXT,
                experience_summary TEXT,
                resolved_list TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_result(config: dict, run_data: dict) -> str:
    run_id = run_data.get("run_id") or uuid.uuid4().hex[:12]
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO result_runs
                (run_id, ticker, date, company_name, rating, signal,
                 analysts, provider, deep_model, quick_model, language,
                 total_elapsed_ms, run_type, scheduled_job_id, json_path,
                 risk_level, next_analysis_date, deep_provider, quick_provider)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            run_data.get("ticker", ""),
            run_data.get("date", ""),
            run_data.get("company_name", ""),
            run_data.get("rating", "Hold"),
            run_data.get("signal", ""),
            run_data.get("analysts", ""),
            run_data.get("provider", ""),
            run_data.get("deep_model", ""),
            run_data.get("quick_model", ""),
            run_data.get("language", "English"),
            run_data.get("total_elapsed_ms", 0),
            run_data.get("run_type", "manual"),
            run_data.get("scheduled_job_id", ""),
            run_data.get("json_path", ""),
            run_data.get("risk_level", ""),
            run_data.get("next_analysis_date", ""),
            run_data.get("deep_provider", ""),
            run_data.get("quick_provider", ""),
        ))
        conn.commit()
    finally:
        conn.close()
    return run_id


def list_results(
    config: dict,
    ticker: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    rating: Optional[str] = None,
    run_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    conn = _get_conn(config)
    try:
        where = []
        params = []
        if ticker:
            from web.ticker_utils import normalize_ticker
            where.append("ticker = ?")
            params.append(normalize_ticker(ticker))
        if date_from:
            where.append("date >= ?")
            params.append(date_from)
        if date_to:
            where.append("date <= ?")
            params.append(date_to)
        if rating:
            where.append("rating = ?")
            params.append(rating)
        if run_type:
            where.append("run_type = ?")
            params.append(run_type)

        sql = "SELECT * FROM result_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_result(config: dict, run_id: str) -> Optional[dict]:
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM result_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def load_full_state(config: dict, run_id: str) -> Optional[dict]:
    row = get_result(config, run_id)
    if not row or not row.get("json_path"):
        return None
    results_dir = config.get("results_dir", "")
    full_path = Path(results_dir) / row["json_path"]
    if not full_path.exists():
        return None
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to load full state from %s", full_path)
        return None




def list_ticker_analyses(config: dict, ticker: str, limit: int = 10) -> list[dict]:
    """List all analysis runs for a given ticker, newest first.

    Returns list of {run_id, ticker, date, rating, signal, created_at, ...}
    """
    conn = _get_conn(config)
    try:
        from web.ticker_utils import normalize_ticker
        rows = conn.execute(
            """SELECT run_id, ticker, date, rating, signal, company_name,
                      created_at, total_elapsed_ms
               FROM result_runs
               WHERE ticker = ?
               ORDER BY date DESC
               LIMIT ?""",
            (normalize_ticker(ticker), limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_ticker_latest_analysis(config: dict, ticker: str) -> Optional[dict]:
    """Get the most recent analysis metadata for a ticker. Returns None if none found."""
    analyses = list_ticker_analyses(config, ticker, limit=1)
    return analyses[0] if analyses else None


def search_analyses(config: dict, ticker: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    rating: str | None = None, limit: int = 20) -> list[dict]:
    """Search analyses by ticker, date range, and/or rating.

    Flexible search for the advisory PM to find relevant past analyses.
    """
    conn = _get_conn(config)
    try:
        where = ["1=1"]
        params = []
        if ticker:
            from web.ticker_utils import normalize_ticker
            where.append("ticker = ?")
            params.append(normalize_ticker(ticker))
        if date_from:
            where.append("date >= ?")
            params.append(date_from)
        if date_to:
            where.append("date <= ?")
            params.append(date_to)
        if rating:
            where.append("rating = ?")
            params.append(rating)
        rows = conn.execute(
            f"""SELECT run_id, ticker, date, rating, signal, company_name,
                       created_at
                FROM result_runs
                WHERE {" AND ".join(where)}
                ORDER BY date DESC
                LIMIT ?""",
            params + [limit]
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_tickers(config: dict) -> list[dict]:
    """Get a list of all tickers that have been analyzed, newest first.
    Returns list of {ticker, name} dicts."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """SELECT DISTINCT ticker FROM result_runs
               ORDER BY date DESC LIMIT 50"""
        ).fetchall()
        tickers = [r["ticker"] for r in rows]
        # Resolve Chinese names
        result = []
        from web.ticker_utils import resolve_company_name
        for t in tickers:
            _, name = resolve_company_name(t)
            result.append({"ticker": t, "name": name or ""})
        return result
    finally:
        conn.close()


def delete_result(config: dict, run_id: str) -> bool:
    conn = _get_conn(config)
    try:
        cur = conn.execute("DELETE FROM result_runs WHERE run_id = ?", (run_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---- Shortlist ----

def init_shortlist(config: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shortlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT NOT NULL,
                name        TEXT DEFAULT '',
                source      TEXT DEFAULT '',
                score       REAL DEFAULT 0,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                price_at_add REAL DEFAULT 0,
                UNIQUE(ticker)
            )
        """)
        # Migration: add price_at_add if not exists (for existing DBs)
        try:
            conn.execute("ALTER TABLE shortlist ADD COLUMN price_at_add REAL DEFAULT 0")
        except Exception:
            pass  # column already exists
        conn.commit()
    finally:
        conn.close()


def add_to_shortlist(config: dict, ticker: str, name: str = "",
                     source: str = "", score: float = 0,
                     price_at_add: float = 0) -> bool:
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO shortlist (ticker, name, source, score, price_at_add, added_at)
            VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
        """, (ticker.upper(), name, source, score, price_at_add))
        conn.commit()
        return True
    finally:
        conn.close()


def remove_from_shortlist(config: dict, ticker: str) -> bool:
    conn = _get_conn(config)
    try:
        cur = conn.execute("DELETE FROM shortlist WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_shortlist(config: dict) -> list[dict]:
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM shortlist ORDER BY score DESC, added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def clear_shortlist(config: dict) -> int:
    conn = _get_conn(config)
    try:
        cur = conn.execute("DELETE FROM shortlist")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_comparison(config: dict, run_ids: list[str]) -> list[dict]:
    if not run_ids:
        return []
    conn = _get_conn(config)
    try:
        placeholders = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"SELECT * FROM result_runs WHERE run_id IN ({placeholders})",
            run_ids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---- Chat Threads & Messages ----

def init_chat_tables(config: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id    TEXT NOT NULL UNIQUE,
                run_id       TEXT DEFAULT NULL,
                title        TEXT DEFAULT '',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id    TEXT NOT NULL,
                role         TEXT NOT NULL,
                content      TEXT NOT NULL,
                tool_calls   TEXT DEFAULT '',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_threads_run
            ON chat_threads(run_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
            ON chat_messages(thread_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_thread_runs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                run_id    TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES result_runs(run_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_thread_runs_link
            ON chat_thread_runs(thread_id, run_id)
        """)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        # Migration: allow NULL run_id for advisory threads
        col_info = conn.execute("PRAGMA table_info(chat_threads)").fetchall()
        run_id_nullable = any(c[1] == "run_id" and c[3] == 0 for c in col_info)
        if not run_id_nullable:
            has_created = any(c[1] == "created_at" for c in col_info)
            has_updated = any(c[1] == "updated_at" for c in col_info)
            if not has_created:
                conn.execute("ALTER TABLE chat_threads ADD COLUMN created_at TEXT")
            if not has_updated:
                conn.execute("ALTER TABLE chat_threads ADD COLUMN updated_at TEXT")
            msg_info = conn.execute("PRAGMA table_info(chat_messages)").fetchall()
            has_msg_tc = any(c[1] == "tool_calls" for c in msg_info)
            has_msg_ca = any(c[1] == "created_at" for c in msg_info)
            if not has_msg_tc:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN tool_calls TEXT DEFAULT ''")
            if not has_msg_ca:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN created_at TEXT")
            conn.execute("ALTER TABLE chat_messages RENAME TO chat_messages_old")
            conn.execute("ALTER TABLE chat_thread_runs RENAME TO chat_thread_runs_old")
            conn.execute("ALTER TABLE chat_threads RENAME TO chat_threads_old")
            conn.execute("""
                CREATE TABLE chat_threads (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL UNIQUE,
                    run_id  TEXT DEFAULT NULL,
                    title   TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT INTO chat_threads
                SELECT id, thread_id,
                    CASE WHEN run_id = '' THEN NULL ELSE run_id END,
                    COALESCE(title, ''),
                    COALESCE(created_at, CURRENT_TIMESTAMP),
                    COALESCE(updated_at, CURRENT_TIMESTAMP)
                FROM chat_threads_old
            """)
            conn.execute("""
                CREATE TABLE chat_messages (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    role    TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                INSERT INTO chat_messages (id, thread_id, role, content, tool_calls, created_at)
                SELECT id, thread_id, role, content,
                    COALESCE(tool_calls, ''),
                    COALESCE(created_at, CURRENT_TIMESTAMP)
                FROM chat_messages_old
            """)
            conn.execute("""
                CREATE TABLE chat_thread_runs (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
                    FOREIGN KEY (run_id) REFERENCES result_runs(run_id) ON DELETE CASCADE
                )
            """)
            conn.execute("INSERT INTO chat_thread_runs (id, thread_id, run_id) SELECT id, thread_id, run_id FROM chat_thread_runs_old")
            conn.execute("DROP TABLE chat_threads_old")
            conn.execute("DROP TABLE chat_messages_old")
            conn.execute("DROP TABLE chat_thread_runs_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_run ON chat_threads(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_thread_runs_link ON chat_thread_runs(thread_id, run_id)")
            conn.commit()

            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_thread_runs_link ON chat_thread_runs(thread_id, run_id)")
            conn.commit()

        # ── Migration: backfill empty company_name ─────────────────
        try:
            needs = conn.execute(
                "SELECT COUNT(*) FROM result_runs WHERE company_name = '' OR company_name IS NULL"
            ).fetchone()[0]
            if needs > 0:
                logger.info("Backfilling %d result_runs rows missing company_name ...", needs)
                from web.ticker_utils import resolve_company_name
                rows = conn.execute(
                    "SELECT id, ticker FROM result_runs WHERE company_name = '' OR company_name IS NULL"
                ).fetchall()
                updated = 0
                for row in rows:
                    _, name = resolve_company_name(row["ticker"])
                    if name:
                        conn.execute("UPDATE result_runs SET company_name = ? WHERE id = ?",
                                     (name, row["id"]))
                        updated += 1
                conn.commit()
                logger.info("Backfill complete: %d/%d rows updated.", updated, needs)
        except Exception:
            logger.exception("company_name backfill failed, continuing")
        # ── End migration ─────────────────────────────────────────
    finally:
        conn.close()


def create_chat_thread(config: dict, run_ids: str | list[str], title: str = "") -> str:
    """Create a new chat thread for one or more analysis runs. Returns the thread_id."""
    import uuid
    if isinstance(run_ids, str):
        run_ids = [run_ids]
    thread_id = uuid.uuid4().hex[:12]
    conn = _get_conn(config)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        primary_run = run_ids[0] if run_ids else None
        if primary_run:
            conn.execute(
                "INSERT INTO chat_threads (thread_id, run_id, title) VALUES (?, ?, ?)",
                (thread_id, primary_run, title)
            )
        else:
            conn.execute(
                "INSERT INTO chat_threads (thread_id, title) VALUES (?, ?)",
                (thread_id, title)
            )
        for rid in run_ids:
            conn.execute(
                "INSERT OR IGNORE INTO chat_thread_runs (thread_id, run_id) VALUES (?, ?)",
                (thread_id, rid)
            )
        conn.commit()
    finally:
        conn.close()
    return thread_id


def get_thread_run_ids(config: dict, thread_id: str) -> list[str]:
    """Get all run_ids associated with a chat thread."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT run_id FROM chat_thread_runs WHERE thread_id = ? ORDER BY id",
            (thread_id,)
        ).fetchall()
        return [r["run_id"] for r in rows]
    finally:
        conn.close()


def list_chat_threads(config: dict, run_id: str | None = None) -> list[dict]:
    """List chat threads, optionally filtered by run_id. Includes associated run_ids per thread."""
    conn = _get_conn(config)
    try:
        if run_id:
            rows = conn.execute(
                """SELECT t.thread_id, t.title, t.created_at, t.updated_at,
                          COUNT(m.id) as message_count
                   FROM chat_threads t
                   LEFT JOIN chat_messages m ON t.thread_id = m.thread_id
                   INNER JOIN chat_thread_runs l ON t.thread_id = l.thread_id
                   WHERE l.run_id = ?
                   GROUP BY t.thread_id
                   ORDER BY t.updated_at DESC""",
                (run_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.thread_id, t.title, t.created_at, t.updated_at,
                          COUNT(m.id) as message_count
                   FROM chat_threads t
                   LEFT JOIN chat_messages m ON t.thread_id = m.thread_id
                   GROUP BY t.thread_id
                   ORDER BY t.updated_at DESC
                   LIMIT 50"""
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["run_ids"] = get_thread_run_ids(config, d["thread_id"])
            result.append(d)
        return result
    finally:
        conn.close()


def delete_chat_thread(config: dict, thread_id: str) -> bool:
    """Delete a thread and its messages (CASCADE handles messages)."""
    conn = _get_conn(config)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_chat_messages(config: dict, thread_id: str) -> list[dict]:
    """Get all messages for a thread, oldest first."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """SELECT role, content, tool_calls, created_at
               FROM chat_messages WHERE thread_id = ?
               ORDER BY created_at ASC""",
            (thread_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_chat_message(config: dict, thread_id: str, role: str,
                      content: str, tool_calls: str = "") -> int:
    """Save a message. Returns the message id."""
    conn = _get_conn(config)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """INSERT INTO chat_messages (thread_id, role, content, tool_calls)
               VALUES (?, ?, ?, ?)""",
            (thread_id, role, content, tool_calls)
        )
        # Update thread's updated_at
        conn.execute(
            "UPDATE chat_threads SET updated_at = CURRENT_TIMESTAMP WHERE thread_id = ?",
            (thread_id,)
        )
        # Auto-set thread title from first user message
        conn.execute(
            """UPDATE chat_threads SET title = ?
               WHERE thread_id = ? AND (title IS NULL OR title = '')""",
            (content[:40], thread_id)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---- RRG Snapshots ----

def init_rrg_snapshots(cfg: dict):
    """Create the rrg_snapshots table if not exists."""
    conn = _get_conn(cfg)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rrg_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                industry_name TEXT NOT NULL,
                rs_ratio REAL,
                rs_momentum REAL,
                quadrant TEXT,
                fund_flow REAL,
                change_pct REAL,
                stock_count INTEGER,
                tail_json TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(date, industry_name)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rrg_snap_date ON rrg_snapshots(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rrg_snap_ind ON rrg_snapshots(industry_name)")
        conn.commit()
    finally:
        conn.close()


def save_rrg_snapshot(cfg: dict, date_str: str, industries: list[dict]):
    """Save one day's RRG data. Upserts on (date, industry_name)."""
    conn = _get_conn(cfg)
    try:
        for ind in industries:
            conn.execute("""
                INSERT INTO rrg_snapshots (date, industry_name, rs_ratio, rs_momentum, quadrant, fund_flow, change_pct, stock_count, tail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, industry_name) DO UPDATE SET
                    rs_ratio=excluded.rs_ratio, rs_momentum=excluded.rs_momentum,
                    quadrant=excluded.quadrant, fund_flow=excluded.fund_flow,
                    change_pct=excluded.change_pct, stock_count=excluded.stock_count,
                    tail_json=excluded.tail_json
            """, (
                date_str, ind["name"], ind.get("rs_ratio", 0), ind.get("rs_momentum", 0),
                ind.get("quadrant", "lagging"), ind.get("fund_flow", 0),
                ind.get("change_pct", 0), ind.get("stock_count", 0),
                json.dumps(ind.get("tail", []), ensure_ascii=False)
            ))
        conn.commit()
    finally:
        conn.close()


def get_rrg_snapshots(cfg: dict, date_str: str | None = None, industry_name: str | None = None) -> list[dict]:
    """Get RRG snapshots, optionally filtered by date and/or industry."""
    conn = _get_conn(cfg)
    try:
        where = []
        params = []
        if date_str:
            where.append("date = ?")
            params.append(date_str)
        if industry_name:
            where.append("industry_name = ?")
            params.append(industry_name)
        sql = "SELECT * FROM rrg_snapshots"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date DESC, rs_ratio DESC"
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["tail"] = json.loads(d["tail_json"]) if d["tail_json"] else []
            del d["tail_json"]
            results.append(d)
        return results
    finally:
        conn.close()


# ---- Stock Picks ----


def save_pick(config: dict, pick_data: dict) -> str:
    """Save a stock pick. Returns pick_id."""
    import uuid
    pick_id = pick_data.get("pick_id") or uuid.uuid4().hex[:12]
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_picks
                (pick_id, ticker, pick_date, source, strategy,
                 smart_money_score, pick_price, reason, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pick_id,
            pick_data.get("ticker", ""),
            pick_data.get("pick_date", ""),
            pick_data.get("source", "advisory"),
            pick_data.get("strategy", ""),
            pick_data.get("smart_money_score", 0),
            pick_data.get("pick_price", 0),
            pick_data.get("reason", ""),
            pick_data.get("notes", ""),
        ))
        conn.commit()
    finally:
        conn.close()
    return pick_id


def get_picks(config: dict, ticker: str = None, limit: int = 20) -> list[dict]:
    """Get past picks, optionally filtered by ticker."""
    conn = _get_conn(config)
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM stock_picks WHERE ticker = ? ORDER BY pick_date DESC LIMIT ?",
                (ticker, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock_picks ORDER BY pick_date DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_picks(config: dict) -> int:
    """Update unresolved picks with latest price data. Returns count resolved.

    PR1: uses the multi-source fallback chain (tushare EOD → tencent realtime
    → akshare → yfinance) instead of yfinance-only, so A-share picks actually
    resolve. Persists latest_price / latest_date so get_reversal_performance
    no longer shows 0.00. If every source fails the pick stays PENDING
    (return_60d NULL) — we never write a bogus zero.
    """
    from quantconclave.hot_tracker.price_source import (
        PriceFetchError, fetch_latest_price,
    )

    conn = _get_conn(config)
    try:
        pending = conn.execute(
            "SELECT * FROM stock_picks WHERE return_60d IS NULL"
        ).fetchall()
        resolved = 0
        for p in pending:
            try:
                quote = fetch_latest_price(p["ticker"], config)
            except PriceFetchError as e:
                logger.warning("resolve_picks: %s", e)
                continue
            last_price = quote["price"]
            latest_date = quote["date"]
            pick_price = p["pick_price"] or 0
            if pick_price <= 0:
                # Backfill a missing pick_price from history close near pick_date
                pick_price = _backfill_pick_price(p["ticker"], p["pick_date"])
                if pick_price and pick_price > 0:
                    conn.execute(
                        "UPDATE stock_picks SET pick_price = ? WHERE pick_id = ?",
                        (pick_price, p["pick_id"]))
            total_return = (last_price - pick_price) / pick_price if pick_price > 0 else 0

            from datetime import datetime
            try:
                pick_d = datetime.strptime(p["pick_date"], "%Y-%m-%d")
            except (ValueError, TypeError):
                pick_d = datetime.now()
            days_ago = (datetime.now() - pick_d).days

            updates = {
                "resolved_at": datetime.now().strftime("%Y-%m-%d"),
                "latest_price": last_price,
                "latest_date": latest_date,
            }
            if days_ago >= 5:
                updates["return_5d"] = round(total_return * 100, 2)
            if days_ago >= 20:
                updates["return_20d"] = round(total_return * 100, 2)
            if days_ago >= 60:
                updates["return_60d"] = round(total_return * 100, 2)

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE stock_picks SET {set_clause} WHERE pick_id = ?",
                list(updates.values()) + [p["pick_id"]]
            )
            resolved += 1
        conn.commit()
    finally:
        conn.close()
    return resolved


def _backfill_pick_price(ts_code: str, pick_date: str) -> float | None:
    """Find the close price on/near pick_date from history (for pick_price=0 rows)."""
    try:
        from quantconclave.hot_tracker.price_source import fetch_price_history
        rows = fetch_price_history(ts_code, days=90)
        for r in rows:
            if r["date"] >= pick_date and r["close"] > 0:
                return r["close"]
        if rows:
            return rows[-1]["close"]
    except Exception:
        pass
    return None


def get_pick_performance_summary(config: dict, days: int = 90) -> dict:
    """Return aggregate performance stats for picks in the last N days."""
    conn = _get_conn(config)
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        picks = conn.execute(
            "SELECT * FROM stock_picks WHERE pick_date >= ?",
            (cutoff,)
        ).fetchall()

        resolved = [p for p in picks if p["return_20d"] is not None]
        if not resolved:
            return {"total_picks": len(picks), "resolved": 0, "message": "No resolved picks yet"}

        wins = len([p for p in resolved if (p["return_20d"] or 0) > 0])
        avg_return = sum(p["return_20d"] or 0 for p in resolved) / len(resolved)
        return {
            "total_picks": len(picks),
            "resolved": len(resolved),
            "win_rate": round(wins / len(resolved) * 100, 1),
            "avg_return_20d": round(avg_return, 2),
            "top_pick": max(resolved, key=lambda p: p["return_20d"] or 0),
        }
    finally:
        conn.close()
