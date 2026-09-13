"""Batch preliminary value+flow analysis for sector scan candidates.

Redesigned agent: dual-dimension screening — valuation (PE/PB) × capital flow
(main force net inflow trends). Identifies undervalued stocks with institutional
accumulation, plus MACD convergence for entry timing.

This is a LIGHTWEIGHT pre-screen — NOT the full 7-analyst + debate pipeline.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from capitalradar.dataflows.eastmoney_sector import (
    get_stock_daily,
    calc_macd,
    detect_golden_cross,
    detect_macd_convergence,
    get_moneyflow_trend,
    get_daily_basic_batch,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data gathering (one stock at a time)
# ---------------------------------------------------------------------------


def _gather_stock_data(candidate: dict, valuation_map: dict[str, dict]) -> dict:
    """Gather valuation + flow + technical data for one candidate stock.

    Args:
        candidate: Dict with ts_code, name, score, market_cap, etc. from scan.
        valuation_map: {ts_code: {close, total_mv, pe, pb}} from batch fetch.

    Returns dict with all gathered fields.
    """
    ts_code = candidate["ts_code"]
    name = candidate.get("name", "")
    code = candidate.get("code", "")
    sector = candidate.get("_sector", "")

    val = valuation_map.get(ts_code, {})
    pe = val.get("pe", 0) or 0
    pb = val.get("pb", 0) or 0
    market_cap = val.get("total_mv", 0) or candidate.get("market_cap", 0) or 0
    close = val.get("close", 0) or candidate.get("close", 0) or 0

    info = {
        "ts_code": ts_code,
        "code": code,
        "name": name,
        "sector": sector,
        "score": candidate.get("score", 0),
        "close": close,
        "market_cap": market_cap,
        "pe": pe,
        "pb": pb,
    }

    # 1. K-line → MACD, RSI, volume, price position
    try:
        klines = get_stock_daily(ts_code, days=90)
        if len(klines) >= 30:
            klines = calc_macd(klines)
            last = klines[-1]
            info["macd_dif"] = round(last.get("dif", 0) or 0, 4)
            info["macd_dea"] = round(last.get("dea", 0) or 0, 4)
            info["macd_bar"] = round(last.get("macd", 0) or 0, 4)

            # MACD convergence (即将金叉)
            has_conv, conv_strength = detect_macd_convergence(klines, lookback=5)
            info["macd_convergence"] = has_conv
            info["convergence_strength"] = round(conv_strength, 4)

            # Golden cross (already happened)
            has_cross, cross_strength = detect_golden_cross(klines, lookback=5)
            info["golden_cross"] = has_cross
            info["cross_strength"] = round(cross_strength, 4)

            # RSI(14)
            closes = [k["close"] for k in klines]
            gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
            losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
            if len(gains) >= 14:
                avg_gain = sum(gains[-14:]) / 14
                avg_loss = sum(losses[-14:]) / 14
                if avg_loss == 0:
                    info["rsi_14"] = 100.0 if avg_gain > 0 else 50.0
                else:
                    info["rsi_14"] = round(100 - 100 / (1 + avg_gain / avg_loss), 1)
            else:
                info["rsi_14"] = 50.0

            # Volume breakout: recent 5-day avg vs 20-day avg
            volumes = [k["volume"] for k in klines]
            vol_5d = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1
            vol_20d = sum(volumes[-25:-5]) / 20 if len(volumes) >= 25 else vol_5d
            info["volume_ratio"] = round(vol_5d / vol_20d, 2) if vol_20d > 0 else 1.0

            # Price position: distance from 60-day high / low
            if len(closes) >= 60:
                high60 = max(closes[-60:])
                low60 = min(closes[-60:])
                range60 = high60 - low60
                info["price_position"] = round((closes[-1] - low60) / range60 * 100, 1) if range60 > 0 else 50.0
            else:
                info["price_position"] = 50.0
        else:
            info.update(_default_tech_fields())
    except Exception as e:
        logger.warning("K-line analysis failed for %s: %s", ts_code, e)
        info.update(_default_tech_fields())

    # 2. Money flow trend (multi-day)
    try:
        mf = get_moneyflow_trend(ts_code, days=10)
        info["main_net_inflow"] = round(mf.get("net_amount", 0) or 0, 2)
        info["flow_cum_3d"] = mf.get("cum_3d", 0)
        info["flow_cum_5d"] = mf.get("cum_5d", 0)
        info["flow_cum_10d"] = mf.get("cum_10d", 0)
        info["consecutive_inflow"] = mf.get("consecutive_inflow", 0)
        info["big_order_divergence"] = mf.get("big_order_divergence", False)
        info["daily_flows"] = mf.get("daily_flows", [])
    except Exception as e:
        logger.warning("Money flow trend failed for %s: %s", ts_code, e)
        info.update(_default_flow_fields())

    # 3. MX real-time 主力资金 (DDX/DDY/DDZ) — 东方财富当日实时快照.
    # tushare moneyflow is EOD/delayed; MX gives the same-day live 主力 signal.
    # Best-effort: absent on missing MX_APIKEY / API failure — never blocks.
    try:
        from capitalradar.dataflows import mx_client
        _d = mx_client.query(f"{ts_code} 主力资金流向")
        _m = mx_client.extract_snapshot_metrics(_d)
        _ddx = next((v for k, v in _m.items() if "当日DDX" in k), None)
        _main = next((v for k, v in _m.items() if "主力净流入" in k), None)
        if _main is not None or _ddx is not None:
            info["mx_main_net"] = _main  # 万元, 已按单位归一
            info["mx_ddx"] = _ddx
    except Exception:
        pass

    return info


def _default_tech_fields() -> dict:
    return {
        "macd_dif": 0, "macd_dea": 0, "macd_bar": 0,
        "macd_convergence": False, "convergence_strength": 0,
        "golden_cross": False, "cross_strength": 0,
        "rsi_14": 50.0, "volume_ratio": 1.0, "price_position": 50.0,
    }


def _build_prompt(stocks_data: list[dict], language: str = "Chinese", context: str = "default") -> str:
    """Build the LLM prompt from gathered stock data, in specified language.

    context: "smart_scan" | "value" | "sector_scan" | "default"
      Controls the analysis focus. Smart Scan emphasizes RPS+momentum,
      Value emphasizes PE/PB+fund flow, etc.
    """
    is_cn = language.lower().startswith("zh") or language.lower() in ("chinese", "cn")

    contexts = {
        "smart_scan": {
            "cn": [
                "你是一位量化分析师。以下股票已通过 RPS(相对价格强度)+动量+量能+资金流三维评分筛选。",
                "请重点分析：1) RPS值是否真正反映市场领先地位；2) 动量是否可持续（MACD/RSI仅作辅助验证，不主导判断）；",
                "3) 资金流是否支持当前价格（主力净流入与市值匹配度）；4) 哪几只最值得送入CapitalRadar深度分析。",
                "**所有输出必须使用中文**。",
            ],
            "en": [
                "You are a quantitative analyst. These stocks passed RPS+momentum+volume+capital flow scoring.",
                "Focus on: 1) Is RPS truly reflecting market leadership? 2) Is momentum sustainable?",
                "3) Does capital flow support current price? 4) Which stocks are most worth deep analysis?",
                "**All output must be in English**.",
            ],
        },
        "value": {
            "cn": [
                "你是一位价值投资分析师。以下股票已通过低估值(PE+PB)+主力资金转向+底部放量突破筛选。",
                "请重点分析：1) 估值是否真正低估（PE/PB与行业对比）；2) 主力资金转向是否可信（是真实反转还是诱多）；",
                "3) 底部突破是否有量能配合；4) 哪几只最值得深度分析。",
                "**所有输出必须使用中文**。",
            ],
            "en": [
                "You are a value investment analyst. These stocks passed low-valuation+f capital turnaround+bottom breakout screening.",
                "Focus on: 1) Is valuation truly cheap? 2) Is capital turnaround genuine?",
                "3) Is the breakout supported by volume? 4) Which stocks are most worth deep analysis?",
                "**All output must be in English**.",
            ],
        },
        "sector_scan": {
            "cn": [
                "你是一位多策略选股分析师。以下股票通过了自定义策略条件筛选。",
                "请以批判性角度验证每个策略条件的可靠性，判断是否存在主力诱多嫌疑，并指出哪几只最值得深度分析。",
                "**所有输出必须使用中文**。",
            ],
            "en": [
                "You are a multi-strategy stock analyst. These stocks passed custom strategy conditions.",
                "Critically verify each condition's reliability, check for manipulation, and identify the most promising candidates.",
                "**All output must be in English**.",
            ],
        },
        "default": {
            "cn": [
                "你是一位资深主力资金分析师。以下是候选股的资金和技术数据。",
                "请基于数据给出批量初步分析，验证各指标的有效性，并指出最值得深度分析的股票。",
                "**所有输出必须使用中文**。",
            ],
            "en": [
                "You are a senior capital flow analyst. Below is fund-flow and technical data for screened candidates.",
                "Provide batch analysis, verify indicator validity, and identify the most promising stocks.",
                "**All output must be in English**.",
            ],
        },
    }

    ctx = contexts.get(context, contexts["default"])
    lang_key = "cn" if is_cn else "en"
    lines = ctx[lang_key].copy()
    lines.append("")

    lines.append("## 候选股数据")
    lines.append("")
    if is_cn:
        lines.append("| # | 股票 | 代码 | 行业 | 评分 | PE | PB | 市值(亿) | RSI | "
                      "主力净流入(万) | 5日累计(万) | 连续流入(天) | 大单背离 | "
                      "MACD收敛 | 量比 | 价格位置% | 实时DDX |")
        lines.append("|----|------|------|------|------|----|----|----------|-----|"
                      "-------------|-----------|------------|---------|"
                      "---------|------|----------|------|")
    else:
        lines.append("| # | Stock | Code | Sector | Score | PE | PB | Cap(B) | RSI | "
                      "Net Flow(10k) | Cum5d(10k) | ConsecIn | BigDiv | "
                      "MACD Conv | VolRatio | PricePos% | Live DDX |")
        lines.append("|----|-------|------|--------|-------|----|----|--------|-----|"
                      "--------------|------------|----------|--------|"
                      "----------|----------|-----------|----------|")

    for i, s in enumerate(stocks_data, 1):
        cap_yi = f"{s.get('market_cap', 0) / 1e8:.0f}" if s.get("market_cap") else "N/A"
        inflow_wan = f"{s.get('main_net_inflow', 0) / 1e4:.1f}" if s.get("main_net_inflow") else "0"
        cum5_wan = f"{s.get('flow_cum_5d', 0) / 1e4:.1f}" if s.get("flow_cum_5d") else "0"
        pe_str = f"{s.get('pe', 0):.1f}" if s.get("pe") and s["pe"] > 0 else "N/A"
        pb_str = f"{s.get('pb', 0):.2f}" if s.get("pb") and s["pb"] > 0 else "N/A"
        conv = "即将金叉" if s.get("macd_convergence") else ("已金叉" if s.get("golden_cross") else "—")
        divergence = "!" if s.get("big_order_divergence") else ""
        rsi = s.get("rsi_14", 50)
        vol_ratio = s.get("volume_ratio", 1.0)
        pos = s.get("price_position", 50)
        # 实时DDX (妙想当日) — '—' when MX unavailable
        mx_ddx = s.get("mx_ddx")
        ddx_str = "—" if mx_ddx is None else f"{float(mx_ddx):+.3f}"

        lines.append(
            f"| {i} | {s['name']} | {s['ts_code']} | {s.get('sector', '')} | "
            f"{s.get('score', 0):.2f} | {pe_str} | {pb_str} | {cap_yi} | {rsi} | "
            f"{inflow_wan} | {cum5_wan} | {s.get('consecutive_inflow', 0)} | "
            f"{divergence} | {conv} | {vol_ratio} | {pos}% | {ddx_str} |"
        )

    lines.append("")

    # Industry benchmarks
    if industry_medians:
        if is_cn:
            lines.append("## 行业估值中枢（中位数）")
            lines.append("")
            lines.append("| 行业 | PE中位 | PB中位 | 样本数 |")
            lines.append("|------|--------|--------|--------|")
        else:
            lines.append("## Industry Valuation Medians")
            lines.append("")
            lines.append("| Industry | PE Median | PB Median | Count |")
            lines.append("|----------|-----------|-----------|-------|")
        for sec, m in sorted(industry_medians.items()):
            lines.append(f"| {sec} | {m['pe_median']:.1f} | {m['pb_median']:.2f} | {m['count']} |")
        lines.append("")

    # Output format instructions
    lines.append("---")
    if is_cn:
        lines.append("请按以下结构输出分析报告：")
        lines.append("")
        lines.append("## 双维度初步分析报告")
        lines.append("")
        lines.append("### 一、估值排名（PE 从低到高，标注 vs 行业中枢）")
        lines.append("选出 PE 最低的 5 只，判断是否真低估还是价值陷阱。")
        lines.append("")
        lines.append("### 二、资金排名（5日累计净流入/市值强度排序）")
        lines.append("选出资金信号最强的 5 只，标注连续流入天数、是否大单背离。参考【实时DDX】列判断当日资金方向（正=主力净流入，负=流出），区分\"持续吸筹\"与\"高位接力\"。")
        lines.append("")
        lines.append("### 三、双维度交叉矩阵")
        lines.append("将全部股票归入四象限：")
        lines.append("- **Leading（价值+资金双优）**: 相对低估 + 主力持续买入")
        lines.append("- **Value Trap（价值陷阱）**: 低PE/PB 但无资金关注")
        lines.append("- **Speculation（资金炒作）**: 主力买入但估值已高")
        lines.append("- **Lagging（双弱）**: 高估且无资金关注")
        lines.append("")
        lines.append("### 四、MACD 即将金叉信号")
        lines.append("列出所有出现 MACD 收敛（即将金叉）或刚金叉的标的，标注信号强度和可信度。")
        lines.append("")
        lines.append("### 五、精选推荐（3-5 只，按优先级排序）")
        lines.append("综合标准：估值合理 + 主力连续流入2天以上 + MACD即将金叉 + 量比>1")
        lines.append("每只给出：1) 双维度亮点 2) 风险提示 3) 建议观察价位")
        lines.append("")
        lines.append('**重要**: 如果某只股票 PE 极低但资金持续流出，必须标注为"可能的价值陷阱"。')
        lines.append('如果某只股票资金大幅流入但 PE 极高，必须标注为"资金炒作风险"。')
    else:
        lines.append("Output format:")
        lines.append("")
        lines.append("## Dual-Dimension Preliminary Analysis")
        lines.append("")
        lines.append("### 1. Valuation Ranking (PE low-to-high, vs industry median)")
        lines.append("Top 5 by lowest PE. Distinguish genuine undervaluation from value traps.")
        lines.append("")
        lines.append("### 2. Flow Ranking (by 5-day cumulative flow / market cap intensity)")
        lines.append("Top 5 by strongest flow signal. Note consecutive inflow days and big-order divergence. Use the Live DDX column for the same-day direction (positive=inflow, negative=outflow) to separate steady accumulation from high-position churn.")
        lines.append("")
        lines.append("### 3. Cross Matrix (all stocks in 4 quadrants)")
        lines.append("- Leading: undervalued + institutional buying")
        lines.append("- Value Trap: cheap but no flow")
        lines.append("- Speculation: strong flow but expensive")
        lines.append("- Lagging: expensive + no flow")
        lines.append("")
        lines.append("### 4. MACD Convergence Signals")
        lines.append("All stocks with convergence (about to golden-cross) or recent cross.")
        lines.append("")
        lines.append("### 5. Top Picks (3-5, priority-ordered)")
        lines.append("Criteria: reasonable valuation + 2d+ consecutive inflow + MACD convergence + vol>1")
        lines.append("Each: (a) dual-dimension highlights (b) risk alert (c) suggested watch price level")

    return "\n".join(lines)


def run_batch_analysis(candidates: list[dict], llm_client=None, context: str = "default") -> dict:
    """Run batch preliminary analysis on selected candidates.

    Args:
        candidates: List of candidate dicts (from scan results).
        llm_client: Optional pre-created LLM client. If None, creates one.
        language: Output language ("Chinese" or "English").

    Returns:
        {"report": str, "stocks_analyzed": int, "error": str or None}
    """
    if not candidates:
        return {"report": "", "stocks_analyzed": 0, "error": "No candidates provided"}

    # Step 1: batch fetch PE/PB for all candidates (single API call)
    ts_codes = [c["ts_code"] for c in candidates]
    valuation_map: dict[str, dict] = {}
    try:
        valuation_map = get_daily_basic_batch(ts_codes)
        logger.info("Batch valuation fetched for %d/%d stocks", len(valuation_map), len(ts_codes))
    except Exception as e:
        logger.warning("Batch valuation fetch failed, continuing without PE/PB: %s", e)

    # Step 2: gather per-stock data in parallel
    logger.info("Gathering data for %d stocks...", len(candidates))
    stocks_data = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_gather_stock_data, c, valuation_map): c
            for c in candidates
        }
        for future in as_completed(futures):
            try:
                data = future.result()
                if data:
                    stocks_data.append(data)
            except Exception as e:
                logger.warning("Data gathering failed for a stock: %s", e)

    if not stocks_data:
        return {"report": "", "stocks_analyzed": 0,
                "error": "Failed to gather data for all stocks"}

    # Sort by score descending
    stocks_data.sort(key=lambda s: s.get("score", 0), reverse=True)

    # Step 2: build prompt and call LLM
    prompt = _build_prompt(stocks_data, context=context)

    if llm_client is None:
        from capitalradar.llm_clients import create_llm_client, resolve_role_llm
        from capitalradar.default_config import DEFAULT_CONFIG
        cfg = DEFAULT_CONFIG
        provider, model, _ = resolve_role_llm(cfg, "quick", model_default="deepseek-v4-flash")
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=cfg.get("backend_url"),
        )
        llm = client.get_llm()
    else:
        llm = llm_client.get_llm() if hasattr(llm_client, "get_llm") else llm_client

    try:
        from langchain_core.messages import HumanMessage
        response = llm.invoke([HumanMessage(content=prompt)])
        report = response.content if hasattr(response, "content") else str(response)
        return {
            "report": report,
            "stocks_analyzed": len(stocks_data),
            "error": None,
            "gathered": stocks_data,
        }
    except Exception as e:
        logger.exception("LLM call failed in batch analysis")
        return {
            "report": prompt,
            "stocks_analyzed": len(stocks_data),
            "error": str(e),
        }


def run_batch_analysis_sse(candidates: list[dict], llm_client=None, language: str = "Chinese", context: str = "default"):
    """Generator variant: yields SSE progress events + final result.

    Yields dicts: {"event": "progress", "current": N, "total": M, "stock": name}
                  {"event": "llm", "message": "..."}
                  {"event": "done", "report": str, "stocks_analyzed": int}
                  {"event": "error", "message": str}
    """
    if not candidates:
        yield {"event": "error", "message": "No candidates provided"}
        return

    total = len(candidates)

    # Step 1: batch fetch PE/PB
    ts_codes = [c["ts_code"] for c in candidates]
    valuation_map: dict[str, dict] = {}
    try:
        valuation_map = get_daily_basic_batch(ts_codes)
    except Exception as e:
        logger.warning("Batch valuation fetch failed: %s", e)

    # Step 2: gather per-stock data in parallel
    gathered = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_gather_stock_data, c, valuation_map): c
            for c in candidates
        }
        for future in as_completed(futures):
            c = futures[future]
            try:
                data = future.result()
                if data:
                    gathered.append(data)
            except Exception as e:
                logger.warning("Data gathering failed for %s: %s",
                               c.get("ts_code"), e)
            yield {
                "event": "progress",
                "current": len(gathered),
                "total": total,
                "stock": c.get("name", c.get("ts_code", "")),
            }

    if not gathered:
        yield {"event": "error", "message": "Failed to gather data for all stocks"}
        return

    gathered.sort(key=lambda s: s.get("score", 0), reverse=True)
    total = len(gathered)

    # Compute industry medians
    industry_medians = _compute_industry_medians(gathered)

    if llm_client is None:
        from capitalradar.llm_clients import create_llm_client, resolve_role_llm
        from capitalradar.default_config import DEFAULT_CONFIG
        cfg = DEFAULT_CONFIG
        provider, model, _ = resolve_role_llm(cfg, "quick", model_default="deepseek-v4-flash")
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=cfg.get("backend_url"),
        )
        llm = client.get_llm()
    else:
        llm = llm_client.get_llm() if hasattr(llm_client, "get_llm") else llm_client

    # For large batches, split into sub-batches
    BATCH_SIZE = 20
    if total <= BATCH_SIZE:
        yield {"event": "llm", "message": f"Data gathered for {total} stocks, calling LLM..."}
        prompt = _build_prompt(gathered, language=language, context=context)
        try:
            from langchain_core.messages import HumanMessage
            response = llm.invoke([HumanMessage(content=prompt)])
            report = response.content if hasattr(response, "content") else str(response)
            yield {"event": "done", "report": report, "stocks_analyzed": total}
        except Exception as e:
            logger.exception("LLM call failed in batch analysis")
            yield {"event": "error", "message": str(e)}
    else:
        sub_batches = [gathered[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
        all_reports = []
        for bi, batch in enumerate(sub_batches):
            batch_label = f"Batch {bi + 1}/{len(sub_batches)} ({len(batch)} stocks)"
            yield {"event": "llm", "message": f"Analyzing {batch_label}..."}
            prompt = _build_prompt(batch, industry_medians, language=language)
            try:
                from langchain_core.messages import HumanMessage
                response = llm.invoke([HumanMessage(content=prompt)])
                report = response.content if hasattr(response, "content") else str(response)
                all_reports.append(f"## {batch_label}\n\n{report}")
            except Exception as e:
                all_reports.append(f"## {batch_label}\n\nError: {e}")
        combined = "\n\n---\n\n".join(all_reports)
        yield {"event": "done", "report": combined, "stocks_analyzed": total}
