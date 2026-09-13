"""Push scheduled-task run results to WeChat via a WeCom (企业微信) group-bot webhook.

Every scheduled run (batch emwl_batch/idx_batch + deep) calls ``push_run_result``
right after its run is persisted, so the WeChat message and the dashboard's
right-panel report come from the SAME stored summary — they never diverge.

WeCom group-bot markdown is capped at 4096 bytes per message, so the content is
split into per-line chunks (each stock is one line); every stock still arrives,
just across sequential WeChat cards. A task only pushes when it carries a
webhook_url (per-task field) or the global ``wecom_webhook_url`` config is set —
tasks without one are completely unaffected. Push failures only log; they never
fail the scheduled run itself.
"""

import logging
import urllib.parse

from quantconclave.dataflows.config import get_config

logger = logging.getLogger(__name__)

# WeCom markdown caps a single message at 4096 bytes; leave a safety margin.
WECOM_MAX_BYTES = 3800

# Label prefix for chunk 2..n so each continuation card stands alone.
_CONTINUE_TITLE = "⏸ 续"

# The ONLY webhook host the push path accepts — webhook_url is user-supplied,
# so it is validated here (both the test endpoint and the scheduled-run hook go
# through push_wecom) to keep it from being used as an SSRF primitive against
# internal hosts.
WECOM_WEBHOOK_HOST = "qyapi.weixin.qq.com"
WECOM_WEBHOOK_PATH = "/cgi-bin/webhook/send"


def _valid_webhook_url(url: str) -> bool:
    """https-only, exact official WeCom host + path, key carried in the query."""
    try:
        u = urllib.parse.urlparse(url)
    except (ValueError, TypeError):
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and host == WECOM_WEBHOOK_HOST
            and u.path == WECOM_WEBHOOK_PATH)


def push_run_result(task_data: dict, summary: dict, ttype: str) -> dict:
    """Entry point called after a scheduled run completes (also the manual
    "推送微信" report-row button).

    Delivery channel resolution (per-task field first, then the global config
    fallback): a webhook URL → group-bot POST; else bot_id+bot_secret → the
    WeCom 智能机器人 long-connection push; neither → no-op. Builds the message
    chunks once and sends each; failures only log, never raise.

    Returns ``{"channel": "webhook"|"bot"|None, "ok": bool, "errmsg": str}`` —
    the scheduled-run hooks ignore it, the manual-push endpoint surfaces it.
    """
    cfg = get_config()
    messages = build_run_messages(task_data, summary, ttype, cfg)

    url = task_data.get("webhook_url") or cfg.get("wecom_webhook_url", "")
    if url:
        ok = True
        errmsg = ""
        for content in messages:
            res = push_wecom(url, content)
            if not res.get("ok"):
                ok = False
                if not errmsg:
                    errmsg = res.get("errmsg") or ""
        return {"channel": "webhook", "ok": ok, "errmsg": errmsg}

    bot_id = task_data.get("bot_id") or cfg.get("wecom_bot_id", "")
    bot_secret = task_data.get("bot_secret") or cfg.get("wecom_bot_secret", "")
    if bot_id and bot_secret:
        from web import wecom_bot
        wecom_bot.ensure_started(bot_id, bot_secret)
        # Optional per-task target users (``push_user``) — registered WeCom
        # userids, one card per user when multiple. Legacy tasks stored a scalar
        # string; normalize it to a list. Empty → backward-compatible behavior
        # (push to the most-recent learned single target).
        targets = task_data.get("push_user")
        if isinstance(targets, str):
            targets = [targets.strip()] if targets.strip() else []
        elif isinstance(targets, list):
            targets = [str(t).strip() for t in targets if str(t).strip()]
        else:
            targets = []
        # Every chunk is attempted even if an earlier one failed (same semantics
        # as the webhook branch): a transient failure on card N must not drop
        # cards N+1.. — the first errmsg is kept for the caller.
        ok = True
        errmsg = ""
        for content in messages:
            if targets:
                for target in targets:
                    res = wecom_bot.reply_markdown(content, target, 1)
                    if not res.get("ok"):
                        ok = False
                        if not errmsg:
                            errmsg = res.get("errmsg") or ""
                        logger.warning("WeCom bot push to %s failed: %s",
                                       target, res.get("errmsg"))
            else:
                res = wecom_bot.push_markdown(content)
                if not res.get("ok"):
                    ok = False
                    if not errmsg:
                        errmsg = res.get("errmsg") or ""
                    logger.warning("WeCom bot push failed: %s", res.get("errmsg"))
        return {"channel": "bot", "ok": ok, "errmsg": errmsg}

    return {"channel": None, "ok": False,
            "errmsg": "未配置微信推送渠道（webhook 或智能机器人）"}


