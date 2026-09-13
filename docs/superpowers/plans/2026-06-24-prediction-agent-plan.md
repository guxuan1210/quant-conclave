# PredictionAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `PredictionAgent` — a standalone prediction module that uses XGBoost for price range (Tool A) and direction probability (Tool B), plus LLM for behavior pattern recognition (Tool C), with a synthesis layer for cross-validation and Chinese-language reporting.

**Architecture:** Independent `capitalradar/prediction/` package with 10 files. `FeatureEngine` builds a standardized feature vector from OHLCV + capital flow + technicals. `PricePredictor` and `DirectionPredictor` load trained XGBoost models. `BehaviorPredictor` queries Smart Money Score + Memory Log and calls LLM. `PredictionAgent` orchestrates all three and runs cross-validation before producing a `PredictionReport`. No changes to existing graph pipeline.

**Tech Stack:** xgboost, scikit-learn (isotonic calibration), numpy, pandas. Reuses existing `capitalradar.dataflows` (tushare/akshare/yfinance), `capitalradar.llm_clients`, `capitalradar.agents.utils.memory`.

**Scope Note:** This plan covers the inference/prediction side. Training pipeline (`trainer.py`, `data_pipeline.py`) and integration (Advisor tool, PM prompt, web UI card) are deferred to a follow-up plan after the core prediction engine is verified working.

---

### Task 1: Package scaffolding + schemas

**Files:**
- Create: `capitalradar/prediction/__init__.py`
- Create: `capitalradar/prediction/schemas.py`
- Create: `tests/prediction/__init__.py`
- Create: `tests/prediction/test_schemas.py`

- [ ] **Step 1: Create the prediction package directory and init**

```
capitalradar/prediction/__init__.py
```
```python
"""PredictionAgent — ML + LLM forecasting for CapitalRadar.

Public API:
    PredictionAgent.predict(ticker, trade_date, config) → PredictionReport
"""
from capitalradar.prediction.agent import PredictionAgent

__all__ = ["PredictionAgent"]
```

```
tests/prediction/__init__.py
```
(empty file)

- [ ] **Step 2: Write the schemas with Pydantic models**

```python
"""Pydantic schemas for PredictionAgent outputs."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────


class PredictionHorizon(str, Enum):
    SHORT = "5d"
    MEDIUM = "20d"


class ConfidenceTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BehaviorPhase(str, Enum):
    ACCUMULATION = "accumulation"
    SHAKEOUT = "shakeout"
    MARKUP = "markup"
    DISTRIBUTION = "distribution"
    EXIT = "exit"
    UNKNOWN = "unknown"


class CrossValidationVerdict(str, Enum):
    ALIGNED = "aligned"
    CONFLICT = "conflict"
    BEHAVIOR_MISMATCH = "behavior_model_mismatch"
    PRICE_MODEL_MISSING = "price_model_missing"


# ── Tool outputs ──────────────────────────────────────────────────────


class PriceRangeOutput(BaseModel):
    """Tool A: predicted price confidence interval."""
    horizon: PredictionHorizon
    lower_bound: float
    upper_bound: float
    median: float
    current_price: float
    interval_width_pct: float
    confidence: ConfidenceTier
    model_available: bool = True
    model_version: str = ""


class DirectionOutput(BaseModel):
    """Tool B: calibrated up/down probability."""
    horizon: PredictionHorizon
    up_probability: float
    down_probability: float
    confidence: ConfidenceTier
    calibration_score: float = 0.0
    model_available: bool = True
    model_version: str = ""


class HistoricalAnalog(BaseModel):
    """A past trade with similar Smart Money Score patterns."""
    date: str
    ticker: str
    phase: str
    outcome: str
    raw_return: Optional[float] = None


class BehaviorOutput(BaseModel):
    """Tool C: institutional behavior phase + next-move prediction."""
    current_phase: BehaviorPhase
    phase_confidence: ConfidenceTier
    predicted_next_behavior: str
    behavior_rationale: str
    historical_analogs: list[HistoricalAnalog] = Field(default_factory=list)
    risk_scenario: str = ""
    model_available: bool = True


# ── Aggregate report ──────────────────────────────────────────────────


class PredictionReport(BaseModel):
    """Full prediction output from PredictionAgent.predict()."""
    ticker: str
    trade_date: str
    current_price: Optional[float] = None

    # Tool outputs
    price_short: Optional[PriceRangeOutput] = None
    price_medium: Optional[PriceRangeOutput] = None
    direction_short: Optional[DirectionOutput] = None
    direction_medium: Optional[DirectionOutput] = None
    behavior: Optional[BehaviorOutput] = None

    # Cross-validation
    cross_validation: CrossValidationVerdict = CrossValidationVerdict.ALIGNED
    cross_validation_detail: str = ""

    # LLM synthesis
    synthesis_summary: str = ""       # 2-3 paragraph Chinese summary
    synthesis_guidance: str = ""      # actionable advice for PM
    overall_confidence: ConfidenceTier = ConfidenceTier.LOW

    # Errors
    errors: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the prediction report as markdown for PM/advisory consumption."""
        lines = [
            f"# Prediction Report: {self.ticker}",
            f"**Analysis Date:** {self.trade_date}",
            "",
        ]
        if self.current_price:
            lines.append(f"**Current Price:** {self.current_price:.2f}")
            lines.append("")

        if self.errors:
            lines.append("## Errors")
            for e in self.errors:
                lines.append(f"- {e}")
            lines.append("")

        if self.direction_short or self.direction_medium:
            lines.append("## Direction Forecast")
            lines.append("")
            lines.append("| Horizon | Up Probability | Down Probability | Confidence |")
            lines.append("|---------|---------------|------------------|------------|")
            for d in [self.direction_short, self.direction_medium]:
                if d:
                    lines.append(
                        f"| {d.horizon.value} | {d.up_probability:.0%} | "
                        f"{d.down_probability:.0%} | {d.confidence.value} |"
                    )
            lines.append("")

        if self.price_short or self.price_medium:
            lines.append("## Price Range Forecast")
            lines.append("")
            lines.append("| Horizon | Lower | Median | Upper | Width | Confidence |")
            lines.append("|---------|-------|--------|-------|-------|------------|")
            for p in [self.price_short, self.price_medium]:
                if p:
                    lines.append(
                        f"| {p.horizon.value} | {p.lower_bound:.2f} | "
                        f"{p.median:.2f} | {p.upper_bound:.2f} | "
                        f"{p.interval_width_pct:.1f}% | {p.confidence.value} |"
                    )
            lines.append("")

        if self.behavior:
            lines.append("## Institutional Behavior Analysis")
            lines.append("")
            lines.append(f"**Current Phase:** {self.behavior.current_phase.value}")
            lines.append(f"**Phase Confidence:** {self.behavior.phase_confidence.value}")
            lines.append(f"**Predicted Next Move:** {self.behavior.predicted_next_behavior}")
            lines.append(f"**Rationale:** {self.behavior.behavior_rationale}")
            if self.behavior.risk_scenario:
                lines.append(f"**Risk Scenario:** {self.behavior.risk_scenario}")
            lines.append("")

        lines.append(f"**Cross-Validation:** {self.cross_validation.value}")
        if self.cross_validation_detail:
            lines.append(f"  {self.cross_validation_detail}")
        lines.append(f"**Overall Confidence:** {self.overall_confidence.value}")
        lines.append("")

        if self.synthesis_summary:
            lines.append("## Synthesis")
            lines.append("")
            lines.append(self.synthesis_summary)
            lines.append("")

        if self.synthesis_guidance:
            lines.append("## Guidance for Portfolio Manager")
            lines.append("")
            lines.append(self.synthesis_guidance)
            lines.append("")

        return "\n".join(lines)
```

- [ ] **Step 3: Write the schema test**

