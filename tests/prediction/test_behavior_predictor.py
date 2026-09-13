"""Tests for BehaviorPredictor."""
import pytest
from unittest.mock import MagicMock, patch
from quantconclave.prediction.behavior_predictor import BehaviorPredictor
from quantconclave.prediction.schemas import (
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
