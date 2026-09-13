"""Unit tests for the WeChat push module (web/wecom_push.py).

Covers message building (deep + batch), the line-boundary byte-chunking that
keeps EVERY stock in a large batch run, the POST plumbing to the WeCom
group-bot webhook, and the "never raises / no-op without webhook" contract.
"""

from __future__ import annotations

import web.wecom_push as wp
from web.wecom_push import (
    WECOM_MAX_BYTES,
    _chunk_text,
    build_batch_message,
    build_deep_message,
    build_failure_message,
    build_run_messages,
    push_run_result,
    push_wecom,
)

_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=testkey"

DEEP_SUMMARY = {
    "ticker": "600036",
    "company_name": "招商银行",
    "date": "2026-08-25",
    "rating": "Buy",
    "signal": "买入",
    "analysts": "market,news",
    "json_path": "600036/QuantConclaveStrategy_logs/full_states_log_2026-08-25.json",
}


def _batch_summary(n=3, twopass_id=None, stage3_id=None):
    verdicts = ["看多", "看空", "观望"]
    return {
        "pool": n,
        "filtered": n,
        "analyzed": n,
        "bullish": 1,
        "bearish": 1,
        "watch": 1,
        "codes": [
            {"code": f"{600000 + i}", "name": f"股票{i}", "change_pct": 2.5 + i,
             "verdict": verdicts[i % 3], "model_name": "qwen3.8:27b"}
            for i in range(n)
        ],
        "twopass_record_id": twopass_id,
        "stage3_record_id": stage3_id,
    }


# ---- message building -------------------------------------------------------


def test_build_deep_message_contains_fields():
    text = build_deep_message({"name": "银行深度"}, DEEP_SUMMARY)
    assert "深度分析完成" in text
    assert "银行深度" in text
    assert "招商银行" in text and "600036" in text
    assert "2026-08-25" in text
    assert "Buy" in text
    assert "买入" in text
    assert "market,news" in text


def test_build_batch_message_counts_and_every_stock():
    text = build_batch_message({"name": "东方自选"}, _batch_summary(3))
    assert "定时选股完成" in text and "东方自选" in text
    assert "股票池**: 3" in text and "入选**: 3" in text
    assert "已分析**: 3" in text
    assert "看多 1" in text and "看空 1" in text and "观望 1" in text
    # Every stock's conclusion appears, one line each.
    for i in range(3):
        assert f"股票{i} ({600000 + i})" in text
        assert ["看多", "看空", "观望"][i % 3] in text


def test_build_batch_message_appends_twopass_and_stage3(monkeypatch):
    monkeypatch.setattr(
        "web.twopass_records.get_twopass_record",
        lambda cfg, rid: {
            "stocks": [
                {"code": "600001", "name": "股票1", "newVerdict": "看多",
                 "newModel": "qwen3.8:27b"},
                {"code": "600002", "name": "股票2", "newVerdict": "看空",
                 "newModel": ""},
            ],
        },
    )
    monkeypatch.setattr(
        "web.stage3_records.get_stage3_record",
        lambda cfg, rid: {"report": "综合来看，当前建议关注银行板块。"},
    )
    text = build_batch_message({"name": "东方自选"}, _batch_summary(
        1, twopass_id=7, stage3_id=9))
    assert "二次分析**: 2 只看多股复核（看多 1 只）" in text
    assert "股票1 (600001) 看多 · qwen3.8:27b" in text
    assert "股票2 (600002) 看空" in text
    assert "综合报告**:\n综合来看，当前建议关注银行板块。" in text


def test_build_batch_message_best_effort_missing_records(monkeypatch):
    """A missing/broken twopass or stage3 record must not crash the message."""
    monkeypatch.setattr(
        "web.twopass_records.get_twopass_record", lambda cfg, rid: None)
    monkeypatch.setattr(
        "web.stage3_records.get_stage3_record",
        lambda cfg, rid: (_ for _ in ()).throw(RuntimeError("db broken")))
    text = build_batch_message({"name": "东方自选"}, _batch_summary(1, 7, 9))
    assert "股票0 (600000) 看多 +2.50%" in text  # base content intact


# ---- chunking ---------------------------------------------------------------


def _bytes_len(s):
    return len(s.encode("utf-8", "replace"))


def test_chunk_splits_long_content_at_line_boundaries():
    # 120 stocks ⇒ comfortably past the 3800-byte cap, must NOT truncate.
    text = build_batch_message({"name": "大选股"}, _batch_summary(120))
    chunks = _chunk_text(text)
    assert len(chunks) >= 2
    for c in chunks:
        assert _bytes_len(c) <= WECOM_MAX_BYTES
    flat = "\n".join(chunks)
    for i in range(120):
        assert f"股票{i} ({600000 + i})" in flat
    assert "股票119 (600119)" in flat  # the very last stock survives


