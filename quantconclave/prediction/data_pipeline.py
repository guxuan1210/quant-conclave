"""Historical data collection for PredictionAgent training.

Fetches multi-year OHLCV + moneyflow data, builds the same feature
vectors the FeatureEngine produces at inference time, and constructs
forward-looking target labels for supervised learning.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Tuple

import numpy as np
import pandas as pd

from quantconclave.dataflows.interface import route_to_vendor
from quantconclave.prediction.feature_engine import FeatureEngine, FEATURE_COLUMNS

logger = logging.getLogger(__name__)

# Sequence length needed to compute the full feature vector (RSI=14, MACD=26, BB=20).
_MIN_FEATURE_WINDOW = 60


def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV bars. Returns DataFrame indexed by date."""
    try:
        raw = route_to_vendor(
            "get_stock_data", ticker,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )
    except Exception as e:
        logger.warning("OHLCV fetch failed for %s: %s", ticker, e)
        return None
    return FeatureEngine._parse_ohlcv(str(raw))


def fetch_moneyflow(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Fetch daily capital flow data. Returns DataFrame indexed by date."""
    from quantconclave.agents.utils.capital_flow_tools import get_money_flow
    try:
        raw = get_money_flow.invoke({
            "ticker": ticker,
            "start_date": start.replace("-", ""),
            "end_date": end.replace("-", ""),
        })
    except Exception as e:
        logger.warning("Moneyflow fetch failed for %s: %s", ticker, e)
        return None
    return FeatureEngine._parse_moneyflow(str(raw))


def build_train_samples(
    ticker: str,
    start_date: str = "2019-01-01",
    end_date: str | None = None,
) -> pd.DataFrame | None:
    """Build labelled training samples for a single ticker.

    For each trading day ``t`` where enough history exists, computes the
    22-dim feature vector and four target labels:

    - ``target_5d_dir``:  1 if close_{t+5} > close_t * 1.005, else 0
    - ``target_20d_dir``: 1 if close_{t+20} > close_t * 1.005, else 0
    - ``target_5d_ret``:  (close_{t+5} - close_t) / close_t
    - ``target_20d_ret``: (close_{t+20} - close_t) / close_t

    Returns a DataFrame with one row per eligible trading day, or None
    if insufficient data is available.
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # Fetch all data in one batch, then iterate
    ohlcv = fetch_ohlcv(ticker, start_date, end_date)
    if ohlcv is None or len(ohlcv) < _MIN_FEATURE_WINDOW + 20:
        logger.info("Insufficient OHLCV for %s: %d bars", ticker, len(ohlcv) if ohlcv is not None else 0)
        return None

    mf = fetch_moneyflow(ticker, start_date, end_date)

    closes = ohlcv["close"].values
    volumes = ohlcv["volume"].values
    highs = ohlcv["high"].values
    lows = ohlcv["low"].values
    dates = ohlcv["date"].dt.strftime("%Y-%m-%d").values

    rows = []
    n = len(closes)

    for i in range(_MIN_FEATURE_WINDOW, n - 20):
        trade_date = str(dates[i])
        close_t = closes[i]

        # Build features using the same logic as FeatureEngine but on-the-fly
        # to avoid per-row overhead of FeatureEngine.build_features()
        features = {}

        # --- Price features ---
        for horizon, lookback in [("5d", 5), ("10d", 10), ("20d", 20)]:
            j = i - lookback
            if j >= 0 and closes[j] > 0:
                features[f"return_{horizon}"] = (close_t - closes[j]) / closes[j]
            else:
                features[f"return_{horizon}"] = np.nan

        # Volatility
        if i >= 20:
            slice_20 = closes[i - 20:i + 1]
            rets = np.diff(slice_20) / slice_20[:-1]
            features["volatility_20d"] = float(np.std(rets) * np.sqrt(252))
            peak = np.maximum.accumulate(closes[i - 19:i + 1])
            dd = (closes[i - 19:i + 1] - peak) / peak
            features["max_drawdown_20d"] = float(np.min(dd))
        else:
            features["volatility_20d"] = np.nan
            features["max_drawdown_20d"] = np.nan

        # --- Volume features ---
        if i >= 20:
            v20 = volumes[i - 20:i + 1]
            avg20 = np.mean(v20)
            features["volume_ratio_20d"] = volumes[i] / avg20 if avg20 > 0 else np.nan
        else:
            features["volume_ratio_20d"] = np.nan
        if i >= 5:
            v5 = volumes[i - 5:i + 1]
            avg5 = np.mean(v5)
            features["volume_ratio_5d"] = volumes[i] / avg5 if avg5 > 0 else np.nan
        else:
            features["volume_ratio_5d"] = np.nan
        if i >= 10:
            v10 = volumes[i - 9:i + 1]
            x = np.arange(10)
            slope = np.polyfit(x, v10, 1)[0] if np.std(v10) > 0 else 0.0
            features["volume_trend"] = float(slope / np.mean(v10)) if np.mean(v10) > 0 else 0.0
        else:
            features["volume_trend"] = np.nan

        # --- Capital flow features ---
        if mf is not None and len(mf) >= 5:
            mf_dates = mf["date"].dt.strftime("%Y-%m-%d").values
            mf_mask = mf_dates <= trade_date
            mf_before = mf.loc[mf_mask]
            n_mf = len(mf_before)
            for horizon, days in [("5d", 5), ("20d", 20)]:
                window = mf_before.iloc[max(0, n_mf - days):]
                if len(window) > 0:
                    features[f"net_flow_{horizon}"] = float(window["net_amount"].sum())
                else:
                    features[f"net_flow_{horizon}"] = np.nan
            if n_mf >= 5:
                recent_mf = mf_before.iloc[-5:]
                big_buy = (recent_mf["buy_elg"] + recent_mf["buy_lg"]).sum()
                big_sell = (recent_mf["sell_elg"] + recent_mf["sell_lg"]).sum()
                features["big_order_dir_5d"] = 1.0 if big_buy > big_sell else (-1.0 if big_sell > big_buy else 0.0)
                features["big_order_net_5d"] = float(big_buy - big_sell)
            else:
                features["big_order_dir_5d"] = np.nan
                features["big_order_net_5d"] = np.nan
        else:
            for col in ["net_flow_5d", "net_flow_20d", "big_order_dir_5d", "big_order_net_5d"]:
                features[col] = np.nan

        # --- Technical features ---
        # RSI(14)
        if i >= 14:
            deltas = np.diff(closes[i - 14:i + 1])
            gains = np.maximum(deltas, 0)
            losses = np.maximum(-deltas, 0)
            avg_gain, avg_loss = np.mean(gains), np.mean(losses)
            rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
            features["rsi_14"] = float(100.0 - 100.0 / (1.0 + rs))
        else:
            features["rsi_14"] = np.nan

        # MACD histogram
        if i >= 26:
            ema12 = _ema(closes[i - 26:i + 1], 12)
            ema26 = _ema(closes[i - 26:i + 1], 26)
            dif = ema12[-1] - ema26[-1]
            dea = _ema(np.full(9, dif), 9)[-1] if not np.isnan(dif) else np.nan
            features["macd_hist"] = float((dif - dea) * 2) if not (np.isnan(dif) or np.isnan(dea)) else np.nan
        else:
            features["macd_hist"] = np.nan

        # Price vs MA
        for period, key in [(20, "price_vs_ma20_pct"), (50, "price_vs_ma50_pct")]:
            if i >= period - 1:
                ma = np.mean(closes[i - period + 1:i + 1])
                features[key] = float((close_t - ma) / ma * 100) if ma > 0 else np.nan
            else:
                features[key] = np.nan

        # ATR(14)
        if i >= 14:
            trs = []
            for k in range(i - 13, i + 1):
                tr = max(highs[k] - lows[k],
                         abs(highs[k] - closes[k - 1]),
                         abs(lows[k] - closes[k - 1]))
                trs.append(tr)
            features["atr_14"] = float(np.mean(trs))
        else:
            features["atr_14"] = np.nan

        # Bollinger position
        if i >= 19:
            ma20 = np.mean(closes[i - 19:i + 1])
            std20 = np.std(closes[i - 19:i + 1])
            features["bb_position"] = float((close_t - ma20) / (2 * std20)) if std20 > 0 else np.nan
        else:
            features["bb_position"] = np.nan

        # Context features (may be NaN)
        features.update({
            "sector_rps": np.nan,
            "market_northbound_flow_dir": np.nan,
            "pe_percentile": np.nan,
            "pb_percentile": np.nan,
        })

        # --- Targets (forward-looking) ---
        for horizon_days, label in [(5, "5d"), (20, "20d")]:
            future_idx = i + horizon_days
            if future_idx < n:
                future_close = closes[future_idx]
                ret = (future_close - close_t) / close_t if close_t > 0 else 0.0
                features[f"target_{label}_ret"] = ret
                features[f"target_{label}_dir"] = 1 if ret > 0.005 else 0
            else:
                features[f"target_{label}_ret"] = np.nan
                features[f"target_{label}_dir"] = np.nan

        rows.append(features)

    if not rows:
        return None

    df = pd.DataFrame(rows)
    # Reorder: features then targets
    target_cols = ["target_5d_dir", "target_20d_dir", "target_5d_ret", "target_20d_ret"]
    cols = FEATURE_COLUMNS + target_cols
    return df[[c for c in cols if c in df.columns]]


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
