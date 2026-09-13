"""Reversal metrics — shared 180-trading-day bottom window for the stock-picking
agent's oversold-rebound / bottom-fishing tools.

Bottom-fishing must be measured against a REAL bottom — at least 180 trading
days — never a truncated 20/40/60-day window that can masquerade as a bottom.
Every caller validates data adequacy before trusting the numbers:

  fetch_reversal_metrics(...) -> {"ok": True, <metrics>}   real 180d bottom data
                              -> {"ok": False, "reason": "insufficient_history",
                                   "n_bars": n}             stock too young for a 180d bottom
                              -> {"ok": False, "reason": "fetch_failed"}  vendor error

Callers must treat ``ok: False`` as an exclusion (surfacing the reason), not as
a signal to fall back to a shorter window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from capitalradar.backtest.data import parse_ohlcv_csv
from capitalradar.dataflows.interface import route_to_vendor

logger = logging.getLogger(__name__)

# ~300 calendar days ≈ 205 trading days — headroom over the 180-bar floor.
DEFAULT_CALENDAR_LOOKBACK = 300
MIN_BARS = 180


def fetch_reversal_metrics(
    code: str,
    trade_date: str,
    config: dict | None = None,
    min_bars: int = MIN_BARS,
    calendar_lookback: int = DEFAULT_CALENDAR_LOOKBACK,
) -> dict:
    """Fetch OHLCV and compute 180-day bottom metrics for *code*.

    ``trade_date`` is the analysis date (YYYY-MM-DD). ``config`` is accepted
    for signature symmetry with other dataflow helpers but not required by the
    vendor chain.

    Returns ``{"ok": True, ...}`` with the metrics, or ``{"ok": False,
    "reason": ...}`` (see module docstring) so callers can report the exclusion
    explicitly instead of silently measuring a truncated window.
    """
    try:
        end = datetime.strptime(trade_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        end = datetime.now()
    start = end - timedelta(days=calendar_lookback)

    try:
        raw = route_to_vendor(
            "get_stock_data", code,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("reversal_metrics: get_stock_data failed for %s: %s", code, e)
        return {"ok": False, "reason": "fetch_failed", "n_bars": 0, "ts_code": code}

    df = parse_ohlcv_csv(str(raw))
    if df is None or df.empty or "close" not in df.columns:
        return {"ok": False, "reason": "fetch_failed", "n_bars": 0, "ts_code": code}

    closes = df["close"].astype(float).tolist()
    n = len(closes)
    if n < min_bars:
        return {"ok": False, "reason": "insufficient_history",
                "n_bars": n, "ts_code": code}

    current = float(closes[-1])
    if current <= 0:
        return {"ok": False, "reason": "fetch_failed", "n_bars": n, "ts_code": code}

    low180 = float(min(closes[-180:]))
    high180 = float(max(closes[-180:]))
    low60 = float(min(closes[-60:]))
    ma20 = float(sum(closes[-20:]) / 20)

    # RSI(14) — canonical implementation from the quant package.
    try:
        from capitalradar.quant.indicators import rsi as _quant_rsi
        rsi_series = _quant_rsi(df, 14)
        rsi_14 = float(rsi_series.iloc[-1]) if len(rsi_series) else 50.0
    except Exception:
        rsi_14 = 50.0

    # Volume ratio: last 5 days vs the previous 20 days.
    vol_ratio_5d = 1.0
    try:
        volumes = df["volume"].astype(float).tolist()
        if n >= 25:
            recent = sum(volumes[-5:]) / 5
            base = sum(volumes[-25:-5]) / 20
            if base > 0:
                vol_ratio_5d = round(recent / base, 2)
    except Exception:
        pass

    def _pct_above(a: float, base: float) -> float:
        return round((a - base) / base * 100, 1) if base > 0 else 0.0

    def _pct_below(a: float, top: float) -> float:
        return round((top - a) / top * 100, 1) if top > 0 else 0.0

    return {
        "ok": True,
        "ts_code": code,
        "current": round(current, 2),
        "low180": round(low180, 2),
        "high180": round(high180, 2),
        "low60": round(low60, 2),
        "ma20": round(ma20, 2),
        "rsi_14": round(rsi_14, 1),
        "n_bars": n,
        "volume_ratio_5d": vol_ratio_5d,
        "price_vs_180d_low": round(current / low180, 3) if low180 > 0 else None,
        "price_vs_180d_high": round(current / high180, 3) if high180 > 0 else None,
        "price_vs_60d_low": round(current / low60, 3) if low60 > 0 else None,
        "price_vs_ma20": round(current / ma20, 3) if ma20 > 0 else None,
        "rally_from_low": _pct_above(current, low180),
        "drop_from_high": _pct_below(current, high180),
    }
