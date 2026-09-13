"""Convert VectorBacktester results into the legacy-compatible backtest dict.

The old backtrader engine returned ``{"results", "equity_curve", "trade_log"}``
with specific keys. This module maps the vectorized result onto that exact
shape so ``report.py`` and the web frontend need no changes.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from quantconclave.quant.backtest.vector import BacktestResult


def _derive_trades(
    executed_weights: pd.DataFrame,
    close_pivot: pd.DataFrame,
    initial_capital: float,
) -> list[dict[str, Any]]:
    """Derive a trade log from a single-symbol executed-weight column.

    A 0->1 transition is an entry (filled at that day's close), a 1->0
    transition is an exit. PnL is computed per share-round of the initial
    capital, matching the fields the old bt TradeList analyzer produced:
    entry_date/exit_date/entry_price/exit_price/pnl/pnl_pct.
    """
    if executed_weights is None or executed_weights.empty:
        return []
    col = executed_weights.columns[0]
    weights = executed_weights[col].astype(float)
    closes = close_pivot[col]

    trades: list[dict[str, Any]] = []
    entry_date = None
    entry_price = 0.0
    for date, w in weights.items():
        w = float(w)
        if entry_date is None and w > 0.5:  # entry
            entry_date = date
            entry_price = float(closes.get(date, np.nan))
        elif entry_date is not None and w <= 0.5:  # exit
            exit_price = float(closes.get(date, np.nan))
            if not np.isnan(entry_price) and entry_price > 0:
                pnl = (exit_price - entry_price) * (initial_capital / entry_price)
                pnl_pct = (exit_price / entry_price - 1.0) * 100.0
                trades.append({
                    "entry_date": str(entry_date)[:10],
                    "exit_date": str(date)[:10],
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })
            entry_date = None
    return trades


def to_compat_results(
    res: BacktestResult,
    initial_capital: float,
    ticker: str,
    panel: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Map a ``BacktestResult`` onto the legacy-compatible backtest dict.

    ``panel`` is the original ``(symbol, eob)`` MultiIndex OHLCV used by the
    backtester; its close prices are needed to derive the trade log.
    """
    result_df = res.result_df
    empty = {
        "results": {
            "total_return_pct": 0.0,
            "annual_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
            "initial_capital": initial_capital,
            "final_value": initial_capital,
        },
        "equity_curve": [],
        "trade_log": [],
    }
    if result_df is None or result_df.empty:
        return empty

    metrics = res.metrics
    total_return = float(metrics.get("total_return", 0.0))
    max_dd = float(metrics.get("max_drawdown", 0.0))
    ann_return = float(metrics.get("ann_return", 0.0))
    sharpe = float(metrics.get("sharpe_ratio", 0.0))

    # Equity curve from the real strategy net-value series
    equity_curve = [
        {"date": d.strftime("%Y-%m-%d"), "value": round(initial_capital * float(eq), 2)}
        for d, eq in result_df["equity"].items()
    ]

    # Trade log from executed weights + close prices
    trade_log: list[dict[str, Any]] = []
    if res.executed_weights is not None and not res.executed_weights.empty:
        if panel is not None and "close" in panel.columns:
            try:
                close_pivot = panel["close"].unstack(level="symbol")
            except Exception:
                close_pivot = None
            if close_pivot is not None and ticker in close_pivot.columns:
                trade_log = _derive_trades(res.executed_weights, close_pivot, initial_capital)

    results = {
        "total_return_pct": round(total_return * 100.0, 2),
        "annual_return": round(ann_return * 100.0, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "total_trades": len(trade_log),
        "initial_capital": initial_capital,
        "final_value": round(initial_capital * (1.0 + total_return), 2),
    }
    return {"results": results, "equity_curve": equity_curve, "trade_log": trade_log}
