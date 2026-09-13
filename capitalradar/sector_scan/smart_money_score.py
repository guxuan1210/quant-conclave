"""Smart Money Detection — 6-dimension continuous scoring for retail investors.

Answers the core question:
  "Is there genuine institutional money behind this stock's recent price
   action, or is it retail noise?"

Six dimensions, each scored 0-100 (NOT the old binary 100/30):
  ① Scale        — How large is the institutional flow relative to turnover?
  ② Direction    — Net buying or net selling?
  ③ Persistence  — Is the direction consistent over the last 5 days?
  ④ Alignment    — Does flow direction match price direction?
  ⑤ Confirmation — Do other data sources (big-order, margin) agree?
  ⑥ Stage        — What lifecycle stage is the stock in?

Weighted composite score → 0-100 with a one-line Chinese verdict.

Public API (stable — do NOT change signatures):
  detect_smart_money(ticker, config) → Dict
  compute_smart_money_score(ticker, config) → Tuple[float, Dict]
"""

from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from capitalradar.dataflows.quote_text import parse_quote_price, parse_quote_change_pct

logger = logging.getLogger(__name__)

# 双源校验结果缓存: ticker -> (trade_date, verdict)。同一标的同一交易日只查一次
# tushare moneyflow_dc（同花顺验证源），控频的同时允许日内复用。verdict 取值
# 与 moneyflow_verifier 对齐: "一致" / "偏差" / "数据缺失"。
_DUAL_SOURCE_CACHE: Dict[str, Tuple[str, str]] = {}

# ── Public API ──────────────────────────────────────────────────────────

def detect_smart_money(ticker: str, config: dict) -> Dict:
    """Run the 6-dimension scoring pipeline.

    Returns:
        verdict: "confirmed" | "moderate" | "weak" | "no_signal" | "error"
        score: 0-100 composite
        dimensions: {name: {"score": 0-100, "detail": str}}
        summary: one-line Chinese verdict
    """
    result = {
        "verdict": "error",
        "score": 0,
        "dimensions": {},
        "summary": "",
        "confidence": "low",
        "stage": "",
    }

    # ── Fetch data ──
    try:
        flow_rows = _fetch_flow_data(ticker, config)
    except Exception as e:
        result["verdict"] = "error"
        result["summary"] = f"{ticker}: 数据获取失败 — {e}"
        return result

    if not flow_rows or len(flow_rows) < 5:
        if flow_rows and flow_rows[0].get("_source") == "realtime":
            return _realtime_limited_result(ticker, flow_rows)
        result["verdict"] = "no_signal"
        result["_data_unavailable"] = True
        result["summary"] = f"{ticker}: 资金流数据不足（需≥5个交易日），评分不可信，不出分"
        return result

    # Validate data quality across the 5/10/20-day windows: if the raw data is
    # all-zero (or missing) on every window, it is data UNAVAILABLE — a data-source
    # gap must not masquerade as a low (bearish) score.
    recent_5 = flow_rows[-5:]
    recent_10 = flow_rows[-10:] if len(flow_rows) >= 10 else flow_rows
    recent_20 = flow_rows[-20:] if len(flow_rows) >= 20 else flow_rows
    if (not any(abs(r["net_amount"]) > 0 for r in recent_5)
            and not any(abs(r["net_amount"]) > 0 for r in recent_10)
            and not any(abs(r["net_amount"]) > 0 for r in recent_20)):
        result["verdict"] = "no_signal"
        result["_data_unavailable"] = True
        result["summary"] = f"{ticker}: 资金流数据缺失（5日/10日/20日原始数据全为0或缺失），评分不可信，不出分"
        return result

    # ── Dual-source trust gate ──
    # Cross-check the primary vendor's raw 净额 against 同花顺 (tushare
    # moneyflow_dc). On 偏差 the two vendors disagree on direction/magnitude —
    # the narrative below would be a coin flip, so the score is withheld and the
    # summary surfaces the conflict instead of a confident 主力 story.
    gate = _dual_source_gate(ticker, flow_rows, config)
    result["_dual_source"] = gate

    price_info = _fetch_price_data(ticker, config)
    result["_price_meta"] = _price_meta_from(price_info)
    is_limit_up = _is_limit_up_day(result["_price_meta"].get("today_change_pct"), ticker)

    # ── Data validity annotation (cheap, zero extra I/O) ──
    # Layer-1 discipline: flows below the 5-day magnitude floor are noise-level;
    # mark the score "不可信" without suppressing it (a small-cap can be tiny but
    # real). The renderer surfaces _validity as a visible annotation.
    net_5d = sum(r["net_amount"] for r in recent_5)
    min_5d_yuan = float(config.get("sms_min_5d_net_abs_wan", 1000.0)) * 1e4  # 万元→元
    validity_flags = []
    if abs(net_5d) < min_5d_yuan:
        validity_flags.append("flow_too_small")
    if gate["verdict"] == "偏差":
        validity_flags.append("dual_source_conflict")
    result["_validity"] = {
        "ok": not validity_flags,
        "flags": validity_flags,
        "note": ("5日净流绝对值低于%s万元，量级过小，评分可信度低"
                 % int(config.get("sms_min_5d_net_abs_wan", 1000.0))
                 if "flow_too_small" in validity_flags else ""),
    }

    # ── Score all 6 dimensions ──
    dims = {}
    dims["scale"] = _score_scale(flow_rows)
    dims["direction"] = _score_direction(flow_rows, is_limit_up=is_limit_up)
    dims["persistence"] = _score_persistence(flow_rows)
    dims["alignment"] = _score_alignment(flow_rows, price_info)
    dims["confirmation"] = _score_confirmation(flow_rows, ticker, config)
    dims["stage"] = _score_stage(flow_rows, dims)

    # ── Weighted composite ──
    weights = {
        "scale": 0.20, "direction": 0.20, "persistence": 0.15,
        "alignment": 0.20, "confirmation": 0.15, "stage": 0.10,
    }
    composite = round(sum(
        dims[name]["score"] * weights[name] for name in weights
    ))

    # ── Verdict ──
    if composite >= 70:
        verdict = "confirmed"
    elif composite >= 50:
        verdict = "moderate"
    elif composite >= 30:
        verdict = "weak"
    else:
        verdict = "no_signal"

    result["verdict"] = verdict
    result["score"] = composite
    result["dimensions"] = dims
    result["stage"] = dims["stage"].get("stage_name", "")
    result["confidence"] = "high" if composite >= 70 else ("medium" if composite >= 50 else "low")
    result["summary"] = _build_summary(ticker, result)

    # ── Dual-source conflict: withhold the confident narrative ──
    # Composite is kept (renderers can show "原始综合分"), but the verdict is
    # demoted to no_signal and the summary becomes a data-trust warning so the
    # reader sees "双源冲突" instead of a directional 主力 story.
    if gate["verdict"] == "偏差":
        result["verdict"] = "no_signal"
        result["confidence"] = "low"
        result["summary"] = (
            f"{ticker}: ⚠️ 双源资金面冲突（东财 vs 同花顺方向/量级不一致，"
            f"{gate.get('note', '')}），数据可信度低，不构成买卖依据"
            f"（原始综合分 {composite}，已停发）"
        )

    # Back-compat: legacy gate structure
    result["gates"] = {
        "scale":      {"passed": dims["scale"]["score"] >= 50,      "detail": dims["scale"]["detail"]},
        "persistence": {"passed": dims["persistence"]["score"] >= 50, "detail": dims["persistence"]["detail"]},
        "alignment":   {"passed": dims["alignment"]["score"] >= 50,   "detail": dims["alignment"]["detail"]},
        "cross":       {"passed": dims["confirmation"]["score"] >= 50, "detail": dims["confirmation"]["detail"],
                        "sources": dims["confirmation"].get("sources", 0)},
    }

    return result