def test_chunk_continuation_titles():
    text = "\n".join("line%d" % i for i in range(500))
    chunks = _chunk_text(text)
    assert len(chunks) >= 2
    assert not chunks[0].startswith("⏸ 续")
    assert all(c.startswith("⏸ 续") for c in chunks[1:])


def test_chunk_pathological_single_long_line():
    """One single line longer than the cap is split in place (not dropped)."""
    long_line = "x" * (WECOM_MAX_BYTES * 3 + 50)
    chunks = _chunk_text(long_line)
    assert len(chunks) >= 4
    joined = "".join(c.replace("⏸ 续", "").replace("\n", "") for c in chunks)
    assert joined == long_line
    for c in chunks:
        assert _bytes_len(c) <= WECOM_MAX_BYTES


def test_build_run_messages_dispatches_on_type():
    deep = build_run_messages({"name": "x"}, DEEP_SUMMARY, "deep")
    assert any("深度分析完成" in c for c in deep)
    batch = build_run_messages({"name": "x"}, _batch_summary(1), "emwl_batch")
    assert any("定时选股完成" in c for c in batch)
    idx = build_run_messages({"name": "x"}, _batch_summary(1), "idx_batch")
    assert any("定时选股完成" in c for c in idx)


# ---- POST plumbing ----------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"errcode": 0, "errmsg": "ok"}
        self.content = bytes(repr(self._payload), "utf-8")

    def json(self):
        return self._payload


def _capture_post(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None, allow_redirects=None):
        calls.append({"url": url, "json": json, "timeout": timeout,
                      "allow_redirects": allow_redirects})
        return _FakeResp()

    monkeypatch.setattr("requests.post", fake_post)
    return calls


def test_push_wecom_posts_markdown(monkeypatch):
    calls = _capture_post(monkeypatch)
    res = push_wecom(_WEBHOOK, "# 你好")
    assert res == {"ok": True, "errcode": 0, "errmsg": "ok"}
    assert len(calls) == 1
    assert calls[0]["url"] == _WEBHOOK
    assert calls[0]["timeout"] == 15
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["json"]["msgtype"] == "markdown"
    assert calls[0]["json"]["markdown"]["content"] == "# 你好"


def test_push_wecom_surfaces_errcode(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, json=None, timeout=None, allow_redirects=None: _FakeResp(
            status_code=200, payload={"errcode": 93000, "errmsg": "invalid webhook"}),
    )
    res = push_wecom(_WEBHOOK, "x")
    assert res["ok"] is False
    assert res["errcode"] == 93000
    assert res["errmsg"] == "invalid webhook"


def test_push_wecom_never_raises(monkeypatch):
    def boom(url, json=None, timeout=None, allow_redirects=None):
        raise ConnectionError("network down")
    monkeypatch.setattr("requests.post", boom)
    res = push_wecom(_WEBHOOK, "x")
    assert res["ok"] is False
    assert res["errcode"] is None


def test_push_wecom_does_not_leak_webhook_key(monkeypatch, caplog):
    """An exception whose message embeds the webhook URL must not echo it into
    the returned errmsg or the log — the key lives in the URL query string."""
    import logging

    def boom(url, json=None, timeout=None, allow_redirects=None):
        raise ConnectionError(
            "HTTPSConnectionPool(host='qyapi.weixin.qq.com', port=443): "
            "Max retries exceeded with url: "
            "/cgi-bin/webhook/send?key=SECRET_KEY_XYZ")

    monkeypatch.setattr("requests.post", boom)
    with caplog.at_level(logging.WARNING, logger="web.wecom_push"):
        res = push_wecom(_WEBHOOK, "x")
    assert res["ok"] is False
    assert res["errmsg"] == "网络错误"
    assert "SECRET_KEY_XYZ" not in res["errmsg"]
    assert "SECRET_KEY_XYZ" not in caplog.text


def test_push_wecom_rejects_non_wecom_url(monkeypatch):
    """SSRF guard: webhook_url is validated against the official WeCom host
    inside push_wecom, so a forged URL (loopback / internal / wrong host) never
    reaches requests.post."""
    calls = _capture_post(monkeypatch)
    for bad in ("http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
                "https://127.0.0.1:9999/steal",
                "https://evil.example.com/cgi-bin/webhook/send?key=x",
                "https://qyapi.weixin.qq.com/other/path"):
        res = push_wecom(bad, "x")
        assert res["ok"] is False and res["errcode"] is None
        assert "仅允许企业微信官方 webhook" in res["errmsg"]
    assert calls == []  # nothing hit the network


