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
