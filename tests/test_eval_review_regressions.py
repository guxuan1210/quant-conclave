"""Regression tests for issues found during the pre-PR evaluation review."""

from __future__ import annotations

import pandas as pd
import pytest

from quantconclave.evaluation import pipeline as P
from quantconclave.evaluation import prices
from quantconclave.evaluation import summary as S
from quantconclave.evaluation.sampling import sample_weekly_stocks
from web import eval_scheduler as ES
from web import eval_store


def _config(tmp_path):
    return {"results_dir": str(tmp_path / "results"), "evaluation": {}}


@pytest.mark.unit
def test_metrics_rows_include_case_id_with_real_store(tmp_path):
    cfg = _config(tmp_path)
    eval_store.init_eval_store(cfg)
    eval_store.create_eval_case(cfg, {
        "case_id": "c1", "week_key": "2026-W01", "ticker": "A.SH",
        "selection_date": "2026-01-05",
    })
    eval_store.create_eval_prediction(cfg, {
        "prediction_id": "p1", "case_id": "c1", "variant": "full",
        "rating": "Buy",
    })
    eval_store.save_eval_return(cfg, {
        "prediction_id": "p1", "case_id": "c1", "horizon": 20,
        "gross_return": 0.10, "net_return": 0.098,
    })

    rows = eval_store.get_eval_metrics_rows(cfg)
    assert rows[0]["case_id"] == "c1"
    assert rows[0]["gross_return"] == pytest.approx(0.10)


@pytest.mark.unit
def test_ablation_delta_reflects_different_decisions():
    rows = [
        {"case_id": "c1", "ticker": "A.SH", "variant": "full",
         "rating": "Buy", "horizon": 20, "gross_return": 0.10,
         "net_return": 0.098},
        {"case_id": "c1", "ticker": "A.SH", "variant": "single",
         "rating": "Hold", "horizon": 20, "gross_return": 0.10,
         "net_return": 0.098},
    ]
    result = S._ablation_delta(rows, 20, friction_bp=20)
    assert result["pairs"][0]["full_strategy_return"] == pytest.approx(0.098)
    assert result["pairs"][0]["single_strategy_return"] == 0.0
    assert result["mean_delta"] == pytest.approx(0.098)


@pytest.mark.unit
def test_month_coverage_uses_horizon_mature_cohort(monkeypatch):
    rows = [
        {"case_id": "c1", "week_key": "2026-W01", "ticker": "A.SH",
         "status": "settled", "variant": "full", "rating": "Buy",
         "confidence": "high", "smart_money_score": 1, "horizon": 20,
         "gross_return": 0.05, "net_return": 0.048,
         "benchmark_return": 0.01, "excess_return": 0.038},
        {"case_id": "c2", "week_key": "2026-W01", "ticker": "B.SH",
         "status": "settled", "variant": "full", "rating": "Hold",
         "confidence": "low", "smart_money_score": 0, "horizon": None,
         "gross_return": None, "net_return": None,
         "benchmark_return": None, "excess_return": None},
    ]
    monkeypatch.setattr(eval_store, "get_eval_metrics_rows", lambda c: rows)
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda c, **k: [
        {"case_id": "c1", "week_key": "2026-W01"},
        {"case_id": "c2", "week_key": "2026-W01"},
    ])
    result = S.compute_summary({"evaluation": {}}, "month")
    assert result["coverage_pct"] == pytest.approx(0.5)


@pytest.mark.unit
def test_coverage_counts_failed_sample_without_prediction(monkeypatch):
    rows = [{
        "case_id": "c1", "week_key": "2026-W01", "ticker": "A.SH",
        "status": "settled", "variant": "full", "rating": "Buy",
        "confidence": "high", "smart_money_score": 1, "horizon": 5,
        "gross_return": 0.05, "net_return": 0.048,
        "benchmark_return": 0.01, "excess_return": 0.038,
    }]
    monkeypatch.setattr(eval_store, "get_eval_metrics_rows", lambda c: rows)
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda c, **k: [
        {"case_id": "c1", "week_key": "2026-W01", "status": "settled"},
        {"case_id": "c2", "week_key": "2026-W01", "status": "failed"},
    ])
    assert S.compute_summary({"evaluation": {}}, "week")["coverage_pct"] == pytest.approx(0.5)