# ---- entry point contract ---------------------------------------------------


def test_push_run_result_noop_without_webhook(monkeypatch):
    """No per-task webhook and no global fallback ⇒ zero network activity —
    the key "don't affect existing tasks" guarantee."""
    monkeypatch.setattr(wp, "get_config", lambda: {"wecom_webhook_url": ""})
    calls = _capture_post(monkeypatch)
    push_run_result({"name": "普通任务", "task_type": "deep"},
                    DEEP_SUMMARY, "deep")
    assert calls == []


def test_push_run_result_uses_global_fallback(monkeypatch):
    monkeypatch.setattr(
        wp, "get_config", lambda: {"wecom_webhook_url": _WEBHOOK})
    calls = _capture_post(monkeypatch)
    push_run_result({"name": "普通任务"}, DEEP_SUMMARY, "deep")
    assert calls and calls[0]["url"] == _WEBHOOK


def test_push_run_result_per_task_overrides_global(monkeypatch):
    task_hook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=task"
    monkeypatch.setattr(
        wp, "get_config", lambda: {"wecom_webhook_url": _WEBHOOK})
    calls = _capture_post(monkeypatch)
    push_run_result({"webhook_url": task_hook}, DEEP_SUMMARY, "deep")
    assert calls and calls[0]["url"] == task_hook


def test_push_run_result_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        wp, "get_config", lambda: {"wecom_webhook_url": _WEBHOOK})
    monkeypatch.setattr(
        "requests.post",
        lambda url, json=None, timeout=None, allow_redirects=None:
            (_ for _ in ()).throw(TimeoutError()),
    )
    # push_run_result returns a structured result — never raises.
    res = push_run_result({"name": "x"}, DEEP_SUMMARY, "deep")
    assert res["channel"] == "webhook"
    assert res["ok"] is False


# ---- WeCom 智能机器人 (botid + secret) channel ------------------------------


def _bot_channel(monkeypatch, cfg, pushes=None, starts=None):
    """Stub web.wecom_bot so push_run_result routes through the bot without
    opening any real WebSocket connection."""
    from web import wecom_bot as wb
    pushes = [] if pushes is None else pushes
    starts = [] if starts is None else starts
    monkeypatch.setattr(
        wb, "ensure_started", lambda b, s: starts.append((b, s)) or {})
    monkeypatch.setattr(
        wb, "push_markdown",
        lambda c, chat_type=1: pushes.append(c) or {"ok": True, "errcode": 0})
    monkeypatch.setattr(wp, "get_config", lambda: cfg)
    return wb, pushes, starts


def test_push_run_result_bot_per_task_creds(monkeypatch):
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "", "wecom_bot_secret": ""}
    _, pushes, starts = _bot_channel(monkeypatch, cfg)
    push_run_result({"bot_id": "b1", "bot_secret": "s1"}, DEEP_SUMMARY, "deep")
    assert starts == [("b1", "s1")]
    assert pushes and any("深度分析完成" in c for c in pushes)


def test_push_run_result_bot_global_fallback(monkeypatch):
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"}
    _, pushes, starts = _bot_channel(monkeypatch, cfg)
    push_run_result({"name": "x"}, DEEP_SUMMARY, "deep")
    assert starts == [("g1", "gs")]
    assert pushes  # global creds pushed the message


def test_push_run_result_bot_missing_secret_noop(monkeypatch):
    from web import wecom_bot as wb
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "", "wecom_bot_secret": ""}
    monkeypatch.setattr(
        wb, "ensure_started",
        lambda b, s: (_ for _ in ()).throw(AssertionError("must not start")))
    monkeypatch.setattr(wp, "get_config", lambda: cfg)
    # bot_id without secret ⇒ no delivery channel ⇒ no-op with channel None.
    res = push_run_result({"bot_id": "b1"}, DEEP_SUMMARY, "deep")
    assert res["channel"] is None
    assert res["ok"] is False


def test_push_run_result_bot_webhook_wins_when_both(monkeypatch):
    """When a webhook URL is present it stays the delivery channel — the bot
    credentials are only consulted when no webhook is configured."""
    from web import wecom_bot as wb
    monkeypatch.setattr(
        wb, "ensure_started",
        lambda b, s: (_ for _ in ()).throw(AssertionError("must not start")))
    monkeypatch.setattr(wp, "get_config", lambda: {
        "wecom_webhook_url": "", "wecom_bot_id": "", "wecom_bot_secret": ""})
    calls = _capture_post(monkeypatch)
    push_run_result({"webhook_url": _WEBHOOK, "bot_id": "b1",
                     "bot_secret": "s1"}, DEEP_SUMMARY, "deep")
    assert calls and calls[0]["url"] == _WEBHOOK