def _dual_source_gate(ticker: str, flow_rows: List[Dict], config: dict) -> Dict:
    """Cross-check the primary flow source against 同花顺 (tushare moneyflow_dc).

    Both sides measure 主力净流入, compared apples-to-apples:
      SMS primary rows carry net_amount = 主力 (超大单+大单, Fix 1); the verifier
      同花顺 moneyflow_dc ``net_amount`` is 同花顺's own 主力 — empirically ≈ 东财
      主力 within a few % (001366 33日逐日对比几乎全部同向). Earlier this gate
      compared 东财 raw net_amount (tushare net_mf_amount, 铁律#6 伪字段) instead
      and produced FALSE 偏差 — 001366 (2026-09-02 涨停) 报 5/10/20日全部方向相反
      while 东财主力 +3628万 vs 同花顺 +3601万 agree. The gate is a *data-trust*
      gate: only a genuine 主力-level disagreement (direction flip on meaningful
      flow, or ratio > max_ratio) is a 偏差 — on 偏差 the direction narrative
      would be a coin flip, so the caller refuses to emit a confident score.

    Returns {"verdict": "一致"|"偏差"|"数据缺失"|"未启用", "note": str, "detail": dict}.
    """
    if not config.get("sms_dual_source_gate", False):
        return {"verdict": "未启用", "note": "双源校验未启用", "detail": {}}
    if any(r.get("_source") == "realtime" for r in flow_rows):
        # Realtime (MX) 主力 has no same-day 同花顺 counterpart; a daily-daily
        # comparison would be apples-to-oranges. Skip rather than misjudge.
        return {"verdict": "未启用", "note": "实时源跳过双源校验", "detail": {}}

    today = datetime.now().strftime("%Y-%m-%d")
    cached = _DUAL_SOURCE_CACHE.get(ticker)
    if cached and cached[0] == today:
        return {"verdict": cached[1], "note": "双源校验(当日缓存)", "detail": {}}

    try:
        from capitalradar.sector_scan.moneyflow_verifier import verify_moneyflow
        # Pass flow_rows AS-IS: their net_amount is already 主力 (Fix 1), and the
        # verifier 同花顺 moneyflow_dc net_amount is 同花顺主力 — 主力 vs 主力 才是
        # 同口径。绝不把 net_amount 覆盖成 _gross_net（东财原始 net_mf_amount）：
        # 那是 铁律#6 伪字段，与同花顺比会产生假偏差（001366 曾 5/10/20日全反向，
        # 实为 东财主力 +3628 vs 同花顺 +3601 同向）。
        res = verify_moneyflow(ticker, flow_rows=flow_rows, config=config)
    except Exception as e:
        logger.warning("dual-source gate failed for %s: %s", ticker, e)
        return {"verdict": "数据缺失", "note": f"双源校验异常: {e}", "detail": {}}

    verdict = res.get("verdict", "数据缺失")
    _DUAL_SOURCE_CACHE[ticker] = (today, verdict)
    return {"verdict": verdict, "note": res.get("note", ""), "detail": res}


