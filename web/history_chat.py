"""History-based Portfolio Manager chat with full data tool access.

Provides the SSE generator and PM agent factory for multi-turn conversations
about past CapitalRadar analyses. The PM agent is bound with all dataflows
tools and receives the full analysis context from stored JSON.
"""

import json
import logging
import re
import threading
from datetime import datetime
from typing import Generator, Optional

from web.results_store import load_full_state, get_result, get_chat_messages, save_chat_message, get_thread_run_ids

logger = logging.getLogger(__name__)


def _extract_tickers(text_str: str) -> list[str]:
    """Extract Chinese A-share ticker codes from natural language text."""
    candidates = re.findall(r'\b\d{6}\b', text_str)
    tickers = []
    for c in candidates:
        if c[0] in '036':
            tickers.append(c)
    suffixed = re.findall(r'\b\d{6}\.(?:SH|SZ|SS)\b', text_str, re.IGNORECASE)
    for s in suffixed:
        base = s.split('.')[0]
        if base not in tickers:
            tickers.append(base)
    return tickers


# Real-time quote text parsing for the 数据核验 footer. Both formats occur in
# the vendor chain: Tencent `**Change**: -0.06 / 2.28%` (涨跌额 / 涨跌幅) and
# akshare/xueqiu `Change: +0.5%`. The 涨跌幅 is the LAST %-terminated number on
# the Change line.
# The regexes live in capitalradar/dataflows/quote_text.py so smart_money_score
# and history_chat share one canonical parse (the same quote text feeds both).
from capitalradar.dataflows.quote_text import parse_quote_change_pct as _parse_quote_change_pct
from capitalradar.dataflows.quote_text import parse_quote_price as _parse_quote_price


def _verify_advisor_facts(final_text: str, config: dict) -> str:
    """Best-effort 数据核验 footer for a completed advisory answer.

    Re-fetches the realtime quote for every A-share ticker mentioned in the
    answer and lists today's 涨跌幅 as ground truth, so a stale or mislabeled
    figure (e.g. a 5-day change quoted as today's move) is immediately visible
    next to the real number. Pure transparency — the answer text is never
    rewritten. Returns '' when nothing is verifiable or the feature is
    disabled, and never raises: a verification failure must not break the
    stream that already produced the answer.
    """
    if not final_text or not final_text.strip():
        return ""
    if not config.get("advisory_data_verify", True):
        return ""

    # _extract_tickers only matches A-share codes (first digit 0/3/6) — the
    # realtime-quote chain is a reliable source for CN stocks on this machine.
    seen, uniq = set(), []
    for t in _extract_tickers(final_text):
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    uniq = uniq[:10]
    if not uniq:
        return ""

    from concurrent.futures import ThreadPoolExecutor
    from capitalradar.dataflows.interface import route_to_vendor

    def _one(code: str):
        try:
            raw = str(route_to_vendor("get_realtime_quote", symbol=code))
            if raw.startswith("# SKIP_VENDOR"):
                return code, None, None
            return code, _parse_quote_change_pct(raw), _parse_quote_price(raw)
        except Exception:  # noqa: BLE001 — best effort
            return code, None, None

    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            rows = list(ex.map(_one, uniq))
    except Exception:  # noqa: BLE001 — never break the stream
        logger.warning("advisor data verification pool failed", exc_info=True)
        return ""

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "",
        "---",
        "**📌 数据核验**（实时行情复核，若与上文数字不一致，以实时为准）:",
    ]
    for code, chg, price in rows:
        if chg is None:
            lines.append(f"- {code}: 实时行情暂不可用，请以交易软件为准")
        else:
            price_txt = f"，现价 {price:.2f}" if price else ""
            lines.append(f"- {code}: 今日涨跌 **{chg:+.2f}%**{price_txt}（{ts}）")
    return "\n".join(lines)


