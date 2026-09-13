"""WeCom 智能机器人 (Enterprise Smart Bot) WebSocket long-connection client.

Delivers scheduled-task result pushes to the user's WeChat via the WeCom
"企业智能机器人" channel (botid + secret) — the companion to the group-bot
webhook path in ``web/wecom_push.py``.

Protocol (official docs "智能机器人长连接"):
  - Connect    ``wss://openws.work.weixin.qq.com``
  - Subscribe  ``{"cmd":"aibot_subscribe","headers":{"req_id":...},
                  "body":{"bot_id":...,"secret":...}}``
  - Heartbeat  ``{"cmd":"ping","headers":{"req_id":...}}`` every ~30 s
  - Incoming   ``aibot_msg_callback`` / ``aibot_event_callback`` → learn the
               target chat: single chat → ``from.userid`` (chat_type=1);
               group chat → ``chatid`` (chat_type=2)
  - Send       ``{"cmd":"aibot_send_msg","headers":{"req_id":...},
                  "body":{"chatid":...,"chat_type":1|2,
                          "msgtype":"markdown","markdown":{"content":...}}}``

A user MUST message the bot at least once before it can push back — the server
captures the chat id from that callback and persists it across restarts (the
target file lives under the results dir). Until a target is learned, sends
return a clear errmsg instead of raising.

Threading model: one daemon thread owns the WebSocket (connect / subscribe /
recv / ping / reconnect with backoff). Sends come from scheduler threads and
correlate responses by req_id through a pending-future map. All failures are
logged and returned in the result dict; they never raise into the run flow.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import uuid
from concurrent.futures import Future

from websocket import WebSocketTimeoutException

from quantconclave.dataflows.config import get_config

logger = logging.getLogger(__name__)

WECOM_BOT_WS_URL = "wss://openws.work.weixin.qq.com"

# Heartbeat cadence recommended by WeCom (30 s) and read-loop timeout.
PING_INTERVAL_S = 30
RECV_TIMEOUT_S = 1.0
CONNECT_TIMEOUT_S = 20
SUBSCRIBE_TIMEOUT_S = 10
SEND_TIMEOUT_S = 15
RECONNECT_BACKOFF_CAP_S = 60
# How long a push waits for the connection/subscribe to come up.
WAIT_CONNECTED_S = 8.0


def _uuid() -> str:
    return uuid.uuid4().hex


# Error replies that mean the WebSocket session itself is no longer valid —
# the connection must be torn down and re-subscribed. These are distinct from
# content/rate-limit rejections, which are retried as-is on the same session.
_SESSION_LOST_MARKERS = (
    "not subscribed", "not_subscribed", "subscribe", "session",
    "unauthorized", "auth fail", "login", "expired", "kicked",
    "logged in elsewhere", "dup", "replaced",
)
# Send failures that are NOT session problems — do not reconnect for these.
_NOT_RECONNECT_MARKERS = ("rate", "frequent", "limit", "too fast",
                          "too long", "length", "content", "size")


def _is_session_lost(errmsg: str) -> bool:
    e = (errmsg or "").lower()
    if any(m in e for m in _NOT_RECONNECT_MARKERS):
        return False
    return any(m in e for m in _SESSION_LOST_MARKERS)


def _scan_targets(node):
    """Recursive fallback: pull the first ``userid`` / ``chatid`` found."""
    single = group = None

    def walk(obj):
        nonlocal single, group
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "userid" and single is None and isinstance(value, str) and value:
                    single = value
                elif key == "chatid" and group is None and isinstance(value, str) and value:
                    group = value
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(node)
    return single, group


def extract_targets(body: dict) -> tuple[str, str]:
    """Return (single_chat_userid, group_chatid) from a callback body.

    Single chat carries no ``chatid`` — the target is the sender's ``userid``.
    Group chat carries ``chatid`` (the sender's userid is irrelevant here).
    Event callbacks without ``chattype`` are scanned recursively as a fallback.
    """
    single = group = ""
    chattype = body.get("chattype")
    if chattype == "group":
        group = body.get("chatid") or ""
    elif chattype == "single":
        single = (body.get("from") or {}).get("userid") or body.get("chatid") or ""
    else:
        single, group = _scan_targets(body)
    return single, group


def _extract_message_text(body: dict) -> str:
    """Return the text payload of an inbound message callback (or "").

    Standard message callbacks carry ``text.content``; event callbacks may wrap
    the same shape under ``msg``/``data``. A recursive scan is the last-resort
    fallback for unknown shapes — the callback payload is vendor-controlled, so
    a missing field must never crash the recv loop.
    """
    if not isinstance(body, dict):
        return ""

    def _content_of(node) -> str:
        if not isinstance(node, dict):
            return ""
        text = node.get("text")
        if isinstance(text, dict):
            content = text.get("content")
            if isinstance(content, str) and content:
                return content
        return ""

    for source in (body, body.get("msg"), body.get("data"),
                   body.get("message")):
        text = _content_of(source)
        if text:
            return text

    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "content" and isinstance(value, str) and value.strip():
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    return found[0] if found else ""


def _callback_chat(body: dict) -> tuple[str, int]:
    """The (chatid, chat_type) to reply to the sender of a callback message.

    Single chat → the sender's userid (chat_type=1); group chat → the chatid
    (chat_type=2). Returns ("", 0) when no target is present.
    """
    single, group = extract_targets(body)
    if group:
        return group, 2
    if single:
        return single, 1
    return "", 0


def _invoke_handler(handler, text: str, chatid: str, chat_type: int) -> None:
    """Call an inbound-message handler on its daemon thread; never raise."""
    try:
        handler(text, chatid, chat_type)
    except Exception:  # noqa: BLE001 — a broken handler must not kill the loop
        logger.exception("WeCom bot message handler failed")


def build_frame(cmd: str, req_id: str, body: dict | None = None) -> dict:
    frame = {"cmd": cmd, "headers": {"req_id": req_id}}
    if body is not None:
        frame["body"] = body
    return frame


class WeComBot:
    """One persistent WebSocket connection to the WeCom smart-bot gateway.

    Module-level ``_BOT`` is the shared instance; ``ensure_started()`` /
    ``push_markdown()`` / ``status()`` / ``stop()`` are the public facade.
    """

    def __init__(self):
        self._lock = threading.Lock()       # guards creds + thread lifecycle
        self._send_lock = threading.Lock()  # serializes outbound sends
        self._pending: dict[str, Future] = {}
        self._pending_lock = threading.Lock()

        self._bot_id = ""
        self._secret = ""
        self._targets: dict[str, str] = {"single": "", "group": ""}
        # Every single-chat userid the bot has learned (they messaged it at
        # least once). Each is an independent advisor channel. ``_targets``'s
        # ``"single"`` stays the MOST RECENT sender for backward-compatible
        # scheduled-run pushes; ``_singles`` is the full registered set.
        self._singles: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ws = None
        self._connected = False
        self._last_error = ""
        # Set when the gateway rejects a frame because our subscription is no
        # longer valid (e.g. a second connection to the same bot_id superseded
        # it, or the session was rotated). The recv loop then tears down and
        # re-subscribes instead of silently staying "connected" but unable to
        # send forever.
        self._session_lost = False
        # Inbound message handler (optional): fn(text, chatid, chat_type),
        # invoked on a daemon thread for every user text message. Set via
        # ``set_message_handler`` — e.g. the bot↔advisor bridge routes these
        # messages into the advisory agent.
        self._message_handler = None
        # User-learned handler (optional): fn(bot_id, userid) invoked inline
        # (guarded) whenever a single-chat userid sends a REAL message — used to
        # auto-activate that user in the advisor bridge's user registry. Set via
        # ``set_user_learned_handler``.
        self._user_learned_handler = None

    # ---- lifecycle ----------------------------------------------------------

    def ensure_started(self, bot_id: str, secret: str) -> dict:
        bot_id = (bot_id or "").strip()
        secret = (secret or "").strip()
        if not bot_id or not secret:
            return {"configured": False, "connected": False,
                    "target": None, "error": ""}
        with self._lock:
            creds_changed = (bot_id != self._bot_id or secret != self._secret)
            self._bot_id = bot_id
            self._secret = secret
            self._load_targets()
            if self._thread and self._thread.is_alive():
                if creds_changed:
                    self._restart_locked()
                return self._status_locked()
            self._start_locked()
            return self._status_locked()

    def stop(self):
        with self._lock:
            self._stop.set()
            ws = self._ws
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            thread = self._thread
            self._thread = None
        if thread:
            thread.join(timeout=5)

    def _start_locked(self):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="wecom-bot")
        self._thread.start()

    def _restart_locked(self):
        """Swap credentials: stop the current connection and respawn."""
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        old = self._thread
        if old and old is not threading.current_thread():
            old.join(timeout=3)
        self._start_locked()

    # ---- connection loop ----------------------------------------------------

    def _run(self):
        backoff = 1
        while not self._stop.is_set():
            self._session_lost = False
            try:
                self._connect_and_read()
                backoff = 1
            except Exception as exc:  # noqa: BLE001 — connection loop must survive
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("WeCom bot connection error: %s", exc)
            finally:
                self._connected = False
                self._ws = None
                self._fail_pending("connection lost")
            if self._stop.is_set():
                break
            logger.info("WeCom bot reconnect in %ss", backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_CAP_S)

    def _connect_and_read(self):
        from websocket import create_connection

        if self._stop.is_set():
            return
        # websocket-client defaults to cert_reqs=CERT_NONE for wss, so the
        # peer certificate must be verified explicitly — otherwise the bot's
        # credentials would be negotiated over a connection with no server
        # authentication (a real botid+secret sent to a MITM).
        ws = create_connection(
            WECOM_BOT_WS_URL, timeout=CONNECT_TIMEOUT_S,
            sslopt={"cert_reqs": ssl.CERT_REQUIRED,
                    "check_hostname": True},
        )
        self._ws = ws
        if not self._subscribe(ws):
            # Bad credentials — do not hammer the gateway; slow retries happen
            # in _run's backoff. The old connection is already dead.
            self._last_error = "subscribe rejected (check bot_id / secret)"
            try:
                ws.close()
            except Exception:
                pass
            return
        self._connected = True
        self._last_error = ""
        logger.info("WeCom bot connected: bot_id=%s", self._bot_id)

        last_ping = time.monotonic()
        while not self._stop.is_set():
            if time.monotonic() - last_ping >= PING_INTERVAL_S:
                self._send_ping(ws)
                last_ping = time.monotonic()
            ws.settimeout(RECV_TIMEOUT_S)
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue
            if not raw:
                break
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("WeCom bot unparseable frame: %.200s", raw)
                continue
            self._handle_frame(frame)
            if self._session_lost:
                # The gateway no longer accepts our session — a plain reconnect
                # with a fresh subscribe restores it. Staying would leave the
                # connection "connected" but unable to send anything.
                logger.info("WeCom bot subscription lost (%s) — reconnecting",
                            self._last_error)
                break
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass

    def _subscribe(self, ws) -> bool:
        req_id = _uuid()
        future = self._register_pending(req_id)
        body = {"bot_id": self._bot_id, "secret": self._secret}
        with self._send_lock:
            ws.send(json.dumps(build_frame("aibot_subscribe", req_id, body)))
        # The subscribe reply must be read INLINE: the recv loop has not started
        # yet, so dispatch frames here until our future resolves (this also
        # handles an early callback that arrives before subscribe completes).
        ws.settimeout(RECV_TIMEOUT_S)
        deadline = time.monotonic() + SUBSCRIBE_TIMEOUT_S
        while not future.done():
            if time.monotonic() >= deadline:
                self._last_error = "subscribe timed out"
                return False
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue
            if not raw:
                return False
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("WeCom bot unparseable frame: %.200s", raw)
                continue
            self._handle_frame(frame)
        res = future.result(timeout=0)
        return bool(res.get("ok"))

    def _send_ping(self, ws):
        try:
            with self._send_lock:
                ws.send(json.dumps(build_frame("ping", _uuid())))
        except Exception as exc:  # noqa: BLE001
            logger.warning("WeCom bot ping failed: %s", exc)

    # ---- frame dispatch -----------------------------------------------------

    def _handle_frame(self, frame: dict):
        req_id = (frame.get("headers") or {}).get("req_id")
        if req_id and req_id in self._pending:
            errcode = frame.get("errcode", 0)
            errmsg = frame.get("errmsg", "")
            fut = self._pending.get(req_id)
            if fut and not fut.done():
                fut.set_result({"ok": errcode == 0, "errcode": errcode,
                                "errmsg": errmsg or ""})
            if errcode != 0 and _is_session_lost(errmsg):
                self._last_error = errmsg or "subscription lost"
                self._session_lost = True
            return
        cmd = frame.get("cmd")
        body = frame.get("body") or {}
        if cmd in ("aibot_msg_callback", "aibot_event_callback"):
            single, group = extract_targets(body)
            changed = False
            if single:
                # Every sender becomes a registered channel; the most recent
                # one is also the backward-compat push target.
                if single not in self._singles:
                    self._singles.add(single)
                    changed = True
                if single != self._targets.get("single"):
                    self._targets["single"] = single
                    changed = True
            if group and group != self._targets.get("group"):
                self._targets["group"] = group
                changed = True
            if changed:
                logger.info("WeCom bot learned target: single=%s group=%s",
                            self._targets["single"], self._targets["group"])
                self._persist()
            # Only real user text messages reach the handler — event callbacks
            # only teach the target chat, they never drive the advisor. A real
            # message also notifies the user-learned handler (registry row for
            # the sending single-chat userid is activated/refreshed).
            if cmd == "aibot_msg_callback":
                if self._targets.get("single"):
                    self._notify_user_learned()
                self._dispatch_message(body)

    def _dispatch_message(self, body: dict):
        """Forward an inbound text message to the registered handler (if any)
        on a daemon thread so the recv loop is never blocked. No text payload
        or no handler → no-op. The handler is channel-agnostic: it receives
        (text, chatid, chat_type) and decides what to do (e.g. run the advisor
        agent) — this class never parses command semantics."""
        text = _extract_message_text(body)
        if not text:
            return
        handler = self._message_handler
        if handler is None:
            return
        chatid, chat_type = _callback_chat(body)
        if not chatid:
            return
        threading.Thread(
            target=_invoke_handler, args=(handler, text, chatid, chat_type),
            daemon=True, name="wecom-bot-msg").start()

    def set_message_handler(self, fn):
        """Register the inbound-message handler: ``fn(text, chatid, chat_type)``
        called on a daemon thread per user text message. Pass None to clear."""
        with self._lock:
            self._message_handler = fn

    def set_user_learned_handler(self, fn):
        """Register the user-learned handler: ``fn(bot_id, userid)`` invoked
        inline (guarded, never blocks the recv loop) whenever a single-chat
        userid sends a real message. Pass None to clear."""
        with self._lock:
            self._user_learned_handler = fn

    def _notify_user_learned(self):
        """Fire the user-learned handler for the most recent single-chat sender.
        Guarded so a broken handler can never break the recv loop."""
        handler = self._user_learned_handler
        if handler is None:
            return
        userid = self._targets.get("single") or ""
        if not userid:
            return
        try:
            handler(self._bot_id, userid)
        except Exception:  # noqa: BLE001
            logger.exception("WeCom bot user-learned handler failed")

    # ---- sending ------------------------------------------------------------

    def send_markdown(self, content: str, chat_type: int = 1) -> dict:
        """Push one markdown message to the preferred learned target.

        Single-chat target (the user's personal chat) wins when available;
        otherwise falls back to a group chatid if one was learned.
        """
        if not self._targets.get("single") and not self._targets.get("group"):
            return {
                "ok": False, "errcode": None,
                "errmsg": "尚未获取到会话：请先在企业微信中给该机器人发一条消息",
            }
        if chat_type == 2 and self._targets.get("group"):
            chatid, ct = self._targets["group"], 2
        else:
            chatid, ct = self._targets["single"], 1
        if not chatid:
            return {"ok": False, "errcode": None,
                    "errmsg": "尚未获取到对应会话目标"}
        if not self._wait_connected(WAIT_CONNECTED_S):
            return {"ok": False, "errcode": None,
                    "errmsg": self._last_error or "机器人连接未就绪"}
        body = {"chatid": chatid, "chat_type": ct, "msgtype": "markdown",
                "markdown": {"content": content}}
        return self._send_frame("aibot_send_msg", body)

    def send_markdown_to(self, content: str, chatid: str,
                         chat_type: int = 1) -> dict:
        """Send one markdown message to a SPECIFIC chat — the chat the user's
        inbound message came from — rather than the preferred learned target.
        This is how the bot replies back into the same conversation."""
        if not chatid:
            return {"ok": False, "errcode": None, "errmsg": "缺少会话 chatid"}
        if not self._wait_connected(WAIT_CONNECTED_S):
            return {"ok": False, "errcode": None,
                    "errmsg": self._last_error or "机器人连接未就绪"}
        body = {"chatid": chatid, "chat_type": chat_type, "msgtype": "markdown",
                "markdown": {"content": content}}
        return self._send_frame("aibot_send_msg", body)

    def _send_frame(self, cmd: str, body: dict) -> dict:
        req_id = _uuid()
        future = self._register_pending(req_id)
        with self._send_lock:
            ws = self._ws
            if ws is None:
                self._unregister_pending(req_id)
                return {"ok": False, "errcode": None, "errmsg": "机器人未连接"}
            try:
                ws.send(json.dumps(build_frame(cmd, req_id, body)))
            except Exception as exc:  # noqa: BLE001
                self._unregister_pending(req_id)
                return {"ok": False, "errcode": None, "errmsg": str(exc)}
        try:
            return future.result(timeout=SEND_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — timeout / no response
            return {"ok": False, "errcode": None,
                    "errmsg": f"{cmd} 响应超时"}

    # ---- target persistence -------------------------------------------------

    def _targets_path(self) -> str:
        try:
            results_dir = get_config().get("results_dir") or ""
        except Exception:  # noqa: BLE001
            results_dir = ""
        if not results_dir:
            results_dir = os.path.expanduser("~/.quantconclave/logs")
        return os.path.join(results_dir, "wecom_bot_targets.json")

    def _load_targets(self):
        self._targets = {"single": "", "group": ""}
        self._singles = set()
        try:
            path = self._targets_path()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                entry = data.get(self._bot_id, {}) if isinstance(data, dict) else {}
                self._targets = {
                    "single": entry.get("single") or "",
                    "group": entry.get("group") or "",
                }
                singles = entry.get("singles")
                if isinstance(singles, list):
                    self._singles = {str(s) for s in singles if s}
                elif self._targets["single"]:
                    # Legacy shape (pre multi-user): only the single owner was
                    # stored — backfill the registered set from it so the new
                    # channel dropdown still shows that user.
                    self._singles = {self._targets["single"]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("WeCom bot target load failed: %s", exc)

    def _persist(self):
        try:
            path = self._targets_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {}
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
            entry = dict(self._targets)
            entry["singles"] = sorted(self._singles)
            data[self._bot_id] = entry
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WeCom bot target persist failed: %s", exc)

    # ---- helpers ------------------------------------------------------------

    def _register_pending(self, req_id: str) -> Future:
        future: Future = Future()
        with self._pending_lock:
            self._pending[req_id] = future
        return future

    def _unregister_pending(self, req_id: str):
        with self._pending_lock:
            self._pending.pop(req_id, None)

    def _fail_pending(self, reason: str):
        with self._pending_lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for req_id, future in pending:
            if not future.done():
                future.set_result(
                    {"ok": False, "errcode": None, "errmsg": reason})

    def _wait_connected(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._connected and self._ws is not None:
                return True
            time.sleep(0.2)
        return False

    def _status_locked(self) -> dict:
        target = ""
        if self._targets.get("single"):
            target = f"single:{self._targets['single']}"
        elif self._targets.get("group"):
            target = f"group:{self._targets['group']}"
        return {
            "configured": bool(self._bot_id and self._secret),
            "connected": bool(self._connected and self._ws is not None),
            "target": target or None,
            "error": self._last_error or "",
            "users": sorted(self._singles),
        }

    def registered_users(self) -> list[str]:
        """All single-chat userids the bot has learned (each messaged it at
        least once) — one independent advisor channel per user."""
        with self._lock:
            return sorted(self._singles)

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()


# Module-level shared instance + facade (used by web.wecom_push / app.py).
_BOT = WeComBot()


def ensure_started(bot_id: str = "", secret: str = "") -> dict:
    """Start/refresh the shared bot connection using explicit creds, else the
    global config fallback (wecom_bot_id / wecom_bot_secret)."""
    cfg = get_config()
    bot_id = bot_id or cfg.get("wecom_bot_id", "")
    secret = secret or cfg.get("wecom_bot_secret", "")
    return _BOT.ensure_started(bot_id, secret)


def push_markdown(content: str, chat_type: int = 1) -> dict:
    return _BOT.send_markdown(content, chat_type=chat_type)


def reply_markdown(content: str, chatid: str, chat_type: int = 1) -> dict:
    """Send a markdown message back to the chat a user messaged from."""
    return _BOT.send_markdown_to(content, chatid, chat_type)


def set_message_handler(fn) -> None:
    """Register the inbound-message handler: ``fn(text, chatid, chat_type)``
    invoked on a daemon thread per user text message (None clears it)."""
    _BOT.set_message_handler(fn)


def set_user_learned_handler(fn) -> None:
    """Register the user-learned handler: ``fn(bot_id, userid)`` invoked inline
    whenever a single-chat userid sends a real message (None clears it). The
    bot↔advisor bridge uses it to auto-activate users in its registry."""
    _BOT.set_user_learned_handler(fn)


def bot_status() -> dict:
    return _BOT.status()


def registered_users() -> list[str]:
    """All learned single-chat userids (independent advisor channels)."""
    return _BOT.registered_users()


def stop_bot() -> None:
    _BOT.stop()
