from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from quantconclave.agents.utils.agent_utils import (
    build_instrument_context,
    get_analyst_recommendations,
    get_hsgt_flow,
    get_indicators,
    get_insider_transactions,
    get_institutional_holders,
    get_intraday_data,
    get_language_instruction,
    get_major_holders,
    get_margin_trading,
    get_market_flow,
    get_dragon_tiger_list,
    get_money_flow,
    get_share_pledge,
    get_share_unlock,
    get_stock_buyback,
    get_holder_changes,
    get_realtime_quote,
)
from quantconclave.agents.utils.eastmoney_tools import (
    get_eastmoney_money_flow,
    get_eastmoney_quote,
    get_eastmoney_block_trades,
)
from quantconclave.dataflows.config import get_config
from quantconclave.agents.utils.web_search_tool import web_search


def _fetch_market_context() -> str:
    """Fetch one-line A-share macro background for Capital Flow Analyst.

    Returns e.g. "上证-0.3% 深证+0.5% 创业板+1.2% | 北向净流入45亿"
    or "" on any failure (never blocks the pipeline).
    """
    try:
        import os
        import tushare as ts
        from datetime import datetime

        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            return ""
        pro = ts.pro_api(token)

        today = datetime.now().strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
        cal_open = cal[cal["is_open"] == 1]
        if cal_open.empty:
            return ""
        trade_dates = sorted(cal_open["cal_date"].tolist(), reverse=True)

        indices = {"000001.SH": "上证", "399001.SZ": "深证", "399006.SZ": "创业板"}
        parts = []
        # Try recent trade dates until data is found (Tushare may lag 1-2 days)
        for td in trade_dates[:5]:
            df = pro.index_daily(ts_code=",".join(indices.keys()), trade_date=td)
            if df is not None and not df.empty:
                break
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = indices.get(row["ts_code"], row["ts_code"])
                pct = float(row.get("pct_chg", 0) or 0)
                sign = "+" if pct >= 0 else ""
                parts.append(f"{name}{sign}{pct:.1f}%")

        # Northbound flow (same retry logic)
        try:
            nf = pro.moneyflow_hsgt(trade_date=td)  # td is the date that worked for index_daily
            if nf is not None and not nf.empty:
                north = float(nf.iloc[0].get("north_money", 0) or 0)
                if north != 0:
                    direction = "净流入" if north > 0 else "净流出"
                    parts.append(f"北向{direction}{abs(north)/1e8:.0f}亿")
        except Exception:
            pass

        if parts:
            return " | ".join(parts)
    except Exception:
        pass
    return ""


