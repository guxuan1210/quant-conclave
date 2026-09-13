"""Persistence for the Eastmoney (东方财富) watchlist feature.

Stores three kinds of data in the shared ``results.db``:

- ``watchlist_stocks``  — the synced self-select list. Merged incrementally
  (INSERT new, UPDATE existing, never DELETE) so local data is preserved.
- ``watchlist_sync_log`` — one row per sync attempt, for auditing.
- ``watchlist_analysis`` — every per-stock analysis as an append-only row
  (code + analyzed_at) so the full history of each analysis is retained.

Reuses ``results_store._get_conn`` / ``_get_db_path`` so WAL, row_factory and
the DB path (``~/.quantconclave/logs/results.db``) stay consistent with the rest
of the dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from web import trade_cal
from web.results_store import _get_conn, _get_db_path

logger = logging.getLogger(__name__)


def _now() -> str:
    """Local ISO timestamp with second precision."""
    return datetime.now().isoformat(timespec="seconds")


def _f(v) -> float:
    """Coerce a value to float, stripping commas. Returns 0.0 on failure."""
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _f_or_none(v) -> Optional[float]:
    """Coerce to float, or None if empty/invalid (frontend shows '-' for NULL)."""
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def init_watchlist_store(config: dict) -> None:
    """Create the three watchlist tables + indexes (idempotent)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_stocks (
                code         TEXT PRIMARY KEY,
                name         TEXT NOT NULL DEFAULT '',
                price        REAL NOT NULL DEFAULT 0,
                change_pct   REAL NOT NULL DEFAULT 0,
                turnover     REAL DEFAULT NULL,
                vol_ratio    REAL DEFAULT NULL,
                synced_at    TEXT NOT NULL,
                source       TEXT DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add source column if missing (safe — ignores if exists)
        try:
            conn.execute("ALTER TABLE watchlist_stocks ADD COLUMN source TEXT DEFAULT ''")
        except Exception:
            pass  # column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_sync_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at   TEXT NOT NULL,
                total       INTEGER DEFAULT 0,
                added       INTEGER DEFAULT 0,
                updated     INTEGER DEFAULT 0,
                removed     INTEGER DEFAULT 0,
                source      TEXT DEFAULT 'mxapi',
                detail_json TEXT DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_watchlist_sync_time
            ON watchlist_sync_log(synced_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_analysis (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                code             TEXT NOT NULL,
                analyzed_at      TEXT NOT NULL,
                rt_price         TEXT DEFAULT '',
                rt_change        TEXT DEFAULT '',
                rt_change_pct    TEXT DEFAULT '',
                rt_high          TEXT DEFAULT '',
                rt_low           TEXT DEFAULT '',
                rt_volume        TEXT DEFAULT '',
                rt_amount        TEXT DEFAULT '',
                flow_detail_json TEXT DEFAULT '[]',
                analysis_text    TEXT DEFAULT '',
                verdict          TEXT DEFAULT '',
                setup_type       TEXT DEFAULT '',
                intraday_json    TEXT DEFAULT '[]',
                flow_data        TEXT DEFAULT '',
                model_name       TEXT DEFAULT '',
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add model_name column if missing (for DBs created before this field existed)
        try:
            conn.execute("ALTER TABLE watchlist_analysis ADD COLUMN model_name TEXT DEFAULT ''")
        except Exception:
            pass  # column already exists
        # Migration: add setup_type column if missing (path-classification framework)
        try:
            conn.execute("ALTER TABLE watchlist_analysis ADD COLUMN setup_type TEXT DEFAULT ''")
        except Exception:
            pass  # column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_watchlist_analysis_code
            ON watchlist_analysis(code)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_watchlist_analysis_time
            ON watchlist_analysis(analyzed_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_close_cache (
                code        TEXT NOT NULL,
                date        TEXT NOT NULL,
                close       REAL,
                PRIMARY KEY (code, date)
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ---- Watchlist stocks (incremental merge, never delete) ----

def merge_watchlist_stocks(
    config: dict, stocks: list[dict], source: str = "mxapi",
) -> dict[str, Any]:
    """Incrementally merge a synced stock list into the DB.

    Existing codes are updated in place (created_at preserved); new codes are
    inserted. Stocks present locally but absent from this batch are left alone
    (never deleted) so local data survives sync shrinks.
    """
    conn = _get_conn(config)
    added = updated = 0
    synced_at = _now()
    try:
        for s in stocks:
            code = str(s.get("code", "")).strip()
            if not code:
                continue
            name = s.get("name", "")
            price = _f(s.get("price"))
            change_pct = _f(s.get("change_pct"))
            turnover = _f_or_none(s.get("turnover"))
            vol_ratio = _f_or_none(s.get("vol_ratio"))

            row = conn.execute(
                "SELECT 1 FROM watchlist_stocks WHERE code=?", (code,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE watchlist_stocks SET name=?, price=?, change_pct=?,"
                    " turnover=?, vol_ratio=?, synced_at=?, source=? WHERE code=?",
                    (name, price, change_pct, turnover, vol_ratio, synced_at, source, code),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO watchlist_stocks"
                    " (code, name, price, change_pct, turnover, vol_ratio, synced_at, source)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (code, name, price, change_pct, turnover, vol_ratio, synced_at, source),
                )
                added += 1
        conn.commit()
    finally:
        conn.close()
    return {"added": added, "updated": updated, "total": len(stocks), "synced_at": synced_at}


def get_watchlist_stocks(config: dict) -> list[dict[str, Any]]:
    """Return all locally-synced watchlist stocks (cache-fallback source)."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT code, name, price, change_pct, turnover, vol_ratio, synced_at, source"
            " FROM watchlist_stocks ORDER BY created_at ASC, code ASC"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_index_cached_codes(config: dict, source: str) -> list[dict[str, Any]]:
    """Return cached stock codes+names for a given index source, without prices."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT code, name FROM watchlist_stocks WHERE source=? ORDER BY code ASC",
            (source,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_watchlist_stock(config: dict, code: str) -> Optional[dict[str, Any]]:
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT code, name, price, change_pct, turnover, vol_ratio, synced_at"
            " FROM watchlist_stocks WHERE code=?", (code,)
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    return dict(row) if row else None


# ---- Sync log ----

def log_sync(
    config: dict, total: int, added: int, updated: int, removed: int = 0,
    source: str = "mxapi", detail_json: Optional[str] = None,
) -> int:
    """Record one sync attempt. Returns the new row id."""
    conn = _get_conn(config)
    try:
        cur = conn.execute(
            "INSERT INTO watchlist_sync_log"
            " (synced_at, total, added, updated, removed, source, detail_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (_now(), total, added, updated, removed, source, detail_json or "{}"),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_sync_history(config: dict, limit: int = 50) -> list[dict[str, Any]]:
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM watchlist_sync_log ORDER BY synced_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---- Analysis history ----

def extract_verdict(text: str) -> str:
    """Mirror the frontend's verdict extraction (app.js analyzeEmwlStockSilent).

    The prompt mandates the analysis to open with ``结论：`` — take the verdict
    token right after it. A full-body scan picks up negated mentions such as
    "不可将全口径数字等同于主力看多" and mis-classifies a 观望 conclusion as
    看多 (000933 08-21 stored 看多 though the text concluded 观望 repeatedly).
    """
    if not text:
        return "观望"
    first = text.split("\n", 1)[0].strip()
    for tag in ("**结论：", "**结论:", "结论：", "结论:"):
        if first.startswith(tag):
            probe = first[len(tag):].lstrip("* ").lstrip()
            for v in ("看多", "看空", "观望"):
                if probe.startswith(v):
                    return v
            break
    # Legacy/fallback (analyses that don't open with 结论：): order matters —
    # check 看多/买入 first, then 看空/卖出, then 观望/持有/中性, otherwise the
    # substring 看空 inside 看多…看空 can mis-fire.
    if "看多" in text or "买入" in text:
        return "看多"
    if "看空" in text or "卖出" in text:
        return "看空"
    if "观望" in text or "持有" in text or "中性" in text:
        return "观望"
    return "观望"


def save_watchlist_analysis(config: dict, result: dict) -> int:
    """Persist one analysis result as an append-only history row.

    ``result`` is the detail-endpoint dict (code, rt_*, flow_detail, analysis,
    intraday, flow_data, model_name). Returns the new row id.
    """
    code = str(result.get("code", "")).strip()
    analysis = result.get("analysis", "")
    model_name = str(result.get("model_name", "") or "")
    # Prefer explicit verdict from structured output, fall back to regex
    verdict = result.get("verdict", "") or extract_verdict(analysis)
    # setup_type comes from the structured path-classification framework; the
    # residual 观望-无明确路径 is the default when the model omitted the field.
    setup_type = str(result.get("setup_type", "") or "观望-无明确路径").strip()
    conn = _get_conn(config)
    try:
        cur = conn.execute(
            "INSERT INTO watchlist_analysis"
            " (code, analyzed_at, rt_price, rt_change, rt_change_pct, rt_high,"
            "  rt_low, rt_volume, rt_amount, flow_detail_json, analysis_text,"
            "  verdict, setup_type, intraday_json, flow_data, model_name)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                code,
                _now(),
                str(result.get("rt_price", "")),
                str(result.get("rt_change", "")),
                str(result.get("rt_change_pct", "")),
                str(result.get("rt_high", "")),
                str(result.get("rt_low", "")),
                str(result.get("rt_volume", "")),
                str(result.get("rt_amount", "")),
                json.dumps(result.get("flow_detail", []), ensure_ascii=False),
                analysis,
                verdict,
                setup_type,
                json.dumps(result.get("intraday", []), ensure_ascii=False),
                result.get("flow_data", ""),
                model_name,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row_to_result(row: dict) -> dict[str, Any]:
    """Rehydrate a DB row into a detail-endpoint-shaped dict."""
    result = {
        "code": row.get("code", ""),
        "analyzed_at": row.get("analyzed_at", ""),
        "analysis_id": row.get("id"),
        "rt_price": row.get("rt_price", ""),
        "rt_change": row.get("rt_change", ""),
        "rt_change_pct": row.get("rt_change_pct", ""),
        "rt_high": row.get("rt_high", ""),
        "rt_low": row.get("rt_low", ""),
        "rt_volume": row.get("rt_volume", ""),
        "rt_amount": row.get("rt_amount", ""),
        "analysis": row.get("analysis_text", ""),
        "verdict": row.get("verdict", ""),
        "setup_type": row.get("setup_type", ""),
        "flow_data": row.get("flow_data", ""),
        "model_name": row.get("model_name", ""),
    }
    try:
        result["flow_detail"] = json.loads(row.get("flow_detail_json", "[]") or "[]")
    except json.JSONDecodeError:
        result["flow_detail"] = []
    try:
        result["intraday"] = json.loads(row.get("intraday_json", "[]") or "[]")
    except json.JSONDecodeError:
        result["intraday"] = []
    return result


def get_watchlist_analysis_history(
    config: dict, code: str, limit: int = 50,
) -> list[dict[str, Any]]:
    """Return analysis history for a code, newest first."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM watchlist_analysis WHERE code=? ORDER BY analyzed_at DESC, id DESC LIMIT ?",
            (code, limit),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [_row_to_result(dict(r)) for r in rows]


def get_latest_watchlist_analysis(
    config: dict, code: str,
) -> Optional[dict[str, Any]]:
    history = get_watchlist_analysis_history(config, code, limit=1)
    return history[0] if history else None


def batch_get_analysis_history(
    config: dict, codes: list[str], limit: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Return {code: [history_rows]} for multiple codes in a single DB query.

    Avoids the N+1 problem in watchlist / index-constituents sync where each
    stock triggered a separate DB open/close cycle.
    """
    if not codes:
        return {}
    conn = _get_conn(config)
    result: dict[str, list[dict[str, Any]]] = {c: [] for c in codes}
    try:
        placeholders = ",".join("?" for _ in codes)
        # Get up to limit * len(codes) rows; partition in Python
        rows = conn.execute(
            f"SELECT * FROM watchlist_analysis WHERE code IN ({placeholders})"
            f" ORDER BY code, analyzed_at DESC, id DESC",
            codes,
        ).fetchall()
        parsed = [_row_to_result(dict(r)) for r in rows]
        for r in parsed:
            code = r["code"]
            if len(result.get(code, [])) < limit:
                result[code].append(r)
    except Exception:
        pass
    finally:
        conn.close()
    return result


# ---- Close-price cache (watchlist_close_cache) ----
#
# The two-pass watchlist flow computes a return for every analysis row ("buy
# at analysis-day close, compare to current price"). The old code did a
# per-stock OHLCV fetch through the vendor chain — 200 serial calls ≈ 20s.
# These helpers back that lookup with a persistent DB table keyed by
# (code, trading_day), populated in bulk from tushare full-market snapshots
# (one ``pro.daily(trade_date=...)`` call covers every watchlist stock for a
# day, ~0.34s each). Repeat runs are pure DB hits.


def _read_close_cache(
    config: dict, keys: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """Bulk-read ``(code, trade_day) -> close`` from the close cache.

    One ``IN`` query per distinct date — the date cardinality is tiny compared
    to the code count, so N stocks never translate into N queries.
    """
    if not keys:
        return {}
    dates = sorted({d for _, d in keys})
    conn = _get_conn(config)
    try:
        placeholders = ",".join("?" for _ in dates)
        rows = conn.execute(
            f"SELECT code, date, close FROM watchlist_close_cache"
            f" WHERE date IN ({placeholders})",
            dates,
        ).fetchall()
        return {(r["code"], r["date"]): r["close"] for r in rows if r["close"]}
    finally:
        conn.close()


def _write_close_cache(
    config: dict, rows: dict[tuple[str, str], float],
) -> int:
    """Write ``(code, trade_day) -> close`` rows (bulk upsert). Returns count."""
    if not rows:
        return 0
    conn = _get_conn(config)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO watchlist_close_cache (code, date, close)"
            " VALUES (?,?,?)",
            [(c, d, v) for (c, d), v in rows.items()],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _snapshot_closes_by_day(
    trade_days: list[str],
) -> dict[str, dict[str, float]]:
    """Fetch tushare full-market daily snapshots for *trade_days*.

    Returns ``{trade_day: {ts_code: close}}``. One ``pro.daily(trade_date=)``
    call per day covers every A-share stock, turning the old per-code serial
    fetch (N × ~0.10s) into a handful of ~0.34s calls. Days whose snapshot
    fails are omitted so callers fall back to the per-code path.
    """
    from quantconclave.dataflows.tushare_data import _get_pro

    pro = _get_pro()
    out: dict[str, dict[str, float]] = {}
    for day in sorted(set(trade_days)):
        try:
            df = pro.daily(trade_date=day)
        except Exception as e:
            logger.warning("tushare daily snapshot %s failed: %s", day, e)
            continue
        if df is None or df.empty:
            continue
        closes = pd.to_numeric(df["close"], errors="coerce")
        valid = closes.notna() & (closes > 0)
        out[day] = dict(zip(df["ts_code"].astype(str)[valid], closes[valid]))
    return out


def prefetch_close_cache(
    config: dict, pairs: list[tuple[str, str]],
) -> int:
    """Backfill ``watchlist_close_cache`` for ``(code, analyzed_at)`` pairs.

    Groups pairs by the last open trading day on/before ``analyzed_at``
    (``trade_cal.last_open_day``), pulls one tushare full-market snapshot per
    distinct day, then writes ``(code, trade_day) -> close`` rows. A rolling
    last-known close is carried across ascending days so a stock suspended on
    its analysis day still gets the nearest prior close — the same semantics
    as the per-code fallback. Returns the number of cache rows written.
    """
    from quantconclave.dataflows.tushare_data import _format_ticker_ts

    by_day: dict[str, list[tuple[str, str]]] = {}
    for code, analyzed_at in pairs:
        trade_day = trade_cal.last_open_day(analyzed_at)
        if trade_day:
            by_day.setdefault(trade_day, []).append((code, analyzed_at))
    if not by_day:
        return 0

    snapshots = _snapshot_closes_by_day(list(by_day.keys()))
    if not snapshots:
        return 0

    # Normalize watchlist codes to tushare ts_code format for matching.
    norm = {code: _format_ticker_ts(code) for code, _ in pairs}
    last_known: dict[str, float] = {}
    to_write: dict[tuple[str, str], float] = {}
    for day in sorted(snapshots.keys()):
        for code, close in snapshots[day].items():
            if close and close > 0:
                last_known[code] = close
        for code, _ in by_day.get(day, []):
            ts = norm[code]
            c = snapshots[day].get(ts)
            if not c or c <= 0:
                c = last_known.get(ts)
            if c and c > 0:
                to_write[(code, day)] = c
    if not to_write:
        return 0
    return _write_close_cache(config, to_write)


def attach_analysis_to_stocks(
    config: dict, stocks: list[dict],
) -> None:
    """Attach verdict + recent_analyses + return validation to stocks in-place.

    Uses batch DB queries so the cost is O(1) instead of O(N). Mutates each
    stock dict in *stocks* directly. Close-price lookups are served from the
    persistent ``watchlist_close_cache`` (bulk-populated from tushare full-
    market snapshots), falling back to the vendor chain only when the cache
    has no row for a code/date pair.
    """
    codes = [s["code"] for s in stocks if s.get("code")]
    if not codes:
        return

    history_map = batch_get_analysis_history(config, codes, limit=3)

    # ── Close-price cache: batch-populate once, then serve from memory ──
    # Every analysis row needs "close on the last trading day at/before its
    # analyzed_at". Gather all (code, trade_day) keys the loop will need, read
    # the persistent DB cache for them, and bulk-backfill anything missing via
    # prefetch_close_cache (full-market tushare snapshots).
    _close_cache: dict[tuple[str, str], float] = {}   # (code, trade_day) -> close
    _ohlcv_cache: dict[str, Any] = {}                 # code → DataFrame (fallback path)
    _needed_pairs: list[tuple[str, str]] = []          # (code, analyzed_at)
    _needed_keys: list[tuple[str, str]] = []           # (code, trade_day)
    for s in stocks:
        code = s["code"]
        for row in history_map.get(code, [])[:3]:
            analyzed_at = row.get("analyzed_at")
            if not analyzed_at:
                continue
            _needed_pairs.append((code, analyzed_at))
            trade_day = trade_cal.last_open_day(analyzed_at)
            if trade_day:
                _needed_keys.append((code, trade_day))
    if _needed_keys:
        _close_cache.update(_read_close_cache(config, _needed_keys))
    if _needed_pairs:
        to_prefetch = []
        for (c, a) in _needed_pairs:
            td = trade_cal.last_open_day(a)
            if td and (c, td) not in _close_cache:
                to_prefetch.append((c, a))
        if to_prefetch:
            prefetch_close_cache(config, to_prefetch)
            _close_cache.update(_read_close_cache(config, _needed_keys))

    def _remember_close(code: str, trade_day: str | None, close: float) -> None:
        """Write a fallback close into the in-memory + DB caches."""
        if trade_day:
            _close_cache[(code, trade_day)] = close
            _write_close_cache(config, {(code, trade_day): close})

    def _cached_close(code: str, date_str: str) -> float | None:
        """Return close price on date, using the DB-backed cache first."""
        import datetime as _dt
        from quantconclave.backtest.data import parse_ohlcv_csv
        from quantconclave.dataflows.interface import route_to_vendor

        try:
            target = _dt.date.fromisoformat(date_str[:10])
        except ValueError:
            return None

        # 1) Persistent cache (keyed by trading day) — the common path.
        trade_day = trade_cal.last_open_day(date_str)
        if trade_day:
            hit = _close_cache.get((code, trade_day))
            if hit:
                return hit

        # 2) Fallback: per-code vendor fetch (rare after batch prefetch).
        #    Reuse the in-memory DataFrame per code, then write the close back
        #    to the persistent cache so future runs are DB hits.
        target_ts = pd.Timestamp(target)
        df = _ohlcv_cache.get(code)
        if df is not None and not df.empty:
            df_dates = pd.to_datetime(df["date"])
            if df_dates.min() <= target_ts <= df_dates.max():
                past = df[df["date"] <= target_ts]
                if not past.empty:
                    close = float(past.iloc[-1]["close"])
                    _remember_close(code, trade_day, close)
                    return close

        start = (target - _dt.timedelta(days=60)).isoformat()
        end = (target + _dt.timedelta(days=5)).isoformat()
        try:
            raw = route_to_vendor("get_stock_data", code,
                                  start_date=start, end_date=end)
            df = parse_ohlcv_csv(str(raw))
        except Exception:
            df = None
        _ohlcv_cache[code] = df
        if df is None or df.empty:
            return None
        past = df[df["date"] <= target_ts]
        close = float(past.iloc[-1]["close"]) if not past.empty else float(df.iloc[0]["close"])
        _remember_close(code, trade_day, close)
        return close

    def _cached_return(code: str, analyzed_at: str, verdict: str,
                       current_price: float | None) -> dict | None:
        """Compute return using cached close and provided current price."""
        if not analyzed_at:
            return None
        close_price = _cached_close(code, analyzed_at)
        if not close_price or close_price <= 0:
            return None
        if current_price is None or current_price <= 0:
            return None
        if verdict and "看空" in str(verdict):
            return_pct = round((close_price - current_price) / close_price * 100.0, 2)
        elif verdict and "观望" in str(verdict):
            return None
        else:
            return_pct = round((current_price - close_price) / close_price * 100.0, 2)
        return {
            "analyzed_at": analyzed_at,
            "buy_price": round(close_price, 2),
            "sell_price": round(current_price, 2),
            "return_pct": return_pct,
        }

    for s in stocks:
        code = s["code"]
        current_price = s.get("price") or 0  # already fetched via batch Tencent
        history = history_map.get(code, [])
        latest = history[0] if history else None
        s["verdict"] = latest["verdict"] if latest else ""
        s["setup_type"] = latest["setup_type"] if latest else ""
        s["last_analyzed_at"] = latest["analyzed_at"] if latest else None
        ret = _cached_return(code, latest["analyzed_at"], latest.get("verdict", ""),
                             current_price) if latest and latest["analyzed_at"] else None
        s["return_pct"] = ret["return_pct"] if ret else None
        s["buy_price"] = ret["buy_price"] if ret else None
        s["sell_price"] = ret["sell_price"] if ret else None
        recent = []
        for row in history[:3]:
            r = _cached_return(code, row["analyzed_at"], row.get("verdict", ""),
                               current_price)
            recent.append({
                "analyzed_at": row["analyzed_at"],
                "verdict": row.get("verdict", ""),
                "setup_type": row.get("setup_type", ""),
                "model_name": row.get("model_name", ""),
                "return_pct": r["return_pct"] if r else None,
                "buy_price": r["buy_price"] if r else None,
                "sell_price": r["sell_price"] if r else None,
            })
        s["recent_analyses"] = recent


# ---- Analysis return validation ----

def _fetch_close_on_date(code: str, date_str: str) -> Optional[float]:
    """Fetch the closing price on a given date (or nearest prior trading day).

    Uses the vendor chain (tushare/akshare) historical daily bars. Returns
    None when the data is unavailable.
    """
    from quantconclave.backtest.data import parse_ohlcv_csv
    from quantconclave.dataflows.interface import route_to_vendor
    import datetime

    try:
        target = datetime.date.fromisoformat(date_str[:10])
    except ValueError:
        return None

    # Fetch a window ending at the analysis date (a few days back to survive
    # weekends/holidays, a few forward in case the analysis was on a weekend).
    start = (target - datetime.timedelta(days=15)).isoformat()
    end = (target + datetime.timedelta(days=3)).isoformat()
    try:
        raw = route_to_vendor("get_stock_data", code,
                              start_date=start, end_date=end)
        df = parse_ohlcv_csv(str(raw))
    except Exception:
        return None
    if df is None or df.empty:
        return None

    # Find the closest row on or before the analysis date.
    target_ts = pd.Timestamp(target)
    past = df[df["date"] <= target_ts]
    if past.empty:
        # No trading day on/before the analysis date → use the earliest row.
        row = df.iloc[0]
    else:
        row = past.iloc[-1]
    return float(row["close"])


def _fetch_current_price(code: str) -> Optional[float]:
    """Fetch the latest real-time price from Tencent (qt.gtimg.cn)."""
    import requests
    try:
        from quantconclave.dataflows.tencent_realtime import _normalize_symbol
        norm = _normalize_symbol(code)
        resp = requests.get(f"http://qt.gtimg.cn/q={norm}", timeout=6)
        resp.encoding = "gbk"
        fld = resp.text.split('="')[1].rstrip('";\n').split("~") if '="' in resp.text else []
        if len(fld) > 3:
            return float(fld[3])
    except Exception:
        pass
    return None


def compute_analysis_return(
    config: dict, code: str, analyzed_at: str, verdict: str = "",
) -> Optional[dict[str, Any]]:
    """Compute return for one analysis, respecting verdict direction.

    - 看多: long — buy at analysis-day close, sell at current price
      return = (current - close) / close * 100  (positive = correct call)
    - 看空: short — sell at analysis-day close, buy back at current price
      return = (close - current) / close * 100  (positive = correct call)
    - 观望: skip — returns None (no trade to validate)

    Returns ``{"analyzed_at", "buy_price", "sell_price", "return_pct"}`` or None.
    """
    close_price = _fetch_close_on_date(code, analyzed_at)
    current_price = _fetch_current_price(code)
    if not close_price or not current_price or close_price <= 0:
        return None
    if verdict and "看空" in str(verdict):
        # Bearish: correct if price dropped — sold high, bought back low
        return_pct = round((close_price - current_price) / close_price * 100.0, 2)
    elif verdict and "观望" in str(verdict):
        # Neutral: no trade, no return to compute
        return None
    else:
        # Bullish (default): correct if price rose — bought low, sold high
        return_pct = round((current_price - close_price) / close_price * 100.0, 2)
    return {
        "analyzed_at": analyzed_at,
        "buy_price": round(close_price, 2),
        "sell_price": round(current_price, 2),
        "return_pct": return_pct,
    }
