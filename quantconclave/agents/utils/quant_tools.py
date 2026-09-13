"""QuantAgent-fused technical analysis tools for QuantConclave.

Phase 1: Advanced technical indicators (Stochastic, ROC, Williams %R, CCI)
Phase 2: Mathematical trendline fitting (support/resistance optimization)
Phase 3: K-line chart visualization → vision LLM pattern recognition
Phase 4: Expanded factor library (CCI, ATR, trend_score, RSRS, ER, MA series,
         SuperTrend, volatility regime) — wrapping quantconclave.quant.indicators.

These are @tool functions callable by Market Analyst, PM, or any agent.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Annotated, Optional

import numpy as np
import pandas as pd
from langchain_core.tools import tool

from quantconclave.quant import indicators as _indicators
from quantconclave.quant import utils as _quant_utils

logger = logging.getLogger(__name__)

# ── Shared: fetch OHLCV data ──────────────────────────────────────────

def _fetch_ohlcv(symbol: str, lookback: int = 60) -> pd.DataFrame | None:
    """Fetch OHLCV data via the configured vendor chain. Returns DataFrame or None."""
    from datetime import datetime, timedelta
    from quantconclave.dataflows.interface import route_to_vendor

    end = datetime.now()
    start = end - timedelta(days=max(lookback, 120))
    try:
        raw = route_to_vendor(
            "get_stock_data", symbol,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("OHLCV fetch failed for %s: %s", symbol, e)
        return None

    # Parse CSV-format OHLCV
    lines = str(raw).split("\n")
    csv_start = next(
        (i for i, l in enumerate(lines)
         if "trade_date" in l.lower() or "Date" in l or "date" in l.lower()),
        None,
    )
    if csv_start is None:
        return None
    import csv as _csv
    reader = _csv.DictReader(io.StringIO("\n".join(lines[csv_start:])))
    rows = []
    for r in reader:
        try:
            date_col = r.get("trade_date", r.get("Date", r.get("date", "")))
            close = float(r.get("close", r.get("Close", 0)) or 0)
            if close <= 0:
                continue
            rows.append({
                "date": str(date_col).strip()[:10],
                "open": float(r.get("open", r.get("Open", close)) or close),
                "high": float(r.get("high", r.get("High", close)) or close),
                "low": float(r.get("low", r.get("Low", close)) or close),
                "close": close,
                "volume": float(r.get("volume", r.get("Volume", r.get("vol", "0"))) or 0),
            })
        except (ValueError, KeyError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Advanced Technical Indicators (@tool wrappers)
# ═══════════════════════════════════════════════════════════════════════

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Compute Exponential Moving Average."""
    result = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.mean(values[:period])
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


