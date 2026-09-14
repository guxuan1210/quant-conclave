"""Tests for the evaluation pipeline orchestrator (retry, ablation selection, purity)."""

from __future__ import annotations

import pytest

from quantconclave.evaluation import pipeline as P


@pytest.mark.unit
def test_extract_confidence():
    assert P.extract_confidence("**Confidence**: High\nrest") == "high"
    assert P.extract_confidence("**Confidence**: medium") == "medium"
    assert P.extract_confidence("**Confidence**：低") == "低"
    assert P.extract_confidence("no confidence marker") == ""


@pytest.mark.unit
def test_retry_success_first_try():
    calls = []

    def run(config, ticker, date):
        calls.append(1)
        return ("state", "Buy", {"llm_calls": 1})

    out = P.run_full_analysis_with_retry({}, "T.SH", "2026-01-05", run_fn=run, max_attempts=2)
    assert out[1] == "Buy"
    assert len(calls) == 1


@pytest.mark.unit
def test_retry_once_then_success():
    calls = {"n": 0}

    def run(config, ticker, date):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return ("state", "Hold", {})

    out = P.run_full_analysis_with_retry({}, "T.SH", "2026-01-05", run_fn=run, max_attempts=2)
    assert out[1] == "Hold"
    assert calls["n"] == 2


@pytest.mark.unit
def test_retry_exhausts_and_raises():
    def run(config, ticker, date):
        raise RuntimeError("always fails")

    with pytest.raises(RuntimeError):
        P.run_full_analysis_with_retry({}, "T.SH", "2026-01-05", run_fn=run, max_attempts=2)


@pytest.mark.unit
def test_select_ablation_cases_deterministic():
    cases = [{"week_key": "2026-W01", "ticker": f"T{i}.SH"} for i in range(10)]
    a = P.select_ablation_cases(cases, 4, seed=1)
    b = P.select_ablation_cases(cases, 4, seed=1)
    assert [c["ticker"] for c in a] == [c["ticker"] for c in b]
    assert len(a) == 4


@pytest.mark.unit
def test_select_ablation_cases_respects_count():
    cases = [{"week_key": "2026-W01", "ticker": f"T{i}.SH"} for i in range(10)]
    assert len(P.select_ablation_cases(cases, 100, seed=1)) == 10
    assert P.select_ablation_cases(cases, 0, seed=1) == []
    assert P.select_ablation_cases([], 4, seed=1) == []


@pytest.mark.unit
def test_run_monthly_ablation_caps_cases(monkeypatch):
    """list_eval_cases returning 60 cases is truncated to the 50-case cap before selection."""
    from web import eval_store

    cases = [
        {"case_id": f"c{i}", "week_key": "2026-W01", "ticker": f"T{i}.SH",
         "selection_date": "2026-01-05", "source": "weekly"}
        for i in range(60)
    ]
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda *a, **k: cases)
    monkeypatch.setattr(eval_store, "get_full_prediction", lambda *a, **k: {"prediction_id": "p"})
    monkeypatch.setattr(eval_store, "load_frozen_reports", lambda *a, **k: {"market_report": "frozen"})

    captured = {}

    def fake_select(pool, count, seed=0):
        captured["len"] = len(pool)
        return []

    monkeypatch.setattr(P, "select_ablation_cases", fake_select)

    P.run_monthly_ablation(config={}, month_key="2026-01")
    assert captured["len"] == 50


@pytest.mark.unit
def test_compute_summary_pure(monkeypatch):
    """compute_summary aggregates rows without any LLM or network access."""
    from web import eval_store
    from quantconclave.evaluation import summary as S

    rows = [
        {"week_key": "2026-W01", "ticker": "A.SH", "status": "settled", "variant": "full",
         "rating": "Buy", "confidence": "high", "smart_money_score": 3.0, "horizon": 5,
         "net_return": 0.06, "benchmark_return": 0.01, "excess_return": 0.05},
        {"week_key": "2026-W01", "ticker": "B.SH", "status": "settled", "variant": "full",
         "rating": "Buy", "confidence": "high", "smart_money_score": 2.0, "horizon": 5,
         "net_return": 0.03, "benchmark_return": 0.01, "excess_return": 0.02},
        {"week_key": "2026-W01", "ticker": "C.SH", "status": "settled", "variant": "full",
         "rating": "Overweight", "confidence": "medium", "smart_money_score": 1.0, "horizon": 5,
         "net_return": 0.00, "benchmark_return": 0.01, "excess_return": -0.01},
    ]
    monkeypatch.setattr(eval_store, "get_eval_metrics_rows", lambda c: rows)
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda c, **k: [
        {"case_id": f"c{i}", "week_key": "2026-W01", "status": "settled"}
        for i in range(3)
    ])

    result = S.compute_summary({}, "week")
    weekly = result["weekly"]
    assert len(weekly) == 1
    w = weekly[0]
    assert w["portfolio_net_return"] == pytest.approx((0.06 + 0.03 + 0.00) / 3)
    assert w["benchmark_return"] == pytest.approx(0.01)
    assert w["excess_return"] == pytest.approx(0.02)
    assert result["mature_weeks"] == 1
    assert result["coverage_pct"] == pytest.approx(1.0)
    assert result["annualized_ir"] is None  # one week only
    assert result["validity"]["status"] == "insufficient_data"
