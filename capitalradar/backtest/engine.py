"""Vectorized backtest engine — replaces the old backtrader Cerebro wrapper.

``run_backtest`` keeps the legacy return shape
``{"results", "equity_curve", "trade_log"}`` so ``report.py`` and the web
frontend work unchanged, but executes via the fast vectorized
``VectorBacktester`` instead of event-driven backtrader.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from capitalradar.quant.backtest.vector import VectorBacktester
from capitalradar.backtest.data import fetch_ohlcv_panel
from capitalradar.backtest.metrics import to_compat_results
from capitalradar.backtest.runs import save_backtest_run
from capitalradar.backtest.strategies import get_strategy_class

logger = logging.getLogger(__name__)


def run_backtest(
    ticker: str,
    strategy: str | Callable,
    parameters: dict,
    start_date: str,
    end_date: str,
    initial_capital: float = 100000.0,
    commission: float = 0.0003,
    slippage_bp: float = 2.0,
    signal_lag: int = 1,
    trade_at: str = "close",
    data: pd.DataFrame | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run a vectorized backtest for a single ticker.

    Args:
        ticker: Stock symbol (e.g. ``601127.SH``).
        strategy: A template ID string (``"ma_cross"``) or a signal function
            ``(df: pd.DataFrame, **params) -> pd.Series`` returning target
            positions in {0, 1}.
        parameters: Strategy parameters merged into the signal function.
        start_date/end_date: Backtest window (YYYY-MM-DD).
        initial_capital: Starting cash.
        commission: One-way commission rate (fraction).
        slippage_bp: Slippage in basis points.
        signal_lag: Bars between signal and execution (1 = next bar).
        trade_at: Execution price column ("open" or "close").
        data: Optional pre-built ``(symbol, eob)`` panel (bypasses network).
        persist: Whether to write the run to the ``backtest_runs`` table.

    Returns:
        Dict with ``results``, ``equity_curve``, ``trade_log`` keys (same shape
        as the old backtrader engine).
    """
    if isinstance(strategy, str):
        signal_fn = get_strategy_class(strategy)
        strategy_name = strategy
    elif callable(strategy):
        signal_fn = strategy
        strategy_name = getattr(strategy, "__name__", "custom")
    else:
        raise TypeError("strategy must be a template ID string or a callable signal function")

    df = data if data is not None else fetch_ohlcv_panel(ticker, start_date, end_date)
    if df is None or df.empty:
        raise ValueError(f"No data for {ticker} from {start_date} to {end_date}")

    # ``df`` must be a ``(symbol, eob)`` MultiIndex panel for VectorBacktester.
    # Normalize a plain single-symbol frame into that shape if passed directly.
    if df.index.names != ["symbol", "eob"]:
        if "eob" in df.columns:
            df = df.copy()
            df["symbol"] = ticker
            df = df.set_index(["symbol", "eob"])[["open", "high", "low", "close", "volume"]]
            df.index.names = ["symbol", "eob"]
        else:
            raise ValueError("data must be indexed by ['symbol', 'eob'] MultiIndex")

    single = df.xs(ticker, level="symbol")

    signal = signal_fn(single, **parameters)
    signal = signal.reindex(single.index).fillna(0.0).astype(float)

    weights = pd.DataFrame({ticker: signal.values}, index=single.index)
    weights.index.name = "eob"

    backtester = VectorBacktester(
        data=df,
        trade_at=trade_at,
        signal_lag=signal_lag,
        commission=commission,
        slippage_bp=slippage_bp,
    )
    res = backtester.run(weights)

    compat = to_compat_results(res, initial_capital, ticker, panel=df)

    if persist:
        try:
            save_backtest_run(
                ticker=ticker,
                strategy_name=strategy_name,
                strategy_id=strategy,
                start_date=start_date,
                end_date=end_date,
                parameters=parameters,
                initial_capital=initial_capital,
                results=compat["results"],
                equity_curve=compat["equity_curve"],
                trade_log=compat["trade_log"],
            )
        except Exception as e:
            logger.warning("Failed to persist backtest run: %s", e)

    return compat
