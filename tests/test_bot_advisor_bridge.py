"""Tests for the WeCom-bot → advisor bridge (web/bot_advisor_bridge.py).

Covers the security gates (feature switch, single-chat-with-a-chatid only —
ANY registered user drives the advisor unless the optional
``wecom_bot_advisor_users`` allowlist is populated), the persistent per-(bot,
user) thread mapping (independent channel per user), the web-advisor↔WeCom
channel binding + reverse mirror switch, and the reply path (advisor's final
text chunked and sent back to the SAME chat — or to the bound user on web→WeChat
pushes). The advisor core (``_history_chat_core``) and the push modules are
stubbed so nothing touches a real LLM, scheduler, or network.
"""

from __future__ import annotations

import json
import os

import web.bot_advisor_bridge as bab


def test_handle_message_ignored_when_disabled(monkeypatch):
    monkeypatch.setattr(
        bab, "get_config", lambda: {"wecom_bot_advisor_enabled": False})
    called = []
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda *a, **k: called.append(a) or "x")
    bab.handle_message("hi", "owner", 1)
    assert called == []


def test_handle_message_gate_ignores_group_and_missing_chatid(monkeypatch):
    """The gate is now single-chat-with-a-chatid only — groups and missing
    targets are ignored, but ANY single-chat user runs the advisor."""
    monkeypatch.setattr(
        bab, "get_config", lambda: {"wecom_bot_advisor_enabled": True})
    called = []
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda *a, **k: called.append(a) or "x")
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: None)
    bab.handle_message("hi", "g1", 2)       # group chat → ignored
    bab.handle_message("hi", "", 1)         # missing chatid → ignored
    assert called == []


def test_handle_message_allowlist_ignores_nonmember(monkeypatch, tmp_path):
    """With ``wecom_bot_advisor_users`` populated, a single-chat user NOT in the
    list is logged and ignored — no advisor turn, no reply (the reply path is
    never reached). The allowlist seeds the registry once, then its advisor flag
    governs (a stranger never appears there and falls back to "not allowed")."""
    monkeypatch.setattr(bab, "get_config", lambda: {
        "wecom_bot_advisor_enabled": True, "wecom_bot_id": "b1",
        "wecom_bot_advisor_users": ["zhangsan"], "results_dir": str(tmp_path)})
    called = []
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda *a, **k: called.append(a) or "x")
    replies = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: replies.append(a) or {})
    bab.handle_message("hi", "stranger", 1)
    assert called == []
    assert replies == []


def test_handle_message_registry_advisor_flag_gates_when_allowlist_empty(monkeypatch, tmp_path):
    """The registry advisor flag supersedes the (empty) config allowlist: a
    user explicitly disabled via the UI is ignored even though the allowlist
    would have allowed everyone."""
    monkeypatch.setattr(bab, "get_config", lambda: {
        "wecom_bot_advisor_enabled": True, "wecom_bot_id": "b1",
        "wecom_bot_advisor_users": [], "results_dir": str(tmp_path)})
    bab.add_wecom_user("zhangsan", "张三")
    bab.update_wecom_user("zhangsan", {"advisor": False})
    called = []
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda *a, **k: called.append(a) or "x")
    replies = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: replies.append(a) or {})
    bab.handle_message("hi", "zhangsan", 1)
    assert called == []
    assert replies == []


def test_handle_message_allowlist_allows_member(monkeypatch, tmp_path):
    """A listed userid drives the advisor and the reply echoes back to them."""
    monkeypatch.setattr(bab, "get_config", lambda: {
        "wecom_bot_advisor_enabled": True, "wecom_bot_id": "b1",
        "wecom_bot_advisor_users": ["zhangsan", "lisi"],
        "results_dir": str(tmp_path)})
    monkeypatch.setattr(bab, "_resolve_thread", lambda bot_id, chatid: f"t:{chatid}")
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda thread_id, text, config, lang: f"已收到: {text}")
    replies = []
    monkeypatch.setattr(
        "web.wecom_bot.reply_markdown",
        lambda content, chatid, chat_type:
            replies.append((content, chatid, chat_type)) or {"ok": True})

    bab.handle_message("你好", "lisi", 1)

    assert len(replies) == 1
    content, chatid, ct = replies[0]
    assert "已收到: 你好" in content
    assert chatid == "lisi"
    assert ct == 1