```python
"""Tests for prediction schemas."""
import pytest
from capitalradar.prediction.schemas import (
    PredictionReport, PriceRangeOutput, DirectionOutput,
    BehaviorOutput, BehaviorPhase, ConfidenceTier,
    PredictionHorizon, CrossValidationVerdict,
)


class TestPriceRangeOutput:
    def test_valid_output(self):
        p = PriceRangeOutput(
            horizon=PredictionHorizon.SHORT,
            lower_bound=58.50, upper_bound=64.20, median=61.00,
            current_price=63.00, interval_width_pct=9.0,
            confidence=ConfidenceTier.MEDIUM,
        )
        assert p.lower_bound < p.median < p.upper_bound
        assert p.interval_width_pct == 9.0

    def test_model_unavailable(self):
        p = PriceRangeOutput(
            horizon=PredictionHorizon.SHORT,
            lower_bound=0, upper_bound=0, median=0,
            current_price=63.00, interval_width_pct=0,
            confidence=ConfidenceTier.LOW, model_available=False,
        )
        assert not p.model_available


class TestDirectionOutput:
    def test_probabilities_sum_to_one(self):
        d = DirectionOutput(
            horizon=PredictionHorizon.MEDIUM,
            up_probability=0.38, down_probability=0.62,
            confidence=ConfidenceTier.MEDIUM, calibration_score=0.92,
        )
        assert pytest.approx(d.up_probability + d.down_probability, abs=0.01) == 1.0

    def test_confidence_tiers(self):
        high = DirectionOutput(
            horizon=PredictionHorizon.SHORT,
            up_probability=0.72, down_probability=0.28,
            confidence=ConfidenceTier.HIGH,
        )
        assert high.confidence == ConfidenceTier.HIGH
        low = DirectionOutput(
            horizon=PredictionHorizon.SHORT,
            up_probability=0.52, down_probability=0.48,
            confidence=ConfidenceTier.LOW,
        )
        assert low.confidence == ConfidenceTier.LOW


class TestPredictionReport:
    def test_to_markdown_all_present(self):
        report = PredictionReport(
            ticker="601127.SS", trade_date="2026-06-24",
            current_price=61.80,
            direction_short=DirectionOutput(
                horizon=PredictionHorizon.SHORT,
                up_probability=0.38, down_probability=0.62,
                confidence=ConfidenceTier.MEDIUM, calibration_score=0.92,
            ),
            price_short=PriceRangeOutput(
                horizon=PredictionHorizon.SHORT,
                lower_bound=58.50, upper_bound=64.20, median=61.00,
                current_price=61.80, interval_width_pct=9.2,
                confidence=ConfidenceTier.MEDIUM,
            ),
            behavior=BehaviorOutput(
                current_phase=BehaviorPhase.DISTRIBUTION,
                phase_confidence=ConfidenceTier.HIGH,
                predicted_next_behavior="continue distributing",
                behavior_rationale="超大单40日>90%净流出",
            ),
            cross_validation=CrossValidationVerdict.ALIGNED,
            synthesis_summary="综合判断...",
            synthesis_guidance="建议等待企稳信号",
            overall_confidence=ConfidenceTier.MEDIUM,
        )
        md = report.to_markdown()
        assert "601127.SS" in md
        assert "61.80" in md
        assert "38%" in md
        assert "58.50" in md
        assert "continue distributing" in md

    def test_to_markdown_with_errors(self):
        report = PredictionReport(
            ticker="000001.SZ", trade_date="2026-06-24",
            errors=["Price model failed: insufficient data"],
        )
        md = report.to_markdown()
        assert "Price model failed" in md
```

