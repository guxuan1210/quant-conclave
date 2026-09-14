"""Trading-day calendar helpers backed by tushare ``trade_cal``.

Centralizes the "last open trading day" lookup used by the watchlist
close-price cache (``attach_analysis_to_stocks``) and the moneyflow cache
miss detection. The calendar is fetched once per day and cached in-memory so
the N+1 watchlist loop doesn't hit tushare's rate limit once per stock.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_cal: dict[str, set[str]] = {"asof": "", "open_days": set()}


def _fetch_open_days() -> set[str]:
    """Fetch the set of open SSE trading days (YYYYMMDD), tushare then AKShare."""
    days = _fetch_open_days_tushare()
    if days:
        return days
    return _fetch_open_days_akshare()


def _fetch_open_days_tushare() -> set[str]:
    """Open days from tushare ``trade_cal`` (empty on failure)."""
    try:
        from quantconclave.dataflows.tushare_data import _get_pro
        pro = _get_pro()
        today = datetime.now().strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
        return set(cal[cal["is_open"] == 1]["cal_date"].astype(str))
    except Exception as e:
        logger.warning("trade_cal fetch failed: %s", e)
        return set()


def _fetch_open_days_akshare() -> set[str]:
    """Open days from AKShare (sina) — empty on failure."""
    try:
        from quantconclave.dataflows.akshare_data import get_trade_calendar_akshare
        return get_trade_calendar_akshare()
    except Exception as e:
        logger.warning("akshare trade_cal fetch failed: %s", e)
        return set()


def get_open_days() -> set[str]:
    """Return open trading days (YYYYMMDD), cached per calendar day."""
    today = datetime.now().strftime("%Y%m%d")
    if _cal["asof"] != today or not _cal["open_days"]:
        _cal["open_days"] = _fetch_open_days()
        _cal["asof"] = today
    return _cal["open_days"]


def last_open_day(date_str: str | None = None) -> str | None:
    """Return the last open trading day on or before *date_str* (YYYYMMDD).

    *date_str* may be ``YYYY-MM-DD`` or ``YYYYMMDD`` and defaults to today.
    Returns ``None`` when the calendar is unavailable so callers can fall back
    to a conservative per-code path instead of guessing a trading day.
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")
    # Normalize both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS" (analyzed_at) forms
    d = date_str.strip().replace("-", "")[:8]
    open_days = get_open_days()
    if not open_days:
        return None
    # Walk back up to 14 calendar days to survive long holidays (Spring Festival etc.).
    dt = datetime.strptime(d, "%Y%m%d")
    for _ in range(15):
        if dt.strftime("%Y%m%d") in open_days:
            return dt.strftime("%Y%m%d")
        dt -= timedelta(days=1)
    return None


def is_first_trading_day_of_week(date_str: str | None = None) -> bool:
    """Whether *date_str* (default today) is the first open day of its ISO week.

    The weekly evaluation job fires Mon–Fri at 16:30 and uses this guard so it
    only actually runs once per week (the first trading day), even when the
    week starts on a Tuesday due to a Monday holiday. A non-open day is always
    False.
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")
    d = date_str.strip().replace("-", "")[:8]
    open_days = get_open_days()
    if not open_days or d not in open_days:
        return False
    dt = datetime.strptime(d, "%Y%m%d")
    monday = dt - timedelta(days=dt.weekday())
    week_start = monday.strftime("%Y%m%d")
    week_open = [x for x in open_days if week_start <= x <= d]
    return min(week_open) == d
