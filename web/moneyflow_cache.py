"""Money flow data cache — persist and reuse tushare moneyflow data locally.

Avoids redundant tushare API calls by caching daily money flow records
per stock in the shared results.db.  New data is appended incrementally;
existing records are left untouched (INSERT OR IGNORE).

Table: moneyflow_cache
  - ts_code: stock code (e.g. 000001.SZ)
  - trade_date: YYYYMMDD
  - net_amount, buy_elg_amount, sell_elg_amount, ... (all moneyflow fields)
  - synced_at: when this row was cached
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Optional

from web.results_store import _get_conn

logger = logging.getLogger(__name__)

# (code, expected_latest_trade_day) -> date(YYYYMMDD) of the last fetch attempt
# that returned no new rows. Bounds refetching to once per code per day even
# when today's moneyflow data isn't published yet.
_last_fetch_attempt: dict[tuple[str, str], str] = {}

# Columns we cache from tushare moneyflow
_MF_COLUMNS = [
    "ts_code", "trade_date",
    "net_amount",
    "buy_elg_amount", "sell_elg_amount",
    "buy_lg_amount", "sell_lg_amount",
    "buy_md_amount", "sell_md_amount",
    "buy_sm_amount", "sell_sm_amount",
]


def init_moneyflow_cache(config: dict) -> None:
    """Create the moneyflow_cache table (idempotent)."""
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS moneyflow_cache (
                ts_code     TEXT NOT NULL,
                trade_date  TEXT NOT NULL,
                net_amount  REAL,
                buy_elg_amount  REAL,
                sell_elg_amount REAL,
                buy_lg_amount   REAL,
                sell_lg_amount  REAL,
                buy_md_amount   REAL,
                sell_md_amount  REAL,
                buy_sm_amount   REAL,
                sell_sm_amount  REAL,
                synced_at   TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (ts_code, trade_date)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mf_cache_code
            ON moneyflow_cache(ts_code)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mf_cache_date
            ON moneyflow_cache(trade_date)
        """)
        # 2026-08-27 tushare moneyflow unit fix (万元→元): rows cached before this
        # change are 万元-scale while new fetches are 元 — a mixed-scale cache is
        # garbage. Purge once (user_version<2) and let the table rebuild from
        # tushare on the next fetch. Brand-new DBs are empty so the DELETE is a no-op.
        if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
            conn.execute("DELETE FROM moneyflow_cache")
            conn.execute("PRAGMA user_version = 2")
        conn.commit()
    finally:
        conn.close()


