"""Tests for the AI Pick screening tools — validated 180-day bottom and
explicit data validation instead of silent empty results.

Pure unit tests: vendor/dataflow functions are monkeypatched, no network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from quantconclave.dataflows.config import get_config
from web.ai_pick_agent import build_aipick_tools


# ── Fakes ───────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return _Resp(self._content)


_STRATEGY_JSON = (
    '{"name": "超跌反弹", "conditions": ['
    '{"field": "smart_money_score", "op": ">=", "value": 50},'
    '{"field": "price_vs_180d_low", "op": "<=", "value": 1.2},'
    '{"field": "rsi_14", "op": "<=", "value": 30}],'
    '"order_by": "smart_money_score", "limit": 10}'
)

_STRATEGY_WITH_MISSING_FIELD = (
    '{"name": "主力抄底", "conditions": ['
    '{"field": "smart_money_score", "op": ">=", "value": 50},'
    '{"field": "price_vs_180d_low", "op": "<=", "value": 1.2},'
    '{"field": "net_inflow_5d", "op": ">=", "value": 5000}],'
    '"order_by": "smart_money_score", "limit": 10}'
)


def _ok_metrics(code: str = "000001.SZ") -> dict:
    return {
        "ok": True, "ts_code": code, "current": 9.5, "low180": 9.0,
        "high180": 15.0, "low60": 9.2, "ma20": 10.2, "rsi_14": 25.0,
        "n_bars": 210, "volume_ratio_5d": 1.2,
        "price_vs_180d_low": 1.056, "price_vs_180d_high": 0.633,
        "price_vs_60d_low": 1.03, "price_vs_ma20": 0.93,
        "rally_from_low": 5.6, "drop_from_high": 36.7,
    }


def _apply_screening_mocks(monkeypatch):
    """Shared mocks for the candidate-pool path (run_smart_screening)."""
    monkeypatch.setattr(
        "quantconclave.sector_scan.rotation.get_rrg_data",
        lambda *a, **k: {"industries": [{"name": "半导体", "quadrant": "leading"}]},
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.smart_scanner.get_industry_stocks",
        lambda name: [{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行"}],
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.smart_scanner.get_daily_basic_batch",
        lambda codes: {"000001.SZ": {"close": 9.5, "total_mv": 200000, "pe": 12.0, "pb": 1.2}},
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.smart_money_score.compute_smart_money_score",
        lambda code, cfg: (75.0, {}),
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.reversal_metrics.fetch_reversal_metrics",
        lambda code, date, config=None: _ok_metrics(code),
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.pick_tracker.record_pick",
        lambda *a, **k: None,
    )


def _build_tools(monkeypatch, strategy_json: str = _STRATEGY_JSON):
    _apply_screening_mocks(monkeypatch)
    return build_aipick_tools(get_config(), _FakeLLM(strategy_json))


def _tool_map(tools):
    return {t.name: t for t in tools}


# ── run_smart_screening ─────────────────────────────────────────────

def test_run_smart_screening_builds_pool_from_ts_code_and_returns_candidates(monkeypatch):
    """The ts_code bug fix: candidates must be produced, not '没有找到候选股票'."""
    tools = _build_tools(monkeypatch)
    result = _tool_map(tools)["run_smart_screening"].invoke(
        {"requirement": "超跌反弹", "market_context": ""}
    )
    assert "没有找到候选股票" not in result
    assert "000001.SZ" in result
    assert "平安银行" in result
    assert "筛选结果" in result
    assert "180日窗口不足剔除 0 只" in result


def test_run_smart_screening_drops_unavailable_fields_with_note(monkeypatch):
    """A condition field the pool lacks must be dropped with an explicit note."""
    tools = _build_tools(monkeypatch, _STRATEGY_WITH_MISSING_FIELD)
    result = _tool_map(tools)["run_smart_screening"].invoke(
        {"requirement": "主力抄底", "market_context": ""}
    )
    assert "已忽略条件" in result
    assert "net_inflow_5d" in result
    # Smart-money + 180d-low conditions still applied → candidate survives.
    assert "000001.SZ" in result


# ── get_hot_reversal_picks ──────────────────────────────────────────

class _FakePro:
    def trade_cal(self, exchange=None, start_date=None, end_date=None):
        return pd.DataFrame({"cal_date": ["20260826"], "is_open": [1]})

    def daily_basic(self, trade_date=None, fields=None):
        return pd.DataFrame({
            "ts_code": [f"{i:06d}.SZ" for i in range(1, 11)],
            "pe": [10.0 + i for i in range(1, 11)],
            "turnover_rate": [2.0] * 10,
            "total_mv": [200000] * 10,
        })


class _FakeFE:
    def build_features(self, code, date, cfg):
        return {}


class _FakeShort:
    model_available = True
    up_probability = 0.6


class _FakeDP:
    def predict(self, features, code, date):
        return {"short": _FakeShort()}


def _rev_for_hot(code: str, date: str, config=None) -> dict:
    if code == "000010.SZ":
        # Too young for a 180-day bottom → must be excluded and counted.
        return {"ok": False, "reason": "insufficient_history", "n_bars": 120, "ts_code": code}
    m = dict(_ok_metrics(code))
    if code == "000002.SZ":
        # 22% above its 180-day low → G1 guardrail rejects (not a bottom).
        m["current"] = 11.0
        m["rally_from_low"] = 22.2
        m["price_vs_180d_low"] = 1.222
    return m


def test_get_hot_reversal_picks_uses_180d_bottom_and_excludes_young(monkeypatch):
    tools = _build_tools(monkeypatch, _STRATEGY_JSON)
    # Override the ok-for-all reversal mock AFTER building (build applies the
    # default mocks) with the scenario mock: 1 young stock + 1 already-rallied.
    monkeypatch.setattr(
        "quantconclave.sector_scan.reversal_metrics.fetch_reversal_metrics", _rev_for_hot
    )
    monkeypatch.setattr("tushare.pro_api", lambda token: _FakePro())
    monkeypatch.setattr("quantconclave.prediction.feature_engine.FeatureEngine", _FakeFE)
    monkeypatch.setattr("quantconclave.prediction.direction_predictor.DirectionPredictor", _FakeDP)

    result = _tool_map(tools)["get_hot_reversal_picks"].invoke({"top_n": 5})

    # Data validation surfaced explicitly: 1 excluded for insufficient history.
    assert "剔除 1 只" in result
    assert "180" in result  # 180-day bottom wording in header/scoring notes

    # Recommendation section must use the 180-day bottom: 000001.SZ survives,
    # 000002.SZ (already 22% off its 180d low) is rejected by G1, and
    # 000010.SZ (insufficient history) is excluded from scoring.
    recs = result.split("## 🎯 精选推荐")[1]
    assert "000001.SZ" in recs
    assert "000002.SZ" not in recs
    assert "000010.SZ" not in recs
