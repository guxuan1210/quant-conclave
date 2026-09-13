"""Bridge between the WeCom 智能机器人 and the advisor agent — both directions.

Bot → advisor: EVERY single-chat user who messages the bot becomes a registered
channel and gets an INDEPENDENT advisor conversation. Each text message is
routed as one advisory turn to the same advisor agent the web UI uses
(``web.history_chat._history_chat_core``) — so each user can ask questions and
drive the scheduled-task tools (list / start / pause / resume / push) via
natural language. The turn runs in that user's own ``微信顾问`` thread
(``_resolve_thread``, keyed ``bot_id|chatid``); the final reply is sent back to
the same chat via ``wecom_bot.reply_markdown``, chunked to the WeCom markdown
size limit. Channels never leak into each other or into the web panel's thread.

Advisor → bot / web↔WeChat sharing: the web advisory panel can BIND to one
registered user (``web_advisor_wecom_binding.json``) and shares in both
directions under independent switches:
- ``web→WeChat``: the existing ``web_advisor_wechat_push`` switch; each
  completed web turn forwards its final reply to the BOUND user's personal chat
  (falling back to the most recent learned user when unbound).
- ``WeChat→web``: the ``wechat_to_web`` flag; when set, the web panel auto-
  follows the bound user's channel thread (the frontend selects that thread_id
  and polls it), so the user's live WeChat Q&A appears in the panel.

Security:
- Commands run only for single-chat users whose per-user ``advisor`` flag is on
  (the ``wecom_advisor_users.json`` registry, seeded once from the optional
  ``wecom_bot_advisor_users`` allowlist so an existing allowlist keeps blocking
  strangers after an upgrade). A chatid the registry has never seen falls back
  to the allowlist semantics (empty list = anyone, the legacy multi-user
  default). Group chats still teach the bot the chat target for scheduled-run
  pushes, but never drive the advisor.
- A ``wecom_bot_advisor_enabled`` config switch kills the whole feature.
- ``web→WeChat`` reply push goes only to registered users who explicitly opted
  in via their per-user ``push_reply`` flag — one recipient at a time (the bound
  user when opted-in, else the most-recently-active opted-in user), never a
  broadcast.
- One turn at a time per process (advisor LLM calls are expensive and the
  per-thread DB writes would interleave).
- Nothing here ever raises into the bot's recv loop — failures become a short
  friendly reply (or are logged).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading

from quantconclave.dataflows.config import get_config

logger = logging.getLogger(__name__)

# Serializes advisor turns so two bot messages never invoke the deep LLM (and
# write chat messages) concurrently.
_TURN_LOCK = threading.Lock()

# Serializes registry reads/writes (atomic tmp+replace makes torn files
# impossible, but two concurrent first-time seeds would still race the JSON).
_REGISTRY_LOCK = threading.Lock()


def _threads_path() -> str:
    """Persisted per-(bot, user) thread map, keyed by ``bot_id|chatid``."""
    cfg = get_config()
    results_dir = (cfg.get("results_dir")
                   or os.path.expanduser("~/.quantconclave/logs"))
    return os.path.join(results_dir, "wecom_bot_threads.json")


def _load_thread_map() -> dict:
    try:
        path = _threads_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("WeCom bot thread map load failed: %s", exc)
    return {}


def _save_thread_map(thread_map: dict) -> None:
    try:
        path = _threads_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(thread_map, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WeCom bot thread map persist failed: %s", exc)


def _resolve_thread(bot_id: str, chatid: str) -> str:
    """The per-user conversation thread a bot turn writes to.

    Every WeCom user has an INDEPENDENT channel: their own ``微信顾问`` thread,
    persisted in ``wecom_bot_threads.json`` keyed by ``bot_id|chatid``. Turns
    never leak into the web panel's thread or another user's thread. The web
    panel can follow a user's channel via the binding + 「微信→网页」 switch — the
    frontend selects this same thread_id, so the two surfaces share one
    conversation only when the user asks them to.
    """
    from quantconclave.default_config import DEFAULT_CONFIG
    from web.results_store import create_chat_thread

    thread_map = _load_thread_map()
    key = f"{bot_id}|{chatid}"
    thread_id = thread_map.get(key)
    if thread_id:
        return thread_id
    # Empty run list → advisory-mode thread (no analysis attached).
    thread_id = create_chat_thread(DEFAULT_CONFIG, [], title=f"微信顾问 {chatid}")
    thread_map[key] = thread_id
    _save_thread_map(thread_map)
    return thread_id


def _owner_chatid() -> str:
    """The single-chat target the bot has learned (the user who messaged it).
    Returns "" when no target is known yet."""
    try:
        from web import wecom_bot
        return wecom_bot._BOT._targets.get("single") or ""
    except Exception:  # noqa: BLE001
        return ""


# ---- web-advisor → WeChat push switch ---------------------------------------


def _push_setting_path() -> str:
    """Persisted web-advisor→WeChat push switch (survives restarts)."""
    cfg = get_config()
    results_dir = (cfg.get("results_dir")
                   or os.path.expanduser("~/.quantconclave/logs"))
    return os.path.join(results_dir, "web_advisor_wechat_push.json")


def get_web_advisor_push_enabled() -> bool:
    """Whether completed web-advisory replies are forwarded to the WeCom bot's
    personal chat. The persisted JSON (written by the UI toggle) wins over the
    config default so the switch survives restarts."""
    try:
        path = _push_setting_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("enabled"), bool):
                return data["enabled"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web-advisor push setting load failed: %s", exc)
    from quantconclave.default_config import DEFAULT_CONFIG
    return bool(DEFAULT_CONFIG.get("web_advisor_wechat_push_enabled", False))


def set_web_advisor_push_enabled(enabled: bool) -> None:
    """Persist the web-advisor→WeChat push switch (atomic temp-file write)."""
    try:
        path = _push_setting_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled)}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web-advisor push setting persist failed: %s", exc)


# ---- web-advisor ↔ WeCom 通道绑定 + 反向开关 --------------------------------


def _binding_path() -> str:
    """Persisted web-advisor↔WeCom channel binding + WeChat→web mirror switch."""
    cfg = get_config()
    results_dir = (cfg.get("results_dir")
                   or os.path.expanduser("~/.quantconclave/logs"))
    return os.path.join(results_dir, "web_advisor_wecom_binding.json")


def get_web_advisor_binding() -> dict:
    """``{"bound_user": str, "wechat_to_web": bool}`` — which registered WeCom
    user the web panel is bound to for cross-surface sharing, and whether the
    panel follows that user's live chat. Survives restarts; ``wechat_to_web``
    defaults to False (sharing is opt-in per direction)."""
    try:
        path = _binding_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "bound_user": str(data.get("bound_user") or ""),
                    "wechat_to_web": bool(data.get("wechat_to_web", False)),
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web-advisor binding load failed: %s", exc)
    return {"bound_user": "", "wechat_to_web": False}


def _save_binding(binding: dict) -> None:
    try:
        path = _binding_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(binding, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web-advisor binding persist failed: %s", exc)


def set_web_advisor_binding(bound_user: str) -> None:
    """Bind the web panel's cross-surface sharing to one WeCom user ("" clears
    the binding → 网页独立 mode). The bound user receives web→WeChat pushes and,
    with ``wechat_to_web`` on, is the channel the web panel follows."""
    binding = get_web_advisor_binding()
    binding["bound_user"] = str(bound_user or "").strip()
    _save_binding(binding)


def set_wechat_advisor_web_enabled(enabled: bool) -> None:
    """Persist the WeChat→web mirror switch: when on, the web panel follows the
    bound user's live chat (the frontend selects the user's channel thread and
    polls it)."""
    binding = get_web_advisor_binding()
    binding["wechat_to_web"] = bool(enabled)
    _save_binding(binding)


# ---- WeCom user registry (wecom_advisor_users.json) + per-user connectivity ---
# One row per WeCom single-chat userid: display ``name``, ``active`` (has the
# user messaged the bot at least once?), and the per-user connectivity matrix:
#   ``advisor``   — may drive the advisor from WeChat (replaces the global
#                   ``wecom_bot_advisor_users`` allowlist once the row exists)
#   ``push_reply``— completed web-advisory replies auto-push to this user's chat
#   ``mirror``    — eligible to be the web panel's bound/mirrored channel
# Rows appear three ways: pre-registered placeholders (admin adds name+userid
# before the user ever messages), auto-activated rows (the learning hook fires
# when a userid sends its first real message), and read-time synthesized rows
# for learned-but-unregistered userids (active, defaults — never persisted).
# Seeding runs off file existence, never a module flag, so tests that swap
# ``results_dir`` re-seed their fresh (file-less) dirs correctly — while an
# existing registry, even one the admin emptied, is never re-seeded.


def _registry_path() -> str:
    """Persisted WeCom user registry (names + active + per-user connectivity)."""
    cfg = get_config()
    results_dir = (cfg.get("results_dir")
                   or os.path.expanduser("~/.quantconclave/logs"))
    return os.path.join(results_dir, "wecom_advisor_users.json")


def _load_users() -> dict:
    """The registry ``users`` dict (userid → entry), ``{}`` on missing/corrupt."""
    try:
        path = _registry_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            users = data.get("users") if isinstance(data, dict) else None
            return users if isinstance(users, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("WeCom user registry load failed: %s", exc)
    return {}


def _save_users(users: dict) -> None:
    """Persist the registry atomically (temp-file + replace)."""
    try:
        path = _registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"users": users}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WeCom user registry persist failed: %s", exc)


def _now_iso() -> str:
    """Local-time ISO timestamp (same shape everywhere it is compared)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _within_throttle(prev: str, now: str) -> bool:
    """True when ``now`` is within ~60s of ``prev`` (both ``_now_iso()`` shape) —
    used to avoid rewriting the registry JSON on every single inbound message."""
    if not prev:
        return False
    try:
        return (datetime.datetime.fromisoformat(now)
                - datetime.datetime.fromisoformat(prev)).total_seconds() < 60.0
    except Exception:  # noqa: BLE001
        return False