@tool
def get_stochastic(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    fastk_period: Annotated[int, "Fast %K period (default 14)"] = 14,
    slowk_period: Annotated[int, "Slow %K smoothing (default 3)"] = 3,
    slowd_period: Annotated[int, "%D smoothing (default 3)"] = 3,
) -> str:
    """Compute Stochastic Oscillator (%K, %D) for a ticker.

    Measures closing price relative to the high-low range over a lookback
    period.  %K > 80 = overbought, %K < 20 = oversold.  %K crossing %D
    is a common entry/exit signal.

    Returns the last 20 values as CSV with date, %K, %D, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=60)
    if df is None or len(df) < fastk_period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"

    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    dates = df["date"].dt.strftime("%Y-%m-%d").values

    k_vals = np.full(n, np.nan)
    d_vals = np.full(n, np.nan)

    for i in range(fastk_period - 1, n):
        h = np.max(highs[i - fastk_period + 1:i + 1])
        l = np.min(lows[i - fastk_period + 1:i + 1])
        k_vals[i] = ((closes[i] - l) / (h - l) * 100) if (h - l) > 0 else 50.0

    # Smooth %K → slow %K, then %D
    for i in range(fastk_period + slowk_period - 2, n):
        k_vals[i] = np.mean(k_vals[i - slowk_period + 1:i + 1])
    for i in range(fastk_period + slowk_period + slowd_period - 3, n):
        d_vals[i] = np.mean(k_vals[i - slowd_period + 1:i + 1])

    recent = slice(max(0, n - 20), n)
    lines = ["date,%K,%D,signal"]
    for i in range(*recent.indices(n)):
        k = round(k_vals[i], 2) if not np.isnan(k_vals[i]) else 0
        d = round(d_vals[i], 2) if not np.isnan(d_vals[i]) else 0
        if k > d:
            sig = "bullish (K above D)"
        elif k < d:
            sig = "bearish (K below D)"
        else:
            sig = "neutral"
        lines.append(f"{dates[i]},{k},{d},{sig}")

    last_k = round(k_vals[-1], 1) if not np.isnan(k_vals[-1]) else 0
    overbought = "OVERBOUGHT (>80)" if last_k > 80 else ("OVERSOLD (<20)" if last_k < 20 else "neutral")
    lines.append(f"\n# Latest %K = {last_k} → {overbought}")
    return "\n".join(lines)


@tool
def get_williams_r(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "Lookback period (default 14)"] = 14,
) -> str:
    """Compute Williams %R for a ticker.

    Similar to Stochastic but inverted: -20 to 0 = overbought, -100 to -80
    = oversold.  Divergence between %R and price is a strong reversal signal.

    Returns the last 20 values as CSV with date, %R, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=60)
    if df is None or len(df) < period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"

    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    dates = df["date"].dt.strftime("%Y-%m-%d").values

    willr = np.full(n, np.nan)
    for i in range(period - 1, n):
        hh = np.max(highs[i - period + 1:i + 1])
        ll = np.min(lows[i - period + 1:i + 1])
        willr[i] = ((hh - closes[i]) / (hh - ll) * -100) if (hh - ll) > 0 else -50.0

    recent = slice(max(0, n - 20), n)
    lines = ["date,williams_r,signal"]
    for i in range(*recent.indices(n)):
        w = round(willr[i], 2) if not np.isnan(willr[i]) else 0
        if w > -20:
            sig = "overbought"
        elif w < -80:
            sig = "oversold"
        else:
            sig = "neutral"
        lines.append(f"{dates[i]},{w},{sig}")

    last_w = round(willr[-1], 1) if not np.isnan(willr[-1]) else 0
    lines.append(f"\n# Latest Williams %R = {last_w}")
    return "\n".join(lines)


