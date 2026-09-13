"""Tests for the stage-3 (阶段三·顾问综合报告) persistence + runner.

Uses a temp results.db via ``config={"results_dir": str(tmp_path)}`` — no
monkeypatching of the LLM layer: ``run_stage3`` accepts an injectable
``_consume`` so the headless chat generator is never actually invoked.
"""

from __future__ import annotations

import json

import pytest

from web.results_store import init_chat_tables, save_chat_message
from web.stage3_records import (
    init_stage3_store,
    save_stage3_record,
    list_stage3_records,
    get_stage3_record,
)
from web.scheduled_batch import run_stage3
from web.twopass_records import init_twopass_store, save_twopass_record


@pytest.fixture()
def config(tmp_path):
    cfg = {"results_dir": str(tmp_path)}
    init_stage3_store(cfg)
    init_twopass_store(cfg)
    init_chat_tables(cfg)
    return cfg


def _bullish_stock(code="600519.SH", name="贵州茅台", verdict="看多"):
    return {
        "code": code, "name": name, "price": 1500.0, "change_pct": 2.1,
        "prevModel": "qwen3:32b", "prevVerdict": "看多",
        "newModel": "deepseek-v4-pro", "newVerdict": verdict,
        "analysis": "主力净流入，看多。",
    }


def _two_pass_record(config, stocks=None):
    stocks = stocks or [_bullish_stock(), _bullish_stock("000001.SZ", "平安银行")]
    return save_twopass_record(config, "emwl", 2, stocks)


def _fake_consume(report="综合报告正文"):
    def consume(thread_id, question, cfg, lang):
        save_chat_message(cfg, thread_id, "assistant", report)
    return consume


# ── save / list / get round-trip ──

def test_save_list_get_roundtrip(config):
    rid = save_stage3_record(
        config, "emwl", 7, "thread123", "deepseek", "deepseek-v4-pro",
        3, "整体看多，茅台最稳。", [_bullish_stock()],
        title="东方自选·二次分析(2模型)·2只复核·顾问综合报告",
    )
    assert rid > 0

    rows = list_stage3_records(config)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["twopass_record_id"] == 7
    assert rows[0]["stock_count"] == 3
    assert rows[0]["model"] == "deepseek-v4-pro"
    assert rows[0]["created_at"]  # non-empty timestamp

    rec = get_stage3_record(config, rid)
    assert rec["report"] == "整体看多，茅台最稳。"
    assert rec["thread_id"] == "thread123"
    assert isinstance(rec["stocks"], list) and rec["stocks"][0]["code"] == "600519.SH"


def test_list_newest_first(config):
    a = save_stage3_record(config, "idx", 1, "", "", "", 1, "r1", [])
    b = save_stage3_record(config, "idx", 2, "", "", "", 1, "r2", [])
    assert [r["id"] for r in list_stage3_records(config)] == [b, a]


def test_get_missing_returns_none(config):
    assert get_stage3_record(config, 9999) is None
    assert list_stage3_records(config) == []


# ── run_stage3 full path (injected consume) ──

def test_run_stage3_full_path(config):
    tp_id = _two_pass_record(config)
    rid = run_stage3("emwl", tp_id, {"stage3": True}, config, _consume=_fake_consume())
    assert rid is not None

    rec = get_stage3_record(config, rid)
    assert rec["report"] == "综合报告正文"
    assert rec["stock_count"] == 2            # only the 看多 subset
    assert rec["twopass_record_id"] == tp_id
    assert rec["thread_id"]                   # a chat thread was created
    assert "顾问综合报告" in rec["title"]
    assert rec["tab"] == "emwl"


def test_run_stage3_independent_model_override(config):
    tp_id = _two_pass_record(config)
    seen = {}

    def consume(thread_id, question, cfg, lang):
        seen["cfg"] = cfg
        save_chat_message(cfg, thread_id, "assistant", "report")

    rid = run_stage3("emwl", tp_id, {
        "stage3": True,
        "stage3_provider": "deepseek",
        "stage3_model": "deepseek-v4-pro",
    }, config, _consume=consume)
    assert rid is not None
    # The advisory agent resolves its deep model from the passed config copy.
    assert seen["cfg"]["deep_think_provider"] == "deepseek"
    assert seen["cfg"]["deep_think_llm"] == "deepseek-v4-pro"
    # Original config untouched.
    assert "deep_think_llm" not in config


def test_run_stage3_question_references_record(config):
    tp_id = _two_pass_record(config)
    seen = {}

    def consume(thread_id, question, cfg, lang):
        seen["q"] = question
        save_chat_message(cfg, thread_id, "assistant", "report")

    run_stage3("emwl", tp_id, {}, config, _consume=consume)
    q = seen["q"]
    assert f"get_twopass_record_detail({tp_id})" in q
    assert "综合报告" in q
    assert "600519.SH" in q  # the bullish table is embedded


def test_run_stage3_empty_report_returns_none(config):
    tp_id = _two_pass_record(config)
    assert run_stage3("emwl", tp_id, {}, config,
                      _consume=_fake_consume("   ")) is None


# ── run_stage3 edge cases ──

def test_run_stage3_missing_twopass_returns_none(config):
    assert run_stage3("emwl", 9999, {}, config) is None


def test_run_stage3_no_bullish_returns_none(config):
    # Both stocks 观望 → no bullish subset → nothing to report on.
    tp_id = _two_pass_record(config, [
        _bullish_stock(verdict="观望"),
        _bullish_stock("000001.SZ", "平安银行", verdict="看空"),
    ])
    assert run_stage3("emwl", tp_id, {}, config) is None


def test_run_stage3_default_consume_tolerates_empty_generator(monkeypatch, config):
    """The real generator path never raises; with no persisted message → None.

    The default consume imports ``stream_history_chat`` lazily at call time, so
    patching the source module's attribute is enough to stub the generator.
    """
    import web.history_chat as hc
    tp_id = _two_pass_record(config)
    monkeypatch.setattr(hc, "stream_history_chat",
                        lambda *a, **k: (_ for _ in ()))
    assert run_stage3("emwl", tp_id, {}, config) is None