def _registry_allowlist() -> list[str]:
    """Cleaned config ``wecom_bot_advisor_users`` (empty = multi-user default)."""
    cfg = get_config()
    return [str(u).strip() for u in cfg.get("wecom_bot_advisor_users", [])
            if str(u).strip()]


def _new_entry(userid: str, active: bool, advisor: bool,
               added_at: str | None = None) -> dict:
    """A fresh registry entry: name defaults to the bare userid, ``push_reply``
    is always opt-in (False), ``mirror`` is on by default so learned users stay
    bindable exactly as they are today."""
    now = added_at or _now_iso()
    return {
        "name": userid,
        "added_at": now,
        "active": bool(active),
        "last_active": now if active else "",
        "advisor": bool(advisor),
        "push_reply": False,
        "mirror": True,
    }


def _ensure_registry_seeded() -> None:
    """One-time migration that runs only when the registry FILE does not exist.

    Keyed off file absence, not row count, so an existing registry is never
    re-seeded — in particular one the admin emptied by deleting every row:
    deleting a user must stick (the row returns only when that user messages
    again and ``on_user_learned`` recreates it). A fresh results_dir has no file
    yet, which is what lets tests (and first runs) seed their dirs cleanly.

    Seeds:
    - a placeholder row (advisor on, inactive until first contact) for every
      config-allowlisted userid, so the allowlist keeps its meaning, and
    - a row for every already-learned userid so an upgrade preserves the legacy
      web→WeChat push recipient — the bound user, or the most-recent single-chat
      owner when nothing is bound — as that user's ``push_reply``. An allowlisted
      user who already messaged the bot is activated now (their placeholder was
      created above), so its ``advisor`` flag governs from the first run.
    """
    if os.path.exists(_registry_path()):
        return
    users: dict = {}
    allow = _registry_allowlist()
    for uid in allow:
        if uid not in users:
            users[uid] = _new_entry(uid, active=False, advisor=True)
    # Legacy web→WeChat recipient = the bound user, else the most-recent
    # single-chat owner — pre-registered on their row so the first push after an
    # upgrade still reaches the person who used to receive it.
    binding = get_web_advisor_binding()
    bound = (binding.get("bound_user") or "").strip()
    owner = ""
    if not bound:
        try:
            from web import wecom_bot
            owner = (wecom_bot._BOT._targets.get("single") or "") if wecom_bot._BOT else ""
        except Exception:  # noqa: BLE001
            owner = ""
    legacy_recv = {bound} if bound else ({owner} if owner else set())
    try:
        from web import wecom_bot
        learned = list(wecom_bot.registered_users() or [])
    except Exception:  # noqa: BLE001
        learned = []
    for uid in learned:
        entry = users.get(uid)
        if entry is None:
            entry = _new_entry(uid, active=True,
                               advisor=(uid in allow) if allow else True)
            users[uid] = entry
        elif not entry.get("active"):
            # Allowlisted user who already messaged the bot: activate the
            # placeholder so the learned user is usable from the first run.
            entry["active"] = True
            if not entry.get("last_active"):
                entry["last_active"] = _now_iso()
        if uid in legacy_recv:
            entry["push_reply"] = True
    if users:
        _save_users(users)


