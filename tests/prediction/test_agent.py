"""Tests for PredictionAgent orchestrator."""
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from quantconclave.prediction.agent import PredictionAgent
from quantconclave.prediction.schemas import (
    PredictionReport, PriceRangeOutput, DirectionOutput, BehaviorOutput,
    PredictionHorizon, ConfidenceTier, BehaviorPhase, CrossValidationVerdict,
)
from quantconclave.prediction.feature_engine import FEATURE_COLUMNS


class TestPredictionAgent:
    @pytest.fixture
    def sample_features(self):
        data = {col: np.random.randn() for col in FEATURE_COLUMNS}
        return pd.DataFrame([data], columns=FEATURE_COLUMNS)

    @pytest.fixture
    def agent(self):
        return PredictionAgent()

    @patch("quantconclave.prediction.agent.FeatureEngine")
    @patch("quantconclave.prediction.agent.PricePredictor")
    @patch("quantconclave.prediction.agent.DirectionPredictor")
    @patch("quantconclave.prediction.agent.BehaviorPredictor")
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