def _is_limit_up_day(today_change_pct: Optional[float], ticker: str) -> bool:
    """Return True when today looks like a limit-up day for the given board.

    A-share price-limit tiers: 主板 10% (ST 5% not detected), 创业板/科创板
    20%, 北交所 30%. A tolerance separates a true limit-up from a big but
    sub-limit move. Flow on a limit-up day is structurally distorted (封单堵板 /
    拆单对倒), so callers downweight its contribution (see ``_score_direction``).
    """
    if today_change_pct is None:
        return False
    code = str(ticker or "").split(".")[0]
    if code.startswith(("300", "301", "688", "689")):
        return today_change_pct >= 19.5
    if code.startswith(("4", "8", "92")):
        return today_change_pct >= 29.0
    return today_change_pct >= 9.8


def format_sms_unavailable(ticker: str, note: str = "") -> str:
    """Shared "data unavailable, no score" tool reply for SMS renderers.

    Used by ai_pick / history_chat / prediction_chat when the breakdown is
    flagged ``_data_unavailable`` — an all-zero or missing raw flow dataset must
    surface as a data-integrity notice, NOT as a low (bearish) score.
    """
    note_part = f" — {note}" if note else ""
    return (
        f"## Smart Money Score: {ticker}\n\n"
        f"⚠️ **数据不可用，不出分** — 资金流原始数据缺失或全为0，评分不可信。{note_part}\n\n"
        f"这不是看空信号。可用 `verify_moneyflow` 交叉验证资金面数据后再下结论。"
    )


def _realtime_limited_result(ticker: str, rows: List[Dict]) -> Dict:
    """Degraded verdict from a single realtime flow snapshot.

    Historical moneyflow vendors were all unavailable (rate-limit /
    network), so only today's direction is known. Return an honest
    directional read rather than a hard error — a data-source outage
    must not masquerade as a low (bearish) score.
    """
    r = rows[0]
    net = r.get("net_amount") or 0
    super_large = r.get("buy_elg_amount") or 0
    large = r.get("buy_lg_amount") or 0
    # An all-zero snapshot (MX returned zeros, or a suspended stock) is data
    # UNAVAILABLE, not a bearish direction — must not emit a 25/45 score.
    if not (net or super_large or large):
        return {
            "verdict": "no_signal",
            "score": 0,
            "dimensions": {},
            "stage": "",
            "confidence": "low",
            "_realtime_only": True,
            "_data_unavailable": True,
            "summary": f"{ticker}: 资金流数据缺失（实时快照全为0），评分不可信，不出分",
        }
    detail = f"主力净流入{net / 1e4:+.0f}万元"
    if super_large or large:
        detail += f"（超大单{super_large / 1e4:+.0f}万 / 大单{large / 1e4:+.0f}万）"
    if net > 0:
        verdict, score = "weak", 45
    else:
        verdict, score = "no_signal", 25
    return {
        "verdict": verdict,
        "score": score,
        "dimensions": {},
        "stage": "",
        "confidence": "low",
        "_realtime_only": True,
        "summary": f"{ticker}: {detail}（历史资金流数据源暂不可用，仅今日实时快照，无法计算完整6维评分）",
    }


def compute_smart_money_score(ticker: str, config: dict) -> Tuple[float, Dict]:
    """Legacy-compatible wrapper. Returns (0-100 score, breakdown dict).

    The breakdown dict uses the old key structure for backward
    compatibility but includes the new dimension scores.

    Additional ``_``-prefixed keys let tool renderers distinguish a
    genuine low score from a data-source outage:
      ``_verdict``       — "error" / "no_signal" / "weak" / ... from detect_smart_money
      ``_realtime_only`` — True when only a single realtime snapshot was available
      ``_note``          — human-readable summary (direction / error reason)
    """
    result = detect_smart_money(ticker, config)
    dims = result.get("dimensions", {})
    total = result.get("score", 0)

    breakdown = {"_total": total}
    for name, d in dims.items():
        breakdown[name] = {"score": d["score"], "detail": d["detail"]}
    breakdown["verdict"] = {"score": total, "detail": result["summary"]}
    breakdown["_verdict"] = result.get("verdict", "error")
    breakdown["_realtime_only"] = result.get("_realtime_only", False)
    breakdown["_data_unavailable"] = result.get("_data_unavailable", False)
    breakdown["_validity"] = result.get("_validity", {})
    breakdown["_note"] = result.get("summary", "")
    # Price metadata (current price / today's change / data date) so tool
    # renderers can surface the unambiguous "today" ground truth.
    breakdown["_meta"] = result.get("_price_meta", {})
    # Legacy gate pass/fail
    for gname, g in result.get("gates", {}).items():
        breakdown.setdefault(gname, g)
    return total, breakdown


# ── Data Fetching ────────────────────────────────────────────────────────

