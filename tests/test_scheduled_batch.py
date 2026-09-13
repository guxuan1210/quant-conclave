"""Tests for the scheduled batch runners (东方自选 / 指数选股).

Uses a temp results.db via ``config={"results_dir": str(tmp_path)}`` — no
network. The network-bound paths (``_analyze_workers``, ``_build_pool``) are
not exercised; we test the pure filters, worker clamp/fallback, the pack-mode
two-pass packing, and the scheduler run log.
"""

from __future__ import annotations

import pytest

from web.scheduler import (
    get_last_scheduler_run,
    init_scheduler_run_store,
    save_scheduler_run,
)
from web.scheduled_batch import (
    _filter_stocks,
    _workers_for,
    _run_batch,
    run_two_pass,
)
from web.stage3_records import get_stage3_record, init_stage3_store
from web.results_store import init_chat_tables, save_chat_message
from web.twopass_records import get_twopass_record, init_twopass_store


@pytest.fixture()
def config(tmp_path):
    return {"results_dir": str(tmp_path)}


# ── _filter_stocks ──
def test_filter_stocks_board_prefixes():
    stocks = [
        {"code": "600036.SH", "name": "招行"},
        {"code": "300750.SZ", "name": "宁德"},
        {"code": "301236.SZ", "name": "软通"},
        {"code": "688981.SH", "name": "中芯"},
        {"code": "830799.BJ", "name": "北交"},
        {"code": "000001.SZ", "name": "平安"},
    ]
    out = _filter_stocks(stocks, excluded_boards=["cyb", "kcb", "bjs"])
    assert sorted(s["code"] for s in out) == ["000001.SZ", "600036.SH"]


def test_filter_stocks_change_pct():
    stocks = [
        {"code": "1", "change_pct": "3.5"},
        {"code": "2", "change_pct": "0.0"},
        {"code": "3", "change_pct": None},  # missing → treated 0
        {"code": "4", "change_pct": "-1.2"},
        {"code": "5"},                       # missing key → treated 0
    ]
    out = _filter_stocks(stocks, change_pct_min=1.0)
    assert [s["code"] for s in out] == ["1"]


def test_filter_stocks_no_constraints():
    stocks = [{"code": "600036.SH"}, {"code": "300750.SZ"}]
    # excluded_boards empty + no change_pct_min → everything passes
    assert len(_filter_stocks(stocks)) == 2
    assert len(_filter_stocks(stocks, excluded_boards=[])) == 2


# ── _workers_for ──
def test_workers_for_clamp_and_fallback():
    many = [{"provider": "a", "model": "m"}] * 10
    assert len(_workers_for({"workers": many})) == 8

    assert _workers_for({"workers": []}) == [{"provider": "", "model": ""}]
    assert _workers_for({}) == [{"provider": "", "model": ""}]

    main = [{"provider": "x", "model": "y"}]
    # two_pass_workers empty → fall back to main workers
    assert _workers_for({"workers": main, "two_pass_workers": []},
                        "two_pass_workers") == main


# ── run_two_pass (pack mode — no LLM, no network) ──
def test_run_two_pass_pack(config):
    init_twopass_store(config)
    analyzed = [
        {"code": "600036.SH", "name": "招行", "price": 38.0, "change_pct": 2.0,
         "verdict": "看多", "model_name": "m1", "analysis": "主力持续流入"},
        {"code": "000001.SZ", "name": "平安", "price": 10.0, "change_pct": -0.5,
         "verdict": "观望", "model_name": "m1", "analysis": "中性"},
    ]
    rid = run_two_pass("emwl", analyzed,
                       {"two_pass": True, "two_pass_mode": "pack",
                        "workers": [{"provider": "p", "model": "m1"}]},
                       config)
    assert rid is not None
    rec = get_twopass_record(config, rid)
    assert rec["tab"] == "emwl"
    assert rec["stock_count"] == 1
    assert rec["bullish_count"] == 1
    assert rec["model_count"] == 1
    assert "东方自选" in rec["title"]
    s = rec["stocks"][0]
    assert s["code"] == "600036.SH"
    assert s["newVerdict"] == "看多"
    assert s["prevVerdict"] == ""
    assert s["newModel"] == "m1"


