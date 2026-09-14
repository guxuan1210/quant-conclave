"""Precise-period settlement for evaluation predictions.

Settles a prediction at the Nth trading day (N in {5,20,60}): enter at the next
trading day's qfq open, exit at the Nth trading day's qfq close, subtract a fixed
round-trip friction, and subtract the 50/50 CSI300/CSI500 benchmark return over
the same window to get the excess return.

This deliberately does NOT reuse ``resolve_picks`` (natural-day / latest-price)
or ``_fetch_returns`` (yfinance-only) — those are not suitable for official
scoring.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import get_eval_config
from .prices import fetch_adjusted_prices, fetch_index_series, nth_trading_day, price_at


def index_return(df, entry_date: str, exit_date: str):
    """Close-to-close return of a date-indexed series over ``[entry, exit]``."""
    e = price_at(df, entry_date, "close")
    x = price_at(df, exit_date, "close")
    if e is None or x is None or e <= 0:
        return None
    return x / e - 1.0


def benchmark_return(config: dict | None, entry_date: str, exit_date: str):
    """Weighted benchmark return = 0.5*CSI300 + 0.5*CSI500 over ``[entry, exit]``."""
    ecfg = get_eval_config(config)
    weights = ecfg["benchmark_weights"]
    codes = ecfg["benchmark_codes"]

    total = 0.0
    total_weight = 0.0
    for key, code in codes.items():
        w = weights.get(key, 0.0)
        if w <= 0:
            continue
        series = fetch_index_series(code, entry_date, exit_date)
        ret = index_return(series, entry_date, exit_date)
        if ret is None:
            continue
        total += w * ret
        total_weight += w
    if total_weight <= 0:
        return None
    # Renormalize so a missing leg doesn't silently inflate/deflate the benchmark.
    return total / total_weight


def settle_entry(config: dict | None, ticker: str, selection_date: str) -> dict:
    """Resolve the entry date (next trading day) and its qfq open price.

    Returns ``{"entry_date": str, "entry_price": float|None}``. A missing open
    price (suspended / data gap) is returned as ``None`` — the caller decides
    whether to retry later (deferred settlement) or fail the case.
    """
    entry_date = nth_trading_day(selection_date, 1, config)
    end_est = (datetime.strptime(entry_date, "%Y-%m-%d")
               + timedelta(days=10)).strftime("%Y-%m-%d")
    df = fetch_adjusted_prices(ticker, selection_date, end_est)
    entry_price = price_at(df, entry_date, "open")
    return {"entry_date": entry_date, "entry_price": entry_price}


def settle_horizon(
    config: dict | None,
    ticker: str,
    entry_date: str,
    entry_price: float,
    horizon: int,
) -> dict:
    """Settle one horizon's return record, or emit ``settled=False`` when the
    exit price isn't available yet. ``entry_price`` is passed in so the deferred
    settlement path doesn't refetch it per horizon."""
    ecfg = get_eval_config(config)
    friction = ecfg["friction_bp"] / 10000.0

    exit_date = nth_trading_day(entry_date, horizon, config)
    end_est = (datetime.strptime(exit_date, "%Y-%m-%d")
               + timedelta(days=5)).strftime("%Y-%m-%d")
    df = fetch_adjusted_prices(ticker, entry_date, end_est)
    exit_price = price_at(df, exit_date, "close")

    base = {
        "horizon": horizon, "entry_date": entry_date, "entry_price": entry_price,
        "exit_date": exit_date, "exit_price": exit_price,
    }
    if exit_price is None:
        return {**base, "gross_return": None, "net_return": None,
                "benchmark_return": None, "excess_return": None, "settled": False}

    gross = exit_price / entry_price - 1.0
    net = gross - friction
    bench = benchmark_return(config, entry_date, exit_date)
    excess = None if bench is None else net - bench
    return {**base, "gross_return": gross, "net_return": net,
            "benchmark_return": bench, "excess_return": excess, "settled": True}


def settle_prediction(
    config: dict | None,
    ticker: str,
    selection_date: str,
    horizons: list[int] | None = None,
) -> dict:
    """Settle a single prediction into per-horizon return records (immediate).

    Returns ``{"failed": bool, "reason": str, "returns": [record, ...]}`` where
    each record has keys ``horizon, entry_date, entry_price, exit_date,
    exit_price, gross_return, net_return, benchmark_return, excess_return``.
    A horizon whose exit price is missing is emitted with all return fields
    ``None`` and ``settled=False`` (the others still settle). A missing entry
    price fails the whole prediction. Kept as a thin wrapper over
    :func:`settle_entry` + :func:`settle_horizon` for tests and one-shot
    callers; the live system uses deferred settlement instead.
    """
    ecfg = get_eval_config(config)
    horizons = horizons or ecfg["holdings_horizons"]

    entry = settle_entry(config, ticker, selection_date)
    entry_date = entry["entry_date"]
    entry_price = entry["entry_price"]
    if entry_price is None:
        return {"failed": True,
                "reason": f"no entry open price on {entry_date} (suspended/missing)",
                "returns": []}

    records = [settle_horizon(config, ticker, entry_date, entry_price, h) for h in horizons]
    return {"failed": False, "reason": "", "returns": records}