def test_handle_message_runs_advisor_for_any_single_chat_user(monkeypatch):
    """The owner gate is gone: a stranger single-chat user (a registered
    channel by messaging the bot once) drives the advisor in their own thread
    and the reply echoes back to THEM."""
    monkeypatch.setattr(bab, "get_config", lambda: {
        "wecom_bot_advisor_enabled": True, "wecom_bot_id": "b1"})
    # Routing test only — the registry gate is covered by its own tests.
    monkeypatch.setattr(bab, "user_can_drive_advisor", lambda chatid: True)
    monkeypatch.setattr(bab, "_resolve_thread",
                        lambda bot_id, chatid: f"t:{chatid}")
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda thread_id, text, config, lang:
                            f"已收到: {text}")
    replies = []
    monkeypatch.setattr(
        "web.wecom_bot.reply_markdown",
        lambda content, chatid, chat_type:
            replies.append((content, chatid, chat_type)) or {"ok": True})

    bab.handle_message("你好", "stranger", 1)

    assert len(replies) == 1
    content, chatid, ct = replies[0]
    assert "已收到: 你好" in content
    assert chatid == "stranger"
    assert ct == 1


def test_handle_message_runs_advisor_and_replies_same_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: {
        "wecom_bot_advisor_enabled": True, "wecom_bot_id": "b1",
        "results_dir": str(tmp_path)})
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    monkeypatch.setattr(bab, "_resolve_thread", lambda bot_id, chatid: "t1")
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda thread_id, text, config, lang:
                            f"已收到: {text}")
    replies = []
    monkeypatch.setattr(
        "web.wecom_bot.reply_markdown",
        lambda content, chatid, chat_type:
            replies.append((content, chatid, chat_type)) or {"ok": True})

    bab.handle_message("把最近一次东方自选发我", "owner", 1)

    assert len(replies) == 1
    content, chatid, ct = replies[0]
    assert "已收到: 把最近一次东方自选发我" in content
    assert chatid == "owner"
    assert ct == 1


def test_handle_message_never_raises(monkeypatch):
    monkeypatch.setattr(
        bab, "get_config", lambda: {"wecom_bot_advisor_enabled": True,
                                    "wecom_bot_id": "b1"})
    monkeypatch.setattr(bab, "user_can_drive_advisor", lambda chatid: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    monkeypatch.setattr(
        bab, "_resolve_thread",
        lambda bot_id, chatid: (_ for _ in ()).throw(RuntimeError("db down")))
    replies = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: replies.append(a) or {})
    bab.handle_message("hi", "owner", 1)  # must not raise
    assert replies == []


def test_handle_message_replies_on_advisor_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: {
        "wecom_bot_advisor_enabled": True, "wecom_bot_id": "b1",
        "results_dir": str(tmp_path)})
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    monkeypatch.setattr(bab, "_resolve_thread", lambda bot_id, chatid: "t1")
    monkeypatch.setattr(bab, "_run_advisor_turn",
                        lambda *a, **k: "⚠️ 顾问智能体暂不可用，请稍后重试。")
    replies = []
    monkeypatch.setattr(
        "web.wecom_bot.reply_markdown",
        lambda content, chatid, chat_type:
            replies.append((content, chatid, chat_type)) or {"ok": True})
    bab.handle_message("hi", "owner", 1)
    assert replies and "暂不可用" in replies[0][0]


# ---- thread mapping ---------------------------------------------------------