@tool
def get_roc(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "Rate-of-Change lookback (default 10)"] = 10,
) -> str:
    """Compute Rate of Change (ROC) for a ticker.

    Measures the percentage price change over N periods.  Positive ROC =
    upward momentum, negative ROC = downward momentum.  ROC crossing zero
    is an early trend-change signal.

    Returns the last 20 values as CSV with date, ROC%, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=60)
    if df is None or len(df) < period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"

    closes = df["close"].values
    dates = df["date"].dt.strftime("%Y-%m-%d").values
    n = len(closes)

    roc = np.full(n, np.nan)
    for i in range(period, n):
        roc[i] = (closes[i] - closes[i - period]) / closes[i - period] * 100

    recent = slice(max(0, n - 20), n)
    lines = ["date,roc_pct,signal"]
    for i in range(*recent.indices(n)):
        r = round(roc[i], 2) if not np.isnan(roc[i]) else 0
        if r > 5:
            sig = "strong bullish momentum"
        elif r > 0:
            sig = "bullish"
        elif r > -5:
            sig = "bearish"
        else:
            sig = "strong bearish momentum"
        lines.append(f"{dates[i]},{r},{sig}")

    last_roc = round(roc[-1], 2) if not np.isnan(roc[-1]) else 0
    lines.append(f"\n# Latest ROC({period}) = {last_roc}%")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Trendline Fitting (from QuantAgent graph_util.py)
# ═══════════════════════════════════════════════════════════════════════

def _check_trend_line(support: bool, pivot: int, slope: float, y: np.ndarray) -> float:
    """Validate a trendline — return squared error or -1 if invalid."""
    intercept = -slope * pivot + y[pivot]
    line_vals = slope * np.arange(len(y)) + intercept
    diffs = line_vals - y
    if support and diffs.max() > 1e-5:
        return -1.0
    if not support and diffs.min() < -1e-5:
        return -1.0
    return float((diffs ** 2).sum())


def _optimize_slope(support: bool, pivot: int, init_slope: float, y: np.ndarray) -> tuple[float, float]:
    """Numerically optimize trendline slope to minimize fitting error."""
    slope_unit = (y.max() - y.min()) / len(y)
    opt_step = 1.0
    min_step = 0.0001
    curr_step = opt_step
    best_slope = init_slope
    best_err = _check_trend_line(support, pivot, init_slope, y)
    if best_err < 0:
        best_err = 0.0

    get_derivative = True
    derivative = None
    while curr_step > min_step:
        if get_derivative:
            slope_change = best_slope + slope_unit * min_step
            test_err = _check_trend_line(support, pivot, slope_change, y)
            derivative = test_err - best_err
            if test_err < 0.0:
                slope_change = best_slope - slope_unit * min_step
                test_err = _check_trend_line(support, pivot, slope_change, y)
                derivative = best_err - test_err
            if test_err < 0.0:
                break
            get_derivative = False

        if derivative is not None and derivative > 0.0:
            test_slope = best_slope - slope_unit * curr_step
        else:
            test_slope = best_slope + slope_unit * curr_step

        test_err = _check_trend_line(support, pivot, test_slope, y)
        if test_err < 0 or test_err >= best_err:
            curr_step *= 0.5
        else:
            best_err = test_err
            best_slope = test_slope
            get_derivative = True

    return (best_slope, -best_slope * pivot + y[pivot])


def _fit_trendlines(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict:
    """Fit optimal support and resistance trendlines to price data.

    Returns: {
        "support_slope": float, "support_intercept": float,
        "resistance_slope": float, "resistance_intercept": float,
        "channel_width_pct": float, "trend_bias": str,
    }
    """
    x = np.arange(len(close))
    coefs = np.polyfit(x, close, 1)
    line_points = coefs[0] * x + coefs[1]

    upper_pivot = int((high - line_points).argmax())
    lower_pivot = int((low - line_points).argmin())

    try:
        support_coefs = _optimize_slope(True, lower_pivot, coefs[0], low)
        resist_coefs = _optimize_slope(False, upper_pivot, coefs[0], high)
    except Exception:
        # Fallback to simple linear regression
        support_coefs = (coefs[0], coefs[1] - np.std(low - line_points))
        resist_coefs = (coefs[0], coefs[1] + np.std(high - line_points))

    width = abs(resist_coefs[1] - support_coefs[1])
    width_pct = (width / close[-1] * 100) if close[-1] > 0 else 0

    avg_slope = (support_coefs[0] + resist_coefs[0]) / 2
    if avg_slope > 0.001:
        bias = "bullish (ascending channel)"
    elif avg_slope < -0.001:
        bias = "bearish (descending channel)"
    else:
        bias = "neutral (horizontal channel)"

    return {
        "support_slope": round(support_coefs[0], 6),
        "support_intercept": round(support_coefs[1], 2),
        "resistance_slope": round(resist_coefs[0], 6),
        "resistance_intercept": round(resist_coefs[1], 2),
        "channel_width_pct": round(width_pct, 2),
        "trend_bias": bias,
    }


@tool
def get_trendlines(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
) -> str:
    """Compute optimal support and resistance trendlines for a ticker.

    Uses numerical optimization (gradient descent on pivot points) to find
    the best-fit support (lower) and resistance (upper) trendlines.  This
    is a mathematical, data-driven approach — NOT a visual guess.

    Returns:
        - Support slope & intercept (price = slope * t + intercept)
        - Resistance slope & intercept
        - Channel width as % of current price
        - Trend bias: bullish (ascending), bearish (descending), or neutral

    Use this to validate entry/exit levels and confirm trend direction.
    """
    df = _fetch_ohlcv(symbol, lookback=60)
    if df is None or len(df) < 30:
        return f"Error: Insufficient OHLCV data for {symbol} (need >= 30 bars)"

    result = _fit_trendlines(df["high"].values, df["low"].values, df["close"].values)
    price = float(df["close"].values[-1])

    support_now = round(result["support_slope"] * len(df) + result["support_intercept"], 2)
    resist_now = round(result["resistance_slope"] * len(df) + result["resistance_intercept"], 2)

    # Distance from current price to support/resistance
    dist_support = round((price - support_now) / price * 100, 1) if price > 0 else 0
    dist_resist = round((resist_now - price) / price * 100, 1) if price > 0 else 0

    return "\n".join([
        f"# Trendline Analysis: {symbol}",
        f"Current Price: {price:.2f}",
        "",
        "## Support Line (blue)",
        f"  Slope: {result['support_slope']:.6f}  |  Intercept: {result['support_intercept']:.2f}",
        f"  Support at current bar: {support_now:.2f}  ({dist_support:+.1f}% from price)",
        "",
        "## Resistance Line (red)",
        f"  Slope: {result['resistance_slope']:.6f}  |  Intercept: {result['resistance_intercept']:.2f}",
        f"  Resistance at current bar: {resist_now:.2f}  ({dist_resist:+.1f}% from price)",
        "",
        f"## Summary",
        f"  Channel Width: {result['channel_width_pct']:.1f}% of price",
        f"  Trend Bias: {result['trend_bias']}",
        f"  Risk/Reward to Resistance: 1:{max(0.1, round(dist_resist / max(abs(dist_support), 0.1), 1))}" if dist_support else "",
    ])


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: K-line Chart → Vision LLM Pattern Recognition
# ═══════════════════════════════════════════════════════════════════════

def _render_kline_base64(df: pd.DataFrame) -> str:
    """Render the last 50 candles as an mplfinance chart → base64 PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import mplfinance as mpf
    except ImportError:
        return ""

    candles = df.tail(50).copy()
    if "date" in candles.columns:
        candles["date"] = pd.to_datetime(candles["date"])
        candles = candles.rename(columns={
            "date": "Datetime", "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume",
        })
    candles = candles.set_index("Datetime")

    try:
        fig, _axlist = mpf.plot(
            candles, type="candle", style="charles",
            figsize=(12, 6), returnfig=True, block=False,
        )
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")
    except Exception as e:
        logger.warning("K-line render failed: %s", e)
        return ""