def build_pm_system_prompt(states: list[dict], config: dict, lang: str = "English") -> str:
    """Build the Portfolio Manager system prompt from one or more stored analysis states."""
    if not states:
        today = datetime.now()
        return (
            f"You are the **CapitalRadar Advisor**, an AI investment consultant powered by CapitalRadar's multi-agent analysis framework.\n\n"
            f"**Current date**: {today.strftime('%Y-%m-%d')} (weekday: {today.strftime('%A')})\n\n"
            f"## Core Capability: CapitalRadar Deep Analysis\n\n"
            f"CapitalRadar runs a 7-analyst team to produce comprehensive stock analysis:\n"
            f"- **Capital Flow Analyst** (core anchor) - detects institutional money movement\n"
            f"- **Market Analyst** - technical indicators and price action\n"
            f"- **Sentiment Analyst** - social media and retail sentiment\n"
            f"- **News Analyst** - news coverage and media manipulation detection\n"
            f"- **Fundamentals Analyst** - financial statements and valuation\n"
            f"- **Competitor & Partner Analysts** - industry context\n\n"
            f"The analysts debate (Bull vs Bear), then a Portfolio Manager makes the final BUY/HOLD/SELL decision.\n\n"
            f"## Your Process (MANDATORY - follow exactly)\n\n"
            f"### Step 1: Check History\n"
            f"ALWAYS call query_past_analyses FIRST to check if we have prior data for the ticker.\n\n"
            f"### Step 2: Report What You Found\n"
            f"**If analyses exist**: Tell the user exactly what's available:\n"
            f"- How many analyses, on which dates\n"
            f"- What ratings/signals were given\n"
            f"- Which reports are available (Capital Flow, Market, News, etc.)\n"
            f"- Offer to: (a) reference past analysis, (b) compare across dates, (c) run a fresh update\n\n"
            f"**If NO analyses exist**: Tell the user clearly and offer TWO options:\n"
            f"1. **Full Deep Analysis** (7 analysts + debate, takes ~3-5 minutes, most thorough)\n"
            f"2. **Quick Assessment** (capital flow + web search + real-time data + indicators, takes ~30 seconds, with core capital flow analysis)\n"
            f"Explain the trade-off and ASK which they prefer. NEVER run analysis without explicit consent.\n\n"
            f"### Step 3: Execute Based on User Choice\n"
            f"- If they choose **Full Deep Analysis**: call run_full_analysis(ticker) directly — it runs the full 7-analyst pipeline, saves results to History, and returns the final decision. Tell the user it takes 3-5 minutes.\n"
            f"- If they choose **Quick Assessment**: use get_money_flow FIRST (core), then get_moneyflow_multi_horizon (多周期主力态度: 当日/5/20/60日净流入及构成), then web_search_current + get_indicators + get_realtime_quote\n"
            f"- If they ask a general question (not stock-specific): answer directly\n\n"
            f"### Step 4: Smart Screening (Stock Discovery)\n"
            f"- When user asks 'what stocks should I buy' or similar, suggest run_smart_screening with their natural language criteria\n"
            f"- run_smart_screening generates a custom strategy, screens A-shares, and returns ranked candidates with explanations\n"
            f"- Use get_top_gainers to quickly discover today's hottest stocks across the ENTIRE market (~5000 stocks) — no industry pre-selection needed\n"
            f"- Use get_multi_factor_ranking when user wants stocks that score high on BOTH price momentum AND institutional capital flow\n\n"
            f"### Step 5: Check Smart Money Score\n"
            f"- Use get_smart_money_score(ticker) to see a stock's 6-dimension institutional score (0-100)\n"
            f"- ≥70=主力确认, 50-69=有主力, 30-49=信号弱, <30=无主力 — <50 auto-downgrade\n"
            f"- **资金面纪律**: SMS<60 一律不构成买入依据；SMS 输出含「数据不可用/不出分/数据降级」字样时，按数据缺失处理，不得据此给出看空或买入结论，先用 verify_moneyflow 交叉验证资金面；SMS 高分但工具显示「数据可信度低」时，必须先用 verify_moneyflow / 东财逐日数据复核资金流后，再谈买入；**主力态度看多周期**——判断主力方向/建仓出货/回流须用 get_moneyflow_multi_horizon 的当日/5/20/60日净流入，禁止只凭当日单日数据下结论；引用'近N日'窗口时N日必须为最后N个连续交易日，禁止自行挑选强势日拼凑（302132曾把跨两个交易日的08-26/08-27/09-01称'近3日+10827万'，真实最后3日超大单仅+3947万）.\n\n"
            f"### Step 6: Review Past Picks\n"
            f"- Use query_pick_performance to see how previous recommendations performed — check win rate and returns\n\n"
            f"## Important Rules\n"
            f"- ALWAYS check history (query_past_analyses) before answering stock questions\n"
            f"- ALWAYS ask for user consent before calling run_full_analysis\n"
            f"- When user asks 'what stocks should I buy' or similar, suggest run_smart_screening with their criteria\n"
            f"- ALWAYS get smart money score (get_smart_money_score) before recommending any stock\n"
            f"- NEVER fabricate analysis data - only report what tools return\n"
            f"- 资金面纪律: SMS<60 不构成买入依据；SMS 报「数据不可用」即按数据缺失处理（不是看空信号），用 verify_moneyflow 复核后再下结论；主力方向看 get_moneyflow_multi_horizon 多周期（当日/5/20/60日），勿只凭单日；'近N日'必须为最后N个连续交易日，禁止挑强势日拼凑窗口\n"
            f"- Be transparent about what data is available vs what you are fetching live\n"
            f"- When run_full_analysis completes, it's auto-saved to History — tell the user\n"
            f"- You have SKILL GENERATION tools: list_pending_experiences, approve_experience, reject_experience, generate_analyst_skill. When the user wants to review/approve extracted experiences and form them into an Analyst Skill, use these tools in order: list → discuss → approve/reject → generate.\n"
            f"- Current date awareness: use the date above for time-sensitive questions\n"
            f"- Write in {lang if lang else 'Chinese'}\n"
        )
    n = len(states)
    primary_ticker = states[0].get("ticker", "N/A") if states else "N/A"

    # Context budget: more generous for multi-analysis to enable comparison
    max_per_report = 1800 if n > 1 else 2400
    max_decision_chars = 3000 if n > 1 else 5000

    prompt = (
        f"You are the **CapitalRadar Advisor**, the central AI investment consulting intelligence. "
        f"You evolved from the Portfolio Manager role into a proactive advisory agent.\n\n"
        f"## Your Process\n\n"
        f"When asked about a stock:\n"
        f"1. **Introduce your process**: Briefly explain what you will do (check history, search web, possibly run analysis)\n"
        f"2. **Check history**: Use query_past_analyses to see if we have prior data\n"
        f"3. **Search web**: Use web_search_current for latest news and ratings\n"
        f"4. **Ask permission**: If no prior analysis exists, ASK before running smart_reanalyze\n"
        f"5. **Be transparent**: Always tell the user what you are doing at each step\n\n"
        f"The user is reviewing {n} past analysis run(s) for **{primary_ticker}** and wants to ask follow-up questions.\n\n"
    )

    # --- Decision Timeline (multi-analysis only) ---
    if n > 1:
        prompt += "## Decision Timeline\n\n"
        prompt += "| # | Date | Decision Highlights |\n"
        prompt += "|---|---|---|\n"
        for i, state in enumerate(states, 1):
            date = state.get("trade_date", "N/A")
            final = str(state.get("final_trade_decision", ""))[:300]
            # Extract first meaningful sentence for the timeline
            snippet = final.split(".")[0] if "." in final else final[:150]
            prompt += f"| {i} | {date} | {snippet} |\n"
        prompt += "\n"

    # --- Analysis Details ---
    for i, state in enumerate(states, 1):
        ticker = state.get("ticker", "N/A")
        date = state.get("trade_date", "N/A")
        prompt += f"## Analysis #{i}: {ticker} on {date}\n\n"

        final = state.get("final_trade_decision", "")
        if final:
            prompt += f"### Final Decision\n{str(final)[:max_decision_chars]}\n\n"

        from capitalradar.catalog import ordered_roles
        report_keys = [(role.report_key, role.label) for role in ordered_roles()]
        for key, label in report_keys:
            report = state.get(key, "")
            if report:
                prompt += f"### {label}\n{str(report)[:max_per_report]}\n\n"

        metrics = state.get("key_metrics", {})
        if metrics:
            prompt += "### Key Metrics\n"
            for analyst, kv in metrics.items():
                if kv:
                    prompt += f"- **{analyst}**: {json.dumps(kv, ensure_ascii=False)}\n"
            prompt += "\n"

    # --- Instructions ---
    prompt += "---\n"
    prompt += (
        "You have access to live data tools (stock prices, indicators, news, "
        "money flow, fundamentals, intraday data, real-time quotes) AND "
        "run_full_analysis(ticker) which runs a fresh full 7-analyst pipeline "
        "and auto-saves results to History.\n\n"
        "Guidelines:\n"
        "- Reference specific data from the analysis reports when relevant.\n"
        "- If the user wants a fresh analysis, offer run_full_analysis (takes 3-5 min) — ask permission first.\n"
    )
    if n > 1:
        prompt += (
            "- **Multi-Analysis Comparison**: These analyses cover the same stock "
            "at different points in time. Compare decisions across dates — identify "
            "trend changes, explain what factors drove rating shifts, and note which "
            "analysts' views changed between analyses.\n"
            "- When the user asks about the stock's trajectory, synthesize insights "
            "across all analysis dates rather than treating each in isolation.\n"
        )
    prompt += (
        "- Call tools to fetch current data when comparing past analyses vs present conditions.\n"
        "- Be concise but thorough. Acknowledge limitations honestly.\n"
        "- If tool calls fail, explain what you tried to fetch and why.\n"
        "- You have SKILL GENERATION tools: list_pending_experiences, approve_experience, reject_experience, generate_analyst_skill. When the user wants to review/approve extracted experiences and form them into an Analyst Skill, use these tools in order: list → discuss → approve/reject → generate.\n"
        "- You have TWO-PASS RECORD tools: get_twopass_records (list saved 二次分析 runs) and get_twopass_record_detail(id) (full per-stock data + LLM conclusions). When the user asks about 二次分析/看多候选 or wants a final recommendation from a two-pass run, call get_twopass_records first, then deep-dive with get_twopass_record_detail, cross-validate with capital-flow/quote tools, and give at most 5 final picks with reasons, position size %, and stop-loss.\n"
        f"- Write in {lang}.\n"
    )
    # Append verified experiences from the library
    from capitalradar.advisory.experience_store import get_active_experiences, log_injection
    try:
        active_exp = get_active_experiences()
        if active_exp:
            prompt += "\n## Experience Library (overridable, {N} items)\n".format(N=len(active_exp))
            prompt += "Use these when applicable; explain if not applicable.\n\n"
            for exp in active_exp:
                cat = exp.get("category", "other")
                tag = exp.get("lesson_abstract", "")
                label = "[{tag}] ".format(tag=tag) if tag else ""
                prompt += "- {label}{content} (tag: {cat})\n".format(label=label, content=exp.get("content",""), cat=cat)
            prompt += "\n"
    except Exception:
        pass
    return prompt