def test_resolve_thread_isolates_per_user(monkeypatch, tmp_path):
    """Every single-chat user has an INDEPENDENT 微信顾问 thread — the web
    panel's newest-thread sharing is gone; turns never leak between users or
    into the web panel's thread. The web-thread lookup is never consulted."""
    monkeypatch.setattr(bab, "get_config", lambda: {"results_dir": str(tmp_path)})
    web_lookup = []
    monkeypatch.setattr("web.results_store.list_chat_threads",
                        lambda *a, **k: web_lookup.append(a) or [])
    created = []

    def fake_create(config, run_ids, title=""):
        created.append(title)
        return f"thread-{len(created)}"

    monkeypatch.setattr("web.results_store.create_chat_thread", fake_create)

    assert bab._resolve_thread("b1", "zhangsan") == "thread-1"
    assert bab._resolve_thread("b1", "lisi") == "thread-2"
    # Same user → same thread (map hit); no new thread created.
    assert bab._resolve_thread("b1", "zhangsan") == "thread-1"
    assert created == ["微信顾问 zhangsan", "微信顾问 lisi"]

    # Per-user resolution NEVER consults the web panel's thread list.
    assert web_lookup == []

    path = os.path.join(tmp_path, "wecom_bot_threads.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["b1|zhangsan"] == "thread-1"
    assert data["b1|lisi"] == "thread-2"


def test_resolve_thread_creates_when_no_map_entry(monkeypatch, tmp_path):
    """A brand-new user gets a dedicated 微信顾问 thread, persisted so later
    messages stay in it (continuity across restarts)."""
    monkeypatch.setattr(bab, "get_config",
                        lambda: {"results_dir": str(tmp_path)})
    created = []
    monkeypatch.setattr(
        "web.results_store.create_chat_thread",
        lambda config, run_ids, title="": created.append(title) or "thread-1")

    t1 = bab._resolve_thread("b1", "owner")
    t2 = bab._resolve_thread("b1", "owner")
    assert t1 == t2 == "thread-1"
    assert created == ["微信顾问 owner"]  # created once, then reused

    monkeypatch.setattr(
        "web.results_store.create_chat_thread",
        lambda config, run_ids, title="": created.append(title) or "thread-2")
    t3 = bab._resolve_thread("b1", "other")
    assert t3 == "thread-2"

    path = os.path.join(tmp_path, "wecom_bot_threads.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["b1|owner"] == "thread-1"
    assert data["b1|other"] == "thread-2"


def test_resolve_thread_uses_persisted_map_when_db_down(monkeypatch, tmp_path):
    """The persisted per-(bot,user) map is authoritative — a broken chat-thread
    store can't make a bot turn fall back to another user's or the web's
    thread."""
    monkeypatch.setattr(bab, "get_config",
                        lambda: {"results_dir": str(tmp_path)})
    with open(os.path.join(tmp_path, "wecom_bot_threads.json"), "w",
              encoding="utf-8") as f:
        json.dump({"b1|owner": "persisted-t"}, f)

    monkeypatch.setattr("web.results_store.create_chat_thread",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert bab._resolve_thread("b1", "owner") == "persisted-t"


def test_owner_chatid_reads_bot_target(monkeypatch):
    from web import wecom_bot

    class _FakeBOT:
        _targets = {"single": "owner", "group": ""}

    monkeypatch.setattr(wecom_bot, "_BOT", _FakeBOT())
    assert bab._owner_chatid() == "owner"


def test_owner_chatid_empty_when_unknown(monkeypatch):
    from web import wecom_bot

    class _FakeBOT:
        _targets = {"single": "", "group": ""}

    monkeypatch.setattr(wecom_bot, "_BOT", _FakeBOT())
    assert bab._owner_chatid() == ""


# ---- web-advisor → WeChat push switch ---------------------------------------


def _fake_config(tmp_path):
    return {"results_dir": str(tmp_path), "wecom_bot_advisor_enabled": True}


def test_get_web_advisor_push_enabled_defaults_off(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    assert bab.get_web_advisor_push_enabled() is False


def test_get_web_advisor_push_enabled_reads_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    path = os.path.join(tmp_path, "web_advisor_wechat_push.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"enabled": True}, f)
    assert bab.get_web_advisor_push_enabled() is True


def test_set_web_advisor_push_enabled_persists_and_reloads(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    bab.set_web_advisor_push_enabled(True)
    assert bab.get_web_advisor_push_enabled() is True
    bab.set_web_advisor_push_enabled(False)
    assert bab.get_web_advisor_push_enabled() is False
    path = os.path.join(tmp_path, "web_advisor_wechat_push.json")
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"enabled": False}


def test_push_web_advisor_reply_noop_when_switch_off(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: False)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    sent = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: sent.append(a) or {})
    bab.push_web_advisor_reply("你好")
    assert sent == []


def test_push_web_advisor_reply_noop_when_no_target(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "")
    sent = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: sent.append(a) or {})
    bab.push_web_advisor_reply("你好")
    assert sent == []


def test_push_web_advisor_reply_noop_when_empty_text(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    sent = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: sent.append(a) or {})
    bab.push_web_advisor_reply("   ")
    assert sent == []


def test_push_web_advisor_reply_pushes_chunked_to_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    # chunk the reply into two cards — both must land in the owner's chat
    monkeypatch.setattr("web.wecom_push._chunk_text",
                        lambda text: [text + "（一）", text + "（二）"])
    sent = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda content, chatid, chat_type:
                            sent.append((content, chatid, chat_type)) or {"ok": True})
    bab.push_web_advisor_reply("分析完成：看多 3 只。")
    assert sent == [
        ("分析完成：看多 3 只。（一）", "owner", 1),
        ("分析完成：看多 3 只。（二）", "owner", 1),
    ]


def test_push_web_advisor_reply_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ws down")))
    bab.push_web_advisor_reply("你好")  # must not raise


# ---- web-advisor ↔ WeCom channel binding + reverse mirror switch ------------


def test_get_web_advisor_binding_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    assert bab.get_web_advisor_binding() == {
        "bound_user": "", "wechat_to_web": False}


def test_set_web_advisor_binding_persists_and_reloads(monkeypatch, tmp_path):
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    bab.set_web_advisor_binding("zhangsan")
    assert bab.get_web_advisor_binding() == {
        "bound_user": "zhangsan", "wechat_to_web": False}
    bab.set_web_advisor_binding("")
    assert bab.get_web_advisor_binding() == {"bound_user": "", "wechat_to_web": False}
    path = os.path.join(tmp_path, "web_advisor_wecom_binding.json")
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"bound_user": "", "wechat_to_web": False}


def test_set_wechat_advisor_web_enabled_independent_of_binding(monkeypatch, tmp_path):
    """The reverse mirror switch is independent from the bound user — flipping
    one never clears the other."""
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    bab.set_wechat_advisor_web_enabled(True)
    assert bab.get_web_advisor_binding() == {"bound_user": "", "wechat_to_web": True}
    bab.set_web_advisor_binding("lisi")
    bab.set_wechat_advisor_web_enabled(False)
    assert bab.get_web_advisor_binding() == {
        "bound_user": "lisi", "wechat_to_web": False}


def test_push_web_advisor_reply_targets_bound_user(monkeypatch, tmp_path):
    """With a bound user, web→WeChat replies go to THAT user — never to the
    (possibly different) most-recent learned user."""
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    bab.set_web_advisor_binding("zhangsan")
    sent = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda content, chatid, chat_type:
                            sent.append((content, chatid, chat_type)) or {"ok": True})
    bab.push_web_advisor_reply("绑定推送")
    assert sent == [("绑定推送", "zhangsan", 1)]


