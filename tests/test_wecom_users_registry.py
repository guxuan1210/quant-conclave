"""Tests for the WeCom user registry + per-user connectivity matrix
(web/bot_advisor_bridge.py's wecom_advisor_users.json section).

Covers the row lifecycle: pre-register placeholder → auto-activation when the
user first messages the bot (``on_user_learned``), the CRUD API on the registry,
the one-time migration that preserves legacy bound-user/owner recipients as
``push_reply`` and allowlist members as ``advisor``, read-time synthesis of
learned-but-unregistered userids (never persisted), and the ~60s write throttle
so chat traffic can't rewrite the JSON per message.

Each test swaps results_dir to a fresh tmp dir so no test ever touches the real
~/.quantconclave/logs registry, and stubs ``web.wecom_bot._BOT`` with a fake that
exposes only ``registered_users()`` / ``_targets`` (what the bridge reads).
"""

from __future__ import annotations

import pytest

import web.bot_advisor_bridge as bab


class _FakeBot:
    """Stand-in for web.wecom_bot._BOT exposing the two surfaces the bridge
    reads: the learned single-chat userids and the current push target."""

    def __init__(self, known=None, single=""):
        self._known = list(known or [])
        self._targets = {"single": single, "group": ""}

    def registered_users(self):
        return list(self._known)


def _cfg(tmp_path, **extra):
    return {"results_dir": str(tmp_path), **extra}


def _stub_bot(monkeypatch, known=None, single=""):
    bot = _FakeBot(known=known, single=single)
    monkeypatch.setattr("web.wecom_bot._BOT", bot)
    return bot


# ---- CRUD --------------------------------------------------------------------


def test_add_wecom_user_placeholder_until_bot_learns(monkeypatch, tmp_path):
    """A userid the bot has never seen is pre-registered inactive; once the bot
    reports it learned, re-adding activates the row (idempotent re-add)."""
    bot = _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))

    row = bab.add_wecom_user("zhangsan", "张三")
    assert row["userid"] == "zhangsan"
    assert row["name"] == "张三"
    assert row["active"] is False          # placeholder (never messaged)
    assert row["advisor"] is True
    assert row["push_reply"] is False      # explicit opt-in, never auto-on
    assert row["mirror"] is True

    bot._known.append("zhangsan")          # user sends its first message
    row = bab.add_wecom_user("zhangsan")
    assert row["active"] is True           # learned → immediately active
    assert row["last_active"]              # stamped now
    assert row["name"] == "张三"           # re-add without name keeps it


def test_add_wecom_user_does_not_reset_flags(monkeypatch, tmp_path):
    """Re-adding an existing user (UI double-submit) only refreshes
    name/active/last_active — connectivity flags the admin set are preserved."""
    _stub_bot(monkeypatch, known=["zhangsan"])
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("zhangsan")
    bab.update_wecom_user("zhangsan", {"advisor": False, "push_reply": True})
    bab.add_wecom_user("zhangsan", "张三")
    row = [r for r in bab.get_wecom_users() if r["userid"] == "zhangsan"][0]
    assert row["advisor"] is False
    assert row["push_reply"] is True
    assert row["name"] == "张三"


def test_add_wecom_user_rejects_blank(monkeypatch, tmp_path):
    _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    with pytest.raises(ValueError):
        bab.add_wecom_user("   ")


def test_update_wecom_user_whitelists_keys_and_raises_unknown(monkeypatch,
                                                             tmp_path):
    """PATCH touches only name/advisor/push_reply/mirror; unknown keys ignored;
    an unknown userid raises KeyError (→ the API layer's 404)."""
    _stub_bot(monkeypatch, known=["zhangsan"])
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("zhangsan")

    row = bab.update_wecom_user(
        "zhangsan", {"name": " 张三 ", "push_reply": True, "mirror": False,
                     "added_at": "EVIL", "last_active": "EVIL"})
    assert row["name"] == "张三"           # trimmed
    assert row["push_reply"] is True
    assert row["mirror"] is False
    assert row["added_at"] != "EVIL"       # unknown keys never written

    with pytest.raises(KeyError):
        bab.update_wecom_user("nobody", {"advisor": True})


