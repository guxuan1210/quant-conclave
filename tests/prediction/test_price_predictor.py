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
        predictor._registry.get_metadata.return_value = {}

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
