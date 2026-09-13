"""Endpoint-layer tests for the scheduled-task edit flow.

Calls the FastAPI handlers directly with a SchedulerManager injected on a temp
store and a patched ``_log_task_audit`` capturing audit writes — no network,
no TestClient, no writes to the real ~/.quantconclave audit DB.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import web.app as app_mod
from web.scheduler import SchedulerManager, init_scheduler_run_store


@pytest.fixture()
def sched(tmp_path, monkeypatch):
    cfg = {"results_dir": str(tmp_path)}
    init_scheduler_run_store(cfg)
    m = SchedulerManager(cfg)
    m.start()
    monkeypatch.setattr(app_mod, "_scheduler_manager", m)
    audit = []
    monkeypatch.setattr(
        app_mod, "_log_task_audit",
        lambda action, job_id, name, ttype, detail=None: audit.append(
            {"action": action, "job_id": job_id, "name": name,
             "ttype": ttype, "detail": detail}))
    try:
        yield m, audit
    finally:
        m.shutdown()
        monkeypatch.setattr(app_mod, "_scheduler_manager", None)


def test_config_endpoints_roundtrip(sched):
    m, audit = sched

    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="指数选股", task_type="idx_batch", cron_expression="54 14 * * *",
        indexes=["highdiv"]))
    job_id = created["job_id"]

    # GET config returns the stored full task_data (the edit form's prefill source).
    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["name"] == "指数选股"
    assert cfg["task_type"] == "idx_batch"
    assert cfg["indexes"] == ["highdiv"]

    # PUT config replaces name/schedule/type on the SAME job id.
    app_mod.update_scheduled_task_config(job_id, app_mod.ScheduledTaskCreate(
        name="改名了", task_type="deep", cron_expression="30 15 * * 1-5",
        ticker="600036", analysts=["market"], language="Chinese"))
    task = m.get_task(job_id)
    assert task["job_id"] == job_id
    assert task["name"] == "改名了"
    assert task["task_type"] == "deep"
    assert "1-5" in task["trigger"]          # schedule actually changed

    # Audit recorded both create and update, with the new config as detail.
    assert [a["action"] for a in audit] == ["create", "update"]
    assert audit[1]["job_id"] == job_id
    assert audit[1]["name"] == "改名了"
    assert audit[1]["ttype"] == "deep"
    assert audit[1]["detail"]["ticker"] == "600036"

    # Missing job → 404 on both endpoints.
    with pytest.raises(HTTPException) as ex:
        app_mod.get_scheduled_task_config("nope")
    assert ex.value.status_code == 404
    with pytest.raises(HTTPException) as ex:
        app_mod.update_scheduled_task_config(
            "nope", app_mod.ScheduledTaskCreate(name="x"))
    assert ex.value.status_code == 404


def test_audit_and_config_redact_credentials(sched):
    """create with a per-task bot_secret + a keyed webhook_url: the GET /config
    response and the audit-log detail both mask the secret and redact the
    webhook key value — while the STORED task_data (read server-side by the
    push path) keeps the real values."""
    m, audit = sched
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="推送任务", task_type="deep", cron_expression="30 9 * * *",
        ticker="600036", analysts=["market"],
        bot_id="aibk123", bot_secret="sm456",
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=h1"))
    job_id = created["job_id"]

    # GET /config: masked, never the real secret/key.
    from urllib.parse import parse_qsl, urlsplit
    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["bot_secret"] == app_mod.BOT_SECRET_MASK
    assert cfg["bot_secret"] != "sm456"
    wq = dict(parse_qsl(urlsplit(cfg["webhook_url"]).query))
    assert wq.get("key") and wq["key"] != "h1"      # redacted but present

    # Audit detail: same redaction, so the audit log never holds the secret.
    audited = next(a for a in audit if a["action"] == "create")
    det = audited["detail"]
    assert det["bot_secret"] == app_mod.BOT_SECRET_MASK
    assert "key=h1" not in det.get("webhook_url", "")
    assert det.get("bot_id") == "aibk123"      # non-secret fields survive

    # Stored task_data (server-side, push path) keeps the REAL values.
    stored = m.get_task_config(job_id)
    assert stored["bot_secret"] == "sm456"
    assert "key=h1" in stored["webhook_url"]


def test_idx_batch_legacy_index_key_normalized(sched):
    """Regression: an older "周期股100" task stored index key "period" (the
    canonical key is "cycle") — prefilled checkboxes never matched, so saving
    the edit form was blocked by "请至少选择一个指数". Creating an idx_batch
    task now normalizes legacy keys to canonical before storing."""
    m, _ = sched
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="旧周期任务", task_type="idx_batch", cron_expression="54 14 * * *",
        indexes=["period"]))
    job_id = created["job_id"]
    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["indexes"] == ["cycle"]


def test_idx_batch_unknown_index_rejected(sched):
    """An index key outside the canonical set is a hard 400 at create AND edit
    time — garbage keys used to be stored and silently break the edit form."""
    for indexes in (["bogus"], ["csi500", "nope"]):
        with pytest.raises(HTTPException) as ex:
            app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
                name="坏指数", task_type="idx_batch",
                cron_expression="54 14 * * *", indexes=indexes))
        assert ex.value.status_code == 400


def test_resolve_index_key_legacy_alias():
    """The alias map covers the one known legacy key; canonical and unknown keys
    pass through untouched (unknown ones are rejected by the caller)."""
    assert app_mod._INDEX_ALIASES == {"period": "cycle"}
    assert app_mod._resolve_index_key("period") == "cycle"
    assert app_mod._resolve_index_key("cycle") == "cycle"
    assert app_mod._resolve_index_key("csi500") == "csi500"
    assert app_mod._resolve_index_key("bogus") == "bogus"


def test_create_route_registered_on_create_scheduled_task():
    """Regression: the @app.post("/api/scheduler/tasks") decorator must sit on
    create_scheduled_task (which registers the job + audit), NOT on the
    _build_task_data helper. A misplaced decorator made the HTTP create return
    the task_data payload with 200 while never calling add_task — the task
    silently never existed."""
    routes = []
    for r in app_mod.app.routes:
        methods = getattr(r, "methods", set()) or set()
        if getattr(r, "path", None) == "/api/scheduler/tasks" and "POST" in methods:
            routes.append(r.endpoint.__name__)
    assert routes == ["create_scheduled_task"]


def test_tasks_endpoint_includes_running_progress(sched):
    """GET /api/scheduler/tasks surfaces running/progress while a run is in
    flight and clears them once the marker is removed."""
    m, _ = sched
    from web import run_progress

    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="指数选股", task_type="idx_batch", cron_expression="54 14 * * *",
        indexes=["highdiv"]))
    job_id = created["job_id"]

    idle = app_mod.list_scheduled_tasks()[0]
    assert idle["running"] is False
    assert idle["progress"] is None

    run_progress.start(job_id, phase="analyzing", total=100)
    run_progress.update(job_id, analyzed=37, current="600036")

    tasks = app_mod.list_scheduled_tasks()
    assert tasks[0]["running"] is True
    assert tasks[0]["progress"]["phase"] == "analyzing"
    assert tasks[0]["progress"]["analyzed"] == 37
    assert tasks[0]["progress"]["total"] == 100

    task = app_mod.get_scheduled_task(job_id)
    assert task["running"] is True
    assert task["progress"]["current"] == "600036"

    run_progress.remove(job_id)
    after = app_mod.list_scheduled_tasks()[0]
    assert after["running"] is False
    assert after["progress"] is None


def _point_cfg_at(sched, monkeypatch):
    """Point app_mod.DEFAULT_CONFIG at the same temp DB the fixture's manager
    uses, so run-log writes/reads and report rendering stay hermetic."""
    m, _ = sched
    cfg = m.config
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG", cfg)
    return m, cfg


def test_runs_endpoint_returns_history_and_report_selects_run(sched, monkeypatch):
    """GET /api/scheduler/tasks/{job_id}/runs lists every recorded run; the
    report endpoint's ?run_id= rebuilds a specific run's report so past runs
    stay queryable (not just the latest)."""
    from web.scheduler import get_scheduler_run, save_scheduler_run

    m, cfg = _point_cfg_at(sched, monkeypatch)
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="指数选股", task_type="idx_batch", cron_expression="54 14 * * *",
        indexes=["highdiv"]))
    job_id = created["job_id"]

    assert app_mod.list_scheduled_task_runs(job_id) == []

    # Two runs, each with a self-contained summary (per-stock codes etc.).
    id1 = save_scheduler_run(cfg, job_id, "指数选股",
                             "idx_batch", {"analyzed": 3, "bullish": 2})
    id2 = save_scheduler_run(cfg, job_id, "指数选股",
                             "idx_batch", {"analyzed": 5, "bullish": 4})

    runs = app_mod.list_scheduled_task_runs(job_id)
    assert [r["id"] for r in runs] == [id2, id1]
    assert runs[0]["summary"]["analyzed"] == 5

    # limit is enforced.
    assert len(app_mod.list_scheduled_task_runs(job_id, limit=1)) == 1

    # Latest report (no run_id) reflects the newest run.
    md_latest = app_mod._build_scheduler_report_md(job_id)
    assert "已分析**: 5" in md_latest and "已分析**: 3" not in md_latest

    # ?run_id= selects the older run's report.
    md_old = app_mod._build_scheduler_report_md(job_id, run_id=id1)
    assert "已分析**: 3" in md_old and "已分析**: 5" not in md_old

    # Unknown run → no report (caller 404s).
    assert app_mod._build_scheduler_report_md(job_id, run_id=999999) is None
    assert get_scheduler_run(cfg, 999999) is None

    # IDOR scoping: a run that belongs to ANOTHER job must not leak into this
    # job's report when its row id is passed as ?run_id=.
    other = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="另一个任务", task_type="idx_batch", cron_expression="40 20 * * *",
        indexes=["highdiv"]))["job_id"]
    foreign = save_scheduler_run(cfg, other, "另一个任务", "idx_batch",
                                 {"analyzed": 7, "bullish": 7})
    assert app_mod._build_scheduler_report_md(job_id, run_id=foreign) is None
    assert get_scheduler_run(cfg, foreign, expected_job_id=job_id) is None
    # ...but the run IS reachable under its own job.
    assert get_scheduler_run(cfg, foreign, expected_job_id=other)["id"] == foreign

    # JSON payload honors run_id too.
    json_old = app_mod.get_scheduled_task_report(job_id, format="json", run_id=id1)
    assert '"analyzed": 3' in json_old.body.decode()


def test_deep_run_recorded_in_run_log_report(sched, monkeypatch):
    """A deep run writes a scheduled_run_log row (last_run becomes visible) and
    the report endpoint renders it from the run's summary."""
    from web.scheduler import save_scheduler_run

    m, cfg = _point_cfg_at(sched, monkeypatch)
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="深度任务", task_type="deep", cron_expression="30 9 * * 1-5",
        ticker="600036", analysts=["market"]))
    job_id = created["job_id"]

    assert m.get_task(job_id)["last_run"] is None
    assert app_mod._build_scheduler_report_md(job_id) is None

    save_scheduler_run(cfg, job_id, "深度任务", "deep", {
        "ticker": "600036", "company_name": "招商银行", "date": "2026-08-23",
        "rating": "buy", "signal": "买入", "analysts": "market",
    })

    task = m.get_task(job_id)
    assert task["last_run"] is not None
    assert task["last_run"]["summary"]["rating"] == "buy"

    md = app_mod._build_scheduler_report_md(job_id)
    assert "600036" in md and "招商银行" in md and "buy" in md

    # Runs endpoint lists the deep run too.
    runs = app_mod.list_scheduled_task_runs(job_id)
    assert len(runs) == 1 and runs[0]["task_type"] == "deep"


