"""Tests for the WeCom 智能机器人 WebSocket client (web/wecom_bot.py).

Covers frame building, target extraction from callbacks, the pending-future
response correlation, the no-target send error, and a full integration against
a local fake WS gateway: subscribe → learn the chat target from a callback →
send a markdown message → persist the target for the next process start.
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import time

import web.wecom_bot as wb


# ---- frame building ---------------------------------------------------------


def test_build_frame_no_body():
    assert wb.build_frame("ping", "r1") == {
        "cmd": "ping", "headers": {"req_id": "r1"}}


def test_build_frame_with_body():
    frame = wb.build_frame("aibot_send_msg", "r2", {"chatid": "c"})
    assert frame["cmd"] == "aibot_send_msg"
    assert frame["headers"]["req_id"] == "r2"
    assert frame["body"] == {"chatid": "c"}


# ---- target extraction ------------------------------------------------------


def test_extract_targets_single_chat():
    assert wb.extract_targets(
        {"chattype": "single", "from": {"userid": "zhangsan"}}) == ("zhangsan", "")


def test_extract_targets_group_chat():
    assert wb.extract_targets(
        {"chattype": "group", "chatid": "chatg1"}) == ("", "chatg1")


def test_extract_targets_event_fallback_scan():
    """Event callbacks carry no chattype — the recursive scan still finds the
    userid / chatid buried in the message payload."""
    assert wb.extract_targets(
        {"event": "hello", "msg": {"from": {"userid": "w"}, "chatid": "cc"}}) == (
            "w", "cc")


# ---- lifecycle without network ---------------------------------------------


def test_ensure_started_empty_creds_is_noop():
    bot = wb.WeComBot()
    res = bot.ensure_started("", "")
    assert res["configured"] is False
    assert res["connected"] is False


def test_send_markdown_without_learned_target():
    """The user MUST message the bot once before it can push back; until then
    sends fail with a clear errmsg and never raise."""
    bot = wb.WeComBot()
    res = bot.send_markdown("你好")
    assert res["ok"] is False
    assert "尚未获取到会话" in res["errmsg"]


def test_handle_frame_resolves_pending_by_req_id():
    bot = wb.WeComBot()
    future = bot._register_pending("req123")
    bot._handle_frame({"headers": {"req_id": "req123"}, "errcode": 0,
                       "errmsg": "ok"})
    assert future.result(timeout=1) == {"ok": True, "errcode": 0, "errmsg": "ok"}


def test_handle_frame_learns_single_target(monkeypatch):
    bot = wb.WeComBot()
    monkeypatch.setattr(bot, "_persist", lambda: None)
    bot._handle_frame({"cmd": "aibot_msg_callback",
                       "body": {"chattype": "single",
                                "from": {"userid": "zhangsan"}}})
    assert bot._targets["single"] == "zhangsan"
    assert bot._singles == {"zhangsan"}


def test_handle_frame_learns_multiple_single_users(monkeypatch):
    """Every single-chat sender becomes a registered channel; the most recent
    one stays the backward-compat push target while the full set is kept."""
    bot = wb.WeComBot()
    monkeypatch.setattr(bot, "_persist", lambda: None)
    for user in ("zhangsan", "lisi"):
        bot._handle_frame({"cmd": "aibot_msg_callback",
                           "body": {"chattype": "single",
                                    "from": {"userid": user}}})
    assert bot._singles == {"zhangsan", "lisi"}
    assert bot._targets["single"] == "lisi"     # most recent sender


def test_registered_users_returns_all_singles(monkeypatch):
    bot = wb.WeComBot()
    monkeypatch.setattr(bot, "_persist", lambda: None)
    for user in ("zhangsan", "lisi"):
        bot._handle_frame({"cmd": "aibot_msg_callback",
                           "body": {"chattype": "single",
                                    "from": {"userid": user}}})
    assert bot.registered_users() == ["lisi", "zhangsan"]   # sorted


def test_handle_frame_learns_group_target(monkeypatch):
    bot = wb.WeComBot()
    monkeypatch.setattr(bot, "_persist", lambda: None)
    bot._handle_frame({"cmd": "aibot_msg_callback",
                       "body": {"chattype": "group", "chatid": "chatg1"}})
    assert bot._targets["group"] == "chatg1"


# ---- user-learned handler (registry auto-activation hook) --------------------


def test_user_learned_handler_fires_on_single_message(monkeypatch):
    """A real single-chat message fires fn(bot_id, userid) — the advisor
    bridge's registry auto-activation hook."""
    bot = wb.WeComBot()
    bot._bot_id = "b1"
    monkeypatch.setattr(bot, "_persist", lambda: None)
    got = []
    bot.set_user_learned_handler(lambda bid, uid: got.append((bid, uid)))
    bot._handle_frame({"cmd": "aibot_msg_callback",
                       "body": {"chattype": "single",
                                "from": {"userid": "zhangsan"}}})
    assert got == [("b1", "zhangsan")]