def build_tool_set(config: dict, llm=None):
    """Return the full set of data tools available to the PM.
    llm is optional — only needed when including Pick Agent screening tools."""
    from functools import partial
    from langchain_core.tools import tool
    from typing import Annotated
    from web.results_store import search_analyses as _search_analyses, load_full_state
    from capitalradar.agents.utils.core_stock_tools import get_stock_data

    # Pre-bind config so inner tools don't rely on closure (LangChain may
    # serialize tools, losing the closure context).
    _search = partial(_search_analyses, config)
    _load_state = partial(load_full_state, config)
    from capitalradar.agents.utils.technical_indicators_tools import get_indicators
    from capitalradar.agents.utils.fundamental_data_tools import (
        get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
    )
    from capitalradar.agents.utils.news_data_tools import (
        get_news, get_insider_transactions, get_global_news,
    )
    from capitalradar.agents.utils.capital_flow_tools import (
        get_money_flow, get_hsgt_flow, get_market_flow, get_margin_trading,
        get_institutional_holders, get_major_holders, get_analyst_recommendations,
    )
    from capitalradar.agents.utils.intraday_tools import (
        get_intraday_data, get_realtime_quote,
    )

    @tool
    def query_past_analyses(
        ticker: Annotated[str, "Stock ticker code (e.g. 600519, 000858, AAPL). Suffixes like .SH/.SZ are auto-normalized."],
    ) -> str:
        """Query past CapitalRadar analysis runs for a specific stock ticker.
        Returns summary of all past analyses including dates, ratings, and available reports.
        Use this FIRST before suggesting any analysis to the user.
        Ticker suffixes (.SH/.SZ) are automatically handled — just pass whatever the user provides."""
        from web.ticker_utils import normalize_ticker, resolve_company_name
        norm = normalize_ticker(ticker)
        results = _search(ticker=norm, limit=10)
        # Fallback: if ticker is bare 6-digit code, try common A-share suffixes
        if not results and norm.isdigit() and len(norm) == 6:
            for suffix in ('.SZ', '.SH'):
                results = _search(ticker=norm + suffix, limit=10)
                if results:
                    norm = norm + suffix
                    break
        if not results:
            return f"No past analyses found for {norm}. The user should be asked if they want a full deep analysis or quick advice."
        # Cross-validate: compare stored company_name with current yfinance name
        _, current_name = resolve_company_name(norm)
        lines = [f"# Past Analyses for {norm}", ""]
        if current_name:
            lines.append(f"**Current yfinance name**: {current_name}")
            lines.append("")
        for r in results:
            rid = r["run_id"]
            date = r.get("date", "N/A")
            rating = r.get("rating", "N/A")
            signal = r.get("signal", "N/A")
            stored_name = r.get("company_name", "")
            lines.append(f"- **{date}** | Rating: {rating} | Signal: {signal}")
            if stored_name:
                display_name = stored_name
                if current_name and stored_name != current_name:
                    display_name += f" ⚠️ (current yfinance: {current_name})"
                lines.append(f"  Company: {display_name}")
            # Check what reports are available
            state = _load_state(rid)
            if state:
                available = []
                for key in ["capital_flow_report", "market_report", "sentiment_report", "news_report", "fundamentals_report"]:
                    if state.get(key):
                        label = key.replace("_report", "").replace("_", " ").title()
                        available.append(label)
                if available:
                    lines.append(f"  Reports: {', '.join(available)}")
        return "\n".join(lines)

    @tool
    def web_search_current(
        query: Annotated[str, "What to search for (e.g. stock news, analyst ratings)"],
        ticker: Annotated[str, "Stock ticker being discussed, to enrich the search query with the correct company name"] = "",
        max_results: Annotated[int, "Max results"] = 5,
    ) -> str:
        """Search the web for current information about a stock or market topic.
        When a ticker is provided, the query is automatically enriched with the
        company name to ensure results are for the correct company."""
        # Enrich query with ticker + company name if provided
        from web.ticker_utils import ticker_to_search_query
        enriched = ticker_to_search_query(query, ticker) if ticker else query

        results = []
        try:
            from duckduckgo_search import DDGS
            for r in DDGS().text(enriched, max_results=max_results):
                results.append({
                    "title": r.get("title", "")[:100],
                    "url": r.get("href", "")[:200],
                    "snippet": r.get("body", "")[:300],
                })
        except Exception:
            pass
        if not results:
            return "[Web search unavailable. Use your knowledge.]"
        lines = [f"# Web Search: {enriched}", ""]
        for i, r in enumerate(results[:max_results], 1):
            lines.append(f"{i}. **{r['title']}**")
            lines.append(f"   {r['snippet']}")
            lines.append(f"   {r['url']}")
            lines.append("")
        return "\n".join(lines)

    @tool
    def run_full_analysis(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH, 000858.SZ, AAPL)"],
        analysis_date: Annotated[str, "Analysis date YYYY-MM-DD (default: today)"] = "",
        language: Annotated[str, "Output language: Chinese or English"] = "Chinese",
    ) -> str:
        """Run a FULL CapitalRadar deep analysis pipeline for the given stock.

        This is NOT a quick assessment — it runs all 7 analysts (Capital Flow,
        Market, Sentiment, News, Fundamentals, Competitor, Partner), followed
        by Bull vs Bear debate, Trader proposal, Risk debate, and Portfolio
        Manager decision. Takes 3-5 minutes. The result is automatically saved
        to History.

        ONLY call this after the user explicitly requests a full deep analysis.
        Tell the user it will take a few minutes before starting.
        """
        from datetime import datetime
        if not analysis_date:
            analysis_date = datetime.now().strftime("%Y-%m-%d")

        from capitalradar.graph.trading_graph import CapitalRadarGraph
        graph = CapitalRadarGraph(
            ["capital_flow", "market", "social", "news", "fundamentals", "competitor", "partner"],
            config=config,
            debug=False,
        )

        try:
            final_state, signal = graph.propagate(ticker, analysis_date)
        except Exception as e:
            return f"Analysis failed: {str(e)}"

        # Persist to History
        final_decision = final_state.get("final_trade_decision", "")
        rating = _parse_rating(final_decision)
        from web.results_store import save_result
        from web.ticker_utils import resolve_company_name, normalize_ticker
        ticker_norm, company_name = resolve_company_name(ticker)
        run_id = save_result(config, {
            "ticker": ticker_norm or normalize_ticker(ticker),
            "date": analysis_date,
            "company_name": company_name,
            "rating": rating,
            "signal": signal or "",
            "analysts": "capital_flow,market,social,news,fundamentals,competitor,partner",
            "provider": config.get("llm_provider", ""),
            "deep_model": config.get("deep_think_llm", ""),
            "quick_model": config.get("quick_think_llm", ""),
            "language": language,
            "run_type": "advisory",
        })

        safe_ticker = (ticker_norm or normalize_ticker(ticker)).replace(".", "_")
        base_url = f"/api/results/{run_id}/download?format="
        lines = [
            f"## Full Analysis Complete: {ticker_norm or ticker}",
            f"**Date**: {analysis_date}",
            f"**Rating**: {rating}",
            f"**Signal**: {signal}",
            f"**Run ID**: {run_id}",
            "",
            f"**Download Report**: [PDF]({base_url}pdf) | [MD]({base_url}md) | [DOCX]({base_url}docx) | [JSON]({base_url}json)",
            "",
            "### Final Decision",
            str(final_decision)[:3000],
        ]
        return "\n".join(lines)

    def _parse_rating(text: str) -> str:
        """Extract Buy/Hold/Sell rating from decision text."""
        import re
        t = str(text).lower()
        if "buy" in t or "买入" in t:
            return "Buy"
        if "sell" in t or "卖出" in t:
            return "Sell"
        if "overweight" in t or "增持" in t:
            return "Overweight"
        if "underweight" in t or "减持" in t:
            return "Underweight"
        return "Hold"

    @tool
    def get_smart_money_score(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH, 000625.SZ)"],
    ) -> str:
        """Get the Smart Money Score (0-100) — 6-dimension institutional flow analysis.

        Six dimensions: ①Scale(规模) ②Direction(方向) ③Persistence(持续性)
        ④Alignment(量价) ⑤Confirmation(印证) ⑥Stage(阶段).
        Weighted composite. ≥70=主力确认, 50-69=有主力, 30-49=信号弱, <30=无主力.
        SCREENING HINT: Use run_smart_screening for batch screening.
        """
        from capitalradar.sector_scan.smart_money_score import (
            compute_smart_money_score, format_sms_unavailable,
        )
        total, breakdown = compute_smart_money_score(ticker, config)

        # Data-source outage / all-zero snapshot must NOT be reported as a
        # bearish score — be honest so the agent falls back gracefully instead
        # of flagging "continuous failure". _data_unavailable is the strictest
        # condition (raw flow all-zero/missing) and is checked first.
        if breakdown.get("_data_unavailable"):
            return format_sms_unavailable(ticker, breakdown.get("_note", ""))
        if breakdown.get("_realtime_only"):
            return (f"## Smart Money Score: {ticker}\n\n"
                    f"⚠️ **数据降级** — 历史资金流数据源（tushare/东财）暂不可用，仅今日实时快照。\n\n"
                    f"{breakdown.get('_note', '')}\n\n"
                    f"建议：稍后重试完整评分，或用 `verify_moneyflow` 交叉验证资金面。")
        if breakdown.get("_verdict") == "error":
            return (f"## Smart Money Score: {ticker}\n\n"
                    f"⚠️ **数据暂时不可用** — 主力资金数据源当前限流或网络故障，无法计算评分。\n\n"
                    f"这不是看空信号。请稍后重试，或改用 `verify_moneyflow` 交叉验证资金面。")

        DIM_LABELS = {"scale":"①规模","direction":"②方向","persistence":"③持续性",
                       "alignment":"④量价","confirmation":"⑤印证","stage":"⑥阶段"}
        # Render only the 6 dimensions — legacy gate keys (e.g. `cross`) lack
        # a numeric "score" and would crash the renderer.
        SCORE_DIMS = ("scale", "direction", "persistence", "alignment", "confirmation", "stage")
        lines = [f"## Smart Money Score: {ticker}", f"**总分: {total}/100**", ""]
        # Layer-1 validity annotation: a tiny 5-day flow (< configured floor) is
        # labelled 不可信 without suppressing the score.
        validity = breakdown.get("_validity") or {}
        if validity.get("flags"):
            lines.append(f"⚠️ **数据可信度低** — {validity.get('note') or '资金流量级过小，评分仅供参考'}")
            lines.append("")
        for dim, info in breakdown.items():
            if dim not in SCORE_DIMS:
                continue
            label = DIM_LABELS.get(dim, dim)
            s = info["score"]
            bar = "█" * int(s / 10) + "░" * (10 - int(s / 10))
            lines.append(f"- {label}: {s:.0f}/100 {bar}")
            if info.get("detail"):
                lines.append(f"  {info['detail']}")
        lines.append("")
        if total >= 70:
            lines.append("✅ 主力确认 — 机构参与度高，值得关注")
        elif total >= 50:
            lines.append("⚠️ 有主力参与 — 力度尚可，但需其他信号配合")
        elif total >= 30:
            lines.append("🔸 信号较弱 — 主力参与不明显，谨慎")
        else:
            lines.append("❌ 无显著主力 — 不推荐")
        return "\n".join(lines)

    @tool
    def run_smart_screening(
        requirement: Annotated[str, "Natural language stock screening requirement, e.g. '帮我找低位启动的新能源票，要有主力建仓信号'"],
        market_context: Annotated[str, "Brief market context, e.g. '大盘震荡，成交量萎缩'"] = "",
    ) -> str:
        """AI-powered stock screening. Describe what you want in natural language and
        the agent will: 1) generate a custom screening strategy, 2) execute it against
        A-share data, 3) return ranked candidates with explanations.

        ALL strategies automatically include smart_money_score >= 50 baseline.
        """
        from capitalradar.sector_scan.dynamic_strategy import (
            build_strategy_prompt, parse_strategy_json, validate_strategy,
            execute_strategy, describe_strategy, STRATEGY_FIELDS,
        )
        from capitalradar.sector_scan.rotation import get_rrg_data
        from capitalradar.sector_scan.smart_scanner import get_industry_stocks, _score_one_stock
        from capitalradar.sector_scan.smart_money_score import compute_smart_money_score
        from capitalradar.sector_scan.pick_tracker import record_pick
        from datetime import datetime

        # ── Phase 1: Generate strategy ──
        prompt = build_strategy_prompt(requirement, market_context)
        from langchain_core.messages import SystemMessage, HumanMessage
        strategy_response = llm_with_tools.invoke([
            SystemMessage(content="You are a stock screening JSON generator. Output ONLY valid JSON."),
            HumanMessage(content=prompt),
        ])
        strategy_raw = strategy_response.content if hasattr(strategy_response, "content") else str(strategy_response)
        try:
            strategy = parse_strategy_json(strategy_raw)
            strategy = validate_strategy(strategy)
        except Exception as e:
            return f"策略生成失败: {e}\nLLM输出: {strategy_raw[:500]}"

        # ── Phase 2: Build candidate pool ──
        try:
            rrg = get_rrg_data(lookback=10, mode="capital")
            industries = rrg.get("industries", [])
            leading_improving = [
                i for i in industries
                if i.get("quadrant") in ("leading", "improving")
            ]
            if not leading_improving:
                leading_improving = industries[:10]  # fallback
        except Exception:
            leading_improving = []

        candidate_pool = []
        today = datetime.now().strftime("%Y-%m-%d")
        for ind in leading_improving[:15]:
            name = ind.get("name", "")
            stocks = get_industry_stocks(name) if name else []
            for s in stocks[:30]:
                code = s.get("code", "")
                if not code:
                    continue
                try:
                    score, _ = compute_smart_money_score(code, config)
                except Exception:
                    # A compute failure is DATA UNAVAILABLE, not a pass — 0.0
                    # excludes the candidate from the >=50 baseline instead of
                    # fabricating a qualifying score. (compute_* already catches
                    # internally, so this branch is near-dead.)
                    score = 0.0
                cand = {
                    "ticker": code,
                    "name": s.get("name", ""),
                    "smart_money_score": score,
                    "rrg_quadrant": ind.get("quadrant", ""),
                    "industry": name,
                    "market_cap": s.get("market_cap", 0),
                }
                # Add money flow data (tushare enhanced)
                try:
                    from capitalradar.dataflows.eastmoney_sector import get_stock_moneyflow
                    mf = get_stock_moneyflow(code, days=5)
                    # get_stock_moneyflow returns 万元 (raw tushare scale). net_5d is
                    # the 5-day 主力(超大+大单) net in 万元 — the field STRATEGY_FIELDS
                    # documents as "5日主力净流入(万元), >5000万为强流入". The old
                    # `net_amount / 1e4` was both the WRONG field (latest single day,
                    # not 5-day) and the WRONG unit (万元→元), silently making every
                    # net_inflow_5d strategy threshold 1万倍 off.
                    cand["net_inflow_5d"] = mf.get("net_5d", mf.get("net_amount", 0))
                    # 20/60日主力净额 (万元) — same 万元 scale as net_5d; lets a
                    # strategy screen on 中/长期主力立场 (get_stock_moneyflow now
                    # always fetches ≥60 trading days regardless of days=5).
                    cand["net_inflow_20d"] = mf.get("net_20d", mf.get("net_5d", 0))
                    cand["net_inflow_60d"] = mf.get("net_60d", mf.get("net_20d", 0))
                    cand["mf_ratio"] = mf.get("mf_ratio", 0)
                    cand["moneyflow_trend"] = mf.get("trend", "stable")
                    cand["big_order_ratio"] = mf.get("buy_elg_ratio", 0) / 100.0
                    cand["net_inflow_ratio"] = mf.get("net_inflow_ratio", 0)
                except Exception:
                    cand["net_inflow_5d"] = 0
                    cand["net_inflow_20d"] = 0
                    cand["net_inflow_60d"] = 0
                    cand["mf_ratio"] = 0
                    cand["moneyflow_trend"] = "stable"
                    cand["big_order_ratio"] = 0
                candidate_pool.append(cand)

        if not candidate_pool:
            return "没有找到候选股票。请尝试调整条件或扩大行业范围。"

        # ── Phase 3: Execute strategy ──
        results = execute_strategy(strategy, candidate_pool)
        if not results:
            strategy["conditions"] = [c for c in strategy["conditions"]
                                       if c.get("field") != "smart_money_score"]
            strategy["conditions"].append({"field": "smart_money_score", "op": ">=", "value": 40})
            results = execute_strategy(strategy, candidate_pool)

        # ── Phase 4: Build output ──
        lines = ["# 🎯 AI 智能选股结果", "", describe_strategy(strategy), "", f"**候选池**: {len(candidate_pool)} 只 (来自 Leading + Improving 行业)", f"**筛选结果**: {len(results)} 只", "", "---", ""]

        for i, r in enumerate(results[:10], 1):
            code = r.get("ticker", "?")
            name = r.get("name", code)
            sms = r.get("smart_money_score", 0)
            stars = "⭐" * min(5, int(sms / 20) + 1)
            lines.append(f"### {i}. {code} {name} {stars}")
            lines.append(f"**主力评分**: {sms:.0f}/100 | 行业: {r.get('industry', 'N/A')} | RRG: {r.get('rrg_quadrant', 'N/A')}")
            lines.append("")

            # Record pick
            try:
                record_pick(config, code, today, "advisory",
                            strategy.get("name", "custom"), sms,
                            s.get("close", 0))
            except Exception:
                pass

        if not results:
            lines.append("⚠️ 没有股票满足当前策略条件。已放宽主力资金门槛重试，仍无结果。建议扩大行业范围或降低估值/技术指标要求。")

        return "\n".join(lines)

    @tool
    def query_pick_performance(
        days: Annotated[int, "How many days back to check (default 90)"] = 90,
    ) -> str:
        """Check how past stock picks have performed. Returns win rate, average returns,
        and a list of recent picks with their outcomes."""
        from capitalradar.sector_scan.pick_tracker import get_performance_report
        from web.results_store import resolve_picks
        resolve_picks(config)
        return get_performance_report(config, days)

    # ── Pick Agent tools (included so Advisory can do stock discovery) ──
    @tool
    def predict_stock_price(
        ticker: Annotated[str, "Stock ticker for prediction (e.g. 601127.SH, 000001.SZ)"],
    ) -> str:
        """Run the PredictionAgent ML+LLM forecast for a specific stock.

        Returns 5-day and 20-day direction probabilities, price range intervals,
        institutional behavior phase classification, and actionable guidance.
        If models are not yet trained, the behavior analysis still works.
        """
        from capitalradar.prediction import PredictionAgent
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            agent = PredictionAgent()
            report = agent.predict(ticker, today, config)
            return report.to_markdown()
        except Exception as e:
            return f"Prediction failed for {ticker}: {e}"

    from web.ai_pick_agent import build_aipick_tools as _build_pick_tools
    _pick_tools = _build_pick_tools(config, llm)
    _pick_by_name = {t.name: t for t in _pick_tools}
    get_market_scan = _pick_by_name.get("get_market_scan_context")
    run_screening = _pick_by_name.get("run_smart_screening")
    get_sms = _pick_by_name.get("get_smart_money_score")
    pick_perf = _pick_by_name.get("query_pick_performance")
    get_tg = _pick_by_name.get("get_top_gainers")
    get_mfr = _pick_by_name.get("get_multi_factor_ranking")
    get_mx = _pick_by_name.get("query_eastmoney_data")
    get_div = _pick_by_name.get("get_high_dividend_stocks")
    get_stp = _pick_by_name.get("get_short_term_picks")
    get_hrp = _pick_by_name.get("get_hot_reversal_picks")
    get_rvp = _pick_by_name.get("get_reversal_performance")

    # ── Two-pass (二次分析) records tools — let the PM analyze saved runs ──
    @tool
    def get_twopass_records(tab: str = "") -> str:
        """List saved two-pass (二次分析) analysis records.

        Each record is one completed two-pass re-analysis run from the
        东方自选 / 指数选股 tabs — the stocks that were previously bullish
        re-checked by N new models. Returns id, time, tab, stock count and
        how many came out 看多 (bullish). Call this to discover which
        two-pass records exist before deep-diving into one. Pass tab="emwl"
        or tab="idx" to filter by source."""
        from web.twopass_records import list_twopass_records
        from capitalradar.dataflows.config import get_config
        try:
            recs = list_twopass_records(get_config())
        except Exception as e:
            return f"Two-pass records unavailable: {e}"
        if tab:
            recs = [r for r in recs if r.get("tab") == tab]
        if not recs:
            return "暂无二次分析记录。请先在东方自选/指数选股 tab 运行二次分析。"
        lines = ["# 二次分析记录 (Two-Pass Records)", ""]
        lines.append("| ID | 时间 | 来源 | 模型数 | 复核只数 | 确认看多 | 标题 |")
        lines.append("|----|------|------|--------|----------|----------|------|")
        for r in recs:
            lines.append(
                f"| {r['id']} | {r['created_at']} | {r['tab']} | {r['model_count']} "
                f"| {r['stock_count']} | {r['bullish_count']} | {r['title']} |"
            )
        lines.append("")
        lines.append("调用 get_twopass_record_detail(id) 查看某条记录的完整股票数据与结论。")
        return "\n".join(lines)

    @tool
    def get_twopass_record_detail(record_id: int) -> str:
        """Get the full detail of one two-pass (二次分析) record.

        Returns the complete per-stock table (code, name, price, change%,
        previous model+verdict, new model+verdict, agree/disagree) PLUS each
        stock's LLM analysis conclusion text. Use this to perform deeper
        analysis on the bullish candidates and pick final recommendations."""
        from web.twopass_records import get_twopass_record
        from capitalradar.dataflows.config import get_config
        try:
            rec = get_twopass_record(get_config(), record_id)
        except Exception as e:
            return f"Failed to load record: {e}"
        if rec is None:
            return f"记录 {record_id} 不存在。先用 get_twopass_records 查看可用记录。"
        stocks = rec.get("stocks", []) or []
        lines = [
            f"# 二次分析记录 #{record_id}",
            f"**{rec.get('title', '')}**",
            f"时间: {rec.get('created_at')} | 模型数: {rec.get('model_count')} "
            f"| 复核: {rec.get('stock_count')} 只 | 确认看多: {rec.get('bullish_count')} 只",
            "",
        ]
        if not stocks:
            lines.append("（记录无股票数据）")
            return "\n".join(lines)
        lines.append("## 复核总表")
        lines.append("| 代码 | 名称 | 现价 | 涨跌幅 | 前次模型 | 前次结论 | 本次模型 | 本次结论 | 一致 |")
        lines.append("|------|------|------|--------|----------|----------|----------|----------|------|")
        for s in stocks:
            chg = s.get("change_pct")
            chg_str = f"{chg:+.2f}%" if chg is not None else "-"
            price = s.get("price")
            price_str = f"{price:.2f}" if price is not None else "-"
            agree = "✅" if s.get("newVerdict") == s.get("prevVerdict") else "❌"
            lines.append(
                f"| {s.get('code', '?')} | {s.get('name', '')} | {price_str} | {chg_str} "
                f"| {s.get('prevModel', '?')} | {s.get('prevVerdict', '?')} "
                f"| {s.get('newModel', '?')} | {s.get('newVerdict', '?')} | {agree} |"
            )
        lines.append("")
        lines.append("## 各股结论摘要")
        for s in stocks:
            lines.append(f"### {s.get('code', '?')} {s.get('name', '')} — {s.get('newVerdict', '?')}")
            analysis = (s.get("analysis") or "").strip()
            if analysis:
                lines.append(analysis[:1200])
            else:
                lines.append("（无分析结论）")
            lines.append("")
        lines.append("---")
        lines.append("请对以上候选做进一步深度分析（主力资金/技术面/消息面交叉验证），最终给出不超过5只的建议。")
        return "\n".join(lines)

    # ── Moneyflow cross-validation ──
    @tool
    def verify_moneyflow(
        ticker: Annotated[str, "Stock ticker (e.g. 600030, 600030.SH)"],
    ) -> str:
        """Cross-validate a stock's moneyflow between 东财逐日(akshare, primary)
        and tushare逐日 (verifier). Returns 5/10/20-day net-inflow comparisons in
        万元, per-window deviation ratio vs the configured threshold, and a verdict
        (一致 / 偏差 / 数据缺失). Use when the Smart Money Score or its underlying
        flow data looks suspicious, or before relying on a low/high SMS figure."""
        from capitalradar.sector_scan.moneyflow_verifier import (
            verify_moneyflow as _verify, _render_verify_moneyflow,
        )
        try:
            d = _verify(ticker, config=config)
            return _render_verify_moneyflow(d)
        except Exception as e:
            return f"资金流交叉验证失败: {e}"

    @tool
    def get_moneyflow_multi_horizon(
        ticker: Annotated[str, "Stock ticker code (e.g. 600519, 000858). Suffixes like .SH/.SZ are auto-normalized."],
    ) -> str:
        """多周期主力资金: 当日/5日/20日/60日 主力(超大单+大单)净流入、构成与强度 (万元)。

        判断主力态度与方向的核心工具 — 60日给季度级立场、20日约一个月建仓/出货趋势、
        5日短期方向、当日单日情绪; 多周期组合可暴露背离 (如60日净流出+近5日转正=
        回流初期)。输出含占日均成交额% (相对强度) 与流通市值 — 绝对净额本身不能判断
        强弱 (同一+5000万, 小盘强吸筹/大盘噪音), 引用时必须结合这些相对量。
        get_money_flow 只有单日/五日近似, 要看主力中长期态度时必须用它。
        数据来源 tushare moneyflow (万元原生), 直接 tushare 拉取、不经过本地缓存。"""
        from web.ticker_utils import normalize_ticker
        from capitalradar.dataflows.eastmoney_sector import (
            get_moneyflow_multi_horizon as _mh,
            format_moneyflow_multi_horizon,
        )
        code = normalize_ticker(ticker)
        agg = _mh(code)
        if not agg.get("source_rows"):
            return f"{code}: 暂无资金流数据(新上市/停牌/tushare不可用)。"
        body = format_moneyflow_multi_horizon(agg)
        return (f"{code} 多周期主力资金(万元, 数据截至{agg['end_date']}, 共{agg['source_rows']}个交易日):\n{body}")

    result = [
        query_past_analyses,
        run_full_analysis,
        predict_stock_price,
        get_stock_data, get_indicators,
        get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
        get_news, get_insider_transactions, get_global_news,
        get_money_flow, get_hsgt_flow, get_market_flow, get_margin_trading,
        get_institutional_holders, get_major_holders, get_analyst_recommendations,
        get_intraday_data, get_realtime_quote,
    ]
    # Add two-pass records tools
    result.append(get_twopass_records)
    result.append(get_twopass_record_detail)
    # Add moneyflow cross-validation tool
    result.append(verify_moneyflow)
    # Add multi-horizon 主力 (当日/5/20/60日 + 构成) tool
    result.append(get_moneyflow_multi_horizon)
    # Add Pick tools
    if get_market_scan: result.append(get_market_scan)
    if run_screening: result.append(run_screening)
    if get_sms: result.append(get_sms)
    if pick_perf: result.append(pick_perf)
    if get_tg: result.append(get_tg)
    if get_mfr: result.append(get_mfr)
    if get_mx: result.append(get_mx)
    if get_div: result.append(get_div)
    if get_stp: result.append(get_stp)
    if get_hrp: result.append(get_hrp)
    if get_rvp: result.append(get_rvp)
    # Add strategy library tools
    from capitalradar.strategy.manager import search_strategies
    from capitalradar.advisory.calibration_tool import run_calibration
    @tool
    def list_strategies(query: str = "") -> str:
        """Search saved strategies from the strategy library."""
        results = search_strategies(query)
        if not results:
            return "No strategies found. Go to the Strategy tab to create one."
        return "\n".join(f"- #{s['id']} {s['name']} ({s['type']})" for s in results)
    if list_strategies: result.append(list_strategies)
    @tool
    def run_system_calibration() -> str:
        """Run system calibration to analyze biases and propose threshold adjustments."""
        return run_calibration(config)
    if run_system_calibration:
        result.append(run_system_calibration)
    @tool
    def compare_backtests(run_ids: str) -> str:
        """Compare multiple backtest results side-by-side. run_ids: comma-separated IDs."""
        ids = [int(x.strip()) for x in run_ids.split(",") if x.strip().isdigit()]
        if len(ids) < 2:
            return "Need at least 2 run IDs to compare. Usage: compare_backtests(\"1,2,3\")"
        results_list = []
        from capitalradar.workspace.store import get_connection, get_config
        conn = get_connection(get_config())
        try:
            for rid in ids:
                try:
                    row = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (rid,)).fetchone()
                    if row:
                        r = json.loads(row["results"]) if isinstance(row["results"], str) else row["results"]
                        results_list.append((row["ticker"], r))
                except Exception:
                    pass
        finally:
            conn.close()
        if len(results_list) < 1:
            return "No backtest data found for the given IDs."
        lines = ["## Backtest Comparison\n"]
        for ticker, r in results_list:
            lines.append(f"**{ticker}**: AnnRet {r.get('annual_return',0):.1f}% | Sharpe {r.get('sharpe',0):.2f} | MDD {r.get('max_drawdown_pct',0):.1f}% | Trades {r.get('total_trades',0)}")
        return "\n".join(lines)

    @tool
    def evaluate_past_recommendations(
        ticker: str,
    ) -> str:
        """Evaluate past CapitalRadar analysis recommendations against current prices.
        Shows what was recommended, price at analysis time, current price, and actual return."""
        import yfinance as yf
        from datetime import datetime
        
        results = _search(ticker=ticker, limit=20)
        if not results:
            return f"No past analyses found for {ticker}."
        
        lines = [f"## Past Recommendation Evaluation: {ticker}", ""]
        lines.append("| Date | Rating | Signal | Price Then | Price Now | Change % |")
        lines.append("|------|--------|--------|------------|-----------|----------|")
        
        for r in results[:10]:
            rid = r["run_id"]
            date = r.get("date", "")
            rating = r.get("rating", "N/A")
            signal = r.get("signal", "N/A")
            
            # Get current price
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1d")
                current_price = hist["Close"].iloc[-1] if not hist.empty else None
                
                # Get price at analysis time
                if date:
                    hist_then = stock.history(start=date, end=date)
                    price_then = hist_then["Close"].iloc[0] if not hist_then.empty else None
                else:
                    price_then = None
                
                if current_price and price_then and price_then > 0:
                    change = (current_price - price_then) / price_then * 100
                    lines.append(f"| {date} | {rating} | {signal} | {price_then:.2f} | {current_price:.2f} | {change:+.1f}% |")
                else:
                    lines.append(f"| {date} | {rating} | {signal} | N/A | N/A | N/A |")
            except Exception:
                lines.append(f"| {date} | {rating} | {signal} | N/A | N/A | Error |")
        
        return "\n".join(lines)
    
    if evaluate_past_recommendations:
        result.append(evaluate_past_recommendations)

    if compare_backtests: result.append(compare_backtests)

    # ── Experience Review & Skill Generation Tools ──
    @tool
    def list_pending_experiences(
        category: str = "",
    ) -> str:
        """List all pending_review experiences with their IDs, categories, and abstracts.
        Use this to show the user what experiences are awaiting approval."""
        from capitalradar.advisory.experience_store import list_experiences
        try:
            pending = list_experiences(status="pending_review")
            if not pending:
                return "No pending experiences to review."
            lines = [f"## Pending Experiences ({len(pending)} total)", ""]
            cats = {}
            for exp in pending:
                c = exp.get("category", "other")
                cats.setdefault(c, []).append(exp)
            for cat, items in sorted(cats.items()):
                lines.append(f"### {cat} ({len(items)})")
                for exp in items:
                    tag = exp.get("lesson_abstract", "") or "no tag"
                    content = str(exp.get("content", ""))[:200]
                    lines.append(f"  - **ID#{exp['id']}** [{tag}]: {content}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing experiences: {e}"
    result.append(list_pending_experiences)

    @tool
    def approve_experience(
        ids: str,
    ) -> str:
        """Approve one or more pending experiences by ID. Changes status to 'active'.
        ids: comma-separated list of experience IDs (e.g. '4,5,6')"""
        from capitalradar.advisory.experience_store import approve_experience
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        if not id_list:
            return "No valid IDs provided."
        approved = 0
        for eid in id_list:
            if approve_experience(eid):
                approved += 1
        return f"Approved {approved}/{len(id_list)} experiences. 可在 Skill 面板点击 \"Generate Version\" 将它们纳入活跃 skill（自动去重、上限 30 条）。"
    result.append(approve_experience)

    @tool
    def reject_experience(
        ids: str,
    ) -> str:
        """Reject (archive) one or more pending experiences by ID.
        ids: comma-separated list of experience IDs (e.g. '4,5,6')"""
        from capitalradar.advisory.experience_store import archive_experience
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        if not id_list:
            return "No valid IDs provided."
        rejected = 0
        for eid in id_list:
            if archive_experience(eid):
                rejected += 1
        return f"Archived (rejected) {rejected}/{len(id_list)} experiences."
    result.append(reject_experience)

    @tool
    def generate_analyst_skill() -> str:
        """Generate a new version of the CapitalRadar Analyst skill from
        all currently active (approved) experiences. Call this AFTER the user
        has reviewed and approved experiences. Returns version info."""
        from capitalradar.graph.skill_generator import generate_skill_version
        try:
            result = generate_skill_version({})
            return (
                f"✅ Skill v{result['version']} generated!\n"
                f"- Rules included: {result['rule_count']}\n"
                f"- Active experiences: {result['active_exp_count']}\n"
                f"- File: {result['file_path']}\n\n"
                f"Now go to the Analyst Skill Control panel in the web UI to activate this version."
            )
        except Exception as e:
            return f"Skill generation failed: {e}"
    result.append(generate_analyst_skill)

    # ── Scheduled-task management tools (WeChat remote control) ──
    # Let the advisor list / start / pause / resume scheduled tasks and push a
    # run's report to WeChat from a chat channel (the WeCom bot bridge).
    from web.advisor_task_tools import (
        list_scheduled_tasks, start_scheduled_task, pause_scheduled_task,
        resume_scheduled_task, get_task_status, push_task_report,
    )
    result.append(list_scheduled_tasks)
    result.append(start_scheduled_task)
    result.append(pause_scheduled_task)
    result.append(resume_scheduled_task)
    result.append(get_task_status)
    result.append(push_task_report)

    return result


def create_history_pm_agent(run_ids: list[str], config: dict, lang: Optional[str] = None):
    """Create a PM LLM instance with tools bound and full analysis context.

    Accepts one or more run_ids. Loads all analysis states and combines them
    into a single system prompt so the PM can answer cross-analysis questions.

    Returns:
        (llm_with_tools, tools, system_prompt, meta): The tool-bound LLM,
        the tool list for manual execution, the system prompt string, and
        the primary analysis metadata dict.
    """
    from capitalradar.llm_clients import create_llm_client, resolve_role_llm

    # Advisory mode: no run_ids, use fallback prompt
    advisory_mode = not run_ids

    states = []
    meta = None
    for rid in run_ids:
        m = get_result(config, rid)
        if not m:
            raise ValueError(f"Analysis run {rid} not found")
        if meta is None:
            meta = m
        s = load_full_state(config, rid)
        if s:
            states.append(s)

    if advisory_mode:
        # Use fallback prompt with full tool access
        system_prompt = build_pm_system_prompt([], config, lang if lang else config.get("output_language", "Chinese"))
        provider, deep_model, _ = resolve_role_llm(config, "deep")
        meta = {}
    else:
        if not states:
            raise ValueError("No analysis states could be loaded")
        # Replay prefers the per-role provider that actually ran, then the
        # run's stored global provider, then current config resolution.
        provider = meta.get("deep_provider") or meta.get("provider") or resolve_role_llm(config, "deep")[0]
        deep_model = meta.get("deep_model") or config.get("deep_think_llm", "")

    client = create_llm_client(
        provider=provider,
        model=deep_model,
        base_url=config.get("backend_url"),
        timeout=300,
    )
    llm = client.get_llm()

    tools = build_tool_set(config, llm)
    llm_with_tools = llm.bind_tools(tools)

    lang = lang or meta.get("language") if meta else config.get("output_language", "Chinese")
    if not advisory_mode:
        system_prompt = build_pm_system_prompt(states, config, lang)

    return llm_with_tools, tools, system_prompt, meta


def _sse_event(name: str, data: dict) -> str:
    """Format an SSE event string (single-line safe)."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


def _history_chat_core(
    thread_id: str,
    question: str,
    config: dict,
    lang: Optional[str] = None,
) -> Generator[tuple[str, dict], None, None]:
    """Run one advisory Q&A turn; yield (event_name, data) tuples.

    The single shared core behind ``stream_history_chat`` (SSE for the web UI)
    and the WeCom bot bridge (which collects only the final
    ``chat-done.full_response``). Resolves run_ids from the thread's linking
    table, loads all analysis contexts + conversation history, creates the
    tool-bound PM agent, and runs an agent loop (LLM -> tool calls -> tool
    results -> LLM). Persists the user question and the PM response.
    """
    run_ids = get_thread_run_ids(config, thread_id)
    # Advisory mode: no run_ids means fresh conversation using tools only
    advisory_mode = not run_ids

    try:
        llm_with_tools, tools, system_prompt, meta = create_history_pm_agent(
            run_ids if not advisory_mode else [],
            config, lang
        )
    except Exception as e:
        yield ("chat-error", {"message": str(e)})
        return

    # Save user message to DB
    save_chat_message(config, thread_id, "user", question)

    yield ("chat-stream-start", {"thread_id": thread_id})

    # Load conversation history from DB
    try:
        history_msgs = get_chat_messages(config, thread_id)
    except Exception:
        history_msgs = []

    # Build messages: system prompt + loaded history + current question
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

    messages = [SystemMessage(content=system_prompt)]
    for h in history_msgs:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))

    # Build tool lookup map
    tool_map = {t.name: t for t in tools}

    # Agent loop
    tool_call_records = []
    max_iterations = 60
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            logger.exception("PM LLM invocation failed")
            yield ("chat-error", {"message": f"LLM error: {str(e)}"})
            return

        # Check for tool calls in response
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)

            for tc in response.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})

                # Sanitize args for display (limit string lengths)
                safe_args = {}
                for k, v in tool_args.items():
                    safe_args[k] = str(v)[:500]

                yield ("chat-tool-call", {
                    "tool_name": tool_name,
                    "args": safe_args,
                })

                # Execute the tool
                func = tool_map.get(tool_name)
                if func:
                    try:
                        result = func.invoke(tool_args)
                        result_str = str(result)[:2000]
                    except Exception as e:
                        result_str = f"Error executing {tool_name}: {str(e)}"
                else:
                    result_str = f"Tool '{tool_name}' not found. Available: {list(tool_map.keys())}"

                yield ("chat-tool-result", {
                    "tool_name": tool_name,
                    "result_snippet": result_str,
                })

                tool_call_records.append({
                    "tool_name": tool_name,
                    "args": safe_args,
                    "result_snippet": result_str,
                })

                messages.append(ToolMessage(
                    content=result_str,
                    tool_call_id=tc.get("id", ""),
                ))
        else:
            # Final response -- no more tool calls
            final_text = response.content if hasattr(response, "content") else str(response)

            # Append a transparent live-quote verification footer so any
            # stale/mislabeled figure (e.g. a 5-day change read as today's) is
            # immediately comparable to the real number. Best-effort: appended
            # BEFORE persisting so DB history, SSE, the WeCom push and the
            # stage-3 report all carry the same verified copy.
            try:
                final_text = final_text + _verify_advisor_facts(final_text, config)
            except Exception:  # noqa: BLE001 — never break the stream
                logger.warning("advisor data verification failed", exc_info=True)

            # Persist PM response
            tc_json = json.dumps(tool_call_records, ensure_ascii=False) if tool_call_records else ""
            msg_id = save_chat_message(config, thread_id, "assistant", final_text, tc_json)

            yield ("chat-done", {
                "full_response": final_text,
                "message_id": msg_id,
                "tool_calls_count": len(tool_call_records),
            })
            return

    # Max iterations reached without final answer
    yield ("chat-error", {
        "message": "Agent reached maximum tool-call iterations without producing a final answer."
    })


def stream_history_chat(
    thread_id: str,
    question: str,
    config: dict,
    lang: Optional[str] = None,
) -> Generator[str, None, None]:
    """SSE generator for a single Q&A turn with the history PM.

    Formats each (event_name, data) tuple from ``_history_chat_core`` into the
    SSE event strings the frontend consumes — the event stream is unchanged by
    the refactor (``chat-stream-start`` / ``chat-tool-call`` / ``chat-tool-result``
    / ``chat-done`` / ``chat-error``; ``[DONE]`` is appended by the endpoint).

    After a completed turn, the final reply is also mirrored to the WeCom bot's
    personal chat when the web-advisor→WeChat push switch is on (default off) —
    pushed on a daemon thread so it never delays the stream.
    """
    for name, data in _history_chat_core(thread_id, question, config, lang):
        yield _sse_event(name, data)
        if name == "chat-done":
            _push_web_advisor_reply(data.get("full_response", ""))


def _push_web_advisor_reply(text: str) -> None:
    """Forward a completed web-advisor reply to WeChat on a daemon thread.

    All work (including the lazy bridge import) happens inside the worker so a
    push problem can never raise into the SSE generator after ``chat-done``.
    """
    if not text or not text.strip():
        return

    def _worker():
        try:
            from web.bot_advisor_bridge import push_web_advisor_reply
            push_web_advisor_reply(text)
        except Exception:  # noqa: BLE001 — never break the SSE stream
            logger.exception("Web-advisor WeChat push thread failed")

    threading.Thread(target=_worker, daemon=True,
                     name="wecom-web-advisor-push").start()


stream_advisory_chat = stream_history_chat  # alias for /api/chat/stream