def get_cached_moneyflow(
    config: dict, code: str, start_date: str, end_date: str,
) -> list[dict]:
    """Get cached money flow data for a stock in a date range.

    Returns rows as dicts with keys matching tushare moneyflow CSV fields.
    Returns empty list if no cached data.
    """
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            "SELECT * FROM moneyflow_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ?"
            " ORDER BY trade_date ASC",
            (code, start_date.replace("-", ""), end_date.replace("-", "")),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_latest_cached_date(config: dict, code: str) -> Optional[str]:
    """Return the most recent trade_date we have cached for a stock, or None."""
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM moneyflow_cache WHERE ts_code=?",
            (code,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def save_moneyflow_csv(
    config: dict, code: str, csv_text: str,
) -> int:
    """Parse a tushare moneyflow CSV response and cache rows.

    Uses INSERT OR IGNORE so duplicate (code, trade_date) pairs are skipped.
    Returns number of NEW rows inserted.
    """
    lines = csv_text.split("\n")
    csv_start = next((i for i, l in enumerate(lines)
                      if "ts_code" in l and "trade_date" in l), None)
    if csv_start is None:
        return 0

    reader = csv.DictReader(io.StringIO("\n".join(lines[csv_start:])))
    conn = _get_conn(config)
    inserted = 0
    try:
        for row in reader:
            td = row.get("trade_date", "").strip()
            if not td:
                continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO moneyflow_cache"
                    " (ts_code, trade_date, net_amount, buy_elg_amount, sell_elg_amount,"
                    "  buy_lg_amount, sell_lg_amount, buy_md_amount, sell_md_amount,"
                    "  buy_sm_amount, sell_sm_amount)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        code,
                        td,
                        float(row.get("net_amount", 0) or 0),
                        float(row.get("buy_elg_amount", 0) or 0),
                        float(row.get("sell_elg_amount", 0) or 0),
                        float(row.get("buy_lg_amount", 0) or 0),
                        float(row.get("sell_lg_amount", 0) or 0),
                        float(row.get("buy_md_amount", 0) or 0),
                        float(row.get("sell_md_amount", 0) or 0),
                        float(row.get("buy_sm_amount", 0) or 0),
                        float(row.get("sell_sm_amount", 0) or 0),
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except (ValueError, KeyError):
                pass
        conn.commit()
    finally:
        conn.close()
    return inserted


def fetch_or_cache_moneyflow(
    config: dict, code: str, start_date: str, end_date: str,
) -> str:
    """Get money flow data, using cache when possible.

    Always fetches a wide window (90 days) from tushare to fill ALL gaps
    in the local cache, then returns the requested range from cache.
    Database persistence is the priority — every fetch writes to DB.
    """
    # Always check what's missing in our cache for a wide window
    fetch_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    fetch_end = end_date

    # Check if cache is complete for the full window
    cached_full = get_cached_moneyflow(config, code, fetch_start, fetch_end)
    latest_cached = get_latest_cached_date(config, code)

    # Fetch only when the last *trading day* (not the raw calendar date) is
    # missing from cache. Comparing against "today" made every stock refetch a
    # 90-day window on weekends, holidays and before the day's data is
    # published — the two-pass watchlist hit that per stock.
    from web.trade_cal import last_open_day
    expected_latest = last_open_day(fetch_end) or fetch_end.replace("-", "")
    need_fetch = not latest_cached or latest_cached < expected_latest

    # Same-day memo: if we already tried fetching up to this trading day today
    # and got no new rows (data not published yet), don't hammer tushare again
    # for every stock in the same process.
    attempt_key = (code, expected_latest)
    today = datetime.now().strftime("%Y%m%d")
    if need_fetch and _last_fetch_attempt.get(attempt_key) == today:
        need_fetch = False

    if need_fetch:
        try:
            from quantconclave.agents.utils.capital_flow_tools import get_money_flow
            raw = str(get_money_flow.invoke({
                "ticker": code,
                "start_date": fetch_start,
                "end_date": fetch_end,
            }))
            new_count = save_moneyflow_csv(config, code, raw)
            if new_count > 0:
                logger.info("Moneyflow cache: %s +%d new rows (fetched %s~%s)",
                           code, new_count, fetch_start, fetch_end)
            else:
                _last_fetch_attempt[attempt_key] = today
            # Re-read full cache
            cached_full = get_cached_moneyflow(config, code, fetch_start, fetch_end)
        except Exception as e:
            _last_fetch_attempt[attempt_key] = today
            logger.warning("Moneyflow fetch failed for %s: %s — using cache only", code, e)

    # Return only the requested range from cache
    cached = get_cached_moneyflow(config, code, start_date, end_date)
    if not cached:
        return ""

    lines = [
        f"# Money Flow (主力资金流向) for {code} from {start_date} to {end_date}",
        f"# Source: tushare moneyflow (cached locally, {len(cached_full)} total cached)",
        f"# Rows in range: {len(cached)} trading days",
        "",
        "ts_code,trade_date,net_amount,buy_elg_amount,buy_elg_vol,sell_elg_amount,sell_elg_vol,buy_lg_amount,buy_lg_vol,sell_lg_amount,sell_lg_vol,buy_md_amount,buy_md_vol,sell_md_amount,sell_md_vol,buy_sm_amount,buy_sm_vol,sell_sm_amount,sell_sm_vol",
    ]
    for r in cached:
        vals = [
            r["ts_code"], r["trade_date"],
            f'{r["net_amount"]:.0f}' if r["net_amount"] else "0",
            f'{r["buy_elg_amount"]:.0f}' if r["buy_elg_amount"] else "0", "0",
            f'{r["sell_elg_amount"]:.0f}' if r["sell_elg_amount"] else "0", "0",
            f'{r["buy_lg_amount"]:.0f}' if r["buy_lg_amount"] else "0", "0",
            f'{r["sell_lg_amount"]:.0f}' if r["sell_lg_amount"] else "0", "0",
            f'{r["buy_md_amount"]:.0f}' if r["buy_md_amount"] else "0", "0",
            f'{r["sell_md_amount"]:.0f}' if r["sell_md_amount"] else "0", "0",
            f'{r["buy_sm_amount"]:.0f}' if r["buy_sm_amount"] else "0", "0",
            f'{r["sell_sm_amount"]:.0f}' if r["sell_sm_amount"] else "0", "0",
        ]
        lines.append(",".join(vals))

    return "\n".join(lines)