def test_push_run_result_bot_failure_only_logs(monkeypatch, caplog):
    from web import wecom_bot as wb
    import logging
    monkeypatch.setattr(wb, "ensure_started", lambda b, s: {})
    monkeypatch.setattr(
        wb, "push_markdown",
        lambda c, chat_type=1: {"ok": False, "errcode": None, "errmsg": "未连接"})
    monkeypatch.setattr(wp, "get_config", lambda: {
        "wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"})
    with caplog.at_level(logging.WARNING, logger="web.wecom_push"):
        res = push_run_result({"name": "x"}, DEEP_SUMMARY, "deep")
    assert res["channel"] == "bot"
    assert res["ok"] is False
    assert res["errmsg"] == "未连接"
    assert "bot push failed" in caplog.text


# ---- push_user: per-task target user (registered WeCom userid) ---------------


def _bot_channel_with_reply(monkeypatch, cfg):
    """Bot-channel stub that also captures reply_markdown (the push_user path)."""
    from web import wecom_bot as wb
    pushes, replies, starts = [], [], []
    monkeypatch.setattr(
        wb, "ensure_started", lambda b, s: starts.append((b, s)) or {})
    monkeypatch.setattr(
        wb, "push_markdown",
        lambda c, chat_type=1: pushes.append(c) or {"ok": True, "errcode": 0})
    monkeypatch.setattr(
        wb, "reply_markdown",
        lambda c, chatid, chat_type=1:
            replies.append((c, chatid, chat_type)) or {"ok": True, "errcode": 0})
    monkeypatch.setattr(wp, "get_config", lambda: cfg)
    return wb, pushes, replies, starts


def test_push_run_result_bot_push_user_targets_reply(monkeypatch):
    """A task configured with push_user delivers to THAT user's chat via
    reply_markdown — never to the global (最近活跃) push target."""
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"}
    _, pushes, replies, starts = _bot_channel_with_reply(monkeypatch, cfg)
    res = push_run_result({"name": "x", "push_user": "lisi"},
                          DEEP_SUMMARY, "deep")
    assert res["channel"] == "bot" and res["ok"] is True
    assert starts == [("g1", "gs")]
    assert replies and any("深度分析完成" in c for c, _, _ in replies)
    assert replies[0][1] == "lisi"        # chatid = the specified user
    assert replies[0][2] == 1             # single-chat
    assert pushes == []                   # 最近活跃 path never used


def test_push_run_result_bot_no_push_user_uses_push_markdown(monkeypatch):
    """No push_user ⇒ backward-compatible behavior: push to the most-recent
    learned single target."""
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"}
    _, pushes, replies, starts = _bot_channel_with_reply(monkeypatch, cfg)
    res = push_run_result({"name": "x"}, DEEP_SUMMARY, "deep")
    assert res["channel"] == "bot" and res["ok"] is True
    assert pushes and any("深度分析完成" in c for c in pushes)
    assert replies == []


def test_push_run_result_bot_blank_push_user_falls_back(monkeypatch):
    """A whitespace-only push_user is treated as unset → 最近活跃 push."""
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"}
    _, pushes, replies, _ = _bot_channel_with_reply(monkeypatch, cfg)
    push_run_result({"name": "x", "push_user": "   "}, DEEP_SUMMARY, "deep")
    assert pushes and replies == []


def test_push_run_result_bot_multi_push_user_fans_out_every_chunk(monkeypatch):
    """push_user as a list delivers EVERY message chunk to EVERY target user —
    the per-task multi-select contract (one user is never dropped because the
    message happened to span cards)."""
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"}
    _, pushes, replies, starts = _bot_channel_with_reply(monkeypatch, cfg)
    # 120-stock batch → multi-chunk content (≥2 cards).
    res = push_run_result({"name": "大选股", "push_user": ["zhangsan", "lisi"]},
                          _batch_summary(120), "emwl_batch")
    assert res["channel"] == "bot" and res["ok"] is True
    assert starts == [("g1", "gs")]
    assert pushes == []                      # 最近活跃 path never used
    by_user = {}
    for c, chatid, ctype in replies:
        by_user.setdefault(chatid, []).append(c)
    assert set(by_user) == {"zhangsan", "lisi"}
    assert all(ct == 1 for _, _, ct in replies)   # each is a single-chat push
    for uid, chunks in by_user.items():
        assert len(chunks) == len(replies) // 2   # every chunk × every user
        assert "股票119 (600119)" in "\n".join(chunks)  # last stock intact both