def test_user_learned_handler_ignores_event_callbacks(monkeypatch):
    """Event callbacks still teach the chat target for scheduled-run pushes but
    never count as "active" — the handler fires only for real messages."""
    bot = wb.WeComBot()
    bot._bot_id = "b1"
    monkeypatch.setattr(bot, "_persist", lambda: None)
    got = []
    bot.set_user_learned_handler(lambda bid, uid: got.append((bid, uid)))
    bot._handle_frame({"cmd": "aibot_event_callback",
                       "body": {"chattype": "single",
                                "from": {"userid": "zhangsan"}}})
    assert bot._targets["single"] == "zhangsan"   # target still learned
    assert got == []                              # but no handler notification


def test_user_learned_handler_cleared_with_none(monkeypatch):
    bot = wb.WeComBot()
    monkeypatch.setattr(bot, "_persist", lambda: None)
    bot.set_user_learned_handler(lambda bid, uid: None)
    bot.set_user_learned_handler(None)
    bot._handle_frame({"cmd": "aibot_msg_callback",
                       "body": {"chattype": "single",
                                "from": {"userid": "zhangsan"}}})
    assert bot._singles == {"zhangsan"}           # frame handled normally


def test_user_learned_handler_never_raises(monkeypatch):
    """A broken handler must never break the recv loop's frame handling."""
    bot = wb.WeComBot()
    bot._bot_id = "b1"
    monkeypatch.setattr(bot, "_persist", lambda: None)
    bot.set_user_learned_handler(
        lambda bid, uid: (_ for _ in ()).throw(RuntimeError("boom")))
    bot._handle_frame({"cmd": "aibot_msg_callback",
                       "body": {"chattype": "single",
                                "from": {"userid": "zhangsan"}}})  # must not raise


# ---- multi-user target persistence ------------------------------------------


def test_load_targets_migrates_legacy_single_shape(monkeypatch, tmp_path):
    """Pre-multi-user targets (only ``single``/``group``) backfill the
    registered set from the single owner, so the channel dropdown still shows
    that user after an upgrade."""
    monkeypatch.setattr(wb, "get_config",
                        lambda: {"results_dir": str(tmp_path)})
    path = os.path.join(tmp_path, "wecom_bot_targets.json")
    os.makedirs(tmp_path, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"testbot": {"single": "zhangsan", "group": "chatg1"}}, f)

    bot = wb.WeComBot()
    bot._bot_id = "testbot"
    bot._load_targets()
    assert bot._targets == {"single": "zhangsan", "group": "chatg1"}
    assert bot._singles == {"zhangsan"}        # backfilled from the legacy single