- [ ] **Step 4: Run tests to verify they fail (module doesn't exist yet)**

Run: `pytest tests/prediction/test_schemas.py -v`
Expected: FAIL with import error

- [ ] **Step 5: Create the schemas module and run tests to verify they pass**

Run: `pytest tests/prediction/test_schemas.py -v`
Expected: all 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add capitalradar/prediction/__init__.py capitalradar/prediction/schemas.py tests/prediction/
git commit -m "feat(prediction): add PredictionAgent package scaffolding and schemas"
```

---

### Task 2: FeatureEngine

**Files:**
- Create: `capitalradar/prediction/feature_engine.py`
- Create: `tests/prediction/test_feature_engine.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for FeatureEngine."""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from capitalradar.prediction.feature_engine import FeatureEngine


MOCK_OHLCV_CSV = """Date,Open,High,Low,Close,Volume
2026-06-17,63.00,64.00,62.00,63.50,10000000
2026-06-18,63.50,66.50,63.00,66.14,15000000
2026-06-19,66.14,66.50,64.00,64.81,12000000
2026-06-20,64.81,65.50,63.00,63.20,9000000
2026-06-23,63.20,64.00,61.50,63.00,11000000
2026-06-24,63.00,63.50,61.00,61.80,14000000
"""

MOCK_MONEYFLOW_CSV = """ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount
601127.SH,20260617,-50000000,80000000,130000000,20000000,15000000
601127.SH,20260618,-30000000,90000000,120000000,18000000,20000000
601127.SH,20260619,-80000000,70000000,150000000,25000000,30000000
601127.SH,20260620,-20000000,85000000,105000000,22000000,18000000
601127.SH,20260623,-60000000,75000000,135000000,19000000,25000000
601127.SH,20260624,-40000000,80000000,120000000,21000000,22000000
"""


class TestFeatureEngine:
    @patch("capitalradar.prediction.feature_engine.route_to_vendor")
    def test_build_features_returns_dataframe(self, mock_route):
        """FeatureEngine should return a DataFrame with one row and correct columns."""
        mock_route.side_effect = lambda method, *args, **kwargs: {
            "get_stock_data": MOCK_OHLCV_CSV,
            "get_money_flow": MOCK_MONEYFLOW_CSV,
        }[method]

        engine = FeatureEngine()
        df = engine.build_features("601127.SH", "2026-06-24")

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1  # one row for the requested date

    @patch("capitalradar.prediction.feature_engine.route_to_vendor")
    def test_build_features_has_required_columns(self, mock_route):
        """Output must include price, volume, capital flow, and technical columns."""
        mock_route.side_effect = lambda method, *args, **kwargs: {
            "get_stock_data": MOCK_OHLCV_CSV,
            "get_money_flow": MOCK_MONEYFLOW_CSV,
        }[method]

        engine = FeatureEngine()
        df = engine.build_features("601127.SH", "2026-06-24")

        required = [
            "return_5d", "return_20d", "volatility_20d",
            "volume_ratio_5d", "volume_ratio_20d",
            "net_flow_5d", "net_flow_20d", "big_order_dir_5d",
            "rsi_14", "macd_hist", "price_vs_ma20_pct", "price_vs_ma50_pct",
            "atr_14", "bb_position",
        ]
        for col in required:
            assert col in df.columns, f"Missing column: {col}"

    @patch("capitalradar.prediction.feature_engine.route_to_vendor")
    def test_missing_data_returns_partial_features(self, mock_route):
        """Should return partial features with NaN for unavailable data."""
        mock_route.side_effect = lambda method, *args, **kwargs: {
            "get_stock_data": MOCK_OHLCV_CSV,
            "get_money_flow": "# SKIP_VENDOR: no data\n",
        }[method]

        engine = FeatureEngine()
        df = engine.build_features("601127.SH", "2026-06-24")
        assert df is not None
        assert pd.isna(df["net_flow_5d"].iloc[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prediction/test_feature_engine.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'capitalradar.prediction.feature_engine'"

- [ ] **Step 3: Implement FeatureEngine**

```python
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

    def __init__(self, lookback_days: int = 60):
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
                rows.append({
                    "date": str(date_col).strip()[:10],
                    "open": float(r.get("open", r.get("Open", close)) or close),
                    "high": float(r.get("high", r.get("High", close)) or close),
                    "low": float(r.get("low", r.get("Low", close)) or close),
                    "close": close,
                    "volume": float(r.get("volume", r.get("Volume", 0)) or 0),
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prediction/test_feature_engine.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add capitalradar/prediction/feature_engine.py tests/prediction/test_feature_engine.py
git commit -m "feat(prediction): add FeatureEngine with price/volume/capital-flow/technical features"
```

---

### Task 3: ModelRegistry

**Files:**
- Create: `capitalradar/prediction/model_registry.py`
- Create: `tests/prediction/test_model_registry.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for ModelRegistry."""
import json
import tempfile
from pathlib import Path
import pytest
import xgboost as xgb
import numpy as np
from capitalradar.prediction.model_registry import ModelRegistry


class TestModelRegistry:
    @pytest.fixture
    def tmp_models_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    @pytest.fixture
    def dummy_model(self):
        """Train a minimal XGBoost model for testing."""
        X = np.random.randn(100, 18)  # 18 features = len(FEATURE_COLUMNS)
        y = (X[:, 0] > 0).astype(float)
        model = xgb.XGBClassifier(n_estimators=2, max_depth=2)
        model.fit(X, y)
        return model

    def test_save_and_load_model(self, tmp_models_dir, dummy_model):
        registry = ModelRegistry(models_dir=str(tmp_models_dir))
        registry.save_model(dummy_model, "direction_5d")
        assert (tmp_models_dir / "direction_5d.json").exists()

        loaded = registry.load_model("direction_5d")
        assert loaded is not None
        assert isinstance(loaded, xgb.XGBClassifier)

    def test_load_missing_model_returns_none(self, tmp_models_dir):
        registry = ModelRegistry(models_dir=str(tmp_models_dir))
        result = registry.load_model("direction_5d")
        assert result is None

    def test_metadata_is_persisted(self, tmp_models_dir, dummy_model):
        registry = ModelRegistry(models_dir=str(tmp_models_dir))
        registry.save_model(dummy_model, "direction_5d", metrics={"accuracy": 0.62})
        meta = registry.get_metadata()
        assert "direction_5d" in meta
        assert meta["direction_5d"]["metrics"]["accuracy"] == 0.62
        assert "training_date" in meta["direction_5d"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prediction/test_model_registry.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ModelRegistry**

```python
"""Model loading, saving, versioning, and fallback for PredictionAgent."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import xgboost as xgb

from capitalradar.prediction.feature_engine import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

_MODEL_METADATA_FILE = "model_metadata.json"
_N_FEATURES = len(FEATURE_COLUMNS)


class ModelRegistry:
    """Load and save XGBoost models with metadata tracking.

    Models are stored as JSON files in ``models_dir`` (default:
    ``~/.capitalradar/models/``).  Metadata (training date, feature list,
    calibration curve, metrics) is persisted in ``model_metadata.json``.

    Usage::

        registry = ModelRegistry()
        model = registry.load_model("direction_5d")
        if model is None:
            # no trained model yet — caller should use fallback
            ...
    """

    def __init__(self, models_dir: str = ""):
        if models_dir:
            self._dir = Path(models_dir)
        else:
            from pathlib import Path as P
            self._dir = P.home() / ".capitalradar" / "models"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path = self._dir / _MODEL_METADATA_FILE

    # ── Public API ──────────────────────────────────────────────────

    def load_model(self, name: str) -> xgb.XGBModel | None:
        """Load a trained model by name. Returns None if not found."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            logger.info("Model %s not found at %s", name, path)
            return None

        try:
            if "direction" in name:
                model = xgb.XGBClassifier()
            else:
                model = xgb.XGBRegressor()
            model.load_model(str(path))
            # Validate feature count
            if hasattr(model, "n_features_in_"):
                expected = model.n_features_in_
                if expected != _N_FEATURES:
                    logger.warning(
                        "Model %s expects %d features but FeatureEngine produces %d",
                        name, expected, _N_FEATURES,
                    )
            logger.info("Loaded model %s", name)
            return model
        except Exception as e:
            logger.error("Failed to load model %s: %s", name, e)
            return None

    def load_calibrator(self, name: str):
        """Load an isotonic calibration curve. Returns None if not found."""
        import pickle
        path = self._dir / f"{name}.pkl"
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.error("Failed to load calibrator %s: %s", name, e)
            return None

    def save_model(
        self, model: xgb.XGBModel, name: str, metrics: dict | None = None,
    ) -> None:
        """Save a trained model and update metadata."""
        path = self._dir / f"{name}.json"
        model.save_model(str(path))
        self._update_metadata(name, metrics or {})
        logger.info("Saved model %s", name)

    def save_calibrator(self, calibrator, name: str) -> None:
        """Save an isotonic calibration curve."""
        import pickle
        path = self._dir / f"{name}.pkl"
        with open(path, "wb") as f:
            pickle.dump(calibrator, f)

    def get_metadata(self) -> dict:
        """Return the full metadata dict."""
        if not self._metadata_path.exists():
            return {}
        try:
            return json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def list_available_models(self) -> list[str]:
        """List model names that have saved files."""
        return sorted(
            p.stem for p in self._dir.glob("*.json")
            if p.name != _MODEL_METADATA_FILE
        )

    # ── Internal ────────────────────────────────────────────────────

    def _update_metadata(self, name: str, metrics: dict) -> None:
        meta = self.get_metadata()
        meta[name] = {
            "training_date": datetime.now(timezone.utc).isoformat(),
            "features": FEATURE_COLUMNS,
            "n_features": _N_FEATURES,
            "metrics": metrics,
        }
        self._metadata_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prediction/test_model_registry.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add capitalradar/prediction/model_registry.py tests/prediction/test_model_registry.py
git commit -m "feat(prediction): add ModelRegistry for XGBoost model save/load/versioning"
```

---

### Task 4: Install xgboost dependency

**Files:**
- Modify: `pyproject.toml` (add xgboost to dependencies)

- [ ] **Step 1: Add xgboost to pyproject.toml dependencies**

Add `"xgboost>=2.1.0",` to the `dependencies` list in `pyproject.toml`, between the last existing entry and the closing bracket. Insert after `"fpdf2>=2.8.0",`:

```toml
    "fpdf2>=2.8.0",
    "xgboost>=2.1.0",
]
```

- [ ] **Step 2: Install xgboost**

Run:
```bash
.venv/Scripts/pip install "xgboost>=2.1.0"
```
Expected: Successfully installed xgboost

- [ ] **Step 3: Verify xgboost import works**

Run:
```bash
.venv/Scripts/python.exe -c "import xgboost; print(xgboost.__version__)"
```
Expected: prints version number

- [ ] **Step 4: Verify existing tests still pass**

Run:
```bash
pytest tests/prediction/ -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "feat(prediction): add xgboost>=2.1.0 dependency"
```

---

### Task 5: DirectionPredictor (Tool B)

**Files:**
- Create: `capitalradar/prediction/direction_predictor.py`
- Create: `tests/prediction/test_direction_predictor.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for DirectionPredictor."""
import pytest
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd
import xgboost as xgb
from capitalradar.prediction.direction_predictor import DirectionPredictor
from capitalradar.prediction.schemas import DirectionOutput, PredictionHorizon, ConfidenceTier
from capitalradar.prediction.feature_engine import FEATURE_COLUMNS


class TestDirectionPredictor:
    @pytest.fixture
    def sample_features(self):
        """A valid feature row."""
        data = {col: np.random.randn() for col in FEATURE_COLUMNS}
        return pd.DataFrame([data], columns=FEATURE_COLUMNS)

    @pytest.fixture
    def trained_model(self):
        """A minimal trained XGBoost classifier."""
        X = np.random.randn(100, len(FEATURE_COLUMNS))
        y = (X[:, 0] > 0).astype(int)
        model = xgb.XGBClassifier(n_estimators=5, max_depth=2)
        model.fit(X, y)
        return model

    def test_predict_returns_direction_output(self, sample_features, trained_model):
        predictor = DirectionPredictor()
        result = predictor._predict_with_model(
            trained_model, sample_features, PredictionHorizon.SHORT,
        )
        assert isinstance(result, DirectionOutput)
        assert result.horizon == PredictionHorizon.SHORT
        assert 0 <= result.up_probability <= 1
        assert 0 <= result.down_probability <= 1
        assert pytest.approx(result.up_probability + result.down_probability, abs=0.02) == 1.0
        assert result.model_available

    def test_predict_no_model_returns_unavailable(self, sample_features):
        predictor = DirectionPredictor()
        predictor._registry = MagicMock()
        predictor._registry.load_model.return_value = None

        result = predictor.predict(sample_features, "601127.SH", "2026-06-24")
        for r in [result["short"], result["medium"]]:
            assert isinstance(r, DirectionOutput)
            assert not r.model_available

    def test_confidence_from_probability(self, sample_features, trained_model):
        predictor = DirectionPredictor()
        # High confidence
        assert predictor._confidence_from_prob(0.75) == ConfidenceTier.HIGH
        # Medium confidence
        assert predictor._confidence_from_prob(0.62) == ConfidenceTier.MEDIUM
        # Low confidence
        assert predictor._confidence_from_prob(0.53) == ConfidenceTier.LOW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prediction/test_direction_predictor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DirectionPredictor**

```python
"""Tool B: XGBoost binary classifier for price direction prediction.

Outputs a calibrated probability of price rising vs. falling over the
given horizon (5-day short-term, 20-day medium-term).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from capitalradar.prediction.feature_engine import FEATURE_COLUMNS
from capitalradar.prediction.model_registry import ModelRegistry
from capitalradar.prediction.schemas import (
    DirectionOutput, PredictionHorizon, ConfidenceTier,
)

logger = logging.getLogger(__name__)


class DirectionPredictor:
    """Predict price direction (up/down) with calibrated probabilities.

    Uses two XGBoost classifiers (5d and 20d horizon), each with an
    isotonic calibration curve to produce well-calibrated probabilities.

    Usage::

        dp = DirectionPredictor()
        result = dp.predict(features_df, ticker, date)
        # result["short"] → DirectionOutput(horizon=5d, up_probability=0.62, ...)
    """

    def __init__(self, models_dir: str = ""):
        self._registry = ModelRegistry(models_dir)

    # ── Public API ──────────────────────────────────────────────────

    def predict(
        self, features: pd.DataFrame, ticker: str, trade_date: str,
    ) -> dict[str, DirectionOutput]:
        """Run direction prediction for both horizons.

        Args:
            features: Single-row DataFrame from FeatureEngine.
            ticker: Stock ticker (for logging only).
            trade_date: Analysis date (for logging only).

        Returns:
            Dict with keys "short" and "medium", each a DirectionOutput.
        """
        results = {}
        for horizon, model_name in [
            (PredictionHorizon.SHORT, "direction_5d"),
            (PredictionHorizon.MEDIUM, "direction_20d"),
        ]:
            model = self._registry.load_model(model_name)
            if model is None:
                results["short" if horizon == PredictionHorizon.SHORT else "medium"] = (
                    DirectionOutput(
                        horizon=horizon,
                        up_probability=0.50, down_probability=0.50,
                        confidence=ConfidenceTier.LOW,
                        model_available=False,
                    )
                )
                continue

            results["short" if horizon == PredictionHorizon.SHORT else "medium"] = (
                self._predict_with_model(model, features, horizon)
            )
        return results

    # ── Internal ────────────────────────────────────────────────────

    def _predict_with_model(
        self, model: xgb.XGBClassifier, features: pd.DataFrame,
        horizon: PredictionHorizon,
    ) -> DirectionOutput:
        """Run prediction using a loaded model with calibration."""
        # Ensure column order matches training
        X = features[FEATURE_COLUMNS].fillna(0).values
        raw_prob = float(model.predict_proba(X)[0, 1])

        # Apply isotonic calibration if available
        calibrated = raw_prob
        calibrator = self._registry.load_calibrator(
            f"calibrator_{horizon.value}"
        )
        if calibrator is not None:
            try:
                calibrated = float(calibrator.predict([raw_prob])[0])
                calibrated = max(0.0, min(1.0, calibrated))
            except Exception:
                pass

        up_prob = round(calibrated, 4)
        down_prob = round(1.0 - calibrated, 4)
        confidence = self._confidence_from_prob(up_prob)

        # Brier score complement from metadata (historical calibration quality)
        meta = self._registry.get_metadata()
        model_meta = meta.get(f"direction_{horizon.value}", {})
        brier_comp = model_meta.get("metrics", {}).get("brier_score_complement", 0.0)

        return DirectionOutput(
            horizon=horizon,
            up_probability=up_prob,
            down_probability=down_prob,
            confidence=confidence,
            calibration_score=round(brier_comp, 4),
            model_available=True,
            model_version=model_meta.get("training_date", ""),
        )

    @staticmethod
    def _confidence_from_prob(up_prob: float) -> ConfidenceTier:
        """Map probability margin from 0.5 to a confidence tier."""
        margin = abs(up_prob - 0.50)
        if margin >= 0.20:
            return ConfidenceTier.HIGH
        if margin >= 0.05:
            return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prediction/test_direction_predictor.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add capitalradar/prediction/direction_predictor.py tests/prediction/test_direction_predictor.py
git commit -m "feat(prediction): add DirectionPredictor (Tool B) with XGBoost + isotonic calibration"
```

---

### Task 6: PricePredictor (Tool A)

**Files:**
- Create: `capitalradar/prediction/price_predictor.py`
- Create: `tests/prediction/test_price_predictor.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for PricePredictor."""
import pytest
from unittest.mock import MagicMock
import numpy as np
import pandas as pd
import xgboost as xgb
from capitalradar.prediction.price_predictor import PricePredictor
from capitalradar.prediction.schemas import PriceRangeOutput, PredictionHorizon, ConfidenceTier
from capitalradar.prediction.feature_engine import FEATURE_COLUMNS


class TestPricePredictor:
    @pytest.fixture
    def sample_features(self):
        data = {col: np.random.randn() for col in FEATURE_COLUMNS}
        return pd.DataFrame([data], columns=FEATURE_COLUMNS)

    def _train_quantile_model(self, X, y, quantile):
        """Train a minimal XGBoost quantile regressor."""
        model = xgb.XGBRegressor(
            n_estimators=3, max_depth=2,
            objective="reg:quantileerror", quantile_alpha=quantile,
        )
        model.fit(X, y)
        return model

    def test_predict_returns_price_range(self, sample_features):
        X_train = np.random.randn(100, len(FEATURE_COLUMNS))
        y_train = np.random.randn(100) * 5 + 60

        predictor = PricePredictor()
        predictor._registry = MagicMock()
        predictor._registry.load_model.side_effect = lambda name: {
            "price_5d_q10": self._train_quantile_model(X_train, y_train, 0.1),
            "price_5d_q50": self._train_quantile_model(X_train, y_train, 0.5),
            "price_5d_q90": self._train_quantile_model(X_train, y_train, 0.9),
            "price_20d_q10": self._train_quantile_model(X_train, y_train, 0.1),
            "price_20d_q50": self._train_quantile_model(X_train, y_train, 0.5),
            "price_20d_q90": self._train_quantile_model(X_train, y_train, 0.9),
        }.get(name)

        result = predictor.predict(sample_features, "601127.SH", "2026-06-24")
        for key in ["short", "medium"]:
            assert isinstance(result[key], PriceRangeOutput)
            assert result[key].model_available
            assert result[key].lower_bound <= result[key].median <= result[key].upper_bound

    def test_predict_no_model_returns_unavailable(self, sample_features):
        predictor = PricePredictor()
        predictor._registry = MagicMock()
        predictor._registry.load_model.return_value = None

        result = predictor.predict(sample_features, "601127.SH", "2026-06-24")
        assert not result["short"].model_available

    def test_confidence_from_width(self):
        predictor = PricePredictor()
        assert predictor._confidence_from_width(5.0) == ConfidenceTier.HIGH
        assert predictor._confidence_from_width(12.0) == ConfidenceTier.MEDIUM
        assert predictor._confidence_from_width(25.0) == ConfidenceTier.LOW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prediction/test_price_predictor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement PricePredictor**

```python
"""Tool A: XGBoost quantile regression for price range prediction.

Outputs a confidence interval (q10, q50, q90) for the future price
at the given horizon (5-day short-term, 20-day medium-term).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from capitalradar.prediction.feature_engine import FEATURE_COLUMNS
from capitalradar.prediction.model_registry import ModelRegistry
from capitalradar.prediction.schemas import (
    PriceRangeOutput, PredictionHorizon, ConfidenceTier,
)

logger = logging.getLogger(__name__)

QUANTILES = {0.10: "q10", 0.50: "q50", 0.90: "q90"}


class PricePredictor:
    """Predict future price range using XGBoost quantile regression.

    Three models per horizon (q10, q50, q90) produce a prediction
    interval.  The interval width relative to current price determines
    the confidence tier.

    Usage::

        pp = PricePredictor()
        result = pp.predict(features_df, ticker, date, current_price=63.0)
        # result["short"] → PriceRangeOutput(lower_bound=58.5, median=61.0, ...)
    """

    def __init__(self, models_dir: str = ""):
        self._registry = ModelRegistry(models_dir)

    # ── Public API ──────────────────────────────────────────────────

    def predict(
        self, features: pd.DataFrame, ticker: str, trade_date: str,
        current_price: float | None = None,
    ) -> dict[str, PriceRangeOutput]:
        """Run price range prediction for both horizons.

        Args:
            features: Single-row DataFrame from FeatureEngine.
            ticker: Stock ticker.
            trade_date: Analysis date.
            current_price: Latest price for interval width calculation.

        Returns:
            Dict with keys "short" and "medium", each a PriceRangeOutput.
        """
        results = {}
        for horizon, prefix in [
            (PredictionHorizon.SHORT, "price_5d"),
            (PredictionHorizon.MEDIUM, "price_20d"),
        ]:
            key = "short" if horizon == PredictionHorizon.SHORT else "medium"
            models = {}
            for q, suffix in QUANTILES.items():
                m = self._registry.load_model(f"{prefix}_{suffix}")
                if m is not None:
                    models[q] = m

            if len(models) < 3:
                results[key] = PriceRangeOutput(
                    horizon=horizon,
                    lower_bound=0, upper_bound=0, median=0,
                    current_price=current_price or 0,
                    interval_width_pct=0,
                    confidence=ConfidenceTier.LOW,
                    model_available=False,
                )
                continue

            results[key] = self._predict_quantiles(
                models, features, horizon, current_price,
            )
        return results

    # ── Internal ────────────────────────────────────────────────────

    def _predict_quantiles(
        self, models: dict[float, xgb.XGBRegressor],
        features: pd.DataFrame, horizon: PredictionHorizon,
        current_price: float | None,
    ) -> PriceRangeOutput:
        """Run prediction using three quantile models."""
        X = features[FEATURE_COLUMNS].fillna(0).values
        preds = {}
        for q, model in models.items():
            preds[q] = float(model.predict(X)[0])

        lower = round(preds[0.10], 2)
        median = round(preds[0.50], 2)
        upper = round(preds[0.90], 2)

        # Enforce ordering (quantile crossing is rare with XGBoost but guard it)
        if lower > median:
            lower, median = median, lower
        if median > upper:
            median, upper = upper, median
        if lower > upper:
            lower, upper = upper, lower

        price_ref = current_price if current_price and current_price > 0 else median
        width_pct = round((upper - lower) / price_ref * 100, 1) if price_ref > 0 else 0.0
        confidence = self._confidence_from_width(width_pct)

        meta = self._registry.get_metadata()
        short_long = "5d" if horizon == PredictionHorizon.SHORT else "20d"
        model_meta = meta.get(f"price_{short_long}_q50", {})

        return PriceRangeOutput(
            horizon=horizon,
            lower_bound=lower, upper_bound=upper, median=median,
            current_price=current_price or median,
            interval_width_pct=width_pct,
            confidence=confidence,
            model_available=True,
            model_version=model_meta.get("training_date", ""),
        )

    @staticmethod
    def _confidence_from_width(width_pct: float) -> ConfidenceTier:
        """Map interval width to a confidence tier.

        Narrow interval → high confidence (model is precise).
        Wide interval → low confidence (model is uncertain).
        """
        if width_pct <= 8.0:
            return ConfidenceTier.HIGH
        if width_pct <= 20.0:
            return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prediction/test_price_predictor.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add capitalradar/prediction/price_predictor.py tests/prediction/test_price_predictor.py
git commit -m "feat(prediction): add PricePredictor (Tool A) with XGBoost quantile regression"
```

---

### Task 7: BehaviorPredictor (Tool C)

**Files:**
- Create: `capitalradar/prediction/behavior_predictor.py`
- Create: `tests/prediction/test_behavior_predictor.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for BehaviorPredictor."""
import pytest
from unittest.mock import MagicMock, patch
from capitalradar.prediction.behavior_predictor import BehaviorPredictor
from capitalradar.prediction.schemas import (
    BehaviorOutput, BehaviorPhase, ConfidenceTier,
)


class TestBehaviorPredictor:
    @pytest.fixture
    def predictor(self):
        return BehaviorPredictor()

    @pytest.fixture
    def mock_llm(self):
        """Return a mock LLM that returns a predictable response."""
        llm = MagicMock()
        response = MagicMock()
        response.content = (
            '{"current_phase": "distribution", '
            '"phase_confidence": "high", '
            '"predicted_next_behavior": "continue distributing on rebounds", '
            '"behavior_rationale": "超大单持续净流出，融资余额下降", '
            '"risk_scenario": "如果跌破60元支撑位可能加速下跌"}'
        )
        llm.invoke.return_value = response
        return llm

    def test_build_prompt_includes_smart_money_context(self, predictor):
        """The LLM prompt should include Smart Money Score and capital flow data."""
        context = {
            "smart_money": {
                "verdict": "divergence",
                "stage": "distribution",
                "gates": {
                    "scale": {"passed": True, "detail": "net/big-order ratio: 8.5%"},
                    "persistence": {"passed": True, "detail": "5d: 1pos/4neg"},
                    "alignment": {"passed": False, "detail": "divergence: price +0.8% but net outflow"},
                    "cross": {"passed": True, "detail": "big-order confirms"},
                },
            },
            "capital_flow_report": "主力资金分析：超大单40日净流出...",
            "analyst_reports": {"sentiment_report": "看跌情绪..."},
        }
        prompt = predictor._build_llm_prompt("601127.SH", "2026-06-24", context)
        assert "601127.SH" in prompt
        assert "distribution" in prompt.lower()
        assert "smart money score" in prompt.lower()

    def test_parse_llm_response_valid_json(self, predictor):
        response_text = (
            '{"current_phase": "accumulation", '
            '"phase_confidence": "medium", '
            '"predicted_next_behavior": "likely to continue accumulating", '
            '"behavior_rationale": "funds flowing in quietly", '
            '"risk_scenario": "sudden market downturn"}'
        )
        result = predictor._parse_llm_response(response_text)
        assert result["current_phase"] == "accumulation"
        assert result["phase_confidence"] == "medium"

    def test_parse_llm_response_invalid_fallback(self, predictor):
        """Invalid JSON should fall back to defaults."""
        result = predictor._parse_llm_response("not json at all")
        assert result["current_phase"] == "unknown"
        assert result["phase_confidence"] == "low"

    def test_find_historical_analogs(self, predictor):
        """Should find past entries with matching ticker from memory log."""
        mock_log = MagicMock()
        mock_log.load_entries.return_value = [
            {"ticker": "601127.SH", "date": "2026-04-15",
             "rating": "Sell", "raw_return": -0.12, "pending": False,
             "decision": "DECISION:\nSell due to institutional outflow\n",
             "reflection": "REFLECTION:\nContinued decline -12% over 10d\n"},
            {"ticker": "000001.SZ", "date": "2026-05-01",
             "rating": "Buy", "raw_return": 0.05, "pending": False,
             "decision": "DECISION:\nBuy\n", "reflection": "REFLECTION:\nOK\n"},
        ]
        analogs = predictor._find_historical_analogs(
            "601127.SH", mock_log, "distribution", max_analogs=1,
        )
        assert len(analogs) == 1
        assert analogs[0]["ticker"] == "601127.SH"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prediction/test_behavior_predictor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement BehaviorPredictor**

```python
"""Tool C: LLM-powered institutional behavior prediction.

Classifies the current lifecycle phase of major capital (accumulation,
shakeout, markup, distribution, exit) and predicts the most likely next
move by combining Smart Money Score data with historical pattern matching
from the Memory Log.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from capitalradar.agents.utils.memory import CapitalRadarMemoryLog
from capitalradar.prediction.schemas import (
    BehaviorOutput, BehaviorPhase, ConfidenceTier, HistoricalAnalog,
)

logger = logging.getLogger(__name__)

# ── LLM prompt template ─────────────────────────────────────────────

_BEHAVIOR_SYSTEM_PROMPT = """You are a veteran institutional trader specialized in
detecting smart-money lifecycle patterns in Chinese A-share markets.

Given the Smart Money Score gate results, capital flow data, and recent
analyst reports, you must:

1. Classify the CURRENT phase: accumulation / shakeout / markup / distribution / exit
2. Predict the most likely NEXT behavior pattern
3. Identify the key risk scenario

Follow the Wyckoff cycle model.  Chinese A-share institutional behavior
tends to follow a 4-stage cycle: 建仓 (accumulation) → 洗盘 (shakeout)
→ 拉升 (markup) → 出货 (distribution) → 退出 (exit).

Current ticker: {ticker}
Analysis date: {trade_date}

Smart Money Score (4-gate validation):
{smart_money_context}

Capital Flow Analyst Report:
{capital_flow_report}

Other Analyst Context:
{analyst_context}

Historical Analogs (past trades with similar patterns):
{historical_analogs}

Return a JSON object with EXACTLY these keys:
- current_phase: one of "accumulation" / "shakeout" / "markup" / "distribution" / "exit" / "unknown"
- phase_confidence: one of "high" / "medium" / "low"
- predicted_next_behavior: 2-3 sentences describing what major capital is most likely to do next
- behavior_rationale: 2-3 sentences explaining WHY (cite specific gate results or flow data)
- risk_scenario: the key tail risk investors should watch for

Write in Chinese.
"""


class BehaviorPredictor:
    """Predict institutional behavior phase and next move using LLM.

    Combines Smart Money Score gate results, capital flow lifecycle
    stage, analyst reports, and historical pattern matching from the
    Memory Log into a structured LLM prompt.

    Usage::

        bp = BehaviorPredictor()
        result = bp.predict(ticker, date, context, config)
        # result → BehaviorOutput(current_phase=DISTRIBUTION, ...)
    """

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────────────

    def predict(
        self, ticker: str, trade_date: str, context: dict, config: dict,
    ) -> BehaviorOutput:
        """Run behavior prediction.

        Args:
            ticker: Stock ticker.
            trade_date: Analysis date.
            context: Dict with smart_money, capital_flow_report, analyst_reports.
            config: CapitalRadar config dict (for LLM + memory log paths).

        Returns:
            BehaviorOutput with phase classification and next-move prediction.
        """
        # Historical pattern matching
        memory_log = CapitalRadarMemoryLog(config)
        sm = context.get("smart_money", {})
        current_stage = sm.get("stage", "")
        analogs = self._find_historical_analogs(
            ticker, memory_log, current_stage,
        )

        # Build prompt and call LLM
        prompt_text = self._build_llm_prompt(ticker, trade_date, context, analogs)
        try:
            llm_response = self._call_llm(prompt_text, config)
            parsed = self._parse_llm_response(llm_response)
        except Exception as e:
            logger.error("Behavior LLM call failed: %s", e)
            return BehaviorOutput(
                current_phase=BehaviorPhase.UNKNOWN,
                phase_confidence=ConfidenceTier.LOW,
                predicted_next_behavior="LLM call failed",
                behavior_rationale=str(e)[:100],
                model_available=True,
            )

        # Build historical analog Pydantic models
        analog_models = [
            HistoricalAnalog(
                date=a["date"], ticker=a["ticker"],
                phase=a.get("phase", ""), outcome=a.get("outcome", ""),
                raw_return=a.get("raw_return"),
            )
            for a in analogs
        ]

        return BehaviorOutput(
            current_phase=BehaviorPhase(parsed.get("current_phase", "unknown")),
            phase_confidence=ConfidenceTier(parsed.get("phase_confidence", "low")),
            predicted_next_behavior=parsed.get("predicted_next_behavior", ""),
            behavior_rationale=parsed.get("behavior_rationale", ""),
            historical_analogs=analog_models,
            risk_scenario=parsed.get("risk_scenario", ""),
            model_available=True,
        )

    # ── Internal: prompt building ──────────────────────────────────

    def _build_llm_prompt(
        self, ticker: str, trade_date: str, context: dict,
        analogs: list[dict] | None = None,
    ) -> str:
        """Build the LLM prompt with all available context."""
        sm = context.get("smart_money", {})
        gates = sm.get("gates", {})

        # Format Smart Money Score context compactly
        sm_lines = [
            f"Verdict: {sm.get('verdict', 'unknown')}",
            f"Stage: {sm.get('stage', 'unknown')}",
            f"Confidence: {sm.get('confidence', 'low')}",
            "",
            "Gate Results:",
        ]
        for name, g in gates.items():
            passed = "PASS" if g.get("passed") else "FAIL"
            sm_lines.append(f"  {name}: {passed} — {g.get('detail', '')}")
        sm_context = "\n".join(sm_lines)

        # Capital flow report (truncated)
        cap_report = context.get("capital_flow_report", "")[:4000]

        # Analyst context
        analyst_reports = context.get("analyst_reports", {})
        analyst_parts = []
        for key, report in analyst_reports.items():
            if report:
                analyst_parts.append(f"--- {key} ---\n{str(report)[:2000]}")
        analyst_context = "\n\n".join(analyst_parts) if analyst_parts else "No additional analyst context."

        # Historical analogs
        if analogs:
            analog_lines = ["| Date | Ticker | Phase | Outcome | Return |",
                            "|------|--------|-------|---------|--------|"]
            for a in analogs[:3]:
                ret_str = f"{a.get('raw_return', 0):+.1%}" if a.get("raw_return") is not None else "N/A"
                analog_lines.append(
                    f"| {a['date']} | {a['ticker']} | {a.get('phase', '')} | "
                    f"{a.get('outcome', '')[:50]} | {ret_str} |"
                )
            analog_text = "\n".join(analog_lines)
        else:
            analog_text = "No historical analogs available."

        return _BEHAVIOR_SYSTEM_PROMPT.format(
            ticker=ticker,
            trade_date=trade_date,
            smart_money_context=sm_context,
            capital_flow_report=cap_report,
            analyst_context=analyst_context,
            historical_analogs=analog_text,
        )

    # ── Internal: LLM call ─────────────────────────────────────────

    def _call_llm(self, prompt_text: str, config: dict) -> str:
        """Call the LLM with the behavior prediction prompt."""
        from capitalradar.llm_clients import create_llm_client

        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm", config.get("deep_think_llm", "")),
            base_url=config.get("backend_url"),
        )
        llm = client.get_llm()
        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": "Analyze the current institutional behavior phase and predict the next move."},
        ]
        response = llm.invoke(messages)
        return response.content if hasattr(response, "content") else str(response)

    # ── Internal: parsing ──────────────────────────────────────────

    @staticmethod
    def _parse_llm_response(response_text: str) -> dict:
        """Parse the LLM's JSON response, with robust fallback."""
        # Try to extract JSON from the response (may be wrapped in markdown)
        text = response_text.strip()
        if "```" in text:
            # Extract code block
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse behavior LLM response as JSON")
            return {
                "current_phase": "unknown",
                "phase_confidence": "low",
                "predicted_next_behavior": response_text[:200],
                "behavior_rationale": "",
                "risk_scenario": "",
            }
        return parsed

    # ── Internal: historical analogs ───────────────────────────────

    def _find_historical_analogs(
        self, ticker: str, memory_log: CapitalRadarMemoryLog,
        current_stage: str, max_analogs: int = 3,
    ) -> list[dict]:
        """Find past Memory Log entries with similar patterns."""
        try:
            entries = memory_log.load_entries()
        except Exception:
            return []

        # Prefer same-ticker entries, then cross-ticker
        same_ticker = [e for e in entries if e.get("ticker") == ticker and not e.get("pending")]
        cross_ticker = [e for e in entries if e.get("ticker") != ticker and not e.get("pending")]

        # Rank by: has outcome data, recency
        def sort_key(e):
            has_outcome = 1 if e.get("raw_return") is not None else 0
            return (has_outcome, e.get("date", ""))

        same_ticker.sort(key=sort_key, reverse=True)
        cross_ticker.sort(key=sort_key, reverse=True)

        combined = same_ticker[:max_analogs]
        if len(combined) < max_analogs:
            combined += cross_ticker[:max_analogs - len(combined)]

        analogs = []
        for e in combined:
            # Extract a short outcome description from reflection or decision
            outcome = ""
            reflection = e.get("reflection", "")
            if reflection:
                outcome = reflection[:100]
            else:
                decision = e.get("decision", "")
                if decision:
                    outcome = decision[:100]
            phase = current_stage or "unknown"
            analogs.append({
                "date": e.get("date", ""),
                "ticker": e.get("ticker", ""),
                "phase": phase,
                "outcome": outcome,
                "raw_return": e.get("raw_return"),
            })
        return analogs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prediction/test_behavior_predictor.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add capitalradar/prediction/behavior_predictor.py tests/prediction/test_behavior_predictor.py
git commit -m "feat(prediction): add BehaviorPredictor (Tool C) with LLM + historical pattern matching"
```

---

### Task 8: PredictionAgent orchestrator + synthesis

**Files:**
- Create: `capitalradar/prediction/agent.py`
- Create: `tests/prediction/test_agent.py`

- [ ] **Step 1: Write the test**

```python
"""Tests for PredictionAgent orchestrator."""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from capitalradar.prediction.agent import PredictionAgent
from capitalradar.prediction.schemas import (
    PredictionReport, PriceRangeOutput, DirectionOutput, BehaviorOutput,
    PredictionHorizon, ConfidenceTier, BehaviorPhase, CrossValidationVerdict,
)
from capitalradar.prediction.feature_engine import FEATURE_COLUMNS


class TestPredictionAgent:
    @pytest.fixture
    def sample_features(self):
        data = {col: np.random.randn() for col in FEATURE_COLUMNS}
        return pd.DataFrame([data], columns=FEATURE_COLUMNS)

    @pytest.fixture
    def agent(self):
        return PredictionAgent()

    @patch("capitalradar.prediction.agent.FeatureEngine")
    @patch("capitalradar.prediction.agent.PricePredictor")
    @patch("capitalradar.prediction.agent.DirectionPredictor")
    @patch("capitalradar.prediction.agent.BehaviorPredictor")
    def test_predict_full_pipeline(
        self, mock_beh, mock_dir, mock_price, mock_fe, agent, sample_features,
    ):
        """Full pipeline should return a PredictionReport with all sections."""
        mock_fe.return_value.build_features.return_value = sample_features

        mock_price.return_value.predict.return_value = {
            "short": PriceRangeOutput(
                horizon=PredictionHorizon.SHORT,
                lower_bound=58.5, upper_bound=64.2, median=61.0,
                current_price=63.0, interval_width_pct=9.0,
                confidence=ConfidenceTier.MEDIUM, model_available=True,
            ),
            "medium": PriceRangeOutput(
                horizon=PredictionHorizon.MEDIUM,
                lower_bound=55.0, upper_bound=70.0, median=62.0,
                current_price=63.0, interval_width_pct=23.8,
                confidence=ConfidenceTier.LOW, model_available=True,
            ),
        }
        mock_dir.return_value.predict.return_value = {
            "short": DirectionOutput(
                horizon=PredictionHorizon.SHORT,
                up_probability=0.38, down_probability=0.62,
                confidence=ConfidenceTier.MEDIUM, model_available=True,
            ),
            "medium": DirectionOutput(
                horizon=PredictionHorizon.MEDIUM,
                up_probability=0.45, down_probability=0.55,
                confidence=ConfidenceTier.LOW, model_available=True,
            ),
        }
        mock_beh.return_value.predict.return_value = BehaviorOutput(
            current_phase=BehaviorPhase.DISTRIBUTION,
            phase_confidence=ConfidenceTier.HIGH,
            predicted_next_behavior="continue distributing",
            behavior_rationale="超大单净流出",
            model_available=True,
        )

        config = {"llm_provider": "openai", "quick_think_llm": "gpt-5.4-mini",
                   "deep_think_llm": "gpt-5.4"}

        report = agent.predict("601127.SH", "2026-06-24", config)

        assert isinstance(report, PredictionReport)
        assert report.ticker == "601127.SH"
        assert report.direction_short is not None
        assert report.price_short is not None
        assert report.behavior is not None

    def test_cross_validation_aligned(self, agent):
        """When A and B agree on direction, cross-validation is ALIGNED."""
        result = agent._cross_validate(
            price_up=True, dir_up=True, behavior_phase="distribution",
            price_avail=True, dir_avail=True, beh_avail=True,
        )
        assert result["verdict"] == CrossValidationVerdict.ALIGNED

    def test_cross_validation_conflict(self, agent):
        """When A and B disagree, cross-validation is CONFLICT."""
        result = agent._cross_validate(
            price_up=True, dir_up=False, behavior_phase="distribution",
            price_avail=True, dir_avail=True, beh_avail=True,
        )
        assert result["verdict"] == CrossValidationVerdict.CONFLICT

    def test_cross_validation_price_missing(self, agent):
        """When price model is unavailable, flag it."""
        result = agent._cross_validate(
            price_up=None, dir_up=True, behavior_phase="distribution",
            price_avail=False, dir_avail=True, beh_avail=True,
        )
        assert result["verdict"] == CrossValidationVerdict.PRICE_MODEL_MISSING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prediction/test_agent.py -v`
Expected: FAIL

- [ ] **Step 3: Implement PredictionAgent (orchestrator + synthesis)**

```python
"""PredictionAgent orchestrator.

Coordinates Tool A (Price), Tool B (Direction), and Tool C (Behavior),
runs cross-validation, and produces a synthesized PredictionReport.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from capitalradar.dataflows.config import get_config as get_runtime_config
from capitalradar.prediction.feature_engine import FeatureEngine
from capitalradar.prediction.price_predictor import PricePredictor
from capitalradar.prediction.direction_predictor import DirectionPredictor
from capitalradar.prediction.behavior_predictor import BehaviorPredictor
from capitalradar.prediction.schemas import (
    PredictionReport, ConfidenceTier, CrossValidationVerdict,
)

logger = logging.getLogger(__name__)


class PredictionAgent:
    """Orchestrate ML + LLM prediction and return a structured report.

    Usage::

        agent = PredictionAgent()
        report = agent.predict("601127.SH", "2026-06-24", config)
        print(report.to_markdown())
    """

    def __init__(self, models_dir: str = ""):
        self._feature_engine = FeatureEngine()
        self._price = PricePredictor(models_dir)
        self._direction = DirectionPredictor(models_dir)
        self._behavior = BehaviorPredictor()

    # ── Public API ──────────────────────────────────────────────────

    def predict(
        self, ticker: str, trade_date: str, config: dict,
    ) -> PredictionReport:
        """Run full prediction pipeline.

        Args:
            ticker: Stock ticker (e.g. "601127.SH").
            trade_date: Analysis date in YYYY-MM-DD format.
            config: CapitalRadar config dict.

        Returns:
            PredictionReport with all tool outputs + synthesis.
        """
        errors: list[str] = []
        current_price: float | None = None

        # ── Build features ──
        try:
            features = self._feature_engine.build_features(
                ticker, trade_date, config,
            )
        except Exception as e:
            logger.exception("FeatureEngine failed for %s", ticker)
            return PredictionReport(
                ticker=ticker, trade_date=trade_date,
                errors=[f"FeatureEngine failed: {e}"],
            )

        # Get current price from features context (last close)
        try:
            from capitalradar.dataflows.interface import route_to_vendor
            rt_raw = route_to_vendor("get_realtime_quote", symbol=ticker)
            import re
            m = re.search(r"Current Price:\s*([\d.]+)", str(rt_raw))
            if m:
                current_price = float(m.group(1))
        except Exception:
            current_price = None

        # ── Run Tool A: Price ──
        price_results = {}
        try:
            price_results = self._price.predict(
                features, ticker, trade_date, current_price,
            )
        except Exception as e:
            logger.exception("PricePredictor failed")
            errors.append(f"PricePredictor: {e}")

        # ── Run Tool B: Direction ──
        dir_results = {}
        try:
            dir_results = self._direction.predict(features, ticker, trade_date)
        except Exception as e:
            logger.exception("DirectionPredictor failed")
            errors.append(f"DirectionPredictor: {e}")

        # ── Run Tool C: Behavior ──
        behavior_output = None
        try:
            context = self._build_behavior_context(ticker, trade_date, config)
            behavior_output = self._behavior.predict(
                ticker, trade_date, context, config,
            )
        except Exception as e:
            logger.exception("BehaviorPredictor failed")
            errors.append(f"BehaviorPredictor: {e}")

        # ── Cross-validation ──
        dir_short = dir_results.get("short")
        price_short = price_results.get("short")

        price_direction_up = None
        if price_short and price_short.model_available and price_short.median > 0:
            price_direction_up = price_short.median > current_price if current_price else None

        dir_up = dir_short.up_probability > 0.50 if dir_short and dir_short.model_available else None

        cv = self._cross_validate(
            price_up=price_direction_up,
            dir_up=dir_up,
            behavior_phase=behavior_output.current_phase.value if behavior_output else None,
            price_avail=price_short.model_available if price_short else False,
            dir_avail=dir_short.model_available if dir_short else False,
            beh_avail=behavior_output is not None and behavior_output.model_available,
        )

        # ── LLM synthesis ──
        synthesis_summary = ""
        synthesis_guidance = ""
        try:
            synthesis = self._llm_synthesis(
                ticker, trade_date, current_price,
                price_results, dir_results, behavior_output, cv, config,
            )
            synthesis_summary = synthesis.get("summary", "")
            synthesis_guidance = synthesis.get("guidance", "")
        except Exception as e:
            logger.exception("LLM synthesis failed")
            errors.append(f"Synthesis: {e}")

        # ── Overall confidence ──
        overall = self._overall_confidence(
            dir_short, cv["verdict"], errors,
        )

        return PredictionReport(
            ticker=ticker,
            trade_date=trade_date,
            current_price=current_price,
            price_short=price_results.get("short"),
            price_medium=price_results.get("medium"),
            direction_short=dir_results.get("short"),
            direction_medium=dir_results.get("medium"),
            behavior=behavior_output,
            cross_validation=cv["verdict"],
            cross_validation_detail=cv["detail"],
            synthesis_summary=synthesis_summary,
            synthesis_guidance=synthesis_guidance,
            overall_confidence=overall,
            errors=errors,
        )

    # ── Cross-validation ───────────────────────────────────────────

    @staticmethod
    def _cross_validate(
        price_up: bool | None,
        dir_up: bool | None,
        behavior_phase: str | None,
        price_avail: bool,
        dir_avail: bool,
        beh_avail: bool,
    ) -> dict:
        """Check consistency across the three tools."""
        if not price_avail:
            return {
                "verdict": CrossValidationVerdict.PRICE_MODEL_MISSING,
                "detail": "Price model unavailable; using direction + behavior only.",
            }

        if price_up is not None and dir_up is not None:
            if price_up == dir_up:
                return {
                    "verdict": CrossValidationVerdict.ALIGNED,
                    "detail": f"Price and direction agree: {'bullish' if price_up else 'bearish'}.",
                }
            else:
                return {
                    "verdict": CrossValidationVerdict.CONFLICT,
                    "detail": (
                        f"Price model says {'up' if price_up else 'down'}, "
                        f"direction model says {'up' if dir_up else 'down'}. "
                        "Treat with caution."
                    ),
                }

        if dir_up is None and price_up is None:
            return {
                "verdict": CrossValidationVerdict.CONFLICT,
                "detail": "Both models returned no directional signal.",
            }

        return {
            "verdict": CrossValidationVerdict.ALIGNED,
            "detail": "Single-model direction (other unavailable).",
        }

    # ── Behavior context builder ───────────────────────────────────

    @staticmethod
    def _build_behavior_context(
        ticker: str, trade_date: str, config: dict,
    ) -> dict:
        """Gather Smart Money Score + analyst reports for Tool C."""
        context: dict = {"smart_money": {}, "capital_flow_report": "",
                          "analyst_reports": {}}
        try:
            from capitalradar.sector_scan.smart_money_score import detect_smart_money
            sm_result = detect_smart_money(ticker, config)
            context["smart_money"] = sm_result
        except Exception:
            pass
        return context

    # ── LLM synthesis ──────────────────────────────────────────────

    @staticmethod
    def _llm_synthesis(
        ticker: str, trade_date: str, current_price: float | None,
        price_results: dict, dir_results: dict,
        behavior_output, cv: dict, config: dict,
    ) -> dict[str, str]:
        """Generate a Chinese-language synthesis of all prediction outputs."""
        from capitalradar.llm_clients import create_llm_client

        # Build a compact summary of tool outputs for the LLM
        parts = [f"Ticker: {ticker}", f"Date: {trade_date}"]
        if current_price:
            parts.append(f"Current Price: {current_price:.2f}")

        for label, d in [("Short-term (5d)", dir_results.get("short")),
                          ("Medium-term (20d)", dir_results.get("medium"))]:
            if d and d.model_available:
                parts.append(
                    f"{label} Direction: {d.up_probability:.0%} up / "
                    f"{d.down_probability:.0%} down (confidence: {d.confidence.value})"
                )

        for label, p in [("Short-term (5d)", price_results.get("short")),
                          ("Medium-term (20d)", price_results.get("medium"))]:
            if p and p.model_available:
                parts.append(
                    f"{label} Price Range: {p.lower_bound:.2f} – "
                    f"{p.upper_bound:.2f} (median: {p.median:.2f})"
                )

        if behavior_output and behavior_output.model_available:
            parts.append(
                f"Behavior Phase: {behavior_output.current_phase.value} "
                f"({behavior_output.phase_confidence.value} confidence)"
            )
            parts.append(f"Predicted Next Move: {behavior_output.predicted_next_behavior}")

        parts.append(f"Cross-Validation: {cv['verdict'].value} — {cv['detail']}")

        prompt = (
            "You are a senior investment advisor synthesizing three independent "
            "prediction models for a Chinese A-share stock.  Write in Chinese.\n\n"
            + "\n".join(parts)
            + "\n\nReturn a JSON with:\n"
            "- summary: 2-3 paragraph synthesis of what all models are saying, "
            "noting convergence or divergence\n"
            "- guidance: 1-2 sentences of actionable guidance for the Portfolio Manager"
        )

        try:
            client = create_llm_client(
                provider=config.get("llm_provider", "openai"),
                model=config.get("quick_think_llm", config.get("deep_think_llm", "")),
                base_url=config.get("backend_url"),
            )
            llm = client.get_llm()
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Synthesize the prediction outputs."},
            ]
            response = llm.invoke(messages)
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            return {"summary": f"Synthesis unavailable: {e}", "guidance": ""}

        # Parse JSON
        try:
            import re
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end + 1])
                return {
                    "summary": parsed.get("summary", text[:500]),
                    "guidance": parsed.get("guidance", ""),
                }
        except json.JSONDecodeError:
            pass
        return {"summary": text[:500], "guidance": ""}

    # ── Confidence aggregation ─────────────────────────────────────

    @staticmethod
    def _overall_confidence(
        dir_short, cv_verdict: CrossValidationVerdict, errors: list[str],
    ) -> ConfidenceTier:
        """Compute overall confidence from cross-validation and tool outputs."""
        if errors:
            return ConfidenceTier.LOW
        if cv_verdict == CrossValidationVerdict.CONFLICT:
            return ConfidenceTier.LOW
        if cv_verdict == CrossValidationVerdict.PRICE_MODEL_MISSING:
            return ConfidenceTier.LOW
        if dir_short and dir_short.model_available:
            margin = abs(dir_short.up_probability - 0.50)
            if margin >= 0.20:
                return ConfidenceTier.HIGH
            if margin >= 0.05:
                return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prediction/test_agent.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add capitalradar/prediction/agent.py tests/prediction/test_agent.py
git commit -m "feat(prediction): add PredictionAgent orchestrator with cross-validation + LLM synthesis"
```

---

### Task 9: Run full test suite + finalize

- [ ] **Step 1: Run all prediction tests**

Run:
```bash
pytest tests/prediction/ -v
```
Expected: all tests PASS

- [ ] **Step 2: Run existing test suite to ensure no regressions**

Run:
```bash
pytest -m "not integration" --ignore=tests/prediction/ -v
```
Expected: all pre-existing tests PASS

- [ ] **Step 3: Verify import from top-level**

Run:
```bash
.venv/Scripts/python.exe -c "from capitalradar.prediction import PredictionAgent; print('Import OK')"
```
Expected: Import OK

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(prediction): PredictionAgent complete — all tests passing"
```