def get_wecom_users() -> list[dict]:
    """Every registered WeCom user, each row carrying ``userid`` + full fields.

    Learned-but-unregistered userids are synthesized on the fly (never written
    on a read) so the list always mirrors what the bot actually knows. Sorted
    by userid.
    """
    _ensure_registry_seeded()
    users = _load_users()
    allow = _registry_allowlist()
    out = [{"userid": uid, **entry} for uid, entry in users.items()]
    seen = {row["userid"] for row in out}
    try:
        from web import wecom_bot
        learned = set(wecom_bot.registered_users() or [])
    except Exception:  # noqa: BLE001
        learned = set()
    for uid in sorted(learned - seen):
        out.append({
            "userid": uid,
            "name": uid,
            "added_at": "",
            "active": True,
            "last_active": "",
            "advisor": (uid in allow) if allow else True,
            "push_reply": False,
            "mirror": True,
        })
    out.sort(key=lambda r: r["userid"].lower())
    return out


def add_wecom_user(userid: str, name: str = "") -> dict:
    """Pre-register a WeCom user (idempotent). A userid the bot has already
    learned becomes ``active`` immediately; otherwise it stays a placeholder
    until the user first messages the bot. Returns the (possibly existing) row.
    """
    userid = str(userid or "").strip()
    if not userid:
        raise ValueError("userid 不能为空")
    with _REGISTRY_LOCK:
        _ensure_registry_seeded()
        users = _load_users()
        try:
            from web import wecom_bot
            learned = set(wecom_bot.registered_users() or [])
        except Exception:  # noqa: BLE001
            learned = set()
        now = _now_iso()
        existing = users.get(userid)
        if existing:
            entry = existing
            if name and name.strip():
                entry["name"] = name.strip()
            if userid in learned:
                entry["active"] = True
                entry["last_active"] = now
        else:
            active = userid in learned
            entry = _new_entry(userid, active=active, advisor=True)
            if name and name.strip():
                entry["name"] = name.strip()
            users[userid] = entry
        _save_users(users)
        return {"userid": userid, **entry}


