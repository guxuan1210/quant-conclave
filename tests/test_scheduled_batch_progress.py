"""Progress-publishing tests for the scheduled batch runners.

Verifies that a scheduled run publishes analyzed/total to ``web.run_progress``
(so the task UI can show 运行中 · 一次分析 · 已分析 X/Y · 已用时) and that the
scheduler entrypoint clears the marker in a finally — even when the run crashes.

``_analyze_workers`` imports ``watchlist_stock_detail`` from ``web.app`` inside
the function, so stubs patch ``web.app``, not ``web.scheduled_batch``.
"""

from __future__ import annotations

import pytest

import web.app as app_mod
import web.scheduled_batch as sb
from web import run_progress
from web.scheduler import _execute_scheduled_analysis


@pytest.fixture()
def config(tmp_path):
    return {"results_dir": str(tmp_path)}


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    import web.run_progress as rp
    with rp._lock:
        rp._RUNS.clear()


def _stub_detail(code, **kwargs):
    return {"verdict": "观望", "setup_type": "", "model_name": "m1",
            "analysis": "", "analysis_id": None, "analyzed_at": ""}


# ── _analyze_workers ──
def test_analyze_workers_publishes_progress(monkeypatch):
    """_analyze_workers only updates the counter; _run_batch does the start,
    so mirror that here: start first, then assert the increments."""
    monkeypatch.setattr(app_mod, "watchlist_stock_detail", _stub_detail)
    stocks = [{"code": "600036.SH", "name": "招行"},
              {"code": "000001.SZ", "name": "平安"},
              {"code": "300750.SZ", "name": "宁德"}]
    run_progress.start("job-x", phase="analyzing", total=len(stocks))
    results = sb._analyze_workers(stocks, [{"provider": "p", "model": "m1"}],
                                  "job-x", inter_delay_ms=0)
    assert len(results) == 3
    p = run_progress.get("job-x")
    assert p["status"] == "running"
    assert p["phase"] == "analyzing"
    assert p["analyzed"] == 3
    assert p["total"] == 3
    assert p["current"] in {"招行", "平安", "宁德"}


def test_analyze_workers_no_job_id_has_no_side_effect(monkeypatch):
    """Backward-compat: callers that omit job_id get the old behavior."""
    monkeypatch.setattr(app_mod, "watchlist_stock_detail", _stub_detail)
    stocks = [{"code": "600036.SH", "name": "招行"}]
    results = sb._analyze_workers(stocks, [{"provider": "p", "model": "m1"}],
                                  inter_delay_ms=0)
    assert len(results) == 1
    assert run_progress.get("") is None


# ── _run_batch via run_batch_task ──
def test_run_batch_publishes_phase_and_counts(config, monkeypatch):
    """A full batch run with job_id ends with phase=analyzing, analyzed==total."""
    monkeypatch.setattr(sb, "_build_pool",
                        lambda t, d, c: [
                            {"code": "600036.SH", "name": "招行", "change_pct": 2.0},
                            {"code": "000001.SZ", "name": "平安", "change_pct": 1.0},
                        ])
    monkeypatch.setattr(app_mod, "watchlist_stock_detail", _stub_detail)

    summary = sb.run_batch_task(config, {
        "task_type": "emwl_batch",
        "job_id": "batch-prog",
        "workers": [{"provider": "p", "model": "m1"}],
    })
    assert summary["analyzed"] == 2
    p = run_progress.get("batch-prog")
    assert p is not None
    assert p["phase"] == "analyzing"
    assert p["analyzed"] == p["total"] == 2
    assert p["elapsed_sec"] >= 0