def test_remove_wecom_user_learned_resets_to_defaults(monkeypatch, tmp_path):
    """Removing a learned user's row clears it from disk — deleting the LAST row
    empties the file WITHOUT re-seeding it. The userid still surfaces as a
    read-time DEFAULT row while the bot knows it (custom name/flags gone), and a
    second remove raises KeyError (no disk row left)."""
    bot = _stub_bot(monkeypatch, known=["zhangsan"])
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("zhangsan", "张三")
    bab.update_wecom_user("zhangsan", {"push_reply": True, "mirror": False})

    bab.remove_wecom_user("zhangsan")
    assert bab._load_users() == {}                # emptied file, NOT re-seeded

    # Still learned by the bot → synthesized default row (not the deleted one).
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["zhangsan"]["name"] == "zhangsan"
    assert rows["zhangsan"]["push_reply"] is False
    assert rows["zhangsan"]["mirror"] is True
    assert bab._load_users() == {}                # read-only synth never persists

    with pytest.raises(KeyError):
        bab.remove_wecom_user("zhangsan")         # no disk row → unknown now


# ---- synthesis (learned-but-unregistered) ------------------------------------


def test_get_wecom_users_synthesizes_learned_user_without_persisting(
        monkeypatch, tmp_path):
    """A userid the bot learned after the registry was seeded (learning hook
    hasn't fired yet) appears in the list with active defaults — but is NEVER
    written to disk on a read-only call."""
    bot = _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("existing")          # seed the file (placeholder)

    bot._known[:] = ["existing", "wangwu"]  # bot learns someone new
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["wangwu"]["active"] is True
    assert rows["wangwu"]["advisor"] is True
    assert rows["wangwu"]["push_reply"] is False
    assert rows["wangwu"]["mirror"] is True

    # The read did not persist the synthesized row.
    assert set(bab._load_users()) == {"existing"}


def test_get_wecom_users_sorted_by_userid(monkeypatch, tmp_path):
    _stub_bot(monkeypatch, known=["bob", "alice"])
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    assert [r["userid"] for r in bab.get_wecom_users()] == ["alice", "bob"]


# ---- migration (one-time seed) ----------------------------------------------


def test_migration_seeds_allowlist_placeholders(monkeypatch, tmp_path):
    """Empty registry + a config allowlist: every allowlisted userid gets a
    placeholder row (advisor on, inactive until they message). Learned users get
    advisor = allowlist membership."""
    bot = _stub_bot(monkeypatch, known=["lisi"])
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(
        tmp_path, wecom_bot_advisor_users=["zhangsan", "lisi"]))
    rows = {r["userid"]: r for r in bab.get_wecom_users()}

    assert rows["zhangsan"]["active"] is False    # placeholder, not met yet
    assert rows["zhangsan"]["advisor"] is True    # allowlist keeps its meaning
    assert rows["lisi"]["active"] is True         # met the bot → active
    assert rows["lisi"]["advisor"] is True
    assert rows["lisi"]["push_reply"] is False    # nobody was the recipient
    assert bab._load_users()                       # migration wrote the file


def test_migration_unbound_owner_becomes_push_reply(monkeypatch, tmp_path):
    """No binding → the legacy most-recent single-chat owner keeps receiving
    web→WeChat pushes after the upgrade (push_reply=True on their row)."""
    _stub_bot(monkeypatch, known=["lisi"], single="lisi")
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["lisi"]["push_reply"] is True
    assert rows["lisi"]["active"] is True


def test_migration_bound_user_becomes_push_reply(monkeypatch, tmp_path):
    """A bound user (the legacy web→WeChat recipient) keeps receiving pushes;
    the owner who is NOT bound does not get the flag."""
    _stub_bot(monkeypatch, known=["lisi", "wangwu"], single="wangwu")
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.set_web_advisor_binding("lisi")
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["lisi"]["push_reply"] is True     # bound recipient preserved
    assert rows["wangwu"]["push_reply"] is False  # owner w/o binding not forced


def test_migration_is_one_time(monkeypatch, tmp_path):
    """Seeding keys off the on-disk registry, so a user learned AFTER the first
    seed is not silently given a legacy push_reply."""
    bot = _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("existing")          # seed happens here (file written)
    bot._known.append("wangwu")             # learned after seeding
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["wangwu"]["push_reply"] is False   # no re-migration, no legacy


