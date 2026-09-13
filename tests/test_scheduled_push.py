"""Hook tests: every scheduled run fires the WeChat push right after its result
is persisted — batch (emwl_batch/idx_batch) and deep — and the push receives the
SAME stored summary the dashboard report renders (they can never diverge).

Heavy dependencies (graph run, batch runner, DB writes, company-name lookup)
are stubbed; only the push plumbing is under test. The "no webhook ⇒ no-op"
guarantee lives inside web.wecom_push.push_run_result and is covered in
tests/test_wecom_push.py.
"""

from __future__ import annotations

import web.scheduler as sched_mod
from web.scheduler import _execute_scheduled_analysis_dispatch

BATCH_SUMMARY = {
    "pool": 3, "filtered": 3, "analyzed": 3,
    "bullish": 1, "bearish": 1, "watch": 1,
    "codes": [
        {"code": "600001", "name": "股票1", "change_pct": 2.5,
         "verdict": "看多", "model_name": "qwen3.8:27b"},
    ],
}


def _install_push_capture(monkeypatch):
    """Stub web.wecom_push.push_run_result and record its calls."""
    calls = []

    def fake_push(task_data, summary, ttype):
        calls.append({"task_data": task_data, "summary": summary, "ttype": ttype})

    monkeypatch.setattr("web.wecom_push.push_run_result", fake_push)
    return calls


def _stub_save(monkeypatch):
    """Don't write scheduler_run_log rows (would touch the real DB)."""
    calls = []

    def fake_save(config, job_id, name, ttype, summary):
        calls.append({"config": config, "job_id": job_id,
                      "name": name, "ttype": ttype, "summary": summary})
        return len(calls)

    monkeypatch.setattr(sched_mod, "save_scheduler_run", fake_save)
    return calls


def test_batch_branch_pushes_after_persist(monkeypatch):
    _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)
    monkeypatch.setattr(
        "web.scheduled_batch.run_batch_task",
        lambda cfg, task_data: dict(BATCH_SUMMARY))

    task_data = {
        "job_id": "emwl-1",
        "name": "东方自选每日",
        "task_type": "emwl_batch",
        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook",
        "workers": [{"provider": "", "model": ""}],
    }
    _execute_scheduled_analysis_dispatch(task_data)

    assert len(pushes) == 1
    assert pushes[0]["ttype"] == "emwl_batch"
    assert pushes[0]["task_data"] is task_data       # same stored config
    assert pushes[0]["summary"]["analyzed"] == 3     # same stored summary
    assert pushes[0]["summary"]["codes"][0]["code"] == "600001"


def test_idx_batch_branch_pushes(monkeypatch):
    _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)
    monkeypatch.setattr(
        "web.scheduled_batch.run_batch_task",
        lambda cfg, task_data: dict(BATCH_SUMMARY))

    task_data = {
        "job_id": "idx-1",
        "name": "指数选股",
        "task_type": "idx_batch",
        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook",
        "indexes": ["highdiv"],
    }
    _execute_scheduled_analysis_dispatch(task_data)

    assert len(pushes) == 1 and pushes[0]["ttype"] == "idx_batch"


def test_batch_branch_carries_push_user_through(monkeypatch):
    """A task configured with per-user push targets (``push_user`` list, the
    shape _build_task_data stores since the multi-select UI) keeps them in the
    task_data handed to push_run_result — the hook plumbing preserves the
    targets verbatim, and push_run_result (tested separately) delivers to them."""
    _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)
    monkeypatch.setattr(
        "web.scheduled_batch.run_batch_task",
        lambda cfg, task_data: dict(BATCH_SUMMARY))

    task_data = {
        "job_id": "emwl-1",
        "name": "东方自选每日",
        "task_type": "emwl_batch",
        "bot_id": "b1",
        "bot_secret": "s1",
        "push_user": ["zhangsan", "lisi"],
        "workers": [{"provider": "", "model": ""}],
    }
    _execute_scheduled_analysis_dispatch(task_data)

    assert len(pushes) == 1
    assert pushes[0]["task_data"]["push_user"] == ["zhangsan", "lisi"]
    assert pushes[0]["task_data"]["bot_id"] == "b1"