def test_push_web_advisor_reply_unbound_falls_back_to_owner(monkeypatch, tmp_path):
    """Unbound (网页独立) still pushes to the most-recent learned user — the
    v109 behavior is preserved when no binding is set."""
    monkeypatch.setattr(bab, "get_config", lambda: _fake_config(tmp_path))
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    monkeypatch.setattr(bab, "_owner_chatid", lambda: "owner")
    sent = []
    monkeypatch.setattr("web.wecom_bot.reply_markdown",
                        lambda content, chatid, chat_type:
                            sent.append((content, chatid, chat_type)) or {"ok": True})
    bab.push_web_advisor_reply("默认推送")
    assert sent == [("默认推送", "owner", 1)]


# ---- /api/advisory/wechat_push endpoints ------------------------------------


def test_wechat_push_endpoints_roundtrip(monkeypatch, tmp_path):
    import web.app as app_mod

    monkeypatch.setattr(bab, "get_config", lambda: {"results_dir": str(tmp_path)})

    # GET with no persisted file → default off
    assert app_mod.get_wechat_push_setting() == {"enabled": False}

    # POST turns it on; GET + the underlying store both reflect it
    assert app_mod.set_wechat_push_setting(
        app_mod.WechatPushSetting(enabled=True)) == {"enabled": True}
    assert app_mod.get_wechat_push_setting() == {"enabled": True}
    assert bab.get_web_advisor_push_enabled() is True

    # POST turns it off again
    app_mod.set_wechat_push_setting(app_mod.WechatPushSetting(enabled=False))
    assert app_mod.get_wechat_push_setting() == {"enabled": False}
    assert bab.get_web_advisor_push_enabled() is False