def build_run_messages(task_data: dict, summary: dict, ttype: str,
                       config: dict | None = None) -> list[str]:
    """Return the markdown chunk(s) for a completed run (≤WECOM_MAX_BYTES each)."""
    cfg = config or get_config()
    if ttype == "deep":
        text = build_deep_message(task_data, summary)
    elif ttype in ("deep_failure", "batch_failure"):
        text = build_failure_message(task_data, summary, ttype)
    else:
        text = build_batch_message(task_data, summary, cfg)
    return _chunk_text(text)


def build_failure_message(task_data: dict, summary: dict, ttype: str) -> str:
    """Scheduled-run failure card — sent instead of a result card when a run
    crashes, so a broken task is never silent on WeChat. Only the error text
    (capped) is carried; the full traceback lives in the server log."""
    name = task_data.get("name", "")
    kind = "深度分析" if ttype == "deep_failure" else "定时选股"
    lines = [f"# ⚠️ {kind}失败 · {name}", ""]
    company = summary.get("company_name") or ""
    ticker = summary.get("ticker", "")
    if ticker or company:
        lines.append(f"**股票**: {company} ({ticker})")
    if summary.get("date"):
        lines.append(f"**日期**: {summary.get('date', '')}")
    err = (summary.get("error") or "").strip()
    if err:
        # Cap the error so a long traceback can't blow the 3800-byte budget.
        lines.append(f"**错误**: {err[:500]}")
    return "\n".join(lines)


def build_deep_message(task_data: dict, summary: dict) -> str:
    """Deep-run card: ticker/company/date/rating/signal/analysts."""
    name = task_data.get("name", "")
    lines = [f"# 📊 深度分析完成 · {name}", ""]
    company = summary.get("company_name") or ""
    ticker = summary.get("ticker", "")
    lines.append(f"**股票**: {company} ({ticker})")
    lines.append(f"**日期**: {summary.get('date', '')}")
    lines.append(f"**评级**: {summary.get('rating', '')}")
    if summary.get("signal"):
        lines.append(f"**信号**: {summary.get('signal', '')}")
    if summary.get("analysts"):
        lines.append(f"**分析师**: {summary.get('analysts', '')}")
    return "\n".join(lines)


def build_batch_message(task_data: dict, summary: dict, config: dict | None = None) -> str:
    """Batch-run card: counts + EVERY per-stock conclusion + (best-effort) the
    ② two-pass and ③ advisory composite report."""
    cfg = config or get_config()
    name = task_data.get("name", "")
    lines = [f"# 📈 定时选股完成 · {name}", ""]
    lines.append(
        f"**股票池**: {summary.get('pool', 0)} | **入选**: {summary.get('filtered', 0)}")
    lines.append(f"**已分析**: {summary.get('analyzed', 0)} 只")
    lines.append(
        f"🟢 看多 {summary.get('bullish', 0)} | 🔴 看空 {summary.get('bearish', 0)} "
        f"| ⚪ 观望 {summary.get('watch', 0)}")
    lines.append("")
    lines.append("**逐只结论**:")
    for c in summary.get("codes", []):
        lines.append(_stock_line(c))

    tp_id = summary.get("twopass_record_id")
    if tp_id:
        try:
            from web.twopass_records import get_twopass_record
            rec = get_twopass_record(cfg, tp_id)
        except Exception:
            rec = None
        if rec:
            stocks = rec.get("stocks") or []
            nb = sum(1 for s in stocks if (s.get("newVerdict") or "") == "看多")
            lines += ["", f"**二次分析**: {len(stocks)} 只看多股复核（看多 {nb} 只）"]
            for s in stocks:
                lines.append(_twopass_line(s))

    s3_id = summary.get("stage3_record_id")
    if s3_id:
        try:
            from web.stage3_records import get_stage3_record
            rec = get_stage3_record(cfg, s3_id)
        except Exception:
            rec = None
        if rec and rec.get("report"):
            lines += ["", "**综合报告**:", str(rec["report"]).strip()]

    return "\n".join(lines)