# ---- learning hook -----------------------------------------------------------


def test_on_user_learned_activates_placeholder(monkeypatch, tmp_path):
    """First real message activates a pre-registered placeholder immediately and
    stamps last_active (so it becomes a valid push recipient)."""
    _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("zhangsan", "张三")
    assert [r["active"] for r in bab.get_wecom_users()] == [False]

    bab.on_user_learned("b1", "zhangsan")
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["zhangsan"]["active"] is True
    assert rows["zhangsan"]["last_active"]
    assert rows["zhangsan"]["name"] == "张三"     # display name kept


def test_on_user_learned_creates_row_for_stranger(monkeypatch, tmp_path):
    """A user nobody registered (no placeholder, no allowlist) gets a fresh row
    on first contact — the bot must be able to push back to whoever messages it."""
    _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.on_user_learned("b1", "wangwu")
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["wangwu"]["active"] is True
    assert rows["wangwu"]["advisor"] is True      # empty allowlist → anyone
    assert rows["wangwu"]["push_reply"] is False  # still explicit opt-in


def test_on_user_learned_allowlist_keeps_blocking_stranger(monkeypatch,
                                                          tmp_path):
    """Upgrade safety: with an allowlist set, a stranger the list used to block
    stays blocked after learning — advisor=False on their row."""
    _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(
        tmp_path, wecom_bot_advisor_users=["zhangsan"]))
    bab.on_user_learned("b1", "stranger")
    rows = {r["userid"]: r for r in bab.get_wecom_users()}
    assert rows["stranger"]["active"] is True
    assert rows["stranger"]["advisor"] is False
    assert bab.user_can_drive_advisor("stranger") is False
    # Allowlisted member (seeded placeholder) still allowed via the row's flag.
    assert bab.user_can_drive_advisor("zhangsan") is True
    # A userid the bot/registry never saw → allowlist fallback blocks too.
    assert bab.user_can_drive_advisor("nobody") is False


def test_on_user_learned_write_throttled_60s(monkeypatch, tmp_path):
    """last_active refreshes are throttled to ~1/min: a second message within
    60s updates nothing on disk; after 60s the refresh is persisted. The real
    _save_users still writes so later calls read the row back from disk."""
    _stub_bot(monkeypatch)
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    clock = {"t": "2026-09-04T00:00:00"}
    monkeypatch.setattr(bab, "_now_iso", lambda: clock["t"])
    real_save = bab._save_users
    count = {"n": 0}

    def counting_save(users):
        count["n"] += 1
        real_save(users)

    monkeypatch.setattr(bab, "_save_users", counting_save)

    bab.on_user_learned("b1", "zhangsan")         # create → save #1
    assert count["n"] == 1
    assert bab._load_users()["zhangsan"]["last_active"] == "2026-09-04T00:00:00"

    clock["t"] = "2026-09-04T00:00:30"            # +30s → within throttle
    bab.on_user_learned("b1", "zhangsan")
    assert count["n"] == 1                         # no rewrite

    clock["t"] = "2026-09-04T00:02:00"            # +2min → past the throttle
    bab.on_user_learned("b1", "zhangsan")
    assert count["n"] == 2
    assert bab._load_users()["zhangsan"]["last_active"] == "2026-09-04T00:02:00"


def test_on_user_learned_blank_and_registry_flag(monkeypatch, tmp_path):
    """Blank userids are ignored (never create a nameless row), and a known row's
    advisor flag governs even when the config allowlist is empty."""
    _stub_bot(monkeypatch, known=["zhangsan"])
    monkeypatch.setattr(bab, "get_config", lambda: _cfg(tmp_path))
    bab.add_wecom_user("zhangsan")
    bab.update_wecom_user("zhangsan", {"advisor": False})

    bab.on_user_learned("b1", "   ")               # no-op
    assert [r["userid"] for r in bab.get_wecom_users()] == ["zhangsan"]

    # Registry flag wins over the empty-allowlist "anyone" fallback.
    assert bab.user_can_drive_advisor("zhangsan") is False
    assert bab.user_can_drive_advisor("someone_else") is True
