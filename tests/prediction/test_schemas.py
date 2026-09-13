"""Tests for prediction schemas."""
import pytest
from quantconclave.prediction.schemas import (
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