def _stock_line(c: dict) -> str:
    name = c.get("name", "")
    code = c.get("code", "")
    verdict = c.get("verdict", "")
    chg = c.get("change_pct")
    chg_str = f" {chg:+.2f}%" if isinstance(chg, (int, float)) else ""
    return f"- {name} ({code}) {verdict}{chg_str}"


def _twopass_line(s: dict) -> str:
    name = s.get("name", "")
    code = s.get("code", "")
    verdict = s.get("newVerdict", "")
    model = s.get("newModel", "")
    tail = f" · {model}" if model else ""
    return f"- {name} ({code}) {verdict}{tail}"


def _chunk_text(text: str, max_bytes: int = WECOM_MAX_BYTES) -> list[str]:
    """Split text at line boundaries into chunks of at most ``max_bytes`` UTF-8
    bytes. Chunks after the first get a ``⏸ 续`` title so each arrives as its
    own readable WeChat card; no line (i.e. no stock) is ever dropped."""
    lines = text.split("\n")
    chunks: list[str] = []
    cur = ""
    cur_bytes = 0
    title_bytes = len((f"{_CONTINUE_TITLE}\n\n").encode("utf-8"))

    def byte_len(s: str) -> int:
        return len(s.encode("utf-8", "replace"))

    def budget() -> int:
        # The FIRST chunk never carries a continuation title; every later chunk
        # gets one in post-processing, so its bytes are reserved up front —
        # otherwise a near-cap chunk would overflow once the title is added.
        return max_bytes if not chunks else max_bytes - title_bytes

    for ln in lines:
        lb = byte_len(ln)
        b = budget()
        if cur and cur_bytes + lb + 1 > b:
            chunks.append(cur)
            cur, cur_bytes = "", 0
            b = budget()
        if lb > b:
            # Pathological single over-long line (e.g. a huge report paragraph):
            # split it into budget-sized pieces at UTF-8 char boundaries.
            raw = ln.encode("utf-8")
            while raw:
                take = raw[:b]
                chunks.append(take.decode("utf-8", "ignore"))
                raw = raw[len(take):]
                b = budget()
            continue
        if cur:
            cur += "\n" + ln
            cur_bytes += lb + 1
        else:
            cur = ln
            cur_bytes = lb
    if cur:
        chunks.append(cur)

    for i in range(1, len(chunks)):
        if not chunks[i].startswith("# "):
            chunks[i] = f"{_CONTINUE_TITLE}\n\n{chunks[i]}"
    return chunks or [""]


def push_wecom(webhook_url: str, content: str, msgtype: str = "markdown") -> dict:
    """POST one message to the WeCom group-bot webhook.

    The webhook URL itself carries the bot key
    (``https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...``). The URL is
    validated against the official WeCom host before the request (SSRF guard),
    and redirects are never followed. Returns ``{"ok", "errcode", "errmsg"}``;
    never raises.
    """
    if not _valid_webhook_url(webhook_url):
        logger.warning("WeCom push rejected: non-WeCom webhook URL")
        return {"ok": False, "errcode": None,
                "errmsg": "仅允许企业微信官方 webhook 地址"
                          "（https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...）"}
    try:
        import requests
        resp = requests.post(
            webhook_url,
            json={"msgtype": msgtype, msgtype: {"content": content}},
            timeout=15,
            allow_redirects=False,
        )
        data = resp.json() if resp.content else {}
        ok = resp.status_code == 200 and data.get("errcode") == 0
        if not ok:
            logger.warning(
                "WeCom push returned %s/%s: %s",
                resp.status_code, data.get("errcode"), data.get("errmsg"))
        return {
            "ok": bool(ok),
            "errcode": data.get("errcode"),
            "errmsg": data.get("errmsg", ""),
        }
    except Exception as e:  # noqa: BLE001 — never let push failure break the run
        # Never log/return str(e): requests exceptions embed the request URL,
        # whose query string carries the webhook key. Log the exception type
        # only and return a generic message — the full traceback lives in the
        # scheduler log at a level that doesn't echo the credential.
        logger.warning("WeCom push failed: %s", type(e).__name__)
        return {"ok": False, "errcode": None, "errmsg": "网络错误"}