def _fetch_flow_data(ticker: str, config: dict) -> Optional[List[Dict]]:
    """Fetch 60-day moneyflow from tushare. Returns list of daily dicts.

    Resilience: the vendor chain (tushare → akshare → eastmoney) is subject
    to per-minute rate limits and transient network/proxy drops. We retry
    with backoff, then fall back to a single-day realtime snapshot so a
    temporary outage yields a directional read instead of a hard error.
    """
    from capitalradar.agents.utils.capital_flow_tools import get_money_flow
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    raw = None
    last_error = None
    for attempt in range(3):
        try:
            raw = get_money_flow.invoke({
                "ticker": ticker, "start_date": start_date, "end_date": end_date,
            })
            break
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(2.0 + attempt * 3.0)   # 2s → 5s backoff (rate limit / transient drop)
    if raw is None:
        logger.warning("_fetch_flow_data: all vendors failed for %s after retries: %s", ticker, last_error)
        rt_rows = _fetch_realtime_flow_signal(ticker)
        if rt_rows is not None:
            return rt_rows
        raise RuntimeError(f"资金流数据源全部不可用（限流或网络故障，已重试3次）: {last_error}")

    text = str(raw)

    # Find header row by scanning for column names
    lines = text.split("\n")
    header_idx = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if "trade_date" in lower and ("net_amount" in lower or "net_mf_amount" in lower):
            header_idx = i
            break
    if header_idx is None:
        # Fallback: look for ts_code + trade_date (old format)
        header_idx = next((i for i, l in enumerate(lines) if "ts_code" in l and "trade_date" in l), None)
    if header_idx is None:
        logger.warning("_fetch_flow_data: cannot find CSV header for %s", ticker)
        return None

    reader = __import__("csv").DictReader(io.StringIO("\n".join(lines[header_idx:])))
    rows = []
    for r in reader:
        try:
            # 主力净额 = 超大单净额 + 大单净额，统一从 buy/sell 四列计算。
            # 供应商原始 net_amount 语义不一（tushare「净流入额」既不是全口径
            # 也不是超大单+大单：涨停日可报 -5052万 而超大单+大单 +3225万），
            # 直接读它会把方向/持续性/阶段维度带偏。原始值保留为 _gross_net
            # 供展示，并在双源校验时按同口径（东财净额 vs 同花顺净额）比对。
            gross = float(r.get("net_amount", r.get("net_mf_amount", 0)) or 0)
            be = float(r.get("buy_elg_amount", 0) or 0)
            se = float(r.get("sell_elg_amount", 0) or 0)
            bl = float(r.get("buy_lg_amount", 0) or 0)
            sl = float(r.get("sell_lg_amount", 0) or 0)
            has_bucket_cols = any(k in r for k in (
                "buy_elg_amount", "sell_elg_amount",
                "buy_lg_amount", "sell_lg_amount",
            ))
            net = (be - se) + (bl - sl) if has_bucket_cols else gross
            rows.append({
                "net_amount": net,
                "_gross_net": gross,
                "buy_elg_amount": be,
                "sell_elg_amount": se,
                "buy_lg_amount": bl,
                "sell_lg_amount": sl,
                "date": str(r.get("trade_date", "")).strip(),
            })
        except (ValueError, KeyError):
            pass
    if len(rows) < 5:
        return None
    # tushare emits NEWEST-first (trade_date DESC), akshare chronological. Normalize
    # to chronological so every rows[-5:]/rows[-20:] window means the MOST RECENT
    # N days — otherwise "近5日" silently selects the OLDEST bars (~55-60d ago).
    rows.sort(key=lambda r: r["date"] or "")
    return rows


def _fetch_realtime_flow_signal(ticker: str) -> Optional[List[Dict]]:
    """Single-day realtime money flow as a last-resort fallback.

    Tries 妙想(MX) real-time 主力资金 (DDX/DDY/DDZ) first, then eastmoney push2
    (which is proxy-blocked on some machines). Returns a 1-row list using the
    same schema as historical flow rows, with a ``_source: "realtime"`` marker
    so callers can distinguish a degraded signal from a full 5+ day history.
    ``None`` if all realtime sources are unreachable.
    """
    # ── 妙想(MX) real-time 主力资金 first (东方财富, same-day live) ──
    try:
        from capitalradar.dataflows import mx_client
        data = mx_client.query(f"{ticker} 主力资金流向")
        m = mx_client.extract_snapshot_metrics(data)
        super_net = next((v for k, v in m.items() if "超大单净流入" in k), None)
        # "大单净流入" is a substring of "超大单净流入" — exclude the 超大 single
        # so lg_net never silently grabs 超大单 when both keys are present.
        lg_net = next(
            (v for k, v in m.items() if "大单净流入" in k and "超" not in k),
            None,
        )
        main_net = next((v for k, v in m.items() if "主力净流入" in k), None)
        if super_net is not None or main_net is not None:
            # mx_client.parse_cn_amount normalizes to 万元 ("6.5亿元" → 65000.0).
            # Convert to yuan (×1e4) so the row matches the yuan convention every
            # downstream x/1e4 (→ 万元 display) assumes — otherwise the 妙想 fallback
            # prints 6.5亿 as "+6万元" (a 1万倍 shrink). eastmoney push2 is already yuan.
            main_net = main_net * 1e4 if main_net is not None else None
            super_net = super_net * 1e4 if super_net is not None else None
            lg_net = lg_net * 1e4 if lg_net is not None else None
            return [{
                "net_amount": main_net if main_net is not None
                else ((super_net or 0) + (lg_net or 0)),
                "buy_elg_amount": float(super_net or 0),
                "sell_elg_amount": 0,
                "buy_lg_amount": float(lg_net or 0),
                "sell_lg_amount": 0,
                "_source": "realtime",
                "_vendor": "mx",
                "_price": 0,
            }]
    except Exception:
        pass
    # ── eastmoney push2 fallback ──
    try:
        from capitalradar.dataflows.eastmoney_realtime_flow import _get_realtime_fund_flow
        rt = _get_realtime_fund_flow(ticker)
    except Exception:
        rt = None
    if not rt:
        return None
    return [{
        "net_amount": rt.get("main_net_inflow") or 0,
        "buy_elg_amount": rt.get("super_large_net") or 0,
        "sell_elg_amount": 0,
        "buy_lg_amount": rt.get("large_net") or 0,
        "sell_lg_amount": 0,
        "_source": "realtime",
        "_price": rt.get("price") or 0,
    }]