def test_persist_writes_singles_list(monkeypatch, tmp_path):
    """The new target shape stores the full registered set alongside the
    most-recent single target."""
    monkeypatch.setattr(wb, "get_config",
                        lambda: {"results_dir": str(tmp_path)})
    bot = wb.WeComBot()
    bot._bot_id = "testbot"
    bot._singles = {"zhangsan", "lisi"}
    bot._targets = {"single": "lisi", "group": ""}
    bot._persist()
    path = os.path.join(tmp_path, "wecom_bot_targets.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["testbot"]["single"] == "lisi"
    assert data["testbot"]["singles"] == ["lisi", "zhangsan"]


def test_persist_load_roundtrip_multiple_users(monkeypatch, tmp_path):
    """Persist → fresh instance → load restores the full registered set (the
    channel dropdown shows every user after a restart)."""
    monkeypatch.setattr(wb, "get_config",
                        lambda: {"results_dir": str(tmp_path)})
    bot = wb.WeComBot()
    bot._bot_id = "testbot"
    bot._singles = {"zhangsan", "lisi"}
    bot._targets = {"single": "lisi", "group": ""}
    bot._persist()

    bot2 = wb.WeComBot()
    bot2._bot_id = "testbot"
    bot2._load_targets()
    assert bot2._singles == {"zhangsan", "lisi"}
    assert bot2._targets["single"] == "lisi"


# ---- session-loss self-heal -------------------------------------------------


def test_session_lost_detected_on_not_subscribed():
    """A send reply saying the websocket is not subscribed must flag a reconnect
    — the connection thread tears down and re-subscribes (fixes the zombie
    connection where a second connection to the same bot_id supersedes ours but
    the socket stays TCP-alive, so pushes fail forever until restart)."""
    bot = wb.WeComBot()
    future = bot._register_pending("r1")
    bot._handle_frame({"cmd": "aibot_send_msg", "headers": {"req_id": "r1"},
                       "errcode": 10003,
                       "errmsg": "aibot websocket not subscribed"})
    assert future.result(timeout=1)["ok"] is False
    assert bot._session_lost is True
    assert "not subscribed" in bot._last_error


def test_rate_limit_does_not_force_reconnect():
    """Rate-limit / content rejections are NOT session problems — reconnecting
    for those would just churn the connection."""
    bot = wb.WeComBot()
    future = bot._register_pending("r2")
    bot._handle_frame({"cmd": "aibot_send_msg", "headers": {"req_id": "r2"},
                       "errcode": 45009, "errmsg": "api freq limited"})
    assert future.result(timeout=1)["ok"] is False
    assert bot._session_lost is False


def test_is_session_lost_markers():
    assert wb._is_session_lost("aibot websocket not subscribed") is True
    assert wb._is_session_lost("session expired, please subscribe again") is True
    assert wb._is_session_lost("api freq limited") is False
    assert wb._is_session_lost("content length exceed limit") is False
    assert wb._is_session_lost("") is False


# ---- full pipeline against a local fake WS gateway -------------------------


def test_integration_subscribe_learn_send_persist(tmp_path, monkeypatch):
    from websockets.sync.server import serve

    monkeypatch.setattr(
        wb, "get_config", lambda: {"results_dir": str(tmp_path)})

    received = []

    def handler(conn):
        while True:
            raw = conn.recv()
            if raw is None:
                break
            frame = json.loads(raw)
            received.append(frame)
            req_id = (frame.get("headers") or {}).get("req_id")
            cmd = frame.get("cmd")
            if cmd == "aibot_subscribe":
                conn.send(json.dumps({"headers": {"req_id": req_id},
                                      "errcode": 0, "errmsg": "ok"}))
                # teach the bot a single-chat target right after subscribing
                conn.send(json.dumps({"cmd": "aibot_msg_callback",
                                      "body": {"chattype": "single",
                                               "from": {"userid": "zhangsan"}}}))
            elif cmd == "aibot_send_msg":
                conn.send(json.dumps({"headers": {"req_id": req_id},
                                      "errcode": 0, "errmsg": "ok"}))

    state: dict = {}
    state["ready"] = threading.Event()

    def _run_server():
        with serve(handler, "127.0.0.1", 0) as server:
            state["server"] = server
            state["ready"].set()
            server.serve_forever()

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    assert state["ready"].wait(5)

    port = state["server"].socket.getsockname()[1]
    monkeypatch.setattr(wb, "WECOM_BOT_WS_URL", f"ws://127.0.0.1:{port}")

    bot = wb.WeComBot()
    monkeypatch.setattr(wb, "_BOT", bot)   # facade push_markdown → this instance
    try:
        bot.ensure_started("testbot", "testsecret")

        deadline = time.time() + 10
        while time.time() < deadline and not bot._targets.get("single"):
            time.sleep(0.05)
        assert bot._targets.get("single") == "zhangsan"

        res = wb.push_markdown("你好，CapitalRadar")
        assert res.get("ok") is True, res

        subs = [f for f in received if f.get("cmd") == "aibot_subscribe"]
        assert len(subs) == 1
        assert subs[0]["body"] == {"bot_id": "testbot", "secret": "testsecret"}

        sends = [f for f in received if f.get("cmd") == "aibot_send_msg"]
        assert len(sends) == 1
        body = sends[0]["body"]
        assert body["chatid"] == "zhangsan"
        assert body["chat_type"] == 1
        assert body["msgtype"] == "markdown"
        assert body["markdown"]["content"] == "你好，CapitalRadar"

        # the learned target survives a process restart (persisted to disk)
        path = os.path.join(tmp_path, "wecom_bot_targets.json")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["testbot"]["single"] == "zhangsan"
        assert data["testbot"]["singles"] == ["zhangsan"]
    finally:
        bot.stop()
        state["server"].shutdown()


# ---- TLS verification -------------------------------------------------------


def test_wss_connection_verifies_tls(monkeypatch):
    """The wss socket must verify the peer certificate — websocket-client
    defaults to cert_reqs=CERT_NONE, which would send the botid+secret to any
    MITM. The fix pins CERT_REQUIRED + check_hostname."""
    import websocket as ws_lib

    captured = {}

    class _FakeWS:
        def __init__(self):
            self.sent = []
            self._acks = 1

        def send(self, raw):
            self.sent.append(raw)

        def recv(self):
            if self._acks > 0:
                self._acks -= 1
                req_id = json.loads(self.sent[-1])["headers"]["req_id"]
                return json.dumps({"headers": {"req_id": req_id},
                                   "errcode": 0, "errmsg": "ok"})
            return None  # end the recv loop

        def settimeout(self, t):
            pass

        def close(self):
            pass

    def fake_create(url, timeout=None, sslopt=None):
        captured["url"] = url
        captured["sslopt"] = sslopt
        return _FakeWS()

    monkeypatch.setattr(ws_lib, "create_connection", fake_create)
    monkeypatch.setattr(wb, "WECOM_BOT_WS_URL", "wss://fake.wecom.example")
    bot = wb.WeComBot()
    bot._bot_id = "testbot"
    bot._secret = "testsecret"
    bot._connect_and_read()
    assert captured["sslopt"] == {"cert_reqs": ssl.CERT_REQUIRED,
                                  "check_hostname": True}


# ---- inbound text extraction / callback chat --------------------------------


def test_extract_message_text_standard():
    assert wb._extract_message_text(
        {"text": {"content": "你好"}}) == "你好"


def test_extract_message_text_wrapped_under_msg_data():
    assert wb._extract_message_text(
        {"msg": {"text": {"content": "hi"}}}) == "hi"
    assert wb._extract_message_text(
        {"data": {"text": {"content": "yo"}}}) == "yo"
    assert wb._extract_message_text(
        {"message": {"text": {"content": "hey"}}}) == "hey"


def test_extract_message_text_recursive_fallback():
    """Event callbacks may not carry text.content at the top level — the
    recursive scan still finds the first content string anywhere."""
    assert wb._extract_message_text(
        {"event": "x", "payload": {"content": "found"}}) == "found"


def test_extract_message_text_empty_shapes():
    assert wb._extract_message_text({}) == ""
    assert wb._extract_message_text({"text": {}}) == ""
    assert wb._extract_message_text({"text": {"content": ""}}) == ""
    assert wb._extract_message_text(None) == ""
    assert wb._extract_message_text("not a dict") == ""


def test_callback_chat_single_group_none():
    assert wb._callback_chat(
        {"chattype": "single", "from": {"userid": "u1"}}) == ("u1", 1)
    assert wb._callback_chat(
        {"chattype": "group", "chatid": "g1"}) == ("g1", 2)
    assert wb._callback_chat({}) == ("", 0)


# ---- message-handler dispatch ------------------------------------------------


def test_dispatch_message_invokes_handler():
    bot = wb.WeComBot()
    got = []
    bot.set_message_handler(
        lambda text, chatid, ct: got.append((text, chatid, ct)))
    bot._dispatch_message({"chattype": "single", "from": {"userid": "u1"},
                           "text": {"content": "hi"}})
    deadline = time.time() + 5
    while time.time() < deadline and not got:
        time.sleep(0.02)
    assert got == [("hi", "u1", 1)]


def test_dispatch_message_no_text_is_noop():
    bot = wb.WeComBot()
    got = []
    bot.set_message_handler(lambda *a: got.append(a))
    bot._dispatch_message({"chattype": "single", "from": {"userid": "u1"}})
    time.sleep(0.1)
    assert got == []


def test_dispatch_message_no_handler_is_noop():
    bot = wb.WeComBot()
    bot._dispatch_message({"chattype": "single", "from": {"userid": "u1"},
                           "text": {"content": "hi"}})  # must not raise


def test_send_markdown_to_requires_chatid():
    bot = wb.WeComBot()
    res = bot.send_markdown_to("你好", "", 1)
    assert res["ok"] is False
    assert "chatid" in res["errmsg"]


def test_set_message_handler_clears_with_none():
    bot = wb.WeComBot()
    bot.set_message_handler(lambda *a: None)
    assert bot._message_handler is not None
    bot.set_message_handler(None)
    assert bot._message_handler is None


# ---- full pipeline: inbound text → handler → reply to the same chat ---------


def test_integration_inbound_text_handler_replies_same_chat(tmp_path, monkeypatch):
    from websockets.sync.server import serve

    monkeypatch.setattr(
        wb, "get_config", lambda: {"results_dir": str(tmp_path)})

    received = []
    handled = []

    def on_message(text, chatid, chat_type):
        handled.append((text, chatid, chat_type))
        wb.reply_markdown("已收到", chatid, chat_type)

    def handler(conn):
        while True:
            raw = conn.recv()
            if raw is None:
                break
            frame = json.loads(raw)
            received.append(frame)
            req_id = (frame.get("headers") or {}).get("req_id")
            cmd = frame.get("cmd")
            if cmd == "aibot_subscribe":
                conn.send(json.dumps({"headers": {"req_id": req_id},
                                      "errcode": 0, "errmsg": "ok"}))
                conn.send(json.dumps({
                    "cmd": "aibot_msg_callback",
                    "body": {"chattype": "single",
                             "from": {"userid": "zhangsan"},
                             "msgtype": "text",
                             "text": {"content": "把最近一次东方自选发我"}},
                }))
            elif cmd == "aibot_send_msg":
                conn.send(json.dumps({"headers": {"req_id": req_id},
                                      "errcode": 0, "errmsg": "ok"}))

    state: dict = {}
    state["ready"] = threading.Event()

    def _run_server():
        with serve(handler, "127.0.0.1", 0) as server:
            state["server"] = server
            state["ready"].set()
            server.serve_forever()

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    assert state["ready"].wait(5)

    port = state["server"].socket.getsockname()[1]
    monkeypatch.setattr(wb, "WECOM_BOT_WS_URL", f"ws://127.0.0.1:{port}")

    bot = wb.WeComBot()
    monkeypatch.setattr(wb, "_BOT", bot)
    wb.set_message_handler(on_message)  # bind the new instance
    try:
        bot.ensure_started("testbot", "testsecret")

        deadline = time.time() + 10
        while time.time() < deadline and not handled:
            time.sleep(0.05)
        assert handled, "message handler never fired"
        assert handled[0][0] == "把最近一次东方自选发我"
        assert handled[0][1] == "zhangsan"
        assert handled[0][2] == 1

        deadline = time.time() + 10
        while time.time() < deadline and not any(
                f.get("cmd") == "aibot_send_msg" for f in received):
            time.sleep(0.05)
        sends = [f for f in received if f.get("cmd") == "aibot_send_msg"]
        assert sends, "handler reply never sent"
        body = sends[-1]["body"]
        assert body["chatid"] == "zhangsan"
        assert body["chat_type"] == 1
        assert body["markdown"]["content"] == "已收到"
    finally:
        bot.stop()
        state["server"].shutdown()