@pytest.mark.unit
def test_period_bucket_requires_its_own_horizon(monkeypatch):
    monkeypatch.setattr(
        eval_store, "get_eval_predictions",
        lambda c, case_id=None, variant=None: [
            {"prediction_id": f"p-{case_id}", "case_id": case_id, "variant": "full"}
        ],
    )
    available = {("p-c1", 5)}
    monkeypatch.setattr(
        eval_store, "get_eval_returns",
        lambda c, prediction_id=None, horizon=None, **kw:
            [{"net_return": 0.01}] if (prediction_id, horizon) in available else [],
    )
    cases = [{"case_id": "c1", "status": "settled"}]
    assert ES._period_bucket_ready({}, cases, "week") is True
    assert ES._period_bucket_ready({}, cases, "month") is False
    assert ES._period_bucket_ready({}, cases, "quarter") is False


@pytest.mark.unit
def test_monthly_ablation_skips_existing_single_prediction(monkeypatch):
    case = {"case_id": "c1", "week_key": "2026-W01", "ticker": "A.SH",
            "selection_date": "2026-01-05", "source": "weekly"}
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda *a, **k: [case])
    monkeypatch.setattr(
        eval_store, "get_eval_predictions",
        lambda *a, **k: [{"prediction_id": "existing", "variant": "single"}],
    )
    monkeypatch.setattr(
        eval_store, "get_full_prediction", lambda *a, **k: {"prediction_id": "full"}
    )
    monkeypatch.setattr(
        eval_store, "load_frozen_reports", lambda *a, **k: {"market_report": "frozen"}
    )
    monkeypatch.setattr(
        P, "run_single_llm_ablation",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call LLM twice")),
    )
    result = P.run_monthly_ablation({"evaluation": {}}, "2026-01")
    assert result["predictions"] == 0
    assert result["skipped"] == 1


@pytest.mark.unit
def test_monthly_ablation_selects_only_cases_with_frozen_full_input(monkeypatch):
    cases = [
        {"case_id": "failed", "week_key": "2026-W01", "ticker": "F.SH",
         "selection_date": "2026-01-05", "status": "failed"},
        {"case_id": "missing", "week_key": "2026-W01", "ticker": "M.SH",
         "selection_date": "2026-01-05", "status": "pending"},
        {"case_id": "good", "week_key": "2026-W01", "ticker": "G.SH",
         "selection_date": "2026-01-05", "status": "settled"},
    ]
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda *a, **k: cases)
    monkeypatch.setattr(
        eval_store, "get_full_prediction",
        lambda c, case_id: {"prediction_id": "p-good"} if case_id == "good" else None,
    )
    monkeypatch.setattr(
        eval_store, "load_frozen_reports",
        lambda c, case_id: {"market_report": "frozen"} if case_id == "good" else {},
    )
    captured = []
    monkeypatch.setattr(
        P, "select_ablation_cases",
        lambda pool, count, seed=0: captured.extend(pool) or [],
    )
    P.run_monthly_ablation({"evaluation": {}}, "2026-01")
    assert [case["case_id"] for case in captured] == ["good"]


@pytest.mark.unit
def test_prediction_variant_is_unique_per_case(tmp_path):
    cfg = _config(tmp_path)
    eval_store.init_eval_store(cfg)
    eval_store.create_eval_case(cfg, {
        "case_id": "c1", "week_key": "2026-W01", "ticker": "A.SH",
        "selection_date": "2026-01-05",
    })
    for pred_id in ("p1", "p2"):
        eval_store.create_eval_prediction(cfg, {
            "prediction_id": pred_id, "case_id": "c1", "variant": "single",
            "rating": "Hold",
        })
    assert len(eval_store.get_eval_predictions(cfg, case_id="c1", variant="single")) == 1