# ── _execute_scheduled_analysis finally-cleanup ──
def test_execute_clears_progress_in_finally(monkeypatch):
    """Even a crashing dispatch must not leave a ghost 'running' entry.

    The dispatch raises (APScheduler logs it and moves on), but the finally in
    ``_execute_scheduled_analysis`` must still drop the progress marker.
    """
    from web import scheduler as sched_mod

    def boom(task_data):
        run_progress.start(task_data["job_id"], phase="deep")
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(sched_mod, "_execute_scheduled_analysis_dispatch", boom)
    with pytest.raises(RuntimeError):
        _execute_scheduled_analysis({"job_id": "deep-crash", "task_type": "deep"})
    assert run_progress.get("deep-crash") is None


def test_execute_deep_branch_starts_progress(monkeypatch):
    """The deep branch publishes phase='deep' BEFORE constructing the graph.

    We let the real dispatch run up to its ``run_progress.start`` line, then
    block QuantConclaveGraph construction so the test needs no LLM/network. The
    finally still clears the marker afterwards.
    """
    from web import scheduler as sched_mod
    import quantconclave.graph.trading_graph as tg

    class BlockGraph:
        def __init__(self, **kwargs):
            raise RuntimeError("graph construction blocked")

    monkeypatch.setattr(tg, "QuantConclaveGraph", BlockGraph)
    with pytest.raises(RuntimeError):
        sched_mod._execute_scheduled_analysis_dispatch({
            "job_id": "deep-run", "task_type": "deep",
            "ticker": "600036", "analysts": ["market"],
        })
    p = run_progress.get("deep-run")
    assert p is not None
    assert p["phase"] == "deep"
    assert p["total"] == 0
    assert p["status"] == "running"


def test_execute_deep_branch_records_run_log(config, monkeypatch):
    """A completed deep run appends a scheduled_run_log row (deep tasks used to
    only write result_runs, so their task UI never showed last_run / history).

    A fake graph returns a finished state; the run record must land in the temp
    store (dispatch imports DEFAULT_CONFIG at call time, so we patch it to the
    temp config) with a self-contained summary the report can rebuild from.
    """
    import quantconclave.default_config as dc
    import quantconclave.graph.trading_graph as tg
    from web import scheduler as sched_mod
    from web.scheduler import get_scheduler_runs, init_scheduler_run_store

    class FakeGraph:
        def __init__(self, **kwargs):
            pass

        def propagate(self, ticker, date_str):
            return ({"final_trade_decision": "Buy"}, "buy")

    saved = {}

    def fake_save_result(cfg, run_data):
        saved.update(run_data)

    # Dispatch reads DEFAULT_CONFIG keys (llm_provider etc.) and writes to its
    # results_dir — so patch a copy of the real config with only the store dir
    # redirected to the temp path.
    init_scheduler_run_store(config)
    cfg = dict(dc.DEFAULT_CONFIG)
    cfg["results_dir"] = config["results_dir"]
    monkeypatch.setattr(dc, "DEFAULT_CONFIG", cfg)             # temp store
    monkeypatch.setattr(tg, "QuantConclaveGraph", FakeGraph)
    monkeypatch.setattr("web.results_store.save_result", fake_save_result)
    monkeypatch.setattr("quantconclave.agents.utils.rating.parse_rating",
                        lambda _s: "buy")
    monkeypatch.setattr("web.ticker_utils.resolve_company_name",
                        lambda t: (t, "招商银行"))

    sched_mod._execute_scheduled_analysis_dispatch({
        "job_id": "deep-save", "task_type": "deep",
        "ticker": "600036", "analysts": ["market"],
        # use_current_date must be False so the given date_str is honored —
        # otherwise dispatch overwrites it with today's date (deterministic).
        "use_current_date": False,
        "date_str": "2026-08-23",
    })

    assert saved["rating"] == "buy"           # result_runs path still works
    runs = get_scheduler_runs(config, "deep-save")
    assert len(runs) == 1
    assert runs[0]["task_type"] == "deep"
    s = runs[0]["summary"]
    assert s["rating"] == "buy"
    assert s["ticker"] == "600036"
    assert s["company_name"] == "招商银行"
    assert s["date"] == "2026-08-23"
