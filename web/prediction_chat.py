"""Prediction Agent — ML+LLM stock forecasting specialist.

Mirrors web/ai_pick_agent.py in structure. The agent has access to:
predict_stock_price, get_smart_money_score, get_realtime_quote,
get_stock_data, get_money_flow, get_indicators.

Uses the same SSE streaming pattern (chat-tool-call → chat-done)
and shared chat_threads/chat_messages tables.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Generator, Optional

from web.results_store import get_chat_messages, save_chat_message

logger = logging.getLogger(__name__)


# ── System Prompt ────────────────────────────────────────────────────

def build_prediction_system_prompt(config: dict, lang: str = "Chinese") -> str:
    """Build the Prediction Agent system prompt."""
    today = datetime.now()
    return (
        f"You are **🔮 CapitalRadar Prediction Agent**, an ML+LLM hybrid forecasting "
        f"specialist for Chinese A-share stocks. You combine XGBoost models with "
        f"institutional capital-flow intelligence.\n\n"
        f"**Current date**: {today.strftime('%Y-%m-%d')} (weekday: {today.strftime('%A')})\n\n"
        f"## Your Mission\n\n"
        f"Provide actionable stock predictions by running quantitative ML models "
        f"AND qualitative behavior analysis. Help retail investors time their "
        f"entry/exit decisions with data-backed forecasts.\n\n"
        f"## Core Principle\n\n"
        f"**Predictions must be data-backed and honest.** Never oversell a "
        f"prediction. If models are uncertain or conflicted, say so plainly. "
        f"The user's money is at stake — transparency &gt; confidence theater.\n\n"
        f"## On First Contact (New Conversation)\n\n"
        f"When the user greets you or starts a new conversation, you MUST:\n"
        f"1. Introduce yourself briefly (who you are, what you can do)\n"
        f"2. Call get_market_overview FIRST to give the user current market "
        f"context (index performance, overall sentiment)\n"
        f"3. Present 3-5 structured options for the user to choose from:\n"
        f"   - 2-3 hot/trending stocks worth predicting (mention ticker + why)\n"
        f"   - 1 prediction style option (quick: direction only / full: price range + behavior)\n"
        f"   - 1 comparison option (\"compare two stocks side by side\")\n"
        f"4. Ask the user which they'd like, or let them type any ticker\n\n"
        f"## During Prediction\n\n"
        f"When the user asks to predict a specific stock, follow this MANDATORY process:\n\n"
        f"### Step 1: Check Institutional Backing\n"
        f"Call get_smart_money_score(ticker) FIRST. Before showing any ML numbers, "
        f"explain what the score means in plain Chinese:\n"
        f"- \"主力资金目前正在[买入/卖出]，力度[强/中等/不足]\"\n"
        f"- \"这个分数说明机构参与度[高/中/低]，所以ML预测的可靠性[相应调整]\"\n"
        f"- If score < 50: warn the user that predictions are less reliable without institutional backing\n\n"
        f"### Step 2: Run ML Prediction\n"
        f"Call predict_stock_price(ticker). Then EXPLAIN every section of the "
        f"output in plain language. Do NOT just paste the raw report. "
        f"Translate each number into a clear statement:\n\n"
        f"**Direction Probabilities：**\n"
        f"- \"ML模型预测未来5个交易日，上涨概率X%，下跌概率Y%\"\n"
        f"- \"这个预测的置信度是[高/中/低]\"\n"
        f"- \"也就是说，模型认为这支股票短期内[大概率上涨/大概率下跌/方向不明]\"\n\n"
        f"**Price Range (Confidence Interval)：**\n"
        f"- \"模型预估5日后的价格区间在 XX.XX - YY.YY 元之间\"\n"
        f"- \"中位数预测是 XX.XX 元（相比当前价的涨跌幅度）\"\n"
        f"- \"这个区间[很窄/适中/很宽]，区间越窄说明模型越确定\"\n\n"
        f"**Institutional Behavior Phase：**\n"
        f"- \"主力资金当前处于[建仓/洗盘/拉升/出货/退出]阶段\"\n"
        f"- \"这意味着接下来主力最可能做的是...\"\n\n"
        f"**Cross-Validation：**\n"
        f"- If ALIGNED: \"价格模型和方向模型意见一致，预测可信度提升\"\n"
        f"- If CONFLICT: \"价格模型和方向模型出现分歧！价格模型看涨但方向模型偏空——"
        f"这种矛盾本身就是一个重要信号，说明市场存在不确定性，操作需格外谨慎\"\n\n"
        f"### Step 3: Supplement with Live Data\n"
        f"After the prediction, call get_realtime_quote for live price, "
        f"and optionally get_stock_data/get_money_flow if user wants more detail.\n"
        f"Cross-reference: does the live market picture support or contradict the ML prediction?\n\n"
        f"### Step 4: Cross-Validate All Signals\n"
        f"Combine Smart Money Score + ML prediction + live data into one verdict:\n"
        f"- All aligned → strongest signal, tell the user\n"
        f"- Smart money contradicts ML → flag this clearly\n"
        f"- ML models disagree internally → flag this clearly\n\n"
        f"### Step 5: Give Actionable Next Steps\n"
        f"Always end with options:\n"
        f"- \"要我深入分析这支股票的其他方面吗？\"\n"
        f"- \"要不要对比一下同行业的另一支股票？\"\n"
        f"- \"需要我解释某个具体指标的含义吗？\"\n\n"
        f"## Explanation Rules\n"
        f"- Direction probability >=70% = strong signal, 55-69% = moderate, <55% = uncertain\n"
        f"- Price interval width <8% = tight prediction (high confidence), >20% = wide (low confidence)\n"
        f"- Show Smart Money Score alongside prediction — capital flow validates or refutes the ML\n"
        f"- When models agree and capital flow supports: strongest signal. Say so.\n"
        f"- When models disagree: flag the conflict, explain both sides, advise caution\n"
        f"- Always give the user a clear next step (another stock? deeper analysis? compare?)\n\n"
        f"## Critical: Ticker Verification (MANDATORY before ANY tool call)\n\n"
        f"Before calling ANY prediction or data tool, you MUST verify the "
        f"stock ticker is correct:\n\n"
        f"1. When the user mentions a company by NAME (e.g. '中微半导', '赛力斯', '茅台'):\n"
        f"   - Call `verify_ticker(company_name)` to look up the correct ticker code\n"
        f"   - This returns the canonical ticker (e.g. '688380.SH'), full company "
        f"     name, and market cap — use THIS ticker for all subsequent tool calls\n"
        f"   - NEVER guess a ticker from a company name — ALWAYS verify first\n\n"
        f"2. When the user gives a bare 6-digit code (e.g. '688380'):\n"
        f"   - Still call `verify_ticker(code)` to confirm the correct exchange "
        f"     suffix (.SH vs .SZ) and get the full company name\n\n"
        f"3. When the user gives a complete ticker (e.g. '601127.SH'):\n"
        f"   - Still call `verify_ticker(ticker)` to confirm the company name matches "
        f"     what the user expects — if the returned name doesn't match what the "
        f"     user said, warn them immediately\n\n"
        f"4. After verification, always echo back:\n"
        f"   '已确认：TICKER = 公司全称（市值：XX亿）'\n"
        f"   This gives the user a chance to correct you if the ticker is wrong.\n\n"
        f"## Important Rules\n"
        f"- predict_stock_price is your core tool — always use it for stock predictions\n"
        f"- get_smart_money_score is mandatory before making any buy/sell suggestion\n"
        f"- NEVER make price guarantees or promise specific returns\n"
        f"- Keep the conversation flowing — ask questions, offer options, engage the user\n"
        f"- Write in {lang}\n"
    )


# ── Tool Building ────────────────────────────────────────────────────

def build_prediction_tools(config: dict, llm):
    """Return the tool set for the Prediction Agent."""
    from langchain_core.tools import tool
    from typing import Annotated

    from capitalradar.sector_scan.smart_money_score import compute_smart_money_score
    from capitalradar.agents.utils.core_stock_tools import get_stock_data
    from capitalradar.agents.utils.technical_indicators_tools import get_indicators
    from capitalradar.agents.utils.capital_flow_tools import get_money_flow
    from capitalradar.agents.utils.intraday_tools import get_realtime_quote

    @tool
    def verify_ticker(
        query: Annotated[str, "Company name or ticker code to look up (e.g. '中微半导', '688380', '601127.SH')"],
    ) -> str:
        """Verify a company name or ticker and return the canonical ticker.

        Call this BEFORE any prediction or data tool to ensure you have
        the correct ticker. Returns: canonical ticker, full company name,
        market cap, and exchange. NEVER guess a ticker — always verify.
        """
        from web.ticker_utils import normalize_ticker, resolve_company_name, verify_cn_stock

        # Try as ticker first
        normalized = normalize_ticker(query)
        if normalized and any(c.isdigit() for c in normalized):
            norm, name = resolve_company_name(normalized)
            if not name:
                name = verify_cn_stock(normalized)
            if name:
                return (
                    f"✅ 已验证: **{norm}** = {name}\n\n"
                    f"请使用 ticker `{norm}` 进行后续所有工具调用。"
                )
            # Valid format but still unknown — proceed with warning
            if _is_valid_ticker_format(normalized):
                return (
                    f"⚠️ ticker `{normalized}` 格式正确，但未能解析公司名称。\n"
                    f"将使用此代码进行后续操作。如不匹配请告知正确代码。"
                )
            return f"⚠️ 无法通过 ticker '{normalized}' 找到公司信息。请尝试用公司名称搜索。"

        # Try as company name — built-in CN lookup first (fast, no API)
        cn_ticker = verify_cn_stock(query)
        if cn_ticker:
            _, cn_name = resolve_company_name(cn_ticker)
            if not cn_name:
                cn_name = verify_cn_stock(cn_ticker)
            return (
                f"✅ 搜索 '{query}' 找到匹配:\n"
                f"**Ticker**: `{cn_ticker}`\n"
                f"**公司名称**: {cn_name}\n\n"
                f"请使用 ticker `{cn_ticker}` 进行后续所有工具调用。"
            )

        # Try yfinance Search as fallback
        try:
            import yfinance as yf
            results = yf.Search(query=query, news_count=0).quotes
            if results:
                top = results[0]
                symbol = top.get("symbol", "")
                name = top.get("shortname") or top.get("longname") or ""
                exchange = top.get("exchange", "")
                mc = top.get("marketCap", 0) or 0
                mc_str = ""
                if mc > 0:
                    mc_yi = mc / 1e8
                    if mc_yi >= 10000:
                        mc_str = f" | 市值: {mc_yi/10000:.1f}万亿"
                    else:
                        mc_str = f" | 市值: {mc_yi:.0f}亿"

                # For A-shares, normalize the suffix
                norm_symbol = normalize_ticker(symbol)

                lines = [f"✅ 搜索 '{query}' 找到匹配:"]
                lines.append(f"**Ticker**: `{norm_symbol}`")
                lines.append(f"**公司名称**: {name}")
                lines.append(f"**交易所**: {exchange}{mc_str}")
                lines.append("")
                lines.append(f"请使用 ticker `{norm_symbol}` 进行后续所有工具调用。")

                # Show top 3 results if multiple matches
                if len(results) > 1:
                    lines.append("")
                    lines.append("其他可能的匹配:")
                    for r in results[1:3]:
                        s = r.get("symbol", "?")
                        n = r.get("shortname", s)
                        lines.append(f"- `{normalize_ticker(s)}` = {n}")
                return "\n".join(lines)
            else:
                return (
                    f"❌ 未找到 '{query}' 的匹配结果。\n\n"
                    f"请尝试:\n"
                    f"- 使用完整的股票代码（如 '688380.SH'）\n"
                    f"- 使用准确的公司全称\n"
                    f"- 确认公司名称没有错别字"
                )
        except Exception as e:
            # Fallback: try resolve_company_name directly
            norm, name = resolve_company_name(query)
            if name:
                return f"✅ 已验证: **{norm}** = {name}\n\n请使用 ticker `{norm}` 进行后续所有工具调用。"
            return f"❌ 无法验证 '{query}': {e}\n\n请提供一个准确的股票代码（如 '688380.SH'）。"

    @tool
    def predict_stock_price(
        ticker: Annotated[str, "Stock ticker (e.g. 601127.SH, 000001.SZ)"],
    ) -> str:
        """Run the full ML+LLM prediction pipeline for a stock.

        Returns 5-day and 20-day:
        - Direction probabilities (up/down, calibrated)
        - Price range confidence intervals (lower/median/upper)
        - Institutional behavior phase classification
        - Cross-model validation verdict
        - Actionable guidance for entry/exit timing.

        Call this FIRST before supplementing with other tools."""
        from capitalradar.prediction import PredictionAgent
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            agent = PredictionAgent()
            report = agent.predict(ticker, today, config)
            return report.to_markdown()
        except Exception as e:
            return f"Prediction failed for {ticker}: {e}"

    @tool
    def get_smart_money_score(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH)"],
    ) -> str:
        """Get the Smart Money Score — 6-dimension institutional flow analysis.

        Dimensions: ①Scale ②Direction ③Persistence ④Alignment ⑤Confirmation ⑥Stage.
        ≥70=主力确认, 50-69=有主力, 30-49=信号弱, <30=无主力.
        Use this to cross-validate ML predictions against capital flow reality."""
        total, breakdown = compute_smart_money_score(ticker, config)

        # Data-source outage / all-zero snapshot must not be reported as a
        # bearish score — be honest. _data_unavailable (raw flow all-zero or
        # missing) is checked first.
        if breakdown.get("_data_unavailable"):
            from capitalradar.sector_scan.smart_money_score import format_sms_unavailable
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
        # a numeric "score".
        SCORE_DIMS = ("scale", "direction", "persistence", "alignment", "confirmation", "stage")
        lines = [f"## Smart Money Score: {ticker}", f"**总分: {total}/100**", ""]
        for dim, info in breakdown.items():
            if dim not in SCORE_DIMS:
                continue
            label = DIM_LABELS.get(dim, dim)
            score = info.get("score", 0)
            detail = info.get("detail", "")
            lines.append(f"- **{label}**: {score}/100 — {detail}")
        if total >= 70:
            lines.append("\n✅ 主力确认 — ML bullish 预测有资金面支撑")
        elif total >= 50:
            lines.append("\n⚠️ 有主力参与 — ML 预测置信度相应调整")
        else:
            lines.append("\n❌ 主力参与不足 — ML 预测缺乏资金面印证")
        return "\n".join(lines)

    @tool
    def get_market_overview(
        dummy: Annotated[str, "Ignored, pass empty string"] = "",
    ) -> str:
        """Get A-share market overview: index performance, trends, sentiment.

        Returns today's snapshot + 5-day + 20-day change for major indices.
        Call this FIRST to give users macro context before predictions.
        """
        try:
            from capitalradar.dataflows.tushare_data import _get_pro
            pro = _get_pro()
            today_str = datetime.now().strftime("%Y%m%d")

            cal = pro.trade_cal(exchange="SSE", start_date="20250101", end_date=today_str)
            cal_open = cal[cal["is_open"] == 1]
            if cal_open.empty:
                return "Market index data unavailable."
            trade_dates = sorted(cal_open["cal_date"].tolist(), reverse=True)
            start_d = trade_dates[min(29, len(trade_dates) - 1)]
            end_d = trade_dates[0]

            index_names = {
                "000001.SH": ("上证指数", "大盘蓝筹"),
                "399001.SZ": ("深证成指", "深市整体"),
                "000300.SH": ("沪深300", "核心资产"),
                "399006.SZ": ("创业板指", "成长股"),
                "000688.SH": ("科创50", "科技股"),
            }

            rows = []
            for code, (name, label) in index_names.items():
                df = pro.index_daily(ts_code=code, start_date=start_d, end_date=end_d)
                if df is None or len(df) == 0:
                    continue
                df = df.sort_values("trade_date")
                closes = df["close"].astype(float).tolist()
                if len(closes) < 2:
                    continue
                latest = closes[-1]
                prev = closes[-2]
                pct_1d = (latest - prev) / prev * 100 if prev else 0
                d5 = closes[-6] if len(closes) >= 6 else closes[0]
                pct_5d = (latest - d5) / d5 * 100 if d5 else 0
                d20 = closes[-21] if len(closes) >= 21 else closes[0]
                pct_20d = (latest - d20) / d20 * 100 if d20 else 0

                if pct_5d > 2 and pct_20d > 0:
                    trend = "持续走强"
                elif pct_5d > 1:
                    trend = "短期反弹" if pct_20d < 0 else "震荡偏强"
                elif pct_5d < -1 and pct_20d < -3:
                    trend = "持续走弱"
                elif pct_5d < -2:
                    trend = "短期回调"
                elif abs(pct_5d) <= 1:
                    trend = "横盘整理"
                else:
                    trend = "方向不明"

                rows.append((name, label, latest, pct_1d, pct_5d, pct_20d, trend))

            if not rows:
                return "Market index data unavailable."

            latest_date = end_d
            lines = [f"## Market Overview ({latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]})", ""]
            lines.append("| Index | Close | Today | 5-Day | 20-Day | Trend |")
            lines.append("|-------|-------|-------|-------|--------|-------|")
            for name, label, close, p1, p5, p20, trend in rows:
                e1 = "+" if p1 >= 0 else ""
                e5 = "+" if p5 >= 0 else ""
                e20 = "+" if p20 >= 0 else ""
                lines.append(f"| {name} | {close:.1f} | {e1}{p1:.2f}% | {e5}{p5:.2f}% | {e20}{p20:.2f}% | {trend} |")

            up_5d = sum(1 for r in rows if r[3] > 0)
            lines.append("")
            if up_5d >= 4:
                lines.append(f"**Market sentiment: Strongly bullish** ({up_5d}/5 indices positive). Broad rally — predictions should account for momentum.")
            elif up_5d >= 2:
                lines.append(f"**Market sentiment: Mixed** ({up_5d}/5 indices positive). Sector rotation likely — stock selection matters more than market timing.")
            else:
                lines.append(f"**Market sentiment: Bearish** ({up_5d}/5 indices positive). Defensive posture advised — predictions should factor in market headwinds.")
            lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"Market overview unavailable: {e}"

    return [
        verify_ticker,
        predict_stock_price, get_smart_money_score, get_market_overview,
        get_realtime_quote, get_stock_data, get_money_flow, get_indicators,
    ]


def _is_valid_ticker_format(s: str) -> bool:
    """Check if a string looks like a valid ticker (not necessarily known)."""
    import re
    if re.match(r'^\d{6}\.(SH|SZ|SS|BJ)$', s, re.IGNORECASE):
        return True
    if re.match(r'^\d{6}$', s):
        return True
    if re.match(r'^[A-Z]{1,5}(\.[A-Z]{1,3})?$', s):
        return True
    return False


def _format_market_cap(ticker: str) -> str:
    """Try to get market cap from yfinance, return formatted string or empty."""
    try:
        import yfinance as yf
        import sys, io
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            info = yf.Ticker(ticker).info
            mc = info.get("marketCap", 0) or 0
            if mc > 0:
                mc_yi = mc / 1e8
                if mc_yi >= 10000:
                    return f" | 市值: {mc_yi/10000:.1f}万亿"
                return f" | 市值: {mc_yi:.0f}亿"
        finally:
            sys.stderr = old
    except Exception:
        pass
    return ""


# ── Agent Creation ───────────────────────────────────────────────────

def create_prediction_agent(config: dict, lang: Optional[str] = None):
    """Create the Prediction Agent LLM instance with tools bound."""
    from capitalradar.llm_clients import create_llm_client, resolve_role_llm

    provider, deep_model, _ = resolve_role_llm(config, "deep")

    client = create_llm_client(
        provider=provider, model=deep_model,
        base_url=config.get("backend_url"), timeout=120,
    )
    llm = client.get_llm()
    tools = build_prediction_tools(config, llm)
    llm_with_tools = llm.bind_tools(tools)
    system_prompt = build_prediction_system_prompt(
        config, lang or config.get("output_language", "Chinese"),
    )
    return llm_with_tools, tools, system_prompt, llm


# ── SSE Helpers ──────────────────────────────────────────────────────

def _sse_event(name: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


# ── SSE Stream Generator ─────────────────────────────────────────────

def stream_prediction_chat(
    thread_id: str, question: str, config: dict,
    lang: Optional[str] = None,
) -> Generator[str, None, None]:
    """SSE generator for Prediction Agent chat.

    Follows the exact same agent-loop pattern as stream_aipick_chat.
    """
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

    try:
        llm_with_tools, tools, system_prompt, llm = create_prediction_agent(config, lang)
    except Exception as e:
        yield _sse_event("chat-error", {"message": str(e)})
        return

    save_chat_message(config, thread_id, "user", question)
    yield _sse_event("chat-stream-start", {"thread_id": thread_id})

    try:
        history_msgs = get_chat_messages(config, thread_id)
    except Exception:
        history_msgs = []

    messages = [SystemMessage(content=system_prompt)]
    for h in history_msgs:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))

    tool_map = {t.name: t for t in tools}
    tool_call_records = []

    for iteration in range(10):
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            yield _sse_event("chat-error", {"message": f"LLM error: {str(e)}"})
            return

        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})
                safe_args = {k: str(v)[:500] for k, v in tool_args.items()}
                yield _sse_event("chat-tool-call", {
                    "tool_name": tool_name, "args": safe_args,
                })

                func = tool_map.get(tool_name)
                if func:
                    try:
                        result = func.invoke(tool_args)
                        result_str = str(result)[:8000]  # prediction reports can be long
                    except Exception as e:
                        result_str = f"Error: {str(e)}"
                else:
                    result_str = (
                        f"Tool '{tool_name}' not found. "
                        f"Available: {list(tool_map.keys())}"
                    )

                yield _sse_event("chat-tool-result", {
                    "tool_name": tool_name,
                    "result_snippet": result_str[:500],
                })
                tool_call_records.append({
                    "tool_name": tool_name,
                    "args": safe_args,
                    "result_snippet": result_str[:2000],
                })
                messages.append(ToolMessage(
                    content=result_str, tool_call_id=tc.get("id", ""),
                ))
        else:
            final_text = (
                response.content
                if hasattr(response, "content") else str(response)
            )
            tc_json = (
                json.dumps(tool_call_records, ensure_ascii=False)
                if tool_call_records else ""
            )
            msg_id = save_chat_message(
                config, thread_id, "assistant", final_text, tc_json,
            )
            yield _sse_event("chat-done", {
                "full_response": final_text,
                "message_id": msg_id,
                "tool_calls_count": len(tool_call_records),
            })
            return

    yield _sse_event("chat-error", {
        "message": (
            "Agent reached maximum tool-call iterations "
            "without producing a final answer."
        ),
    })
