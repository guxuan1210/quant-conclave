"""RPS (Relative Price Strength) — O'Neil-style market-relative momentum.

Calculates the percentile rank of a stock's price return over a given
lookback period against ALL other A-share stocks. RPS > 90 means the
stock outperformed 90% of the market.

Key insight: RPS is a TRUE market-relative measure, unlike RSI/MACD
which only look at the stock's own price history. Market leaders
almost always show RPS > 85 before major moves.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_RPS_CACHE: dict[str, dict[str, float | None]] = {}  # {date_lookback: {ts_code: rps_value}}


def compute_rps(lookback_days: int = 120) -> dict[str, float | None]:
    """Compute RPS percentile for all A-share stocks over the given lookback.

    Returns {ts_code: rps_value (0-100)} where values > 90 are market leaders.
    Cached per (date, lookback) to avoid redundant API calls.
    """
    import tushare as ts

    today = datetime.now().strftime("%Y%m%d")
    cache_key = f"{today}_{lookback_days}"
    if cache_key in _RPS_CACHE:
        return _RPS_CACHE[cache_key]

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        logger.warning("TUSHARE_TOKEN not set — RPS unavailable")
        return {}

    pro = ts.pro_api(token)

    # Get all active stocks
    try:
        stocks_df = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
    except Exception as e:
        logger.warning("stock_basic failed for RPS: %s", e)
        return {}

    all_codes = stocks_df["ts_code"].tolist()
    if not all_codes:
        return {}

    # Calculate start date (~lookback days + buffer for weekends/holidays)
    start_date = (datetime.now() - timedelta(days=lookback_days + 30)).strftime("%Y%m%d")
    end_date = today

    returns = {}

    # Batch-fetch daily data in chunks of 500 (Tushare limit)
    import time
    logger.info("Computing RPS for %d stocks (lookback=%d days)...", len(all_codes), lookback_days)

    for i in range(0, len(all_codes), 500):
        batch = all_codes[i:i + 500]
        try:
            df = pro.daily(
                ts_code=",".join(batch),
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,close",
            )
        except Exception as e:
            logger.warning("RPS daily batch failed at %d: %s", i, e)
            time.sleep(1)
            continue

        if df is None or df.empty:
            continue

        df = df.sort_values("trade_date")
        for code, grp in df.groupby("ts_code"):
            if len(grp) < 2:
                continue
            grp_sorted = grp.sort_values("trade_date")
            first_close = float(grp_sorted.iloc[0]["close"])
            last_close = float(grp_sorted.iloc[-1]["close"])
            if first_close > 0:
                pct_change = (last_close - first_close) / first_close * 100
                returns[code] = round(pct_change, 4)

        time.sleep(0.3)  # rate limit

    if not returns:
        return {}

    # Sort by return and assign percentile
    sorted_returns = sorted(returns.items(), key=lambda x: x[1])
    n = len(sorted_returns)
    result: dict[str, float | None] = {}
    for rank, (code, _) in enumerate(sorted_returns):
        result[code] = round((rank / (n - 1)) * 100, 1)

    # Fill None for stocks without data
    for code in all_codes:
        if code not in result:
            result[code] = None

    _RPS_CACHE[cache_key] = result
    logger.info("RPS computed: %d stocks, %d with RPS>90", n,
                sum(1 for v in result.values() if v is not None and v > 90))
    return result


def get_rps_for_stock(ts_code: str, lookback_days: int = 120) -> float | None:
    """Get RPS value for a single stock."""
    rps_map = compute_rps(lookback_days)
    return rps_map.get(ts_code)