def _fetch_price_data(ticker: str, config: dict) -> Optional[Dict]:
    """Fetch 30-day price data.

    Returns {"closes": [...], "dates": [...], "current": float|None,
    "today_change": float|None, "data_date": str|None}. ``today_change`` is the
    single-day (今日) change ratio so callers never misread a multi-day move as
    today's; ``data_date`` is the trade date the price actually refers to.
    """
    try:
        from capitalradar.agents.utils.core_stock_tools import get_stock_data
        from capitalradar.dataflows.interface import route_to_vendor

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        price_data = get_stock_data.invoke({
            "symbol": ticker, "start_date": start_date, "end_date": end_date,
        })
        text = str(price_data)
        lines = text.split("\n")

        # Find header
        hdr = next((i for i, l in enumerate(lines) if "trade_date" in l.lower() or "Date" in l or "date" in l.lower()), None)
        if hdr is None:
            return None

        reader = __import__("csv").DictReader(io.StringIO("\n".join(lines[hdr:])))
        closes, dates = [], []
        for r in reader:
            try:
                close = float(r.get("close", r.get("Close", 0)) or 0)
                if close > 0:
                    closes.append(close)
                    date_val = str(r.get("trade_date", r.get("Date", r.get("date", "")))).strip()[:10]
                    dates.append(date_val)
            except (ValueError, KeyError):
                pass

        # get_stock_data returns rows NEWEST-first (trade_date DESC, tushare).
        # Reverse to chronological so closes[-1] / dates[-1] are the MOST RECENT
        # bar — the stale-check and every "[-1]/[-5]" index below rely on it.
        closes.reverse()
        dates.reverse()

        if len(closes) < 5:
            return {"closes": closes, "dates": dates, "current": None,
                    "today_change": None, "data_date": dates[-1] if dates else None}

        # Freshness: ALWAYS attempt a realtime quote. The daily bar alone is not
        # enough — a stale bar (halted / pre-open) or a duplicated "today" bar both
        # produce a meaningless +0.00%. Prefer the realtime quote's own 涨跌幅 when
        # present (Tencent `**Change**: {涨跌额} / {涨跌幅}%`, fld[32] is the
        # authoritative single-day move); fall back to price-vs-last-close, then to
        # the last two daily closes only when the last bar already IS today.
        today_str = datetime.now().strftime("%Y-%m-%d")
        last_date = dates[-1] if dates else ""
        data_date = last_date or None
        rt_current = None
        rt_change = None
        try:
            rt_raw = route_to_vendor("get_realtime_quote", symbol=ticker)
            # Shared parsers cover Tencent `**Current Price**: 24.35` and akshare
            # `Current Price: 24.35`; the %-number after Change is today's
            # authoritative move (fld[32]) that a price-diff cannot see.
            rt_change = parse_quote_change_pct(rt_raw)
            rt_current = parse_quote_price(rt_raw)
            if rt_current is not None or rt_change is not None:
                data_date = today_str
        except Exception:
            pass

        # Today's single-day change — best available ground truth first:
        #   1. realtime 涨跌幅 (authoritative)    2. realtime price vs last close
        #   3. last two daily closes (only when the last bar already IS today)
        today_change = None
        if rt_change is not None:
            today_change = rt_change / 100.0
        elif rt_current is not None and closes:
            today_change = (rt_current - closes[-1]) / closes[-1]
        elif last_date == today_str and len(closes) >= 2:
            today_change = (closes[-1] - closes[-2]) / closes[-2]

        return {"closes": closes, "dates": dates, "current": rt_current,
                "today_change": today_change, "data_date": data_date}
    except Exception as e:
        logger.warning("_fetch_price_data failed for %s: %s", ticker, e)
        return None


def _price_meta_from(price_info: Optional[Dict]) -> Dict:
    """Extract a stable price metadata block for tool renderers.

    Gives the LLM an unambiguous "today" ground truth — the current price, the
    single-day change %, and the date the price refers to — so it never has to
    infer today's move from a multi-day window. Missing data → key absent /
    None, never a fabricated number.
    """
    if not price_info:
        return {}
    closes = price_info.get("closes") or []
    cur = price_info.get("current")
    meta = {
        "price": None,
        "today_change_pct": None,
        "data_date": price_info.get("data_date"),
    }
    if cur and cur > 0:
        meta["price"] = cur
    elif closes:
        meta["price"] = closes[-1]
    tc = price_info.get("today_change")
    if tc is not None:
        meta["today_change_pct"] = round(tc * 100, 2)
    return meta