def test_wecom_binding_endpoints_roundtrip(monkeypatch, tmp_path):
    """GET/POST /api/advisory/wecom_binding: mirror-eligible registered users
    (with full rows in ``entries``) + binding persist + thread_id resolution
    ONLY when bound AND the mirror switch is on."""
    import web.app as app_mod
    from web import wecom_bot as wb

    monkeypatch.setattr(bab, "get_config", lambda: {"results_dir": str(tmp_path)})
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG", {"wecom_bot_id": "b1"})

    class _FakeBOT:
        _targets = {"single": "", "group": ""}

        def registered_users(self):
            return ["zhangsan", "lisi"]

    monkeypatch.setattr(wb, "_BOT", _FakeBOT())

    # Defaults: learned users auto-backfilled as active mirror-eligible rows.
    r = app_mod.get_wecom_binding()
    assert r["users"] == ["lisi", "zhangsan"]        # sorted; active && mirror
    assert r["bound_user"] == ""
    assert r["wechat_to_web"] is False
    assert r["thread_id"] is None
    byid = {e["userid"]: e for e in r["entries"]}
    assert set(byid) == {"lisi", "zhangsan"}
    assert byid["zhangsan"]["active"] is True
    assert byid["zhangsan"]["mirror"] is True

    # Bind a user → persisted; thread_id stays None while the mirror is off.
    r = app_mod.set_wecom_binding(
        app_mod.WecomBindingPayload(bound_user="zhangsan"))
    assert r["bound_user"] == "zhangsan"
    assert r["thread_id"] is None
    assert bab.get_web_advisor_binding()["bound_user"] == "zhangsan"

    # Flip the mirror → the bound user's channel thread resolves.
    monkeypatch.setattr(bab, "_resolve_thread",
                        lambda bot_id, chatid: f"t:{chatid}")
    r = app_mod.set_wecom_binding(
        app_mod.WecomBindingPayload(wechat_to_web=True))
    assert r["wechat_to_web"] is True
    assert r["thread_id"] == "t:zhangsan"

    # Clearing the binding → no thread to follow (mirror on but unbound).
    r = app_mod.set_wecom_binding(
        app_mod.WecomBindingPayload(bound_user=""))
    assert r["bound_user"] == ""
    assert r["thread_id"] is None


# ---- per-user connectivity matrix (wecom_advisor_users.json) -----------------


def _fake_learned_bot(monkeypatch, users, single=""):
    from web import wecom_bot as wb

    class _FakeBOT:
        _targets = {"single": single, "group": ""}

        def registered_users(self):
            return list(users)

    monkeypatch.setattr(wb, "_BOT", _FakeBOT())


def test_push_web_advisor_reply_uses_push_reply_matrix(monkeypatch, tmp_path):
    """Web→WeChat reply push follows the per-user matrix: the bound user when
    they opted into push_reply; when unbound, the single most-recently-active
    opted-in user. Never a broadcast."""
    _fake_learned_bot(monkeypatch, ["alice", "bob"])
    monkeypatch.setattr(bab, "get_config",
                        lambda: {"results_dir": str(tmp_path)})
    monkeypatch.setattr(bab, "get_web_advisor_push_enabled", lambda: True)
    bab.get_wecom_users()                       # seed (alice/bob active)
    bab.update_wecom_user("bob", {"push_reply": True})   # bob opts in
    sent = []
    monkeypatch.setattr(
        "web.wecom_bot.reply_markdown",
        lambda content, chatid, chat_type:
            sent.append((content, chatid, chat_type)) or {"ok": True})

    # Unbound → only the opted-in user (bob) receives it — alice never does.
    bab.push_web_advisor_reply("网页结果1")
    assert sent == [("网页结果1", "bob", 1)]

    # Bound to bob (opted-in) → bob; bound to alice (not opted-in) → no push.
    sent.clear()
    bab.set_web_advisor_binding("bob")
    bab.push_web_advisor_reply("网页结果2")
    assert sent == [("网页结果2", "bob", 1)]

    sent.clear()
    bab.set_web_advisor_binding("alice")
    bab.push_web_advisor_reply("网页结果3")
    assert sent == []

    # Alice opts in and messages later → unbound recipient switches to her.
    bab.set_web_advisor_binding("")
    bab.update_wecom_user("alice", {"push_reply": True})
    sent.clear()
    bab.push_web_advisor_reply("网页结果4")
    assert sent and sent[0][1] == "alice"


