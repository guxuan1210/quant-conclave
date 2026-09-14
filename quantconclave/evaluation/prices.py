"""Price + trading-day primitives for evaluation settlement.

The settlement spec is: enter at the *next* trading day's qfq-adjusted open, exit
at the Nth trading day's qfq-adjusted close (N in {5,20,60}), and compare against
a 50/50 CSI300/CSI500 benchmark over the same window.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def nth_trading_day(anchor_date: str, n: int, config: dict | None = None) -> str:
    """Return the date ``n`` trading days after ``anchor_date`` (anchor = day 0).

    Uses the shared trade calendar (tushare then AKShare) when available,
    anchoring at the first open day >= ``anchor_date`` then advancing ``n`` open
    days. Falls back to a calendar estimate only when the calendar is entirely
    unreachable, so settlement never deadlocks.
    """
    try:
        from web.trade_cal import get_open_days
        open_days = get_open_days()
        if open_days:
            start = anchor_date.replace("-", "")[:8]
            ordered = sorted(open_days)
            idx = 0
            for i, d in enumerate(ordered):
                if d >= start:
                    idx = i
                    break
            if idx + n < len(ordered):
                d = ordered[idx + n]
                return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    except Exception:
        pass
    from quantconclave.hot_tracker.settlement import compute_settle_date
    return compute_settle_date(anchor_date, trading_days=n, config=config)


def fetch_adjusted_prices(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Return qfq-adjusted daily bars as a date-indexed DataFrame (open/close columns).

    Calls AKShare directly (its ``get_stock_data_akshare`` returns qfq 前复权).
    Missing adjusted data is left unsettled for a later retry; an unadjusted
    fallback would silently corrupt official returns around corporate actions.
    """
    from quantconclave.backtest.data import parse_ohlcv_csv
    from quantconclave.dataflows.akshare_data import get_stock_data_akshare

    try:
        raw = get_stock_data_akshare(ticker, start_date=start_date, end_date=end_date)
    except Exception as exc:
        logger.warning("qfq price fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()
    if not raw or str(raw).startswith("# SKIP_VENDOR"):
        return pd.DataFrame()
    df = parse_ohlcv_csv(str(raw))
    if df.empty:
        return df
    return df


def fetch_index_series(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Return an index daily series (date-indexed, ``close`` column).

    Tries tushare ``index_daily`` first, then AKShare (sina) as a fallback when
    the tushare token lacks the index interface. Returns an empty frame on total
    failure so the benchmark leg degrades to "unsettled" rather than raising.
    """
    df = _fetch_index_series_tushare(index_code, start_date, end_date)
    if not df.empty:
        return df
    return _fetch_index_series_akshare(index_code, start_date, end_date)


def _fetch_index_series_tushare(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    from quantconclave.dataflows.tushare_data import _get_pro

    try:
        pro = _get_pro()
        df = pro.index_daily(
            ts_code=index_code,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
    except Exception as e:
        logger.warning("tushare index_daily failed for %s: %s", index_code, e)
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    return df[["date", "close"]].copy()


def _fetch_index_series_akshare(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    from quantconclave.dataflows.akshare_data import get_index_daily_akshare

    try:
        return get_index_daily_akshare(index_code, start_date, end_date)
    except Exception as e:
        logger.warning("akshare index daily failed for %s: %s", index_code, e)
        return pd.DataFrame()


def _date_of(df: pd.DataFrame, date_str: str):
    """Locate the row matching ``date_str`` (``YYYY-MM-DD``) in a date-indexed frame."""
    if df.empty:
        return None
    d = pd.to_datetime(date_str)
    if "date" in df.columns:
        mask = df["date"].dt.normalize() == d.normalize()
        rows = df[mask]
    else:
        mask = df.index.normalize() == d.normalize()
        rows = df[mask]
    if rows.empty:
        return None
    return rows.iloc[0]


def price_at(df: pd.DataFrame, date_str: str, field: str):
    """Return ``field`` (open/close) on ``date_str``, or None if missing."""
    row = _date_of(df, date_str)
    if row is None or field not in row.index:
        return None
    val = row[field]
    try:
        val = float(val)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None