@tool
def get_chart_pattern(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
) -> str:
    """Generate a K-line chart and analyze candlestick patterns.

    Renders the last 50 candles as a candlestick chart and uses the LLM's
    vision capability to identify patterns: head-and-shoulders, double
    top/bottom, triangles, flags, engulfing candles, doji, hammers, etc.

    NOTE: This tool requires a multimodal LLM (GPT-4V, Claude Vision,
    Qwen-VL) that can interpret images. If the current LLM cannot process
    images, this tool returns the chart description only.

    Returns a structured pattern assessment with the base64 chart image.
    """
    df = _fetch_ohlcv(symbol, lookback=60)
    if df is None or len(df) < 30:
        return f"Error: Insufficient OHLCV data for {symbol}"

    img_b64 = _render_kline_base64(df)
    if not img_b64:
        return "Error: mplfinance not available for chart rendering."

    closes = df["close"].values
    n = len(closes)

    # Compute basic context for the LLM to use alongside the image
    pct_change_5d = round((closes[-1] - closes[-5]) / closes[-5] * 100, 2) if n >= 5 else 0
    pct_change_20d = round((closes[-1] - closes[-20]) / closes[-20] * 100, 2) if n >= 20 else 0

    # Find recent swing highs/lows
    recent = closes[-20:]
    swing_high = round(float(np.max(recent)), 2)
    swing_low = round(float(np.min(recent)), 2)

    context = "\n".join([
        f"# K-line Chart Pattern Analysis: {symbol}",
        f"Current Price: {closes[-1]:.2f}",
        f"5-day Change: {pct_change_5d:+.2f}%",
        f"20-day Change: {pct_change_20d:+.2f}%",
        f"20-day High: {swing_high:.2f}",
        f"20-day Low: {swing_low:.2f}",
        f"Candles Shown: 50",
        "",
        "## Visual Pattern Detection",
        "The chart image below shows the last 50 candlesticks. Identify:",
        "- Reversal patterns: head-and-shoulders, double top/bottom, rounded top/bottom",
        "- Continuation patterns: flags, pennants, triangles (ascending/descending/symmetric)",
        "- Single-candle signals: doji, hammer, shooting star, engulfing, marubozu",
        "- Support/resistance zones visible on the chart",
        "- Volume anomalies (spikes on breakouts, declining on pullbacks)",
        "",
        "If you CAN see the image, describe the pattern(s) you detect.",
        "If you CANNOT see images, state that and use the numerical context above.",
        "",
        f"[Chart image (base64 PNG): data:image/png;base64,{img_b64[:200]}... ({len(img_b64)} chars total)]",
    ])

    return context


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Expanded factor library (@tool wrappers over quant.indicators)
# ═══════════════════════════════════════════════════════════════════════

