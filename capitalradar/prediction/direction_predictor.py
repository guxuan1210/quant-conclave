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