def test_webhook_url_roundtrip_through_create_config(sched):
    """The per-task WeChat webhook's URL host/path survive create → stored
    task_data → GET config, but its credential ``key`` value is REDACTED in the
    browser response (never returned to the edit form); the STORED task_data
    keeps the real URL for the server-side push path."""
    m, _ = sched
    hook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook"
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="推送任务", task_type="deep", cron_expression="30 9 * * *",
        ticker="600036", analysts=["market"], webhook_url=hook))
    job_id = created["job_id"]

    # GET config (edit-form prefill source): host/path present, key redacted.
    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["webhook_url"].startswith(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=")
    assert "key=hook" not in cfg["webhook_url"]

    # Stored task_data keeps the real key (server-side push path).
    assert m.get_task_config(job_id)["webhook_url"] == hook

    # Edit WITHOUT a webhook resets it to "" — the form always sends the
    # (possibly empty) field, so a cleared box clears the stored value.
    app_mod.update_scheduled_task_config(job_id, app_mod.ScheduledTaskCreate(
        name="推送任务2", task_type="deep", cron_expression="30 10 * * *",
        ticker="600036", analysts=["market"]))
    assert app_mod.get_scheduled_task_config(job_id)["webhook_url"] == ""

    # Batch tasks redact on GET too.
    b = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="批推", task_type="emwl_batch", cron_expression="30 9 * * *",
        workers=[{"provider": "", "model": ""}],
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook2"))
    bcfg = app_mod.get_scheduled_task_config(b["job_id"])
    assert bcfg["webhook_url"].startswith(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=")
    assert "hook2" not in bcfg["webhook_url"]


def test_webhook_test_endpoint_delegates_to_push(monkeypatch):
    """POST /api/scheduler/webhook/test calls push_wecom with the URL and a
    test markdown message, and surfaces errcode/errmsg to the frontend."""
    captured = {}
    monkeypatch.setattr(
        "web.wecom_push.push_wecom",
        lambda url, content: captured.update(url=url, content=content) or
        {"ok": True, "errcode": 0, "errmsg": "ok"})
    res = app_mod.test_scheduler_webhook(app_mod.WebhookTestPayload(
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook",
        name="测试任务"))
    assert res["ok"] is True and res["errcode"] == 0
    assert captured["url"] == (
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook")
    assert "测试任务" in captured["content"]           # name flows into the card
    assert "markdown" in captured["content"] or "微信" in captured["content"]

    # A non-zero errcode reaches the caller unchanged.
    monkeypatch.setattr(
        "web.wecom_push.push_wecom",
        lambda url, content: {"ok": False, "errcode": 93000, "errmsg": "invalid"})
    res = app_mod.test_scheduler_webhook(app_mod.WebhookTestPayload(
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=hook"))
    assert res["ok"] is False and res["errcode"] == 93000
    assert res["errmsg"] == "invalid"


def test_push_user_roundtrip_through_create_config(sched):
    """The per-task 推送用户 (registered WeCom userids) survives create →
    stored task_data → GET config (the edit form's prefill source), and clears
    when the edit form sends none. Stored form is always a list: a legacy
    scalar string is normalized on write, and the multi-select UI sends an
    array."""
    m, _ = sched
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="推送任务", task_type="deep", cron_expression="30 9 * * *",
        ticker="600036", analysts=["market"], push_user="zhangsan"))
    job_id = created["job_id"]

    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["push_user"] == ["zhangsan"]

    # Multi-select UI sends a list → kept verbatim (stripped, blanks dropped).
    app_mod.update_scheduled_task_config(job_id, app_mod.ScheduledTaskCreate(
        name="推送任务2", task_type="deep", cron_expression="30 10 * * *",
        ticker="600036", analysts=["market"], push_user=["alice", " bob"]))
    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["push_user"] == ["alice", "bob"]

    # Edit WITHOUT a push_user resets it to [] (form always sends the field).
    app_mod.update_scheduled_task_config(job_id, app_mod.ScheduledTaskCreate(
        name="推送任务3", task_type="deep", cron_expression="30 10 * * *",
        ticker="600036", analysts=["market"]))
    assert app_mod.get_scheduled_task_config(job_id)["push_user"] == []

    # Batch tasks store it too.
    b = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="批推", task_type="emwl_batch", cron_expression="30 9 * * *",
        workers=[{"provider": "", "model": ""}], push_user=["lisi"]))
    assert app_mod.get_scheduled_task_config(b["job_id"])["push_user"] == ["lisi"]


def test_bot_id_secret_roundtrip_through_create_config(sched):
    """bot_id survives create → stored task_data → GET config, but bot_secret
    NEVER round-trips: the edit-form prefill gets the mask (the real secret
    lives in .env / is read server-side by the push path only). A webhook-only
    save (empty creds) clears the stored per-task secret."""
    m, _ = sched
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="推送任务", task_type="deep", cron_expression="30 9 * * *",
        ticker="600036", analysts=["market"],
        bot_id="aibk123", bot_secret="sm456"))
    job_id = created["job_id"]

    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["bot_id"] == "aibk123"
    assert cfg["bot_secret"] == app_mod.BOT_SECRET_MASK   # masked, never "sm456"
    assert cfg["bot_secret"] != "sm456"

    # Saving the edit form (which always sends both fields) without creds
    # clears the stored values.
    app_mod.update_scheduled_task_config(job_id, app_mod.ScheduledTaskCreate(
        name="推送任务2", task_type="deep", cron_expression="30 10 * * *",
        ticker="600036", analysts=["market"]))
    cfg2 = app_mod.get_scheduled_task_config(job_id)
    assert cfg2["bot_id"] == "" and cfg2["bot_secret"] == ""