def test_batch_branch_legacy_scalar_push_user_flows_unchanged(monkeypatch):
    """A pre-upgrade task whose stored task_data still carries a scalar
    ``push_user`` string reaches push_run_result untouched — normalization is
    push_run_result's job (tested in test_wecom_push.py), never the dispatch."""
    _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)
    monkeypatch.setattr(
        "web.scheduled_batch.run_batch_task",
        lambda cfg, task_data: dict(BATCH_SUMMARY))

    task_data = {
        "job_id": "emwl-1",
        "name": "东方自选每日",
        "task_type": "emwl_batch",
        "bot_id": "b1",
        "bot_secret": "s1",
        "push_user": "lisi",
        "workers": [{"provider": "", "model": ""}],
    }
    _execute_scheduled_analysis_dispatch(task_data)

    assert len(pushes) == 1
    assert pushes[0]["task_data"]["push_user"] == "lisi"


def test_deep_branch_pushes_after_persist(monkeypatch):
    saves = _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)

    class FakeGraph:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def propagate(self, ticker, date):
            return {"final_trade_decision": "评级：买入"}, "买入信号"

    monkeypatch.setattr("quantconclave.graph.trading_graph.QuantConclaveGraph",
                        FakeGraph)
    monkeypatch.setattr("web.ticker_utils.resolve_company_name",
                        lambda t: ("600036", "招商银行"))
    monkeypatch.setattr("web.results_store.save_result", lambda cfg, d: None)

    task_data = {
        "job_id": "deep-1",
        "name": "银行深度",
        "task_type": "deep",
        "ticker": "600036",
        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook",
        "analysts": ["market", "news"],
        "use_current_date": True,
    }
    _execute_scheduled_analysis_dispatch(task_data)

    assert len(pushes) == 1
    assert pushes[0]["ttype"] == "deep"
    assert pushes[0]["task_data"] is task_data
    s = pushes[0]["summary"]
    assert s["ticker"] == "600036"
    assert s["company_name"] == "招商银行"
    assert s["rating"] == "Buy"                       # parsed from 评级：买入
    assert s["signal"] == "买入信号"
    assert s["analysts"] == "market,news"
    assert s["json_path"].startswith("600036/")

    # The push happens AFTER the run row is persisted (same ordering as the
    # dashboard report source).
    assert len(saves) == 1
    assert saves[0]["ttype"] == "deep"
    assert saves[0]["summary"] == s


def test_batch_branch_failure_pushes_failure_card_then_raises(monkeypatch):
    """A crashed batch must not be silent on WeChat: push a batch_failure card
    (carrying the error), then re-raise so APScheduler records the failure."""
    _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)

    def boom(cfg, task_data):
        raise RuntimeError("东财不可用")

    monkeypatch.setattr("web.scheduled_batch.run_batch_task", boom)
    task_data = {
        "job_id": "emwl-1",
        "name": "东方自选每日",
        "task_type": "emwl_batch",
        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook",
    }
    import pytest
    with pytest.raises(RuntimeError, match="东财不可用"):
        _execute_scheduled_analysis_dispatch(task_data)

    assert len(pushes) == 1
    assert pushes[0]["ttype"] == "batch_failure"
    err = pushes[0]["summary"]["error"]
    assert "RuntimeError" in err and "东财不可用" in err


def test_deep_branch_failure_pushes_failure_card(monkeypatch):
    """A crashed deep run pushes a deep_failure card instead of staying silent."""
    _stub_save(monkeypatch)
    pushes = _install_push_capture(monkeypatch)

    class BoomGraph:
        def __init__(self, **kwargs):
            pass

        def propagate(self, ticker, date):
            raise TimeoutError("llm 超时")

    monkeypatch.setattr("quantconclave.graph.trading_graph.QuantConclaveGraph",
                        BoomGraph)
    task_data = {
        "job_id": "deep-1",
        "name": "银行深度",
        "task_type": "deep",
        "ticker": "600036",
        "use_current_date": True,
        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook",
    }
    _execute_scheduled_analysis_dispatch(task_data)  # must NOT raise

    assert len(pushes) == 1
    assert pushes[0]["ttype"] == "deep_failure"
    s = pushes[0]["summary"]
    assert s["ticker"] == "600036"
    assert "TimeoutError" in s["error"]


def test_redact_urls_strips_credential_bearing_url():
    """A webhook URL (key in the query string) must never reach the log."""
    from web.scheduler import _redact_urls
    out = _redact_urls(
        "push failed: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET_KEY")
    assert "SECRET_KEY" not in out
    assert "<redacted-url>" in out
