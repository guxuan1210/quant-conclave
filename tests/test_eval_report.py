"""Tests for fixed-template evaluation reports + WeCom summaries (no LLM)."""

from __future__ import annotations

import pytest

from web import eval_report as R


def _metrics(**over):
    base = {
        "period": "week",
        "period_key": "2026-W02",
        "horizon": 5,
        "portfolio_net_return": 0.04,
        "benchmark_return": 0.01,
        "excess_return": 0.03,
        "positions": 3,
        "cum_excess_return": 0.06,
        "annualized_ir": 1.25,
        "max_drawdown_delta": -0.02,
        "coverage_pct": 0.8,
        "validity": {"status": "effective"},
        "direction": {"directional_total": 10, "correct": 7, "accuracy": 0.7},
        "mature_weeks": 6,
    }
    base.update(over)
    return base


@pytest.mark.unit
def test_build_wecom_summary_fields():
    md = R.build_wecom_summary(_metrics())
    assert "评测周报" in md
    assert "5日" in md
    assert "**周期**: 2026-W02" in md
    assert "**组合净收益**: 4.00%" in md
    assert "**基准收益**: 1.00%" in md
    assert "**超额收益**: 3.00%" in md
    assert "**累计超额**: 6.00%" in md
    assert "**年化信息比率**: 1.25" in md
    assert "**回撤差**: -2.00%" in md
    assert "**覆盖率**: 80.0%" in md
    assert "**方向正确率**: 70.0% (7/10)" in md
    assert "**有效性**: effective" in md


@pytest.mark.unit
def test_build_wecom_summary_month_missing_fields():
    md = R.build_wecom_summary({"period": "month", "period_key": "2026-01"})
    assert "评测月报" in md
    assert "20日" in md
    assert "**组合净收益**" not in md
    assert "**累计超额**: —" in md
    assert "**有效性**: —" in md


@pytest.mark.unit
def test_report_build_never_touches_llm(monkeypatch):
    """Building a report must not invoke any LLM factory / resolver."""
    from quantconclave.evaluation import summary as SUMM
    from quantconclave.llm_clients import factory as F

    def boom(*a, **k):
        raise AssertionError("LLM factory must not be called by report builders")

    monkeypatch.setattr(F, "resolve_role_llm", boom)
    monkeypatch.setattr(F, "create_llm_client", boom)
    monkeypatch.setattr(SUMM, "compute_summary", lambda c, p: {
        "weekly": [{"week_key": "2026-W02", "portfolio_net_return": 0.04,
                    "benchmark_return": 0.01, "excess_return": 0.03, "positions": 3}],
        "cum_excess_return": 0.06,
        "annualized_ir": 1.25,
        "max_drawdown_delta": -0.02,
        "coverage_pct": 0.8,
        "validity": {"status": "effective"},
        "direction": {},
        "mature_weeks": 6,
    })

    report = R.build_weekly_report({}, "2026-W02")
    assert report["metrics"]["period"] == "week"
    assert report["metrics"]["period_key"] == "2026-W02"
    assert "评测周报" in report["markdown"]


@pytest.mark.unit
def test_push_eval_report_archives_and_pushes(monkeypatch):
    from web import eval_store

    saved = {}
    monkeypatch.setattr(eval_store, "upsert_eval_report", lambda c, row: saved.update(row))
    monkeypatch.setattr(R, "_push_markdown",
                        lambda c, md: {"channel": "webhook", "ok": True, "errmsg": ""})
    monkeypatch.setattr(R, "build_weekly_report",
                        lambda c, key: {"metrics": {"period": "week", "period_key": key,
                                                    "validity": {"status": "effective"}},
                                        "markdown": "# md", "generated_at": "2026-01-01 00:00:00"})

    out = R.push_eval_report({}, "week", "2026-W02")
    assert out["push"]["ok"] is True
    assert saved["period"] == "week"
    assert saved["period_key"] == "2026-W02"
    assert saved["is_valid"] == 1
    assert saved["report"]["markdown"] == "# md"
