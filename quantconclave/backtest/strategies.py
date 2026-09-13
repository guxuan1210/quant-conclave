"""Vectorized signal functions for all 7 strategy templates.

Replaces the previous backtrader ``bt.SignalStrategy`` classes. Each signal
function takes a single-symbol OHLCV DataFrame (columns open/high/low/close/
volume, ascending by date) and returns a **target position** Series in {0, 1}
(full long, all-in/all-out).

A ``_stateful`` helper implements the holding state machine with forward-fill,
mirroring the SIGNAL_LONG / SIGNAL_LONGEXIT semantics of the old backtrader
strategies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _stateful(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """Build a target-position Series from boolean entry/exit masks.

    Position becomes 1 on entry, 0 on exit, and holds until the next signal
    (forward-fill). Never enters before the first entry signal.
    """
    pos = pd.Series(np.nan, index=entry.index, dtype=float)
    pos[entry.fillna(False)] = 1.0
    pos[exit_.fillna(False)] = 0.0
    return pos.ffill().fillna(0.0)


def _ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (equivalent to bt.ind.EMA)."""
    return series.ewm(span=span, adjust=False).mean()


def ma_cross_signal(
    df: pd.DataFrame,
    fast_period: int = 5,
    slow_period: int = 20,
) -> pd.Series:
    """Buy when SMA(fast) crosses above SMA(slow), sell when it crosses below."""
    close = df["close"]
    fast = close.rolling(fast_period, min_periods=fast_period).mean()
    slow = close.rolling(slow_period, min_periods=slow_period).mean()
    entry = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    exit_ = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    return _stateful(entry, exit_)


def macd_signal(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.Series:
    """Buy when MACD line crosses above signal, sell when it crosses below."""
    close = df["close"]
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    entry = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    exit_ = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    return _stateful(entry, exit_)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder-style RSI, equivalent to bt.ind.RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)


def rsi_signal(
    df: pd.DataFrame,
    period: int = 14,
    oversold: int = 30,
    overbought: int = 70,
) -> pd.Series:
    """Buy when RSI drops below oversold, sell when it rises above overbought."""
    rsi = _rsi(df["close"], period)
    entry = rsi < oversold
    exit_ = rsi > overbought
    return _stateful(entry, exit_)


def bollinger_signal(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.Series:
    """Buy when close touches lower band, sell when it reaches upper band."""
    close = df["close"]
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    entry = close < lower
    exit_ = close > upper
    return _stateful(entry, exit_)


def turtle_signal(
    df: pd.DataFrame,
    entry_period: int = 20,
    stop_atr: float = 2.0,
) -> pd.Series:
    """Buy on N-day high breakout, sell on N-day low breakdown.

    ``stop_atr`` is kept in the signature for template compatibility with the
    old backtrader TurtleStrategy, but the vectorized version exits on the
    N-day low (same as the original bt strategy, where stop_atr was unused).
    """
    high = df["high"]
    low = df["low"]
    highest = high.rolling(entry_period, min_periods=entry_period).max()
    lowest = low.rolling(entry_period, min_periods=entry_period).min()
    entry = df["close"] >= highest.shift(1)
    exit_ = df["close"] <= lowest.shift(1)
    return _stateful(entry, exit_)


def ma_arrange_signal(
    df: pd.DataFrame,
    short_ma: int = 5,
    mid_ma: int = 20,
    long_ma: int = 60,
) -> pd.Series:
    """Buy when short > mid > long MA (bullish arrangement), sell when broken."""
    close = df["close"]
    short = close.rolling(short_ma, min_periods=short_ma).mean()
    mid = close.rolling(mid_ma, min_periods=mid_ma).mean()
    long = close.rolling(long_ma, min_periods=long_ma).mean()
    arranged = (short > mid) & (mid > long)
    entry = arranged & ~arranged.shift(1, fill_value=False)
    exit_ = ~arranged & arranged.shift(1, fill_value=False)
    return _stateful(entry, exit_)


def volume_breakout_signal(
    df: pd.DataFrame,
    volume_mult: float = 1.5,
    price_period: int = 20,
) -> pd.Series:
    """Buy when volume exceeds N-day average by M multiple AND price hits new
    high. Sell when price falls back below its N-day average."""
    close = df["close"]
    volume = df["volume"]
    avg_vol = volume.rolling(price_period, min_periods=price_period).mean()
    highest = close.rolling(price_period, min_periods=price_period).max()
    avg_price = close.rolling(price_period, min_periods=price_period).mean()
    entry = (volume > avg_vol * volume_mult) & (close >= highest.shift(1))
    exit_ = close < avg_price
    return _stateful(entry, exit_)


SIGNAL_MAP = {
    "ma_cross": ma_cross_signal,
    "macd": macd_signal,
    "rsi": rsi_signal,
    "bollinger": bollinger_signal,
    "turtle": turtle_signal,
    "ma_arrange": ma_arrange_signal,
    "volume_breakout": volume_breakout_signal,
}


def get_strategy_class(template_id: str):
    """Return the signal function for a template ID (backward-compatible name)."""
    fn = SIGNAL_MAP.get(template_id)
    if fn is None:
        raise ValueError(f"Unknown template: {template_id}. Available: {list(SIGNAL_MAP.keys())}")
    return fn


def is_builtin_template(template_id: str) -> bool:
    return template_id in SIGNAL_MAP
