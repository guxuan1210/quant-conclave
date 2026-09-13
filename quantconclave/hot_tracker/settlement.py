"""Settlement logic for tracked hot picks (PR1).

Primitives: compute the 20-trading-day settle date and classify a tracked
pick's outcome. The daily monitor (PR3) calls these — PR1 only builds the
pure functions so they can be unit-tested without a running scheduler.
"""

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Status values used across hot_tracker_picks.status
PENDING = "PENDING"
SETTLED = "SETTLED"
STOPPED = "STOPPED"
PROFIT_TAKEN = "PROFIT_TAKEN"


def compute_settle_date(pick_date: str, trading_days: int = 20, config: dict | None = None) -> str:
    """Settlement date = pick_date + 20 TRADING days (YYYY-MM-DD).

    Uses the tushare trade calendar when available. Falls back to a calendar
    estimate (~1.45 natural days per trading day) if the calendar is
    unreachable, so the tracker never deadlocks on a missing calendar.
    """
    try:
        import os

        import tushare as ts

        token = os.environ.get("TUSHARE_TOKEN", "")
        pro = ts.pro_api(token)
        start = pick_date.replace("-", "")
        end = (datetime.strptime(pick_date, "%Y-%m-%d") + timedelta(days=120)).strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
        open_dates = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())

        # Anchor at the first open day on/after pick_date, then advance N more.
        idx = 0
        for i, d in enumerate(open_dates):
            if d >= start:
                idx = i
                break
        if idx + trading_days < len(open_dates):
            settle = open_dates[idx + trading_days]
            return f"{settle[:4]}-{settle[4:6]}-{settle[6:8]}"
    except Exception as e:
        logger.warning("trade_cal unavailable, estimating settle date: %s", e)

    est = datetime.strptime(pick_date, "%Y-%m-%d") + timedelta(days=int(trading_days * 1.45))
    return est.strftime("%Y-%m-%d")


def classify_settlement(ret_pct: float | None, settle_date: str, today: str | None = None,
                        stop_loss_pct: float = -8.0, take_profit_pct: float = 20.0) -> tuple[str, bool]:
    """Classify a tracked pick given its current return since pick.

    Priority: take-profit > stop-loss > maturity settlement > still PENDING.
    Returns ``(status, is_terminal)`` — terminal statuses are SETTLED /
    STOPPED / PROFIT_TAKEN and stop the daily monitor for that pick.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    if ret_pct is None:
        return PENDING, False
    if ret_pct >= take_profit_pct:
        return PROFIT_TAKEN, True
    if ret_pct <= stop_loss_pct:
        return STOPPED, True
    if today >= settle_date:
        return SETTLED, True
    return PENDING, False


def append_track_entry(track_json: str | None, entry: dict) -> str:
    """Append a daily snapshot entry to hot_tracker_picks.daily_track_json."""
    try:
        rows = json.loads(track_json) if track_json else []
    except (ValueError, TypeError):
        rows = []
    rows.append(entry)
    return json.dumps(rows, ensure_ascii=False)
