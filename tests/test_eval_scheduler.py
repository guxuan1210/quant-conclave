"""Tests for evaluation scheduler tasks and the first-trading-day guard."""

from __future__ import annotations

import pytest

from web import trade_cal
from web import eval_scheduler as ES


# --- is_first_trading_day_of_week -------------------------------------

@pytest.mark.unit
def test_first_trading_day_monday(monkeypatch):
    monkeypatch.setattr(trade_cal, "get_open_days",
                        lambda: {"20260105", "20260106", "20260107"})
    assert trade_cal.is_first_trading_day_of_week("2026-01-05") is True


@pytest.mark.unit
def test_first_trading_day_tuesday_after_holiday(monkeypatch):
    monkeypatch.setattr(trade_cal, "get_open_days", lambda: {"20260106", "20260107"})
    assert trade_cal.is_first_trading_day_of_week("2026-01-06") is True


@pytest.mark.unit
def test_not_first_trading_day(monkeypatch):
    monkeypatch.setattr(trade_cal, "get_open_days",
                        lambda: {"20260105", "20260106", "20260107"})
    assert trade_cal.is_first_trading_day_of_week("2026-01-07") is False


@pytest.mark.unit
def test_holiday_monday_not_open(monkeypatch):
    monkeypatch.setattr(trade_cal, "get_open_days", lambda: {"20260106", "20260107"})
    assert trade_cal.is_first_trading_day_of_week("2026-01-05") is False


@pytest.mark.unit
def test_weekend_not_trading_day(monkeypatch):
    monkeypatch.setattr(trade_cal, "get_open_days", lambda: {"20260105", "20260106"})
    assert trade_cal.is_first_trading_day_of_week("2026-01-10") is False


# --- run_evaluation_task ----------------------------------------------

@pytest.mark.unit
def test_run_weekly_skips_non_first_day(monkeypatch):
    monkeypatch.setattr(trade_cal, "is_first_trading_day_of_week",
                        lambda *a, **k: False)
    out = ES.run_evaluation_task("evaluation_weekly", {})
    assert out["skipped"] is True


@pytest.mark.unit
def test_run_weekly_runs_pipeline(monkeypatch):
    from quantconclave.evaluation import pipeline as P
    monkeypatch.setattr(trade_cal, "is_first_trading_day_of_week",
                        lambda *a, **k: True)
    monkeypatch.setattr(P, "run_weekly_evaluation",
                        lambda c: {"status": "ok", "week_key": "2026-W02"})
    out = ES.run_evaluation_task("evaluation_weekly", {})
    assert out["status"] == "ok"
    assert out["week_key"] == "2026-W02"


@pytest.mark.unit
def test_run_settle_skips_non_trading_day(monkeypatch):
    monkeypatch.setattr(trade_cal, "get_open_days", lambda: set())
    out = ES.run_evaluation_task("evaluation_settle", {})
    assert out["skipped"] is True


@pytest.mark.unit
def test_run_settle_settles_and_pushes(monkeypatch):
    from datetime import datetime
    from quantconclave.evaluation import pipeline as P
    today = datetime.now().strftime("%Y%m%d")
    monkeypatch.setattr(trade_cal, "get_open_days", lambda: {today})
    monkeypatch.setattr(P, "settle_pending_cases",
                        lambda c: {"settled": 2, "settled_horizons": 5,
                                   "failed": 0, "skipped": 1})
    monkeypatch.setattr(ES, "_push_due_reports", lambda c: [("week", "2026-W02")])
    out = ES.run_evaluation_task("evaluation_settle", {})
    assert out["settled"] == 2
    assert out["reports_pushed"] == 1


@pytest.mark.unit
def test_run_unknown_type_skips():
    out = ES.run_evaluation_task("nope", {})
    assert out["skipped"] is True
    assert "unknown" in out["reason"]


# --- register_evaluation_tasks ----------------------------------------

@pytest.mark.unit
def test_register_disabled_returns():
    calls = []

    class M:
        def add_task(self, t):
            calls.append(t)

    ES.register_evaluation_tasks(M(), {"evaluation": {"enabled": False}})
    assert calls == []


@pytest.mark.unit
def test_register_adds_all_evaluation_tasks():
    calls = []

    class M:
        def add_task(self, t):
            calls.append(t)

    ES.register_evaluation_tasks(M(), {"evaluation": {"enabled": True}})
    assert [c["job_id"] for c in calls] == [
        "evaluation_weekly", "evaluation_settle", "evaluation_monthly"
    ]
