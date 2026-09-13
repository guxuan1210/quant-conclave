"""Feature engineering pipeline for PredictionAgent.

Transforms raw market data (OHLCV, capital flow, fundamentals) into a
standardized numeric feature vector suitable for XGBoost models.
"""
from __future__ import annotations

import io
import csv
import logging
from typing import Optional

import numpy as np
import pandas as pd

from capitalradar.dataflows.interface import route_to_vendor

logger = logging.getLogger(__name__)

# Column order that models expect — must be consistent across training
# and inference.  All values are floats; missing data → NaN.
FEATURE_COLUMNS = [
    # Price momentum
    "return_5d", "return_10d", "return_20d",
    # Volatility
    "volatility_20d",
    # Drawdown
    "max_drawdown_20d",
    # Volume
    "volume_ratio_5d", "volume_ratio_20d", "volume_trend",
    # Capital flow
    "net_flow_5d", "net_flow_20d", "big_order_dir_5d",
    "big_order_net_5d",
    # Technical indicators
    "rsi_14", "macd_hist", "price_vs_ma20_pct", "price_vs_ma50_pct",
    "atr_14", "bb_position",
    # Market context (may be NaN if unavailable)
    "sector_rps", "market_northbound_flow_dir",
    # Valuation (may be NaN if unavailable)
    "pe_percentile", "pb_percentile",
]