def update_wecom_user(userid: str, patch: dict) -> dict:
    """Update a registered user's name / connectivity flags. Unknown keys in
    ``patch`` are ignored; an unknown userid raises ``KeyError``."""
    userid = str(userid or "").strip()
    with _REGISTRY_LOCK:
        _ensure_registry_seeded()
        users = _load_users()
        entry = users.get(userid)
        if entry is None:
            raise KeyError(userid)
        for key, value in (patch or {}).items():
            if key == "name":
                name = str(value or "").strip()
                if name:
                    entry["name"] = name
            elif key in ("advisor", "push_reply", "mirror"):
                entry[key] = bool(value)
        _save_users(users)
        return {"userid": userid, **entry}


def remove_wecom_user(userid: str) -> None:
    """Remove a user's registry row. Unknown userid raises ``KeyError``. The bot
    still remembers the userid, so its next message auto-re-activates a row —
    removing a row means "unmanaged", not "blocked" (blocked = ``advisor`` off).
    """
    userid = str(userid or "").strip()
    with _REGISTRY_LOCK:
        _ensure_registry_seeded()
        users = _load_users()
        if userid not in users:
            raise KeyError(userid)
        users.pop(userid, None)
        _save_users(users)


def on_user_learned(bot_id: str, userid: str) -> None:
    """Learning-hook target (registered from the app lifespan): a single-chat
    userid sent a real message — activate its registry row (create it if the
    bot learned a user nobody had registered) and refresh ``last_active``.

    ``advisor`` honours the config allowlist when one is set, so a stranger an
    existing allowlist used to block stays blocked after an upgrade; ``push_reply``
    is never auto-enabled here (it is an explicit opt-in). Registry writes are
    throttled to ~1/min so chat traffic doesn't rewrite JSON per message.
    """
    userid = str(userid or "").strip()
    if not userid:
        return
    allow = _registry_allowlist()
    with _REGISTRY_LOCK:
        _ensure_registry_seeded()
        users = _load_users()
        now = _now_iso()
        entry = users.get(userid)
        if entry is None:
            users[userid] = _new_entry(userid, active=True,
                                       advisor=(userid in allow) if allow else True)
            _save_users(users)
            return
        entry["active"] = True
        if not entry.get("last_active"):
            # Placeholder activated by first contact — persist immediately.
            entry["last_active"] = now
            _save_users(users)
        elif not _within_throttle(entry.get("last_active") or "", now):
            entry["last_active"] = now
            _save_users(users)


def user_can_drive_advisor(chatid: str) -> bool:
    """Registry ``advisor`` flag when the userid is known; otherwise fall back
    to the config allowlist semantics (empty = anyone, the legacy default) so a
    not-yet-seeded registry behaves exactly like today."""
    chatid = str(chatid or "").strip()
    if not chatid:
        return False
    byid = {e["userid"]: e for e in get_wecom_users()}
    entry = byid.get(chatid)
    if entry is not None:
        return bool(entry.get("advisor"))
    allowed = _registry_allowlist()
    return (chatid in allowed) if allowed else True