def test_run_two_pass_no_bullish(config):
    init_twopass_store(config)
    analyzed = [{"code": "1", "verdict": "观望", "model_name": "m"}]
    assert run_two_pass("idx", analyzed,
                        {"two_pass": True, "two_pass_mode": "pack"}, config) is None


# ── scheduler run log ──
def test_scheduler_run_log(config):
    init_scheduler_run_store(config)
    rid = save_scheduler_run(config, "job-1", "东方自选每日", "emwl_batch",
                             {"pool": 50, "analyzed": 45, "bullish": 3})
    assert rid > 0
    last = get_last_scheduler_run(config, "job-1")
    assert last is not None
    assert last["job_id"] == "job-1"
    assert last["task_type"] == "emwl_batch"
    assert last["summary"]["bullish"] == 3
    assert last["summary"]["analyzed"] == 45
    # no run recorded yet
    assert get_last_scheduler_run(config, "missing") is None


def test_scheduler_run_log_newest_wins(config):
    init_scheduler_run_store(config)
    save_scheduler_run(config, "j", "t", "deep", {"analyzed": 1})
    save_scheduler_run(config, "j", "t", "deep", {"analyzed": 2})
    last = get_last_scheduler_run(config, "j")
    assert last["summary"]["analyzed"] == 2


# ── _run_batch → stage-3 trigger ──
def _stub_analyzed():
    return [{"code": "600036.SH", "name": "招行", "price": 38.0, "change_pct": 2.0,
             "verdict": "看多", "model_name": "m1", "analysis": "主力持续流入"}]


def test_run_batch_triggers_stage3(config, monkeypatch):
    import web.scheduled_batch as sb
    init_twopass_store(config)
    monkeypatch.setattr(sb, "_build_pool",
                        lambda t, d, c: [{"code": "600036.SH", "name": "招行"}])
    monkeypatch.setattr(sb, "_analyze_workers",
                        lambda stocks, workers, job_id=None: _stub_analyzed())
    monkeypatch.setattr(sb, "run_two_pass", lambda tab, analyzed, td, c: 55)
    seen = {}
    def _stage3(tab, tp_id, td, c):
        seen["tab"], seen["tp_id"] = tab, tp_id
        return 66
    monkeypatch.setattr(sb, "run_stage3", _stage3)

    summary = sb._run_batch(config, {
        "task_type": "emwl_batch", "two_pass": True, "stage3": True,
        "workers": [{"provider": "p", "model": "m1"}],
    }, "emwl_batch")
    assert summary["twopass_record_id"] == 55
    assert summary["stage3_record_id"] == 66
    assert seen == {"tab": "emwl", "tp_id": 55}


def test_run_batch_summary_includes_codes(config, monkeypatch):
    """The run summary carries per-stock conclusions so the scheduled-task
    report can render ① per-stock table from the stored run log."""
    import web.scheduled_batch as sb
    init_twopass_store(config)
    monkeypatch.setattr(sb, "_build_pool",
                        lambda t, d, c: [{"code": "600036.SH", "name": "招行", "change_pct": 2.0}])
    monkeypatch.setattr(sb, "_analyze_workers",
                        lambda stocks, workers, job_id=None: [
                            {"code": "600036.SH", "name": "招行",
                             "change_pct": 2.0, "verdict": "看多",
                             "model_name": "m1"}])
    monkeypatch.setattr(sb, "run_two_pass",
                        lambda *a, **k: pytest.fail("two_pass off → must not run"))

    summary = sb._run_batch(config, {
        "task_type": "emwl_batch", "two_pass": False,
        "workers": [{"provider": "p", "model": "m1"}],
    }, "emwl_batch")

    assert summary["analyzed"] == 1
    assert summary["codes"] == [{
        "code": "600036.SH", "name": "招行", "change_pct": 2.0,
        "verdict": "看多", "model_name": "m1",
    }]