def test_masked_bot_secret_never_stored_as_real_secret(sched):
    """A create/update that sends the literal mask (an API caller, or the edit
    form's mask→"" guard bypassed) must store "" — never the placeholder — so
    push falls back to the global .env secret instead of authing with '••••••'."""
    m, _ = sched
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="推送任务", task_type="deep", cron_expression="30 9 * * *",
        ticker="600036", analysts=["market"],
        bot_id="aibk123", bot_secret=app_mod.BOT_SECRET_MASK))
    job_id = created["job_id"]
    cfg = app_mod.get_scheduled_task_config(job_id)
    assert cfg["bot_secret"] == ""   # placeholder collapsed, not stored

    app_mod.update_scheduled_task_config(job_id, app_mod.ScheduledTaskCreate(
        name="推送任务2", task_type="deep", cron_expression="30 10 * * *",
        ticker="600036", analysts=["market"],
        bot_secret=app_mod.BOT_SECRET_MASK))
    assert app_mod.get_scheduled_task_config(job_id)["bot_secret"] == ""


def test_bot_test_endpoint_delegates(monkeypatch):
    """POST /api/scheduler/bot/test ensures the bot starts with the given
    credentials, pushes a test card, and surfaces ok/errmsg to the frontend."""
    from web import wecom_bot as wb
    captured = {}
    monkeypatch.setattr(
        wb, "ensure_started",
        lambda bid, sec: captured.update(started=(bid, sec)) or {})
    monkeypatch.setattr(
        wb, "push_markdown",
        lambda content, chat_type=1: captured.update(content=content) or
        {"ok": True, "errcode": 0, "errmsg": "ok"})
    res = app_mod.test_scheduler_bot(app_mod.BotTestPayload(
        bot_id="aibk123", bot_secret="sm456", name="测试任务"))
    assert res["ok"] is True and res["errcode"] == 0
    assert captured["started"] == ("aibk123", "sm456")
    assert "测试任务" in captured["content"]

    # A failed send (e.g. no learned target yet) reaches the caller verbatim.
    monkeypatch.setattr(
        wb, "push_markdown",
        lambda content, chat_type=1: {"ok": False, "errcode": None,
                                      "errmsg": "尚未获取到会话"})
    res = app_mod.test_scheduler_bot(app_mod.BotTestPayload(
        bot_id="aibk123", bot_secret="sm456"))
    assert res["ok"] is False
    assert "尚未获取到会话" in res["errmsg"]

    # Masked secret (the form auto-fills the mask, never the real secret) falls
    # back to the global .env secret so the test button works out of the box.
    monkeypatch.setattr(
        wb, "push_markdown",
        lambda content, chat_type=1: captured.update(content=content) or
        {"ok": True, "errcode": 0, "errmsg": "ok"})
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG",
                        {"wecom_bot_id": "gid", "wecom_bot_secret": "gsec"})
    captured.clear()
    res = app_mod.test_scheduler_bot(app_mod.BotTestPayload(
        bot_id="", bot_secret=app_mod.BOT_SECRET_MASK))
    assert res["ok"] is True
    assert captured["started"] == ("gid", "gsec")