def _web_reply_recipients() -> list[str]:
    """Which single-chat userid a completed web-advisory reply is pushed to.

    One operator panel ⇒ at most one recipient (never a broadcast): the bound
    user when that user has ``push_reply`` on; when unbound, the single
    most-recently-active user with ``push_reply`` on. With no registry rows at
    all (not yet seeded), falls back to the legacy bound-user / most-recent
    owner so pre-upgrade behavior and its tests are unchanged.
    """
    binding = get_web_advisor_binding()
    bound = (binding.get("bound_user") or "").strip()
    entries = get_wecom_users()
    if not entries:
        return [bound] if bound else ([_owner_chatid()] if _owner_chatid() else [])
    byid = {e["userid"]: e for e in entries}
    if bound:
        entry = byid.get(bound)
        if entry and entry.get("active") and entry.get("push_reply"):
            return [bound]
        return []
    candidates = [e for e in entries
                  if e.get("active") and e.get("push_reply") and e.get("last_active")]
    if not candidates:
        return []
    candidates.sort(key=lambda e: e.get("last_active") or "", reverse=True)
    return [candidates[0]["userid"]]


def push_web_advisor_reply(text: str) -> None:
    """Forward one completed web-advisory reply to its single WeChat recipient
    (see ``_web_reply_recipients``: the opted-in bound user, else the most
    recently active opted-in user — never a broadcast).

    Called on a daemon thread after ``chat-done`` so the SSE stream is never
    delayed. No-op when the switch is off or no recipient is known yet; chunked
    to the WeCom markdown size limit like the bot echo.
    """
    if not text or not text.strip():
        return
    if not get_web_advisor_push_enabled():
        return
    recipients = _web_reply_recipients()
    if not recipients:
        return
    try:
        from web.wecom_push import _chunk_text
        from web import wecom_bot
        for chatid in recipients:
            for chunk in _chunk_text(text):
                wecom_bot.reply_markdown(chunk, chatid, 1)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Web-advisor WeChat push failed: %s", exc)


def _run_advisor_turn(thread_id: str, text: str, config: dict,
                      lang: str) -> str:
    """Run one advisory turn non-streaming; return the final reply text.

    The advisor core never raises (errors become ``chat-error`` events), but a
    defensive guard keeps a broken turn from ever reaching the reply path.
    """
    from web.history_chat import _history_chat_core
    final = ""
    try:
        for name, data in _history_chat_core(thread_id, text, config, lang):
            if name == "chat-done":
                final = str(data.get("full_response", "") or "")
            elif name == "chat-error":
                final = f"⚠️ {data.get('message', '顾问智能体出错了')}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Advisor turn failed")
        final = "⚠️ 顾问智能体暂不可用，请稍后重试。"
    return final


def handle_message(text: str, chatid: str, chat_type: int) -> None:
    """Inbound-message handler for the WeCom bot (runs on a daemon thread).

    Gates on the feature switch and single-chat only — ANY user who has messaged
    the bot is a registered channel and drives the advisor in their own thread
    (the user chose full ability for all registered users). Never raises.
    """
    cfg = get_config()
    if not cfg.get("wecom_bot_advisor_enabled", True):
        return
    # Only single chats drive the advisor; every registered user runs in their
    # own channel. Group chats still learn the push target but never execute.
    if chat_type != 1 or not chatid:
        return
    # Per-user ``advisor`` flag (registry); a chatid the registry has never seen
    # falls back to the config allowlist semantics. Not-enabled users are
    # logged and ignored, never answered.
    if not user_can_drive_advisor(chatid):
        logger.info("WeCom bot advisor: chatid %r not enabled, ignoring", chatid)
        return

    with _TURN_LOCK:
        try:
            from quantconclave.default_config import DEFAULT_CONFIG
            from web.wecom_push import _chunk_text
            from web import wecom_bot

            bot_id = (cfg.get("wecom_bot_id") or "")
            thread_id = _resolve_thread(bot_id, chatid)
            lang = DEFAULT_CONFIG.get("output_language") or "Chinese"
            reply = _run_advisor_turn(thread_id, text, DEFAULT_CONFIG, lang)
            if not reply.strip():
                reply = "（顾问没有返回内容，请再试一次。）"
            for chunk in _chunk_text(reply):
                wecom_bot.reply_markdown(chunk, chatid, chat_type)
        except Exception:  # noqa: BLE001 — never break the bot's recv loop
            logger.exception("WeCom bot advisor bridge failed")