def test_wecom_users_endpoints_roundtrip(monkeypatch, tmp_path):
    """CRUD /api/advisory/wecom_users: placeholder add (inactive until the user
    messages), connectivity PATCH, delete — with proper 400/404 behaviour."""
    import web.app as app_mod

    _fake_learned_bot(monkeypatch, [])
    monkeypatch.setattr(bab, "get_config", lambda: {"results_dir": str(tmp_path)})

    assert app_mod.list_wecom_users() == {"users": []}

    r = app_mod.create_wecom_user(app_mod.WecomUserAdd(userid="zhangsan", name="张三"))
    assert r["user"]["active"] is False          # placeholder (never messaged)
    assert r["user"]["name"] == "张三"
    assert [u["userid"] for u in r["users"]] == ["zhangsan"]

    # PATCH flips the connectivity flags (whitelisted).
    r = app_mod.patch_wecom_user(app_mod.WecomUserUpdate(
        userid="zhangsan", push_reply=True, mirror=False))
    assert r["user"]["push_reply"] is True
    assert r["user"]["mirror"] is False

    # Unknown userid → 404 on PATCH/DELETE; blank userid → 400 on create.
    for op in (lambda: app_mod.patch_wecom_user(app_mod.WecomUserUpdate(
                   userid="nobody", advisor=True)),
               lambda: app_mod.delete_wecom_user("nobody")):
        try:
            op()
            assert False, "expected HTTPException"
        except app_mod.HTTPException as exc:
            assert exc.status_code == 404

    try:
        app_mod.create_wecom_user(app_mod.WecomUserAdd(userid="  "))
        assert False, "expected HTTPException"
    except app_mod.HTTPException as exc:
        assert exc.status_code == 400

    r = app_mod.delete_wecom_user("zhangsan")
    assert [u["userid"] for u in r["users"]] == []


def test_set_wecom_binding_rejects_ineligible_user(monkeypatch, tmp_path):
    """Only active + mirror-enabled users are bindable to the web panel."""
    import web.app as app_mod

    _fake_learned_bot(monkeypatch, ["zhangsan"])
    monkeypatch.setattr(bab, "get_config", lambda: {"results_dir": str(tmp_path)})

    # Mirror disabled → not bindable.
    bab.update_wecom_user("zhangsan", {"mirror": False})
    try:
        app_mod.set_wecom_binding(app_mod.WecomBindingPayload(bound_user="zhangsan"))
        assert False, "expected HTTPException"
    except app_mod.HTTPException as exc:
        assert exc.status_code == 400

    # Unknown user (never seen by bot or registry) → not bindable.
    try:
        app_mod.set_wecom_binding(app_mod.WecomBindingPayload(bound_user="carol"))
        assert False, "expected HTTPException"
    except app_mod.HTTPException as exc:
        assert exc.status_code == 400

    # Mirror back on → bindable again.
    bab.update_wecom_user("zhangsan", {"mirror": True})
    assert app_mod.set_wecom_binding(
        app_mod.WecomBindingPayload(bound_user="zhangsan"))["bound_user"] == "zhangsan"


def test_delete_bound_user_clears_binding(monkeypatch, tmp_path):
    """Deleting the currently-bound user also clears the web binding so it never
    dangles at a removed userid."""
    import web.app as app_mod

    _fake_learned_bot(monkeypatch, ["zhangsan"])
    monkeypatch.setattr(bab, "get_config", lambda: {"results_dir": str(tmp_path)})

    bab.set_web_advisor_binding("zhangsan")
    assert bab.get_web_advisor_binding()["bound_user"] == "zhangsan"
    app_mod.delete_wecom_user("zhangsan")
    assert bab.get_web_advisor_binding()["bound_user"] == ""