def _render_series(
    df: pd.DataFrame,
    values: dict[str, pd.Series],
    columns: list[str],
    latest_note: str,
    signal_fn=None,
) -> str:
    """Render indicator values as CSV (last 20 rows) + a trailing # comment.

    Matches the return convention of the existing quant tools: first line is
    the CSV header, then the last 20 rows rounded to 2 decimals, then a
    ``# Latest ...`` interpretation comment for the LLM.

    Args:
        df: The OHLCV frame (for the ``date`` column).
        values: {column_name: Series} aligned to df.index.
        columns: CSV header column names (besides ``date``).
        latest_note: The trailing ``#`` comment text.
        signal_fn: Optional ``(row_dict) -> str`` producing a per-row signal.
    """
    dates = df["date"].dt.strftime("%Y-%m-%d").values
    n = len(df)
    recent = slice(max(0, n - 20), n)
    idxs = range(*recent.indices(n))

    lines = ["date," + ",".join(columns) + (",signal" if signal_fn else "")]
    for i in idxs:
        row_vals = []
        for col in columns:
            s = values[col]
            v = float(s.iloc[i]) if not pd.isna(s.iloc[i]) else 0.0
            row_vals.append(f"{v:.2f}")
        if signal_fn:
            row_dict = {col: (float(values[col].iloc[i]) if not pd.isna(values[col].iloc[i]) else 0.0) for col in columns}
            row_vals.append(signal_fn(row_dict))
        lines.append(f"{dates[i]}," + ",".join(row_vals))
    lines.append(f"\n# Latest: {latest_note}")
    return "\n".join(lines)