class FeatureEngine:
    """Build standardized feature vectors from raw market data.

    Uses the existing ``route_to_vendor`` dispatch so vendor selection
    (tushare/akshare/yfinance) is handled by the framework's config.

    Usage::

        engine = FeatureEngine()
        features = engine.build_features("601127.SH", "2026-06-24")
    """

    def __init__(self, lookback_days: int = 120):
        self._lookback_days = lookback_days

    # ── Public API ──────────────────────────────────────────────────

    def build_features(
        self, ticker: str, trade_date: str, config: dict | None = None,
    ) -> pd.DataFrame:
        """Build a single-row DataFrame of features for *ticker* on *trade_date*.

        Returns a DataFrame with one row and columns matching FEATURE_COLUMNS.
        Missing or unavailable data is represented as NaN.
        """
        ohlcv = self._fetch_ohlcv(ticker, trade_date)
        moneyflow = self._fetch_moneyflow(ticker, trade_date)

        features: dict[str, float] = {}

        if ohlcv is not None and len(ohlcv) >= 5:
            features.update(self._price_features(ohlcv))
            features.update(self._volume_features(ohlcv))
            features.update(self._technical_features(ohlcv))
        else:
            logger.warning("Insufficient OHLCV data for %s", ticker)

        if moneyflow is not None and len(moneyflow) >= 5:
            features.update(self._capital_flow_features(moneyflow))

        features.update(self._context_features(ticker, trade_date, config))

        # Ensure all expected columns exist, filling missing with NaN
        row = {}
        for col in FEATURE_COLUMNS:
            row[col] = features.get(col, np.nan)
        return pd.DataFrame([row], columns=FEATURE_COLUMNS)

    # ── Data fetching ───────────────────────────────────────────────

    def _fetch_ohlcv(self, ticker: str, trade_date: str) -> pd.DataFrame | None:
        """Fetch OHLCV bars as a DataFrame (index=date, cols=Open/High/Low/Close/Volume)."""
        from datetime import datetime, timedelta
        end = datetime.strptime(trade_date, "%Y-%m-%d")
        start = end - timedelta(days=self._lookback_days)
        try:
            raw = route_to_vendor(
                "get_stock_data", ticker,
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning("get_stock_data failed for %s: %s", ticker, e)
            return None

        return self._parse_ohlcv(str(raw))

    def _fetch_moneyflow(self, ticker: str, trade_date: str) -> pd.DataFrame | None:
        """Fetch capital flow data as a DataFrame."""
        from capitalradar.agents.utils.capital_flow_tools import get_money_flow
        from datetime import datetime, timedelta
        end = datetime.strptime(trade_date, "%Y-%m-%d")
        start = end - timedelta(days=self._lookback_days)
        try:
            raw = get_money_flow.invoke({
                "ticker": ticker,
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
            })
        except Exception as e:
            logger.warning("get_money_flow failed for %s: %s", ticker, e)
            return None

        return self._parse_moneyflow(str(raw))

    @staticmethod
    def _parse_ohlcv(text: str) -> pd.DataFrame | None:
        """Parse CSV-format OHLCV text into a DataFrame."""
        lines = text.split("\n")
        # Find the header line
        csv_start = next(
            (i for i, l in enumerate(lines)
             if "trade_date" in l.lower() or "Date" in l or "date" in l.lower()),
            None,
        )
        if csv_start is None:
            return None
        reader = csv.DictReader(io.StringIO("\n".join(lines[csv_start:])))
        rows = []
        for r in reader:
            try:
                date_col = r.get("trade_date", r.get("Date", r.get("date", "")))
                close = float(r.get("close", r.get("Close", 0)) or 0)
                if close <= 0:
                    continue
                # Tushare uses 'vol'; other vendors may use 'volume' or 'Volume'
                vol_str = r.get("volume", r.get("Volume", r.get("vol", "0")))
                volume = float(vol_str or 0)
                rows.append({
                    "date": str(date_col).strip()[:10],
                    "open": float(r.get("open", r.get("Open", close)) or close),
                    "high": float(r.get("high", r.get("High", close)) or close),
                    "low": float(r.get("low", r.get("Low", close)) or close),
                    "close": close,
                    "volume": volume,
                })
            except (ValueError, KeyError):
                continue
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
        return df

    @staticmethod
    def _parse_moneyflow(text: str) -> pd.DataFrame | None:
        """Parse CSV-format moneyflow text into a DataFrame."""
        lines = text.split("\n")
        csv_start = next(
            (i for i, l in enumerate(lines)
             if "ts_code" in l and "trade_date" in l), 0,
        )
        if csv_start is None:
            return None
        reader = csv.DictReader(io.StringIO("\n".join(lines[csv_start:])))
        rows = []
        for r in reader:
            try:
                net = float(r.get("net_amount", 0) or 0)
                rows.append({
                    "date": str(r.get("trade_date", "")).strip()[:10],
                    "net_amount": net,
                    "buy_elg": float(r.get("buy_elg_amount", 0) or 0),
                    "sell_elg": float(r.get("sell_elg_amount", 0) or 0),
                    "buy_lg": float(r.get("buy_lg_amount", 0) or 0),
                    "sell_lg": float(r.get("sell_lg_amount", 0) or 0),
                })
            except (ValueError, KeyError):
                continue
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
        return df

    # ── Feature extractors ──────────────────────────────────────────

    @staticmethod
    def _price_features(df: pd.DataFrame) -> dict[str, float]:
        """Extract price momentum and volatility features."""
        closes = df["close"].values
        n = len(closes)
        features = {}
        for horizon, idx in [("5d", max(0, n - 5)), ("10d", max(0, n - 10)),
                              ("20d", max(0, n - 20))]:
            if n > n - idx:
                features[f"return_{horizon}"] = (
                    closes[-1] - closes[idx]) / closes[idx] if closes[idx] > 0 else 0.0
            else:
                features[f"return_{horizon}"] = np.nan

        if n >= 20:
            returns = np.diff(closes[-21:]) / closes[-21:-1]
            features["volatility_20d"] = float(np.std(returns) * np.sqrt(252))
            peak = np.maximum.accumulate(closes[-20:])
            drawdowns = (closes[-20:] - peak) / peak
            features["max_drawdown_20d"] = float(np.min(drawdowns))
        else:
            features["volatility_20d"] = np.nan
            features["max_drawdown_20d"] = np.nan
        return features

    @staticmethod
    def _volume_features(df: pd.DataFrame) -> dict[str, float]:
        """Extract volume-based features."""
        volumes = df["volume"].values
        n = len(volumes)
        features = {}
        if n >= 20:
            avg_20 = np.mean(volumes[-20:])
            features["volume_ratio_20d"] = (
                volumes[-1] / avg_20 if avg_20 > 0 else np.nan
            )
        else:
            features["volume_ratio_20d"] = np.nan
        if n >= 5:
            avg_5 = np.mean(volumes[-5:])
            features["volume_ratio_5d"] = (
                volumes[-1] / avg_5 if avg_5 > 0 else np.nan
            )
        else:
            features["volume_ratio_5d"] = np.nan
        # Volume trend: slope of 10-day volume
        if n >= 10:
            v10 = volumes[-10:]
            x = np.arange(10)
            slope = np.polyfit(x, v10, 1)[0] if np.std(v10) > 0 else 0.0
            features["volume_trend"] = float(slope / np.mean(v10)) if np.mean(v10) > 0 else 0.0
        else:
            features["volume_trend"] = np.nan
        return features

    @staticmethod
    def _capital_flow_features(df: pd.DataFrame) -> dict[str, float]:
        """Extract capital flow features from moneyflow data."""
        n = len(df)
        features = {}
        for horizon, days in [("5d", 5), ("20d", 20)]:
            window = df.iloc[max(0, n - days):]
            if len(window) > 0:
                features[f"net_flow_{horizon}"] = float(window["net_amount"].sum())
            else:
                features[f"net_flow_{horizon}"] = np.nan

        # Big-order direction (last 5 days)
        if n >= 5:
            recent = df.iloc[-5:]
            big_buy = (recent["buy_elg"] + recent["buy_lg"]).sum()
            big_sell = (recent["sell_elg"] + recent["sell_lg"]).sum()
            features["big_order_dir_5d"] = 1.0 if big_buy > big_sell else (-1.0 if big_sell > big_buy else 0.0)
            features["big_order_net_5d"] = float(big_buy - big_sell)
        else:
            features["big_order_dir_5d"] = np.nan
            features["big_order_net_5d"] = np.nan
        return features

    @staticmethod
    def _technical_features(df: pd.DataFrame) -> dict[str, float]:
        """Compute technical indicators from OHLCV DataFrame."""
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        n = len(closes)
        features = {}

        # RSI(14)
        if n >= 15:
            deltas = np.diff(closes[-15:])
            gains = np.maximum(deltas, 0)
            losses = np.maximum(-deltas, 0)
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
            features["rsi_14"] = float(100.0 - 100.0 / (1.0 + rs))
        else:
            features["rsi_14"] = np.nan

        # MACD histogram
        if n >= 26:
            ema12 = FeatureEngine._ema(closes, 12)
            ema26 = FeatureEngine._ema(closes, 26)
            dif = ema12[-1] - ema26[-1]
            dea = FeatureEngine._ema(np.array([dif] * 9), 9)[-1] if not np.isnan(dif) else np.nan
            features["macd_hist"] = float((dif - dea) * 2) if not (np.isnan(dif) or np.isnan(dea)) else np.nan
        else:
            features["macd_hist"] = np.nan

        # Price vs MA20 / MA50 distance
        for period, key in [(20, "price_vs_ma20_pct"), (50, "price_vs_ma50_pct")]:
            if n >= period:
                ma = np.mean(closes[-period:])
                features[key] = float((closes[-1] - ma) / ma * 100) if ma > 0 else np.nan
            else:
                features[key] = np.nan

        # ATR(14)
        if n >= 15:
            trs = []
            for i in range(-14, 0):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
                trs.append(tr)
            features["atr_14"] = float(np.mean(trs))
        else:
            features["atr_14"] = np.nan

        # Bollinger position
        if n >= 20:
            ma20 = np.mean(closes[-20:])
            std20 = np.std(closes[-20:])
            features["bb_position"] = (
                float((closes[-1] - ma20) / (2 * std20)) if std20 > 0 else np.nan
            )
        else:
            features["bb_position"] = np.nan
        return features

    @staticmethod
    def _context_features(
        ticker: str, trade_date: str, config: dict | None,
    ) -> dict[str, float]:
        """Extract market context features (may be NaN for unavailable data)."""
        features: dict[str, float] = {
            "sector_rps": np.nan,
            "market_northbound_flow_dir": np.nan,
            "pe_percentile": np.nan,
            "pb_percentile": np.nan,
        }
        # Try to get sector RPS
        try:
            from capitalradar.sector_scan.rps import get_rps
            rps_val = get_rps(ticker, 120)
            if rps_val is not None:
                features["sector_rps"] = float(rps_val)
        except Exception:
            pass
        # Try to get northbound flow direction
        try:
            from capitalradar.agents.utils.capital_flow_tools import get_hsgt_flow
            from datetime import datetime, timedelta
            end = datetime.strptime(trade_date, "%Y-%m-%d")
            start = end - timedelta(days=10)
            hsgt_raw = get_hsgt_flow.invoke({
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
            })
            hsgt_text = str(hsgt_raw)
            if "流入" in hsgt_text or "净买" in hsgt_text:
                features["market_northbound_flow_dir"] = 1.0
            elif "流出" in hsgt_text or "净卖" in hsgt_text:
                features["market_northbound_flow_dir"] = -1.0
            else:
                features["market_northbound_flow_dir"] = 0.0
        except Exception:
            pass
        return features

    @staticmethod
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
