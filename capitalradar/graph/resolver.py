"""Batch outcome tracker: resolve ALL pending memory_log entries."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import yfinance as yf

logger = logging.getLogger(__name__)

def fetch_returns(ticker: str, trade_date: str, holding_days: int = 60) -> tuple:
    """Fetch raw and alpha return for ticker over holding_days from trade_date."""
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end = start + timedelta(days=holding_days + 7)
        end_str = end.strftime("%Y-%m-%d")
        stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
        if len(stock) < 2:
            return None, None, None
        actual_days = min(holding_days, len(stock) - 1)
        raw = float((stock["Close"].iloc[actual_days] - stock["Close"].iloc[0]) / stock["Close"].iloc[0])
        # Try benchmark for alpha
        bench = yf.Ticker("SPY").history(start=trade_date, end=end_str)
        if len(bench) >= 2:
            bd = min(actual_days, len(bench) - 1)
            bench_ret = float((bench["Close"].iloc[bd] - bench["Close"].iloc[0]) / bench["Close"].iloc[0])
            alpha = raw - bench_ret
        else:
            alpha = None
        return raw, alpha, actual_days
    except Exception as e:
        logger.warning("fetch_returns failed for %s on %s: %s", ticker, trade_date, e)
        return None, None, None

def resolve_all_pending(config: dict) -> dict:
    """Resolve ALL pending memory_log entries by fetching price data."""
    from capitalradar.agents.utils.memory import CapitalRadarMemoryLog
    ml = CapitalRadarMemoryLog(config)
    pending = ml.get_pending_entries()
    if not pending:
        return {"resolved": 0, "total_pending": 0}
    updates = []
    for entry in pending:
        raw, alpha, days = fetch_returns(entry["ticker"], entry["date"])
        if raw is not None:
            direction = "profit" if raw > 0 else "loss"
            reflection = (
                f"Auto-resolved at T+{days}d. "
                f"Raw return: {raw*100:.1f}%. "
                f"The decision was a {direction} outcome."
            )
            updates.append({
                "ticker": entry["ticker"],
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })
    if updates:
        ml.batch_update_with_outcomes(updates)
    return {"resolved": len(updates), "total_pending": len(pending)}