# ── Dimension Scoring ────────────────────────────────────────────────────

def _score_scale(rows: List[Dict]) -> Dict:
    """① Scale (0-100): How large is institutional flow relative to big-order volume?

    Uses the same net_avg/big_avg ratio as the old Gate 1, but maps
    to a continuous score instead of a binary pass/fail.
    """
    recent = rows[-5:]
    net_avg = sum(abs(r["net_amount"]) for r in recent) / 5
    big_avg = sum(
        abs(r["buy_elg_amount"]) + abs(r["sell_elg_amount"]) +
        abs(r["buy_lg_amount"]) + abs(r["sell_lg_amount"])
        for r in recent
    ) / 5

    if big_avg == 0:
        return {"score": 10, "detail": "无交易数据", "ratio_pct": 0}

    ratio = net_avg / big_avg
    ratio_pct = round(ratio * 100, 2)

    # Continuous mapping
    if ratio >= 0.20:   score = 100
    elif ratio >= 0.15: score = 90
    elif ratio >= 0.10: score = 80
    elif ratio >= 0.07: score = 70
    elif ratio >= 0.05: score = 60
    elif ratio >= 0.03: score = 45
    elif ratio >= 0.02: score = 30
    elif ratio >= 0.01: score = 20
    else:               score = 10

    return {
        "score": score,
        "detail": f"净流/大单比 {ratio_pct:.1f}% (净均{net_avg/1e4:.0f}w / 大单均{big_avg/1e4:.0f}w)",
        "ratio_pct": ratio_pct,
    }


def _score_direction(rows: List[Dict], is_limit_up: bool = False) -> Dict:
    """② Direction (0-100): Net buying or net selling?

    Considers both 5-day and 20-day net amounts. Pure inflow = high score,
    pure outflow = low score. Mixed = middle.

    ``is_limit_up``: on a limit-up day order-flow is structurally distorted
    (封单堵板 / 拆单对倒 / 被动成交失真) and a single such day can dwarf the
    other four. It is downweighted to 20% (numerator AND denominator, so the
    ratio stays unbiased) so it cannot dominate the read; the *reported*
    5日净额 stays the true raw sum.
    """
    recent_5 = rows[-5:]
    raw_nets_5 = [r["net_amount"] for r in recent_5]
    raw_net_5d = sum(raw_nets_5)

    recent_20 = rows[-20:] if len(rows) >= 20 else rows
    net_20d = sum(r["net_amount"] for r in recent_20)

    if is_limit_up and len(raw_nets_5) >= 2:
        weights = [1.0] * (len(raw_nets_5) - 1) + [0.2]
        wsum = sum(weights)
        net_5d = sum(n * w for n, w in zip(raw_nets_5, weights)) / wsum
        total_abs_5d = sum(abs(n) * w for n, w in zip(raw_nets_5, weights)) / wsum
    else:
        net_5d = raw_net_5d
        # Total magnitude for context
        total_abs_5d = sum(abs(n) for n in raw_nets_5)

    if total_abs_5d == 0:
        return {"score": 50, "detail": "无显著资金流动", "net_5d_wan": 0}

    # Direction score: net/total_abs mapped to 0-100
    direction_ratio_5d = net_5d / total_abs_5d  # -1.0 (pure out) to +1.0 (pure in)
    direction_ratio_20d = net_20d / sum(abs(r["net_amount"]) for r in recent_20) if recent_20 else 0

    # Blend 5d (70%) and 20d (30%)
    blended = direction_ratio_5d * 0.7 + direction_ratio_20d * 0.3

    # Map -1..+1 to 0..100
    score = round(50 + blended * 50)
    score = max(0, min(100, score))

    net_5d_wan = round(raw_net_5d / 1e4, 0)
    net_20d_wan = round(net_20d / 1e4, 0)

    if blended > 0.5:
        desc = "强烈净流入"
    elif blended > 0.15:
        desc = "温和净流入"
    elif blended > -0.15:
        desc = "买卖均衡"
    elif blended > -0.5:
        desc = "温和净流出"
    else:
        desc = "强烈净流出"

    limit_note = "，涨停日资金已降权" if is_limit_up else ""
    return {
        "score": score,
        "detail": f"{desc} (5日{net_5d_wan:+.0f}w / 20日{net_20d_wan:+.0f}w){limit_note}",
        "net_5d_wan": net_5d_wan,
        "net_20d_wan": net_20d_wan,
    }


