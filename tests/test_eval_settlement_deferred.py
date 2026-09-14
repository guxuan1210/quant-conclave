"""Tests for deferred settlement (settle_pending_cases) — pure, no network/DB."""

from __future__ import annotations

import pytest

from quantconclave.evaluation import pipeline as P
from web import eval_store

CFG = {"evaluation": {}}


def _patch_store(monkeypatch, cases, predictions, returns):
    """Mock web.eval_store with in-memory state; returns a calls recorder."""
    calls = {"settled": [], "failed": [], "saved": []}

    def list_cases(config, **kw):
        return list(cases)

    def get_preds(config, case_id=None, variant=None):
        return [p for p in predictions if p["case_id"] == case_id]

    def get_returns(config, prediction_id=None, case_id=None, horizon=None):
        return list(returns.get((prediction_id, horizon), []))

    def save_return(config, ret):
        calls["saved"].append(ret)

    def mark_settled(config, case_id):
        calls["settled"].append(case_id)

    def mark_failed(config, case_id, reason):
        calls["failed"].append((case_id, reason))

    monkeypatch.setattr(eval_store, "list_eval_cases", list_cases)
    monkeypatch.setattr(eval_store, "get_eval_predictions", get_preds)
    monkeypatch.setattr(eval_store, "get_eval_returns", get_returns)
    monkeypatch.setattr(eval_store, "save_eval_return", save_return)
    monkeypatch.setattr(eval_store, "mark_case_settled", mark_settled)
    monkeypatch.setattr(eval_store, "mark_case_failed", mark_failed)
    return calls


def _case(case_id="c1", status="pending", ticker="T.SH", selection_date="2026-01-05"):
    return {"case_id": case_id, "week_key": "2026-W02", "ticker": ticker,
            "selection_date": selection_date, "status": status}


def _pred(pred_id="p1", case_id="c1", variant="full"):
    return {"prediction_id": pred_id, "case_id": case_id, "variant": variant}


@pytest.mark.unit
def test_skip_when_entry_in_future(monkeypatch):
    calls = _patch_store(monkeypatch, [_case()], [_pred()], returns={})
    monkeypatch.setattr(P, "settle_entry", lambda c, t, s: {"entry_date": "2026-02-01", "entry_price": None})
    summary = P.settle_pending_cases(CFG, as_of_date="2026-01-15")
    assert summary["skipped"] == 1
    assert calls["failed"] == [] and calls["settled"] == [] and calls["saved"] == []


@pytest.mark.unit
def test_entry_missing_within_grace_skips(monkeypatch):
    calls = _patch_store(monkeypatch, [_case()], [_pred()], returns={})
    monkeypatch.setattr(P, "settle_entry", lambda c, t, s: {"entry_date": "2026-01-06", "entry_price": None})
    monkeypatch.setattr(P, "_entry_grace_exceeded", lambda *a, **k: False)
    summary = P.settle_pending_cases(CFG, as_of_date="2026-01-10")
    assert summary["skipped"] == 1
    assert calls["failed"] == []


@pytest.mark.unit
def test_entry_missing_beyond_grace_fails(monkeypatch):
    calls = _patch_store(monkeypatch, [_case()], [_pred()], returns={})
    monkeypatch.setattr(P, "settle_entry", lambda c, t, s: {"entry_date": "2026-01-06", "entry_price": None})
    monkeypatch.setattr(P, "_entry_grace_exceeded", lambda *a, **k: True)
    summary = P.settle_pending_cases(CFG, as_of_date="2026-01-20")
    assert summary["failed"] == 1
    assert calls["failed"] == [("c1", "no entry price on 2026-01-06")]


@pytest.mark.unit
def test_full_settles_and_marks(monkeypatch):
    calls = _patch_store(monkeypatch, [_case()], [_pred()], returns={})
    monkeypatch.setattr(P, "settle_entry", lambda c, t, s: {"entry_date": "2026-01-06", "entry_price": 100.0})
    exits = {"5": "2026-01-13", "20": "2026-02-03", "60": "2026-04-02"}
    monkeypatch.setattr(P, "nth_trading_day", lambda a, n, config=None: exits[str(n)])
    monkeypatch.setattr(P, "settle_horizon",
                        lambda c, t, ed, ep, h: {"horizon": h, "settled": True, "exit_date": exits[str(h)]})
    summary = P.settle_pending_cases(CFG, as_of_date="2026-04-05")
    assert summary["settled_horizons"] == 3
    assert summary["settled"] == 1
    assert calls["settled"] == ["c1"]
    assert len(calls["saved"]) == 3


@pytest.mark.unit
def test_future_exit_skipped(monkeypatch):
    calls = _patch_store(monkeypatch, [_case()], [_pred()], returns={})
    monkeypatch.setattr(P, "settle_entry", lambda c, t, s: {"entry_date": "2026-01-06", "entry_price": 100.0})
    exits = {"5": "2026-01-13", "20": "2026-02-03", "60": "2026-04-02"}
    monkeypatch.setattr(P, "nth_trading_day", lambda a, n, config=None: exits[str(n)])
    monkeypatch.setattr(P, "settle_horizon",
                        lambda c, t, ed, ep, h: {"horizon": h, "settled": True, "exit_date": exits[str(h)]})
    summary = P.settle_pending_cases(CFG, as_of_date="2026-02-05")
    # h=5 and h=20 settle; h=60 (2026-04-02) is still in the future.
    assert summary["settled_horizons"] == 2
    assert summary["settled"] == 1  # full 5-day horizon done
    assert len(calls["saved"]) == 2


@pytest.mark.unit
def test_fully_settled_skips(monkeypatch):
    returns = {
        ("p1", 5): [{"net_return": 0.01}],
        ("p1", 20): [{"net_return": 0.02}],
        ("p1", 60): [{"net_return": 0.03}],
    }
    calls = _patch_store(monkeypatch, [_case()], [_pred()], returns=returns)
    monkeypatch.setattr(P, "settle_entry",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not refetch entry")))
    summary = P.settle_pending_cases(CFG, as_of_date="2026-04-05")
    assert summary == {"settled": 0, "settled_horizons": 0, "failed": 0, "skipped": 0}