def test_push_run_result_bot_multi_list_strips_and_drops_blanks(monkeypatch):
    """List entries are trimmed and empties dropped (mirrors _build_task_data),
    so a UI checkbox set can't create a bogus empty target."""
    cfg = {"wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"}
    _, pushes, replies, _ = _bot_channel_with_reply(monkeypatch, cfg)
    push_run_result({"name": "x", "push_user": [" zhangsan ", "", "  ", "lisi"]},
                    DEEP_SUMMARY, "deep")
    got = [r[1] for r in replies]
    assert got == ["zhangsan", "lisi"]


def test_push_run_result_bot_multi_user_failure_aggregates(monkeypatch):
    """A failure pushing to one user must not drop the other users nor later
    chunks: every (user, chunk) pair is attempted, ok=False carries the first
    errmsg."""
    from web import wecom_bot as wb
    monkeypatch.setattr(wb, "ensure_started", lambda b, s: {})
    calls = []
    def flaky(c, chatid, chat_type=1):
        calls.append((c, chatid))
        if chatid == "zhangsan":
            return {"ok": False, "errcode": None, "errmsg": "连接断开"}
        return {"ok": True, "errcode": 0}
    monkeypatch.setattr(wb, "reply_markdown", flaky)
    monkeypatch.setattr(wp, "get_config", lambda: {
        "wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"})
    # 120-stock batch → multi-chunk, multi-user (2 users × ≥2 chunks each).
    res = push_run_result({"name": "大选股", "push_user": ["zhangsan", "lisi"]},
                          _batch_summary(120), "emwl_batch")
    assert res["channel"] == "bot"
    assert res["ok"] is False
    assert res["errmsg"] == "连接断开"
    zhang = [c for c, uid in calls if uid == "zhangsan"]
    lisi = [c for c, uid in calls if uid == "lisi"]
    assert len(zhang) >= 2 and len(lisi) >= 2   # every chunk tried for both
    assert "股票119 (600119)" in "\n".join(lisi)  # lisi got the full set anyway


# ---- failure cards + bot multi-chunk continuation ----------------------------


def test_build_failure_message_deep():
    text = build_failure_message({"name": "银行深度"}, {
        "ticker": "600036", "company_name": "招商银行", "date": "2026-08-27",
        "error": "ValueError: boom"}, "deep_failure")
    assert "深度分析失败" in text and "银行深度" in text
    assert "招商银行 (600036)" in text
    assert "2026-08-27" in text
    assert "ValueError: boom" in text


def test_build_failure_message_batch_caps_error():
    text = build_failure_message({"name": "选股"}, {"error": "x" * 3000},
                                 "batch_failure")
    assert "定时选股失败" in text and "选股" in text
    assert len(text) < 2000  # the error is capped, not echoed in full


def test_build_run_messages_dispatches_failure_types():
    deep_f = build_run_messages({"name": "x"}, {"error": "boom"}, "deep_failure")
    assert any("深度分析失败" in c for c in deep_f)
    batch_f = build_run_messages({"name": "x"}, {"error": "boom"}, "batch_failure")
    assert any("定时选股失败" in c for c in batch_f)


def test_push_run_result_bot_continues_after_failed_chunk(monkeypatch):
    """A failed chunk must NOT drop the remaining chunks — every card is
    attempted (webhook semantics), ok=False with the first errmsg kept."""
    from web import wecom_bot as wb
    calls = []
    monkeypatch.setattr(wb, "ensure_started", lambda b, s: {})
    def flaky(c, chat_type=1):
        calls.append(c)
        if len(calls) == 2:
            return {"ok": False, "errcode": None, "errmsg": "连接断开"}
        return {"ok": True, "errcode": 0}
    monkeypatch.setattr(wb, "push_markdown", flaky)
    monkeypatch.setattr(wp, "get_config", lambda: {
        "wecom_webhook_url": "", "wecom_bot_id": "g1", "wecom_bot_secret": "gs"})
    # 120-stock batch → multi-chunk message (≥2 cards).
    res = push_run_result({"name": "大选股"}, _batch_summary(120), "emwl_batch")
    assert res["channel"] == "bot"
    assert res["ok"] is False
    assert res["errmsg"] == "连接断开"
    assert len(calls) >= 2        # chunk 3+ still attempted after chunk 2 failed
    flat = "\n".join(calls)
    assert "股票119 (600119)" in flat  # the LAST stock still arrived
