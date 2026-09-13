"""Server-side batch runners for scheduled 东方自选 / 指数选股 tasks.

``web/scheduler._execute_scheduled_analysis`` dispatches task_type in
("emwl_batch", "idx_batch") here. The batch flow mirrors what the frontend
does for a manual batch run (app.js runBatchEmwlAnalysis / runIdxBatch):

  - build the stock pool (eastmoney watchlist, or index constituents),
  - apply board / change-pct filters (same prefix rules as app.js),
  - analyze each stock with 1-8 model workers (shared queue, throttled),
  - optionally re-analyze the 看多 subset and write a twopass_records row.

Per-stock results are already persisted to ``watchlist_analysis`` inside
``watchlist_stock_detail``, so they are queryable immediately.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)

# Mirror of the frontend board-prefix rules (app.js filterByBoard).
# 创业板=300/301, 科创板=688, 北交所=8/9.
_BOARD_PREFIX = {
    "cyb": ("300", "301"),
    "kcb": ("688",),
    "bjs": ("8", "9"),
}

_TAB = {"emwl_batch": "emwl", "idx_batch": "idx"}

# Throttle between per-stock calls to stay under tushare / Tencent rate limits.
INTER_DELAY_MS = 300.0


def run_batch_task(config: dict, task_data: dict) -> dict:
    """Scheduler-thread entry point — dispatch by task_type."""
    from web.app import _load_env
    _load_env()
    ttype = task_data.get("task_type", "emwl_batch")
    if ttype == "idx_batch":
        return run_idx_batch(config, task_data)
    return run_emwl_batch(config, task_data)


def _build_pool(task_type: str, task_data: dict, config: dict) -> list[dict]:
    """Collect the stock pool (code/name/price/change_pct) for the task."""
    if task_type == "idx_batch":
        from web.app import get_index_constituents
        stocks, seen = [], set()
        for key in (task_data.get("indexes") or [])[:9]:
            r = get_index_constituents(
                index=key, full_refresh=task_data.get("full_refresh", False))
            for s in (r.get("stocks") or []):
                code = s.get("code")
                if code and code not in seen:
                    seen.add(code)
                    stocks.append(s)
        return stocks
    from web.app import get_eastmoney_watchlist
    return get_eastmoney_watchlist().get("stocks") or []


def _filter_stocks(stocks: list[dict], excluded_boards=None,
                   change_pct_min: Optional[float] = None) -> list[dict]:
    """Mirror frontend filterByBoard + filterByChangePct.

    Board is derived from the code prefix (the watchlist_stocks table has no
    board column). ``change_pct <= change_pct_min`` is dropped; a missing
    change_pct is treated as 0.
    """
    excluded = set(excluded_boards or [])
    out = []
    for s in stocks:
        code = str(s.get("code") or "")
        c = code.upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        skip = False
        for board in excluded:
            for prefix in _BOARD_PREFIX.get(board, ()):
                if c.startswith(prefix):
                    skip = True
                    break
            if skip:
                break
        if skip:
            continue
        if change_pct_min is not None:
            try:
                chg = float(s.get("change_pct") or 0)
            except (TypeError, ValueError):
                chg = 0.0
            if chg <= change_pct_min:
                continue
        out.append(s)
    return out


def _workers_for(task_data: dict, key: str = "workers") -> list[dict]:
    ws = task_data.get(key) or []
    if not ws and key != "workers":
        ws = task_data.get("workers") or []  # two_pass_workers empty → fall back to workers
    if not ws:
        ws = [{"provider": "", "model": ""}]
    return ws[:8]


def _analyze_workers(stocks: list[dict], workers: list[dict],
                     job_id: str = "",
                     inter_delay_ms: float = INTER_DELAY_MS) -> list[dict]:
    """Analyze each stock with up to 8 workers from a shared queue.

    Each worker pulls the next un-analyzed stock and calls the same
    ``watchlist_stock_detail`` function the frontend batch uses. Analysis is
    persisted inside that function, so every row is immediately queryable.

    When ``job_id`` is given, per-stock progress is published to
    ``web.run_progress`` so the scheduled-task UI can show an approximate
    in-flight indicator (analyzed/total + current ticker). An empty job_id is
    a no-op, so callers outside the scheduler are unaffected.
    """
    from web.app import watchlist_stock_detail
    from web import run_progress
    if not stocks:
        return []
    n = max(1, min(len(workers), 8))
    results, idx, lock = [], [0], threading.Lock()
    done = [0]

    def _one(worker):
        while True:
            with lock:
                if idx[0] >= len(stocks):
                    return
                s = stocks[idx[0]]
                idx[0] += 1
                current_name = s.get("name") or s.get("code") or ""
                run_progress.update(job_id, current=current_name)
            time.sleep(inter_delay_ms / 1000.0)
            try:
                r = watchlist_stock_detail(
                    s["code"],
                    provider=worker.get("provider", ""),
                    model=worker.get("model", ""),
                    # Pass explicitly — omitting these leaves the FastAPI
                    # Query(default="") FieldInfo objects as defaults, which
                    # break create_llm_client (see watchlist_stock_detail).
                    provider2=worker.get("provider2", ""),
                    model2=worker.get("model2", ""),
                )
                with lock:
                    results.append({
                        **s,
                        "verdict": r.get("verdict", ""),
                        "setup_type": r.get("setup_type", ""),
                        "model_name": r.get("model_name", ""),
                        "analysis": r.get("analysis", ""),
                        "analysis_id": r.get("analysis_id"),
                        "analyzed_at": r.get("analyzed_at", ""),
                    })
                    done[0] += 1
                    run_progress.update(job_id, analyzed=done[0],
                                        current=current_name)
            except Exception:
                logger.exception("Scheduled analyze failed for %s", s.get("code"))

    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in ex.map(_one, workers[:n]):
            pass
    return results


def run_emwl_batch(config: dict, task_data: dict) -> dict:
    return _run_batch(config, task_data, "emwl_batch")


def run_idx_batch(config: dict, task_data: dict) -> dict:
    return _run_batch(config, task_data, "idx_batch")


def _run_batch(config: dict, task_data: dict, task_type: str) -> dict:
    from web import run_progress
    job_id = task_data.get("job_id", "")
    tab = _TAB.get(task_type, "emwl")
    stocks = _build_pool(task_type, task_data, config)
    filtered = _filter_stocks(stocks, task_data.get("excluded_boards", []),
                              task_data.get("change_pct_min"))
    logger.info("Scheduled %s: pool=%d filtered=%d",
                task_type, len(stocks), len(filtered))
    run_progress.start(job_id, phase="analyzing", total=len(filtered))
    results = _analyze_workers(filtered, _workers_for(task_data), job_id)
    bullish = [r for r in results if (r.get("verdict") or "") == "看多"]
    summary = {
        "pool": len(stocks),
        "filtered": len(filtered),
        "analyzed": len(results),
        "bullish": len(bullish),
        "bearish": sum(1 for r in results if (r.get("verdict") or "") == "看空"),
        "watch": sum(1 for r in results if (r.get("verdict") or "") == "观望"),
        # Lightweight per-stock conclusions so the scheduled-task report can
        # render ① per-stock table without re-querying the analysis store.
        "codes": [
            {
                "code": r.get("code", ""),
                "name": r.get("name", ""),
                "change_pct": r.get("change_pct"),
                "verdict": r.get("verdict", ""),
                "model_name": r.get("model_name", ""),
            }
            for r in results
        ],
    }
    if task_data.get("two_pass") and bullish:
        run_progress.update(job_id, phase="twopass", total=len(bullish),
                            analyzed=0)
        twopass_id = run_two_pass(tab, results, task_data, config)
        summary["twopass_record_id"] = twopass_id
        if twopass_id and task_data.get("stage3"):
            run_progress.update(job_id, phase="stage3", total=0, analyzed=0)
            summary["stage3_record_id"] = run_stage3(tab, twopass_id, task_data, config)
    logger.info("Scheduled %s done: %s", task_type, summary)
    return summary


def run_two_pass(tab: str, analyzed: list[dict], task_data: dict,
                 config: dict) -> Optional[int]:
    """Write a twopass_records row for the 看多 subset.

    mode ``reanalyze`` (default): re-run LLM analysis on the 看多 stocks with
        ``two_pass_workers`` (defaults to ``workers``) — same as the frontend
        二次分析 flow. Costs extra LLM calls per 看多 stock.
    mode ``pack``: zero extra cost — record this batch's conclusion as
        ``newVerdict`` (and leave ``prevVerdict`` empty).
    """
    from web.twopass_records import save_twopass_record
    bullish = [r for r in analyzed if (r.get("verdict") or "") == "看多"]
    if not bullish:
        return None
    if task_data.get("two_pass_mode", "reanalyze") == "pack":
        records = [{
            "code": r.get("code", ""),
            "name": r.get("name", ""),
            "price": r.get("price"),
            "change_pct": r.get("change_pct"),
            "prevModel": "",
            "prevVerdict": "",
            "newModel": r.get("model_name", ""),
            "newVerdict": r.get("verdict", ""),
            "analysis": r.get("analysis", ""),
        } for r in bullish]
        return save_twopass_record(config, tab, max(1, len(_workers_for(task_data))),
                                   records)

    tp_workers = _workers_for(task_data, "two_pass_workers")
    fresh = _analyze_workers(bullish, tp_workers,
                             task_data.get("job_id", ""))
    by_code = {r.get("code"): r for r in fresh}
    records = []
    for s in bullish:
        f = by_code.get(s.get("code")) or {}
        records.append({
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "price": s.get("price"),
            "change_pct": s.get("change_pct"),
            "prevModel": s.get("model_name", ""),
            "prevVerdict": s.get("verdict", ""),
            "newModel": f.get("model_name", ""),
            "newVerdict": f.get("verdict", ""),
            "analysis": f.get("analysis", s.get("analysis", "")),
        })
    return save_twopass_record(config, tab, max(1, len(tp_workers)), records)


def run_stage3(tab: str, twopass_id: int, task_data: dict, config: dict,
               _consume=None) -> Optional[int]:
    """阶段③：二次分析后的一次无头顾问综合报告.

    Runs ONE headless advisory conversation against the 看多 subset of the
    given two-pass record, persists the composite report as a
    ``stage3_records`` row, and returns the new record id (or None on any
    failure — never raises, so a failed stage-3 does not break the batch).

    The advisory agent is invoked through the existing live chat generator
    ``stream_history_chat`` (advisory mode, empty run_ids), which persists the
    final answer to ``chat_messages`` internally; we only consume the generator
    and read the last assistant message afterwards. ``_consume`` is injectable
    for tests (defaults to iterating the real generator).
    """
    from web.twopass_records import get_twopass_record
    from web.results_store import (
        init_chat_tables, create_chat_thread, get_chat_messages,
    )

    def _default_consume(tid, q, c, lg):
        from web.history_chat import stream_history_chat
        return list(stream_history_chat(tid, q, c, lg))

    rec = get_twopass_record(config, twopass_id)
    if not rec:
        return None
    bullish = [s for s in (rec.get("stocks") or [])
               if (s.get("newVerdict") or "").strip() == "看多"]
    if not bullish:
        return None

    # Independent stage-3 model: override the deep role on a config copy.
    cfg = config.copy()
    if task_data.get("stage3_provider"):
        cfg["deep_think_provider"] = task_data["stage3_provider"]
    if task_data.get("stage3_model"):
        cfg["deep_think_llm"] = task_data["stage3_model"]

    rows = ["| 代码 | 名称 | 涨跌幅 | 前次结论 | 本次模型 | 本次结论 |",
            "|---|---|---|---|---|---|"]
    for s in bullish:
        rows.append(
            f"| {s.get('code', '')} | {s.get('name', '')} | "
            f"{s.get('change_pct', '')} | {s.get('prevVerdict', '')} | "
            f"{s.get('newModel', '')} | {s.get('newVerdict', '')} |"
        )
    table = "\n".join(rows)

    question = "\n".join([
        f"以下是一条【二次分析】看多候选记录（记录ID {twopass_id}）："
        f"{rec.get('title', '')} · {rec.get('stock_count', 0)}只复核 · "
        f"{len(bullish)}只确认看多。",
        "",
        table,
        "",
        "请对这批候选做综合研判，输出一份【综合报告】：",
        f"1. 先调用 get_twopass_record_detail({twopass_id}) 获取每只股票的完整分析结论；",
        "2. 对每只候选逐一交叉验证（get_smart_money_score / get_realtime_fund_flow / "
        "get_stock_data / web_search_current / query_eastmoney_data 等），"
        "核对主力资金、技术面、消息面；",
        "3. 输出【每股结论】：代码、名称、结论（看多/看空/观望）、核心理由（引用具体数字）；",
        "4. 输出【整体研判】：市场环境与资金主线、板块强弱、风险提示、组合建议。",
        "",
        "若确认看多不足，如实说明并给出可观察的备选。",
    ])

    lang = task_data.get("language", "Chinese")
    title = f"{rec.get('title', '')}·顾问综合报告"

    try:
        init_chat_tables(cfg)  # idempotent; guarantees chat_threads exists
        thread_id = create_chat_thread(cfg, [], title)
        consume = _consume or _default_consume
        consume(thread_id, question, cfg, lang)
        msgs = [m for m in get_chat_messages(cfg, thread_id)
                if m.get("role") == "assistant"]
        if not msgs:
            logger.error("Stage3 empty report for twopass %s", twopass_id)
            return None
        report = (msgs[-1].get("content") or "").strip()
        if not report:
            logger.error("Stage3 blank report for twopass %s", twopass_id)
            return None
    except Exception:
        logger.exception("Stage3 failed for twopass %s", twopass_id)
        return None

    from web.stage3_records import save_stage3_record
    return save_stage3_record(
        cfg, tab, twopass_id, thread_id,
        task_data.get("stage3_provider", ""), task_data.get("stage3_model", ""),
        len(bullish), report, rec.get("stocks") or [], title,
    )