def _score_persistence(rows: List[Dict]) -> Dict:
    """③ Persistence (0-100): Is the direction consistent over 5 days?

    Counts same-direction days + trend in magnitude.
    """
    recent = rows[-5:]
    signs = [1 if r["net_amount"] > 0 else (-1 if r["net_amount"] < 0 else 0) for r in recent]
    same_dir = max(sum(1 for s in signs if s > 0), sum(1 for s in signs if s < 0))

    # Base score from consistency
    if same_dir >= 5:   base = 100
    elif same_dir >= 4: base = 80
    elif same_dir >= 3: base = 60
    elif same_dir >= 2: base = 40
    else:               base = 20

    # Trend bonus/penalty
    if len(recent) >= 4:
        mid = len(recent) // 2
        older = sum(abs(r["net_amount"]) for r in recent[:mid]) / mid
        newer = sum(abs(r["net_amount"]) for r in recent[mid:]) / (len(recent) - mid)
        if older > 0:
            trend_ratio = newer / older
            if trend_ratio >= 1.3:   trend_bonus = 15
            elif trend_ratio >= 1.15: trend_bonus = 10
            elif trend_ratio >= 1.05: trend_bonus = 5
            elif trend_ratio >= 0.85: trend_bonus = 0
            else:                    trend_bonus = -10
        else:
            trend_bonus = 0
    else:
        trend_bonus = 0

    score = max(0, min(100, base + trend_bonus))
    dominant = "流入" if sum(1 for s in signs if s > 0) >= 3 else ("流出" if sum(1 for s in signs if s < 0) >= 3 else "均衡")

    return {
        "score": score,
        "detail": f"{dominant}主导 ({same_dir}/5天同向, 趋势{'加强' if trend_bonus>0 else '减弱' if trend_bonus<0 else '持平'})",
        "same_direction_days": same_dir,
    }


def _score_alignment(rows: List[Dict], price_info: Optional[Dict]) -> Dict:
    """④ Alignment (0-100): Do price and flow agree?

    price_up + flow_in  = ideal (90-100)
    price_down + flow_in = accumulation (65-80)
    price_up + flow_out = distribution (20-40)
    price_down + flow_out = aligned bearish (60-70)
    flat = 50
    """
    if not price_info or len(price_info.get("closes", [])) < 5:
        return {"score": 50, "detail": "价格数据不足 — 使用默认分"}

    closes = price_info["closes"]
    rt = price_info.get("current")

    # Compute 5d price change
    if rt is not None and rt > 0 and len(closes) >= 5:
        price_5d = (rt - closes[-5]) / closes[-5] if closes[-5] else 0
    else:
        price_5d = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] else 0

    # Today's single-day change — the number users read as "今日涨跌". It is
    # carried from _fetch_price_data; the 5-day move below is a different window
    # and MUST NOT be quoted as today's move.
    today_change = price_info.get("today_change")
    today_str = f"{today_change * 100:+.2f}%" if today_change is not None else "N/A"

    recent = rows[-5:]
    net_5d = sum(r["net_amount"] for r in recent)

    price_up = price_5d > 0.005
    price_down = price_5d < -0.005
    flow_in = net_5d > 0
    flow_out = net_5d < 0

    pct_str = f"{price_5d*100:+.1f}%"
    flow_str = f"{net_5d/1e4:+.0f}w"

    if price_up and flow_in:
        # Ideal: price up + money in = confirmed bullish
        mag_score = min(100, 85 + int(abs(price_5d * 100) * 2))
        return {"score": mag_score, "detail": f"量价齐升: 5日价格{pct_str}, 今日{today_str}, 净流入{flow_str} — 主力拉升中"}
    elif price_up and flow_out:
        # Distribution: price up + money out. Tightened 2026-09-01: a single
        # outflow day amid a 5-day price rise is usually intraday noise /
        # profit-taking, NOT institutional distribution — only call it 拉高出货
        # when the outflow is sustained (the two most recent days are both out).
        consec_out = 0
        for r in reversed(recent):
            if r["net_amount"] < 0:
                consec_out += 1
            else:
                break
        if consec_out >= 2:
            return {"score": 25, "detail": f"拉高出货: 5日价格{pct_str}, 今日{today_str}, 净流出{flow_str} — 机构在涨中派发"}
        return {"score": 40, "detail": f"量价背离: 5日价格{pct_str}, 今日{today_str}, 净流出{flow_str} — 但仅连续{consec_out}日流出，未确认拉高出货"}
    elif price_down and flow_in:
        # Accumulation: price down + money in = 打压吸筹
        return {"score": 70, "detail": f"打压吸筹: 5日价格{pct_str}, 今日{today_str}, 净流入{flow_str} — 机构低位拿货"}
    elif price_down and flow_out:
        # Aligned bearish: both down
        return {"score": 30, "detail": f"量价齐跌: 5日价格{pct_str}, 今日{today_str}, 净流出{flow_str} — 趋势下行"}
    else:
        return {"score": 50, "detail": f"量价平淡: 5日价格{pct_str}, 今日{today_str}, 资金{flow_str}"}