@pytest.mark.unit
def test_frozen_reports_are_immutable(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    eval_store.init_eval_store(cfg)
    original = {"market_report": "original"}
    eval_store.save_frozen_reports(cfg, "c1", original, "hash-1")
    eval_store.create_eval_prediction(cfg, {
        "prediction_id": "p1", "case_id": "c1", "variant": "full",
        "rating": "Buy",
    })
    eval_store.save_frozen_reports(
        cfg, "c1", {"market_report": "overwritten"}, "hash-2"
    )
    monkeypatch.setattr(
        "web.results_store.load_full_state",
        lambda *a, **k: {"market_report": "overwritten"},
    )
    assert eval_store.load_frozen_reports(cfg, "c1")["market_report"] == "original"


@pytest.mark.unit
def test_adjusted_price_fetch_does_not_fall_back_to_unadjusted(monkeypatch):
    from quantconclave.dataflows import akshare_data
    from quantconclave.dataflows import interface

    monkeypatch.setattr(akshare_data, "get_stock_data_akshare", lambda *a, **k: "# SKIP_VENDOR unavailable")
    monkeypatch.setattr(
        interface, "route_to_vendor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unadjusted fallback forbidden")),
    )
    assert prices.fetch_adjusted_prices("A.SH", "2026-01-01", "2026-02-01").empty


@pytest.mark.unit
def test_weekly_retry_resumes_case_without_full_prediction(monkeypatch):
    case = {"case_id": "c1", "week_key": "2026-W02", "ticker": "A.SH",
            "company_name": "A", "index_source": "csi300", "industry": "X",
            "selection_date": "2026-01-05", "source": "weekly", "status": "pending"}
    monkeypatch.setattr(eval_store, "list_eval_cases", lambda *a, **k: [case])
    monkeypatch.setattr(eval_store, "get_eval_predictions", lambda *a, **k: [])
    monkeypatch.setattr(eval_store, "create_eval_prediction", lambda c, p: p["prediction_id"])
    monkeypatch.setattr(eval_store, "mark_case_pending", lambda *a, **k: None)
    monkeypatch.setattr(eval_store, "save_frozen_reports", lambda *a, **k: None)
    monkeypatch.setattr(P, "run_full_analysis_with_retry", lambda *a, **k: ({}, "Hold", {}))
    monkeypatch.setattr(P, "_persist_result_run", lambda *a, **k: "run-1")
    monkeypatch.setattr(P, "_estimate_run_cost", lambda *a, **k: 0.0)
    result = P.run_weekly_evaluation(
        {"evaluation": {"csi300_count": 1, "csi500_count": 0}}, "2026-01-05"
    )
    assert result["predictions"] == 1
    assert result["resumed"] is True


@pytest.mark.unit
def test_weekly_sample_is_independent_of_upstream_order():
    candidates = [
        {"code": f"S{i}.SH", "name": str(i), "industry": chr(65 + i % 3),
         "list_date": "20200101"}
        for i in range(12)
    ]
    cfg = {"evaluation": {"csi300_count": 4, "csi500_count": 0}}
    a = sample_weekly_stocks(cfg, "2026-01-05", {"csi300": candidates, "csi500": []})
    b = sample_weekly_stocks(cfg, "2026-01-05", {"csi300": list(reversed(candidates)), "csi500": []})
    assert a == b


@pytest.mark.unit
def test_scheduler_registers_monthly_ablation():
    calls = []

    class Manager:
        def add_task(self, task):
            calls.append(task)

    ES.register_evaluation_tasks(Manager(), {"evaluation": {"enabled": True}})
    assert [c["job_id"] for c in calls] == [
        "evaluation_weekly", "evaluation_settle", "evaluation_monthly"
    ]