def test_bot_status_endpoint(monkeypatch):
    """GET /api/scheduler/bot/status mirrors the shared bot's status AND carries
    the global bot_id for auto-fill — but never the real secret (masked + a
    boolean flag only)."""
    from web import wecom_bot as wb
    monkeypatch.setattr(
        wb, "bot_status",
        lambda: {"configured": True, "connected": False,
                 "target": "single:zhangsan", "error": ""})
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG",
                        {"wecom_bot_id": "gid", "wecom_bot_secret": "gsec"})
    st = app_mod.get_scheduler_bot_status()
    assert st["configured"] is True
    assert st["target"] == "single:zhangsan"
    # Global bot_id merged in (auto-fill source); the secret is masked.
    assert st["bot_id"] == "gid"
    assert st["secret_configured"] is True
    assert st["bot_secret"] == app_mod.BOT_SECRET_MASK
    assert st["bot_secret"] != "gsec"

    # Absent creds degrade to "" / False instead of raising.
    monkeypatch.setattr(app_mod, "DEFAULT_CONFIG", {})
    st2 = app_mod.get_scheduler_bot_status()
    assert st2["bot_id"] == ""
    assert st2["secret_configured"] is False
    assert st2["bot_secret"] == ""


def test_push_report_endpoint(sched, monkeypatch):
    """POST /api/scheduler/tasks/{job_id}/push re-pushes the latest run's WeChat
    card via the SAME push_run_result as the scheduled hook (matching channel +
    content), and 404s when a task has no run yet."""
    from web.scheduler import save_scheduler_run

    m, cfg = _point_cfg_at(sched, monkeypatch)
    created = app_mod.create_scheduled_task(app_mod.ScheduledTaskCreate(
        name="推送任务", task_type="emwl_batch", cron_expression="30 9 * * *",
        webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=h"))
    job_id = created["job_id"]

    # No runs yet ⇒ clear 404.
    with pytest.raises(HTTPException) as ex:
        app_mod.push_scheduled_task_report(job_id)
    assert ex.value.status_code == 404

    summary = {"analyzed": 3, "bullish": 2, "pool": 5}
    save_scheduler_run(cfg, job_id, "推送任务", "emwl_batch", summary)

    seen = {}
    monkeypatch.setattr(
        "web.wecom_push.push_run_result",
        lambda task, sum_, ttype: seen.update(task=task, sum=sum_, ttype=ttype)
        or {"channel": "webhook", "ok": True, "errmsg": ""})

    res = app_mod.push_scheduled_task_report(job_id)
    assert res["ok"] is True
    assert seen["ttype"] == "emwl_batch"
    assert seen["sum"]["analyzed"] == 3
    assert seen["task"]["webhook_url"].endswith("key=h")

    # A past run is selectable by ?run_id=.
    id2 = save_scheduler_run(cfg, job_id, "推送任务", "emwl_batch",
                             {"analyzed": 9, "bullish": 8, "pool": 10})
    seen.clear()
    app_mod.push_scheduled_task_report(job_id, run_id=id2)
    assert seen["sum"]["analyzed"] == 9

    # Unknown run_id ⇒ no run resolves ⇒ 404 (nothing to push for that id).
    seen.clear()
    with pytest.raises(HTTPException) as ex:
        app_mod.push_scheduled_task_report(job_id, run_id=999999)
    assert ex.value.status_code == 404
    assert seen == {}

    # Missing task ⇒ 404.
    with pytest.raises(HTTPException) as ex:
        app_mod.push_scheduled_task_report("nope")
    assert ex.value.status_code == 404