def _score_confirmation(rows: List[Dict], ticker: str, config: dict) -> Dict:
    """⑤ Confirmation (0-100): Do other data sources agree?

    Checks: big-order, margin. More sources = higher score.
    (Northbound removed 2026-09-01: per-stock 北向 disclosure ended 2024-08-19;
    ``moneyflow_hsgt`` is market-level holdings, so it could never confirm a
    per-stock signal — the source was dead code that always read 北向中性.)
    """
    recent = rows[-5:]
    net_dir = 1 if sum(r["net_amount"] for r in recent) > 0 else -1
    sources = 0
    details = []

    # 1. Big-order direction
    big_buy = sum(r["buy_elg_amount"] + r["buy_lg_amount"] for r in recent)
    big_sell = sum(r["sell_elg_amount"] + r["sell_lg_amount"] for r in recent)
    big_dir = 1 if big_buy > big_sell else (-1 if big_sell > big_buy else 0)
    if big_dir == net_dir:
        sources += 1
        details.append(f"大单确认({'买' if net_dir>0 else '卖'})")
    else:
        details.append(f"大单背离(买{big_buy/1e4:.0f}w vs 卖{big_sell/1e4:.0f}w)")

    # 2. Margin trend
    try:
        from capitalradar.agents.utils.capital_flow_tools import get_margin_trading
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        margin_raw = str(get_margin_trading.invoke({"ticker": ticker, "start_date": start_date, "end_date": end_date}))
        mdir = _parse_margin_direction(margin_raw)
        if mdir is not None:
            if mdir == net_dir:
                sources += 1
                details.append("融资确认")
            else:
                details.append("融资背离")
        else:
            details.append("融资中性")
    except Exception:
        details.append("融资不可用")

    # Map sources to score. Max reachable is 2 (big-order + margin); 100 only
    # becomes reachable if a third usable confirmation source is added later.
    score_map = {3: 100, 2: 80, 1: 60, 0: 30}
    score = score_map.get(sources, 30)

    return {
        "score": score,
        "detail": "; ".join(details),
        "sources": sources,
    }


def _parse_margin_direction(text: str) -> Optional[int]:
    """Parse margin balance direction. Returns 1 (rising), -1 (falling), or None.

    tushare ``margin_detail`` is returned NEWEST-first, so sort chronologically
    before comparing: ``balances[-1]`` is the most recent day, ``balances[-5]``
    the value ~5 sessions ago. The pre-fix code compared the unsorted list's
    ``[-1]`` vs ``[-5]`` (oldest vs 5th-newest), which INVERTED the trend — a
    rising margin balance (bullish leverage) read as falling. (Verified live on
    600030: margin rising, old code returned -1.)
    """
    lines = text.split("\n")
    hdr = next((i for i, l in enumerate(lines) if "trade_date" in l and "rzye" in l), None)
    if hdr is None:
        return None
    reader = __import__("csv").DictReader(io.StringIO("\n".join(lines[hdr:])))
    dated = []
    for r in reader:
        try:
            dated.append((str(r.get("trade_date", "")).strip(),
                          float(r.get("rzye", 0) or 0)))
        except (ValueError, KeyError):
            pass
    dated.sort(key=lambda d: d[0])  # chronological ascending
    balances = [b for _, b in dated]
    if len(balances) >= 5:
        return 1 if balances[-1] > balances[-5] else -1
    return None


def _score_stage(rows: List[Dict], dims: Dict) -> Dict:
    """⑥ Stage (0-100): Capital flow lifecycle classification."""
    recent_5 = rows[-5:]
    recent_20 = rows[-20:] if len(rows) >= 20 else recent_5

    net_5d = sum(r["net_amount"] for r in recent_5)
    net_20d = sum(r["net_amount"] for r in recent_20)
    net_dir = 1 if net_5d > 0 else -1

    persistence = dims.get("persistence", {})
    scale = dims.get("scale", {})

    # Determine trend
    if len(recent_5) >= 4:
        mid = len(recent_5) // 2
        older = sum(abs(r["net_amount"]) for r in recent_5[:mid]) / mid
        newer = sum(abs(r["net_amount"]) for r in recent_5[mid:]) / (len(recent_5) - mid)
        ratio = newer / older if older > 0 else 1.0
    else:
        ratio = 1.0

    ratio_pct = scale.get("ratio_pct", 5)

    if net_dir > 0:
        if ratio > 1.2 and ratio_pct > 10:
            stage_name = "发力"
            score = 90
            desc = "加速流入，主力强力做多"
        elif ratio > 1.1:
            stage_name = "建仓"
            score = 80
            desc = "流入趋势上升，机构建仓中"
        elif net_20d > 0:
            stage_name = "蓄力"
            score = 70
            desc = "温和持续流入，等待加速"
        else:
            stage_name = "转向"
            score = 60
            desc = "近期由出转入，方向正在改变"
    else:
        if ratio > 1.2 and ratio_pct > 10:
            stage_name = "退出"
            score = 10
            desc = "加速流出，主力清仓"
        elif net_20d * net_dir > 0:
            stage_name = "分布"
            score = 30
            desc = "持续流出，机构派发中"
        else:
            stage_name = "转向"
            score = 50
            desc = "近期由入转出，需警惕"

    return {
        "score": score,
        "detail": f"{stage_name}阶段: {desc}",
        "stage_name": stage_name,
    }


# ── Summary ──────────────────────────────────────────────────────────────

def _build_summary(ticker: str, result: Dict) -> str:
    """Build a one-line Chinese summary."""
    score = result.get("score", 0)
    verdict = result.get("verdict", "error")
    dims = result.get("dimensions", {})

    labels = {v: k for k, v in {
        "confirmed": "主力确认",
        "moderate": "有主力参与",
        "weak": "信号较弱",
        "no_signal": "无显著主力",
        "error": "数据错误",
    }.items()}
    label = labels.get(verdict, verdict)

    # Add key dimension detail
    direction = dims.get("direction", {})
    alignment = dims.get("alignment", {})

    parts = [f"{ticker}: {label} ({score}分)"]
    if direction:
        parts.append(direction.get("detail", "").split("(")[0].strip())
    if alignment and alignment.get("score", 50) < 50:
        parts.append(alignment.get("detail", ""))

    return " — ".join(p for p in parts if p)