@tool
def get_cci(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "CCI lookback period (default 20)"] = 20,
) -> str:
    """Compute the Commodity Channel Index (CCI) for a ticker.

    Measures current price relative to its typical-price moving average.
    CCI > +100 = overbought, CCI < -100 = oversold. Divergence between CCI
    and price is a strong reversal signal.

    Returns the last 20 values as CSV with date, cci, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    vals = _indicators.cci(df, period=period)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    state = "overbought (> +100)" if last > 100 else ("oversold (< -100)" if last < -100 else "neutral")
    return _render_series(
        df, {"cci": vals}, ["cci"], f"CCI({period}) = {last:.2f} → {state}",
        signal_fn=lambda r: "overbought" if r["cci"] > 100 else ("oversold" if r["cci"] < -100 else "neutral"),
    )


@tool
def get_atr(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "ATR lookback period (default 14)"] = 14,
) -> str:
    """Compute the Average True Range (ATR) for a ticker.

    Measures market volatility. Higher ATR = wider ranges / more volatile;
    lower ATR = calm / consolidating. Use with price to set stop-loss or
    position sizing.

    Returns the last 20 values as CSV with date, atr, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    vals = _quant_utils.calculate_atr(df["high"], df["low"], df["close"], period)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    mean = float(vals.dropna().mean()) if len(vals.dropna()) else 0.0
    state = "high volatility" if last > 1.5 * mean else ("low volatility" if last < 0.5 * mean else "normal")
    return _render_series(
        df, {"atr": vals}, ["atr"], f"ATR({period}) = {last:.2f} → {state}",
        signal_fn=lambda r: "high vol" if r["atr"] > 1.5 * mean else ("low vol" if r["atr"] < 0.5 * mean else "normal"),
    )


@tool
def get_trend_score(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "Trend regression window (default 25)"] = 25,
) -> str:
    """Compute the Trend Score for a ticker.

    A quantitative trend-strength score from rolling regression of log-price
    on time: slope × R². Positive = strong uptrend, negative = strong
    downtrend, near zero = flat/choppy.

    Returns the last 20 values as CSV with date, trend_score, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    vals = _indicators.trend_score(df, period=period)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    state = "strong uptrend" if last > 0.3 else ("strong downtrend" if last < -0.3 else ("mild trend" if abs(last) > 0.1 else "flat"))
    return _render_series(
        df, {"trend_score": vals}, ["trend_score"], f"trend_score({period}) = {last:.2f} → {state}",
        signal_fn=lambda r: "bullish" if r["trend_score"] > 0.1 else ("bearish" if r["trend_score"] < -0.1 else "neutral"),
    )


@tool
def get_rsrs(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "RSRS regression window (default 18)"] = 18,
) -> str:
    """Compute the RSRS (Resistance Support Relative Strength) for a ticker.

    Regresses daily high on low over a rolling window and z-scores the slope.
    Rising RSRS = buyers stepping in / support strengthening; falling RSRS =
    distribution pressure. Useful for timing index/sector rotation entries.

    Returns the last 20 values as CSV with date, rsrs, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < period * 2 + 5:
        return f"Error: Insufficient OHLCV data for {symbol} (need >= {period * 2 + 5} bars)"
    vals = _indicators.rsrs(df, period=period)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    state = "support strengthening (bullish)" if last > 1 else ("distribution (bearish)" if last < -1 else "neutral")
    return _render_series(
        df, {"rsrs": vals}, ["rsrs"], f"RSRS({period}) = {last:.2f} → {state}",
        signal_fn=lambda r: "bullish" if r["rsrs"] > 1 else ("bearish" if r["rsrs"] < -1 else "neutral"),
    )