def test_run_batch_no_stage3_when_disabled(config, monkeypatch):
    import web.scheduled_batch as sb
    init_twopass_store(config)
    monkeypatch.setattr(sb, "_build_pool",
                        lambda t, d, c: [{"code": "600036.SH"}])
    monkeypatch.setattr(sb, "_analyze_workers",
                        lambda stocks, workers, job_id=None: _stub_analyzed())
    monkeypatch.setattr(sb, "run_two_pass", lambda tab, analyzed, td, c: 55)
    monkeypatch.setattr(sb, "run_stage3",
                        lambda *a, **k: pytest.fail("stage3 must not run"))

    summary = sb._run_batch(config, {
        "task_type": "emwl_batch", "two_pass": True, "stage3": False,
        "workers": [{"provider": "p", "model": "m1"}],
    }, "emwl_batch")
    assert summary["twopass_record_id"] == 55
    assert "stage3_record_id" not in summary


def test_run_batch_no_stage3_without_bullish(config, monkeypatch):
    import web.scheduled_batch as sb
    init_twopass_store(config)
    monkeypatch.setattr(sb, "_build_pool",
                        lambda t, d, c: [{"code": "600036.SH"}])
    monkeypatch.setattr(sb, "_analyze_workers",
                        lambda stocks, workers, job_id=None: [
                            {"code": "600036.SH", "verdict": "观望",
                             "model_name": "m1"}])
    monkeypatch.setattr(sb, "run_two_pass",
                        lambda *a, **k: pytest.fail("no bullish → no two-pass"))
    monkeypatch.setattr(sb, "run_stage3",
                        lambda *a, **k: pytest.fail("no bullish → no stage3"))

    summary = sb._run_batch(config, {
        "task_type": "emwl_batch", "two_pass": True, "stage3": True,
        "workers": [{"provider": "p", "model": "m1"}],
    }, "emwl_batch")
    assert summary["bullish"] == 0
    assert "twopass_record_id" not in summary
    assert "stage3_record_id" not in summary


def test_run_batch_stage3_chains_to_real_record(config, monkeypatch):
    """End-to-end (no LLM): real pack-mode two-pass → real run_stage3 with an
    injected consume → a stage3_records row is written."""
    import web.scheduled_batch as sb
    init_twopass_store(config)
    init_stage3_store(config)
    init_chat_tables(config)
    monkeypatch.setattr(sb, "_build_pool",
                        lambda t, d, c: [{"code": "600036.SH", "name": "招行"}])
    monkeypatch.setattr(sb, "_analyze_workers",
                        lambda stocks, workers, job_id=None: _stub_analyzed())

    def fake_consume(thread_id, question, cfg, lang):
        save_chat_message(cfg, thread_id, "assistant", "整体看多，招行最强。")

    real_stage3 = sb.run_stage3
    def _inject(tab, tp_id, td, c):
        return real_stage3(tab, tp_id, td, c, _consume=fake_consume)
    monkeypatch.setattr(sb, "run_stage3", _inject)

    summary = sb._run_batch(config, {
        "task_type": "emwl_batch", "two_pass": True, "two_pass_mode": "pack",
        "stage3": True, "stage3_provider": "deepseek", "stage3_model": "ds-pro",
        "workers": [{"provider": "p", "model": "m1"}],
    }, "emwl_batch")

    assert summary["twopass_record_id"]
    assert summary["stage3_record_id"]
    rec = get_stage3_record(config, summary["stage3_record_id"])
    assert rec["report"] == "整体看多，招行最强。"
    assert rec["twopass_record_id"] == summary["twopass_record_id"]
    assert rec["stock_count"] == 1
    assert rec["model"] == "ds-pro"
    # The underlying twopass record exists and references the same bullish stock.
    tp = get_twopass_record(config, summary["twopass_record_id"])
    assert tp["bullish_count"] == 1