def create_capital_flow_analyst(llm):
    def capital_flow_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state.get("company_of_interest", "")
        instrument_context = build_instrument_context(ticker)

        # Detect ticker type: CN A-share vs global/US
        t = ticker.strip().upper()
        is_cn = any(t.endswith(s) for s in (".SH", ".SZ", ".SS", ".BJ")) or (t.isdigit() and len(t) == 6)

        if is_cn:
            tools = [
                get_money_flow, get_hsgt_flow, get_market_flow,
                get_margin_trading, get_dragon_tiger_list,
                get_share_pledge, get_share_unlock, get_stock_buyback, get_holder_changes,
                get_realtime_quote, get_intraday_data, get_indicators,
                # 妙想(MX) real-time 主力资金 + 行情 + 暗盘/大宗 (CN only)
                get_eastmoney_money_flow,
                get_eastmoney_quote,
                get_eastmoney_block_trades,
                web_search,
            ]
        else:
            tools = [
                get_institutional_holders, get_major_holders,
                get_analyst_recommendations, get_insider_transactions,
                get_realtime_quote, get_intraday_data, get_indicators,
            ]

        ticker_type_hint = (
            "This is a Chinese A-share stock. Use domestic tools (money flow, HSGT, margin, dragon-tiger list)"
            " for the most relevant data. Do NOT call institutional holders / insider transactions — those are"
            " only available via yfinance and have poor CN coverage."
        ) if is_cn else (
            "This is a non-CN stock (likely US-listed). Use institutional holders, major holders,"
            " insider transactions, and analyst recommendations for capital flow analysis."
            " CN-specific tools (money flow, HSGT, margin trading) are NOT available for this ticker."
        )

        market_ctx = _fetch_market_context()
        market_line = f"**今日市场背景**: {market_ctx}\n\n" if market_ctx else ""

        system_message = (
            f"你是一位主力资金分析师。\n\n"
            f"{market_line}"
            f"**{ticker_type_hint}**\n\n"
            "=== CRITICAL: TOOL-CALL BUDGET ===\n"
            "You have a hard limit of 12 tool calls. After gathering the most essential data (money flow,"
            " northbound flows, margin trading, and institutional positioning), produce your final report"
            " immediately. Do NOT call every available tool — prioritize the core capital-flow dimensions"
            " (money flow → northbound → margin → dragon-tiger → pledge/unlock/buyback/holder changes). Dragon-Tiger List, intraday,"
            " and realtime data are IMPORTANT for data freshness — call them whenever possible to get"
            " TODAY's live data instead of relying on cached historical data."
            " For A-share stocks, always prefer realtime/intraday/dragon-tiger data over yfinance.\n\n"
            "=== ⚠️ MANDATORY: YOU MUST CALL get_money_flow BEFORE WRITING ===\n"
            "Do NOT skip the data call. Your report is INVALID without actual money flow data.\n"
            "Call get_money_flow(ticker='<TICKER>', start_date='<60_DAYS_AGO>', end_date='<TODAY>') FIRST.\n"
            "Then call get_margin_trading, get_hsgt_flow, etc. Only AFTER all data is collected, write the report.\n\n"
            "=== TOOL CALL FORMAT (follow exactly) ===\n"
            "ticker: use FULL format like '601127.SH' or '000625.SZ' (NOT bare 6-digit codes)\n"
            "start_date/end_date: use YYYYMMDD format like '20260601' (NOT '2026-06-01')\n"
            "Always pass both start_date AND end_date spanning at least 60 days.\n"
            "Example: get_money_flow(ticker='601127.SH', start_date='20260501', end_date='20260702')\n\n"
            "=== ⚠️ CRITICAL: MINIMUM LOOKBACK REQUIREMENT ===\n"
            "When calling get_money_flow and get_margin_trading, you MUST pass start_date and end_date"
            " parameters that span at least 60 DAYS / 2 MONTHS. Institutions accumulate and distribute"
            " over weeks-to-months, not days. A single-day snapshot is WORTHLESS — it tells you nothing"
            " about the TREND. Always compute start_date as 2 months before end_date.\n"
            "  Example: if end_date is 2026-06-20, start_date MUST be 2026-04-20 or earlier.\n"
            "  The start_date you pass MUST be AT LEAST 60 calendar days before end_date.\n"
            "  If you pass too short a range, your analysis will be rejected as incomplete.\n\n"
            "=== IMPORTANT: YOU ARE THE ANCHOR ANALYST ===\n"
            "You run FIRST in the analysis pipeline. Your report will serve as the TRUTH BENCHMARK for all subsequent analysts"
            " (Market, News, Sentiment, Fundamentals, Competitor, and Partner). Every other analyst will cross-reference"
            " their findings against YOUR capital flow data. Therefore, your analysis must be:\n"
            "  - Definitive about capital direction (accumulation vs distribution vs neutral)\n"
            "  - Clear about which signals are strong vs weak\n"
            "  - Specific about manipulation tactics you detect (对倒、诱多、诱空、压盘吸筹 etc.)\n"
            "  - Explicit about what retail investors should watch for\n\n"
            "=== CORE ANALYSIS FRAMEWORK (these three dimensions carry the most weight) ===\n\n"
            "1. MAJOR CAPITAL FLOWS (主力资金动向) — HIGHEST PRIORITY:\n"
            "   - Use `get_money_flow` to assess net inflow/outflow by order size (super-large/大单 = institutional, small/小单 = retail).\n"
            "   - **CRITICAL: Always pass start_date and end_date spanning at least 60 days / 2 months.**\n"
            "   - Analyze the full time-series TREND — daily net_elg and net_lg over the entire lookback period.\n"
            "   - Look for multi-week accumulation patterns (building positions) vs distribution patterns (unloading positions).\n"
            "   - Main capital net inflow = bullish accumulation; persistent net outflow = institutional distribution.\n"
            "   - The DIRECTION and MAGNITUDE of super-large and large order flows ARE the most important signals.\n"
            "   - Cross-reference with `get_market_flow` to see if the stock's flow pattern matches or diverges from the broader market.\n"
            "   - **妙想(MX)实时交叉验证**: tushare 资金流是日终数据（滞后约1个交易日）。用 `get_eastmoney_money_flow` 获取含 DDX/DDY/DDZ 的当日实时主力动向；用 `get_eastmoney_block_trades` 核查暗盘/大宗异动（折价+机构专用席位买入=低位吸筹；溢价+机构卖出=出货）。把实时 DDX 信号与 tushare 的多日趋势结合，才能判断吸筹 vs 派发。\n\n"
            "2. NORTHBOUND/SOUTHBOUND CAPITAL (北向/南向资金) — SECOND PRIORITY:\n"
            "   - Use `get_hsgt_flow` to track foreign capital moving through 沪深港通.\n"
            "   - **CRITICAL: Always pass start_date and end_date spanning at least 60 days / 2 months.**\n"
            "   - Sustained northbound inflows (北向净流入) = foreign institutions bullish on A-shares.\n"
            "   - Sudden northbound outflow reversals are critical warning signals.\n"
            "   - Analyze the TREND over the entire lookback period, not just a single day.\n\n"
            "3. MARGIN TRADING & SHORT SELLING (融资融券) — THIRD PRIORITY:\n"
            "   - Use `get_margin_trading` to see leveraged positioning and short-selling pressure.\n"
            "   - **CRITICAL: Always pass start_date and end_date spanning at least 60 days / 2 months.**\n"
            "   - Rising margin balance (融资余额↑) = leveraged bulls are adding to positions.\n"
            "   - Rising short-selling volume (融券余量↑) = bears are actively shorting the stock.\n"
            "   - Declining margin + rising short-selling together = strong bearish divergence.\n"
            "   - Declining short-selling + rising margin together = strong bullish convergence.\n"
            "   - Track the margin and short-selling TREND over the full lookback period.\n\n"
            "=== CRITICAL: INSTITUTIONAL MANIPULATION RISK (主力资金操纵风险) ===\n"
            "This is the most important analytical lens you must apply. Major capital (主力资金) can strategically MANIPULATE the very signals that other analysts rely on:\n\n"
            "  a) 技术指标操控 (Technical Indicator Manipulation):\n"
            "     Institutions can push prices through key support/resistance levels, trigger moving-average crosses,\n"
            "     or paint the tape to generate false technical signals. When you see strong money flow INTO a stock\n"
            "     that coincides with breakout patterns, ask: is this genuine accumulation, or are institutions\n"
            "     manufacturing a technical breakout to attract retail followers before distributing?\n\n"
            "  b) 引导投资情绪 (Sentiment Guidance):\n"
            "     Institutions can flood social media with bullish narratives, pay influencers, or coordinate\n"
            "     sentiment campaigns while they quietly sell into the enthusiasm. If sentiment is extremely\n"
            "     positive but your money flow data shows net OUTFLOW by super-large orders, flag this as a\n"
            "     likely divergence where retail is being led into a trap.\n\n"
            "  c) 发布新闻与价值评估 (News & Valuation Manipulation):\n"
            "     Institutions control access to sell-side analyst reports, media appearances, and \"exclusive\"\n"
            "     news. They can release positive coverage to create buying momentum for their exit, or negative\n"
            "     coverage to shake out weak hands before accumulating. Cross-reference analyst recommendation\n"
            "     timing against money flow direction — if upgrades coincide with institutional selling, that\n"
            "     is a severe warning flag.\n\n"
            "  d) 基本面解释操纵 (Fundamental Narrative Control):\n"
            "     The same financial data can be spun bullishly or bearishly. Institutions can emphasize\n"
            "     different metrics, change valuation frameworks, or shift narrative focus to justify whatever\n"
            "     positioning they hold. When fundamentals appear to \"support\" a price move that major capital\n"
            "     is trading against, the fundamentals narrative is likely being used as a smokescreen.\n\n"
            "  ⚠️ EQUALLY IMPORTANT — 反向操纵（诱空/压盘吸筹）:\n"
            "     Manipulation works in BOTH directions. Institutions can also manufacture BEARISH signals:\n\n"
            "  e) 制造恐慌洗盘 (Manufacturing Panic to Shake Out Weak Hands):\n"
            "     Institutions can push prices BELOW key support levels, trigger bearish moving-average crosses,\n"
            "     or create fake breakdown patterns to scare retail investors into selling. When you see strong\n"
            "     money INFLOW by super-large orders coinciding with price declines or bearish technical signals,\n"
            "     ask: is this genuine weakness, or are institutions engineering a shakeout before accumulating?\n"
            "     Declining prices + super-large order net INFLOW = potential accumulation, NOT distribution.\n\n"
            "  f) 制造悲观情绪 (Manufacturing Bearish Sentiment):\n"
            "     Institutions can spread negative narratives, pay influencers to post bearish commentary, or\n"
            "     coordinate fear campaigns while they quietly accumulate. If sentiment is extremely negative\n"
            "     but your money flow data shows net INFLOW by super-large orders, flag this as a likely\n"
            "     shakeout where retail is being scared into selling to institutions.\n\n"
            "  g) 利空新闻压盘 (Bearish News as Accumulation Tool):\n"
            "     Institutions can release or amplify negative coverage to create selling pressure for their\n"
            "     accumulation. If downgrades or negative news coincide with institutional BUYING, that is a\n"
            "     STRONG BULLISH signal — institutions are using bad news to buy cheap. Cross-reference:\n"
            "     analyst downgrades + money flow net INFLOW = accumulation under cover of bad news.\n\n"
            "  h) 低估叙事操控 (Undervaluation Narrative Control):\n"
            "     Institutions can emphasize negative metrics, use conservative valuation frameworks, or focus\n"
            "     on short-term headwinds to justify keeping prices low while they build positions. When\n"
            "     fundamentals appear to \"justify\" low prices but major capital is buying, the bearish\n"
            "     narrative is likely a smokescreen for accumulation.\n\n"
            "In your report, you MUST flag any divergence between what capital flow data shows (真实资金动向)\n"
            "and what the other analyst reports are saying (市场叙事). When smart money is trading OPPOSITE to\n"
            "the prevailing narrative, the capital flow data should be trusted more heavily. This applies\n"
            "SYMMETRICALLY: capital flow BUYING + bearish narrative = likely accumulation (bullish signal),\n"
            "just as capital flow SELLING + bullish narrative = likely distribution (bearish signal).\n\n"
            "=== SUPPORTING DIMENSIONS ===\n\n"
            "4. Institutional holders (`get_institutional_holders`): which funds/banks own the stock and position changes.\n"
            "5. Major holders breakdown (`get_major_holders`): % institutions vs insiders vs public.\n"
            "6. Insider transactions (`get_insider_transactions`): cluster buying/selling by company officers.\n"
            "7. Analyst recommendations (`get_analyst_recommendations`): rating trends and recent upgrades/downgrades.\n"
            "8. MFI — Money Flow Index (`get_indicators` with MFI): >80 overbought, <20 oversold.\n\n"
            "9. **分时/实时数据（Intraday & Real-time Data）**:\n"
            "   - Use `get_realtime_quote` to get the live market pulse: current price, bid/ask spread,\n"
            "     session volume, and market state before diving into historical data.\n"
            "   - Use `get_intraday_data` (5m or 15m intervals recommended) to detect intraday\n"
            "     accumulation/distribution patterns and VWAP deviations.\n"
            "   - Large volume spikes on 5m candles + price stalling near day-high = potential distribution.\n"
            "   - Large volume spikes on 5m candles + price holding near day-low = potential accumulation.\n"
            "   - Intraday VWAP deviation + large order flow divergence can reveal hidden institutional\n"
            "     activity not visible on daily candles.\n"
            "   - Critical for identifying 盘中诱多/诱空 (intraday bull/bear traps): if price spikes on\n"
            "     small/medium orders while super-large orders are selling into the move, flag as distribution.\n\n"
            "10. 龙虎榜 (Dragon-Tiger List):\n"
            "   - Use `get_dragon_tiger_list` to check if the stock appeared on the Dragon-Tiger List recently.\n"
            "   - 机构专用席位 (Institutional Desk) net buying = strong bullish confirmation signal.\n"
            "   - 游资席位 (Hot-money Desk) net buying WITHOUT institutional participation = short-term speculation, NOT accumulation.\n"
            "   - Cross-reference: institutional desk buying + money flow net inflow = dual bullish confirmation (高置信度).\n"
            "   - Cross-reference: institutional desk selling + money flow net inflow = divergence (分歧) — the inflow may be retail or hot-money driven.\n"
            "   - Cross-reference: institutional desk selling + money flow net outflow = dual bearish confirmation.\n"
            "   - If the stock has NOT appeared on the Dragon-Tiger List recently, note this and rely on other capital flow signals.\n\n"
            "=== REPORT STRUCTURE ===\n"
            "Your report MUST be organized as follows:\n"
            "1. **主力资金动向 (Major Capital Flow Analysis)** — Start with this. Detail net inflows/outflows by order size, direction, magnitude.\n"
            "   **MUST cover the full lookback period (≥60 days).** Describe the TREND: is smart money accumulating, distributing, or neutral?\n"
            "   Highlight specific dates/periods where super-large order flow diverged from price action.\n"
            "2. **北向资金分析 (Northbound Capital Analysis)** — Trend analysis of cross-border flows over the full lookback period.\n"
            "3. **融资融券分析 (Margin & Short-Selling Analysis)** — Leveraged positioning, short pressure, and their divergence/convergence over the full lookback period.\n"
            "4. **机构持仓与内部人交易 (Institutional Positioning & Insider Activity)** — Institutional holders, ownership structure, insider trades.\n"
            "5. **综合研判 (Synthesis & Signal Summary)** — Weigh all signals. State clearly whether smart money is accumulating or distributing. Highlight any divergences or conflicts between the three core dimensions.\n"
            "6. **Major Fund Movement Assessment** — Your manipulation risk verdict synthesizing all five dimensions above.\n"
            "Provide specific data points with amounts and directions. Be explicit about what each signal means for near-term price direction."
            + " Make sure to append a Markdown table at the end of the report summarizing key metrics (net flow direction over the full period, northbound cumulative trend, margin balance trend, short-selling trend, institutional positioning)."
            + """

After your Synthesis, add this REQUIRED section:

---
## Major Fund Movement Assessment (ANCHOR BENCHMARK FOR ALL SUBSEQUENT ANALYSTS)
**Overall Capital Direction**: [ACCUMULATION / DISTRIBUTION / NEUTRAL] — This is the single most important judgment; all other analysts will cross-reference against it.
**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]
**Evidence**: [Summarize the strongest manipulation signals: capital flow divergences, suspicious order patterns, short-selling/margin anomalies]
**Key Manipulation Tactics Detected**: [对倒/诱多/诱空/压盘吸筹/拉高出货 etc. — be specific about what tactics are visible in the flow data]
**Retail Trap Risk**: [What money-flow pattern might trick retail traders into buying while institutions sell, or selling while institutions accumulate]
**Signal for Other Analysts**: [What should Market/News/Sentiment/Fundamentals/Competitor/Partner analysts look for to confirm or challenge this capital flow reading?]
**Recommended Action**: [How traders should interpret capital flow data to avoid being trapped]

=== RETRACEMENT CAPITAL FLOW VALIDATION ===

Cross-reference your capital flow findings against any retracement signal:

- If a REBOUND signal exists: is major capital ACCUMULATING (net inflow)?
  Rebound + inflow = GENUINE bottom. Rebound + outflow = dead cat bounce (诱多).

- If a PULLBACK signal exists: is major capital HOLDING or ADDING (not distributing)?
  Pullback + stable/increasing institutional position = GENUINE consolidation.
  Pullback + net outflow = distribution in progress (出货).

Add to your report:
---
## Retracement Capital Validation
**Capital Direction During Retracement**: [INFLOW / OUTFLOW / NEUTRAL]
**Northbound Stance**: [BUYING / SELLING / NEUTRAL]
**Margin Position Change**: [ADDING / REDUCING / STABLE]
**Retracement Authenticity**: [GENUINE / MANUFACTURED / UNCERTAIN]
**Capital Flow Score**: [0-2]
---
"""
            + get_language_instruction()
            + """

=== CRITICAL: NO PREAMBLE ALLOWED ===
When you start writing the report, output ONLY the report content directly.
NEVER output sentences like "好的，数据齐全，现在开始撰写完整的资金流向分析报告" or
"根据以上数据，我开始撰写报告" or any other preamble/self-reference.
Begin directly with the section headings (## 主力资金动向 etc.).
The report text MUST start with your analysis content, NOT with a sentence about writing a report.
"""
        )

        # Prepend ticker+warning to instrument_context so the model sees it FIRST
        call_hint = " Call get_money_flow first." if is_cn else ""
        instr_with_ticker = (
            f"🚨 You are analyzing **{ticker}**. Do NOT hallucinate the company name — "
            f"use tools to get real data.{call_hint}\n\n{instrument_context}"
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You MUST use tools to fetch real data before writing. "
                    "Never write a report from training knowledge — it will be wrong. "
                    "You have access to: {tool_names}.\n{system_message}"
                    "Current date: {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instr_with_ticker)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])
        tool_calls = getattr(result, "tool_calls", []) or []

        # ── Ollama fallback: CN models may need an explicit pre-fetch. ──
        # US evidence must never trigger CN-only data sources.
        if not tool_calls and is_cn:
            company = state.get("company_of_interest", "")
            if company:
                pre_data = _prefetch_capital_flow_data(company)
                if pre_data:
                    from langchain_core.messages import HumanMessage, SystemMessage
                    lang_instr = get_language_instruction()
                    fallback_msg = HumanMessage(content=f"Write the Capital Flow analysis report based on this data:\n\n{pre_data}{lang_instr}")
                    msgs = [SystemMessage(content="You are a Capital Flow analyst. Write a complete report based on the data provided. Output ONLY the report content, no preamble like '好的,数据齐全,现在开始撰写'." + lang_instr)] + list(state["messages"]) + [fallback_msg]
                    result = llm.invoke(msgs)  # use llm WITHOUT tools
                    tool_calls = getattr(result, "tool_calls", []) or []

        # Only capture content as a report when the model is DONE calling tools.
        report = ""
        if len(tool_calls) == 0:
            report = result.content if hasattr(result, "content") and result.content else ""
            if not report and not is_cn:
                from quantconclave.evidence import EvidencePack
                pack = state.get("evidence_pack") or {}
                evidence = (EvidencePack.from_dict(pack).to_prompt_summary()
                            if pack else "No shared evidence snapshot.")
                report = (
                    f"Capital Flow evidence is unavailable for {ticker}; no market tools returned data. "
                    f"This is NO_DATA, not a neutral signal.\n\n{evidence}"
                )

        # Safety net: if tool-call budget is exhausted but the model still
        # wants to call tools, force ONE final call WITHOUT tools to produce
        # a report.  Some models (gemma4, qwen3.5 on Ollama) never stop
        # calling tools on their own.
        if not report and tool_calls:
            _existing = sum(1 for m in state["messages"] if hasattr(m, "tool_calls") and m.tool_calls)
            _max_calls = get_config().get("max_analyst_tool_calls", 12)
            if _existing >= _max_calls - 1:  # about to hit the limit
                from langchain_core.messages import HumanMessage, SystemMessage
                lang = get_language_instruction()
                force_msg = HumanMessage(
                    content=f"You have all the data you need. Write your complete capital flow analysis report NOW. "
                            f"Output ONLY the report — no tool calls, no preamble.{lang}"
                )
                msgs = [SystemMessage(
                    content="You are a capital flow analyst. Write a complete report based on the tool results above. "
                            "Output ONLY the report text, no tool calls, no thinking out loud." + lang
                )] + list(state["messages"]) + [force_msg]
                final = llm.invoke(msgs)  # NO tools — force prose output
                report = final.content if hasattr(final, "content") and final.content else ""
                if not report or len(report.strip()) < 200:
                    report = "Capital Flow data collected but LLM failed to generate report. See raw data in tool outputs."
                result = final

        # Strip raw tool-call XML from report (some LLMs inline tool syntax)
        if report:
            import re as _re
            report = _re.sub(r'<invoke[^>]*>.*?</invoke>', '', report, flags=_re.DOTALL)
            report = _re.sub(r'<invoke[^>]*?/>', '', report)
            report = _re.sub(r'\n{3,}', '\n\n', report).strip()

        return {
            "messages": [result],
            "capital_flow_report": report,
        }

    return capital_flow_analyst_node


def _prefetch_capital_flow_data(ticker: str) -> str:
    """Pre-fetch data for Ollama models that don't reliably call tools."""
    from datetime import datetime, timedelta
    ed = datetime.now().strftime("%Y-%m-%d")
    sd = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    parts = []
    try:
        from quantconclave.agents.utils.capital_flow_tools import get_money_flow, get_margin_trading, get_hsgt_flow
        r1 = get_money_flow.invoke({"ticker": ticker, "start_date": sd, "end_date": ed})
        parts.append(f"=== Money Flow Data ===\n{str(r1)[:6000]}")
    except Exception as e:
        parts.append(f"Money Flow: unavailable ({e})")
    try:
        r2 = get_margin_trading.invoke({"ticker": ticker, "start_date": sd, "end_date": ed})
        parts.append(f"=== Margin Trading Data ===\n{str(r2)[:3000]}")
    except Exception:
        pass
    try:
        r3 = get_hsgt_flow.invoke({"start_date": sd, "end_date": ed})
        parts.append(f"=== Northbound Flow Data ===\n{str(r3)[:2000]}")
    except Exception:
        pass
    return "\n\n".join(parts) if parts else ""