@tool
def get_er(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "Efficiency Ratio lookback (default 14)"] = 14,
) -> str:
    """Compute the Kaufman Efficiency Ratio (ER) for a ticker.

    |net change| / |sum of per-bar changes| over the window. ER near 1 =
    strong smooth trend; ER near 0 = choppy / range-bound. Used to size trend
    vs mean-reversion exposure.

    Returns the last 20 values as CSV with date, er, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    vals = _indicators.er(df, period=period)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    state = "strong trend" if last > 0.5 else ("choppy/range" if last < 0.2 else "moderate trend")
    return _render_series(
        df, {"er": vals}, ["er"], f"ER({period}) = {last:.2f} → {state}",
        signal_fn=lambda r: "trending" if r["er"] > 0.5 else ("range-bound" if r["er"] < 0.2 else "moderate"),
    )


@tool
def get_ma_series(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    fast: Annotated[int, "Fast MA period (default 5)"] = 5,
    slow: Annotated[int, "Slow MA period (default 20)"] = 20,
) -> str:
    """Compute a fast/slow moving-average series with cross signals.

    Returns both MAs plus their difference. Fast above slow = bullish
    arrangement (golden cross pattern), fast below slow = bearish.

    Returns the last 20 values as CSV with date, ma_fast, ma_slow, ma_cross,
    and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < slow + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    close = df["close"]
    ma_fast = close.rolling(fast, min_periods=fast).mean()
    ma_slow = close.rolling(slow, min_periods=slow).mean()
    cross = _indicators.ma_cross(df, short=fast, long=slow)
    last = float(cross.iloc[-1]) if not pd.isna(cross.iloc[-1]) else 0.0
    state = "bullish (fast above slow)" if last > 0 else ("bearish (fast below slow)" if last < 0 else "flat")
    return _render_series(
        df, {"ma_fast": ma_fast, "ma_slow": ma_slow, "ma_cross": cross},
        ["ma_fast", "ma_slow", "ma_cross"], f"MA({fast}/{slow}) cross = {last:.2f} → {state}",
        signal_fn=lambda r: "golden cross" if r["ma_cross"] > 0 else ("dead cross" if r["ma_cross"] < 0 else "neutral"),
    )


@tool
def get_supertrend(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    period: Annotated[int, "ATR period for SuperTrend (default 10)"] = 10,
    multiplier: Annotated[float, "ATR multiplier for band width (default 3.0)"] = 3.0,
) -> str:
    """Compute the SuperTrend indicator for a ticker.

    A trend-following band built on ATR. Positive value = price above the
    band (uptrend); negative = price below the band (downtrend). Sign flips
    mark trend reversals.

    Returns the last 20 values as CSV with date, supertrend, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < period * 2 + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    vals = _indicators.supertrend(df, period=period, multiplier=multiplier)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    state = "uptrend (bullish)" if last > 0 else ("downtrend (bearish)" if last < 0 else "neutral")
    return _render_series(
        df, {"supertrend": vals}, ["supertrend"], f"SuperTrend({period},{multiplier}) = {last:.2f} → {state}",
        signal_fn=lambda r: "uptrend" if r["supertrend"] > 0 else ("downtrend" if r["supertrend"] < 0 else "neutral"),
    )


@tool
def get_volatility_regime(
    symbol: Annotated[str, "Ticker symbol, e.g. 600519.SH or AAPL"],
    short_period: Annotated[int, "Short volatility window (default 10)"] = 10,
    long_period: Annotated[int, "Long volatility window (default 60)"] = 60,
) -> str:
    """Compute the volatility regime ratio for a ticker.

    short-vol / long-vol. Ratio > 1.5 = high-volatility regime (risky,
    widen stops); ratio < 0.8 = low-volatility regime (calmer, tighter).

    Returns the last 20 values as CSV with date, regime, and interpretation.
    """
    df = _fetch_ohlcv(symbol, lookback=120)
    if df is None or len(df) < long_period + 5:
        return f"Error: Insufficient OHLCV data for {symbol}"
    vals = _indicators.volatility_regime(df, short_period=short_period, long_period=long_period)
    last = float(vals.iloc[-1]) if not pd.isna(vals.iloc[-1]) else 0.0
    state = "high volatility" if last > 1.5 else ("low volatility" if last < 0.8 else "normal")
    return _render_series(
        df, {"regime": vals}, ["regime"], f"vol_regime = {last:.2f} → {state}",
        signal_fn=lambda r: "high vol" if r["regime"] > 1.5 else ("low vol" if r["regime"] < 0.8 else "normal"),
    )


# Single registry of the Phase-4 tools so all registration points can expand
# from one place.
ADVANCED_INDICATOR_TOOLS = [
    get_cci,
    get_atr,
    get_trend_score,
    get_rsrs,
    get_er,
    get_ma_series,
    get_supertrend,
    get_volatility_regime,
]
