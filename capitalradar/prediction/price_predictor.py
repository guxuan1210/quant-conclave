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

        lower = round(preds[0.10], 4)
        median = round(preds[0.50], 4)
        upper = round(preds[0.90], 4)

        # If we have a current price, convert from returns to absolute price levels
        if current_price and current_price > 0:
            lower = round(float(current_price * (1 + lower)), 2)
            median = round(float(current_price * (1 + median)), 2)
            upper = round(float(current_price * (1 + upper)), 2)

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
