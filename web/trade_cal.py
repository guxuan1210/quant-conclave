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
    """Fetch the set of open SSE trading days (YYYYMMDD) from tushare."""
    try:
        from capitalradar.dataflows.tushare_data import _get_pro
        pro = _get_pro()
        today = datetime.now().strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
        return set(cal[cal["is_open"] == 1]["cal_date"].astype(str))
    except Exception as e:
        logger.warning("trade_cal fetch failed: %s", e)
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
