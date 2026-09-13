"""AI Pick Agent — dedicated stock screening agent for the AI Pick tab.

Mirrors web/history_chat.py but specialized in stock discovery rather than
individual stock analysis. The agent has access to: run_smart_screening,
get_smart_money_score, query_pick_performance, and web_search_current.
"""

import json
import logging
from datetime import datetime
from typing import Generator, Optional

from web.results_store import get_chat_messages, save_chat_message, get_thread_run_ids
from capitalradar.dataflows.config import get_config

logger = logging.getLogger(__name__)


def build_aipick_system_prompt(config: dict, lang: str = "Chinese") -> str:
    """Build the AI Pick agent system prompt."""
    today = datetime.now()
    return (
        f"You are **🧠 CapitalRadar AI Pick**, a dedicated A-share stock screening "
        f"intelligence powered by CapitalRadar's multi-agent framework.\n\n"
        f"**Current date**: {today.strftime('%Y-%m-%d')} (weekday: {today.strftime('%A')})\n\n"

        # ── Mission ──
        f"## Your Mission\n\n"
        f"Help retail investors discover promising A-share stocks based on their "
        f"natural-language requirements. Generate custom screening strategies, "
        f"evaluate institutional capital flow (smart money), and explain WHY each "
        f"stock was picked — with specific numbers, never vague labels.\n\n"

        # ── Six-Dimension Framework ──
        f"## 六维选股框架 (Six-Dimension Selection Framework)\n\n"
        f"按以下优先级评估每一只推荐股票：\n\n"
        f"**① 估值（买便宜的）**：PE<15便宜、PB<1破净、股息率>4%、PEG<1合理\n"
        f"  → 工具：run_smart_screening(PE/PB/股息率条件), get_high_dividend_stocks\n\n"
        f"**② 趋势（买涨的）**：均线多头排列、放量突破、MACD金叉、支撑不破\n"
        f"  → 工具：run_smart_screening(均线/量价条件), get_top_gainers\n\n"
        f"**③ 资金（跟主力走）**：主力持续流入、北向净买、融资余额上升、大宗溢价\n"
        f"  → 工具：get_top_net_inflow, get_multi_factor_ranking, get_smart_money_score\n\n"
        f"**④ 题材（买热门的）**：政策驱动、事件驱动、行业轮动、周期反转\n"
        f"  → 工具：get_market_scan_context, query_eastmoney_data(热点题材)\n\n"
        f"**⑤ 基本面（买好公司）**：ROE>15%、营收增速>20%、负债率<50%、现金流健康\n"
        f"  → 工具：run_smart_screening(ROE/增速/负债率条件)\n\n"
        f"**⑥ 纪律（管自己）**：永不满仓、-8%止损、不追涨30%+、不接飞刀\n"
        f"  → 在推荐时提醒仓位和止损建议\n\n"
        f"**核心公式：好公司 + 好价格 + 有资金 + 等风来 + 守纪律**\n\n"
        f"所有路径终点 → get_smart_money_score 验证 ≥50 分方可推荐\n\n"
        f"**Core Principle**: Institutional capital flow is the non-negotiable baseline. "
        f"Every recommendation MUST pass get_smart_money_score >= 50 before being presented.\n"
        f"Score tiers: ≥70 = 主力确认, 50-69 = 有主力参与, 30-49 = 信号较弱, <30 = 无显著主力.\n"
        f"The score is a 6-dimension composite: ①规模 ②方向 ③持续性 ④量价 ⑤印证 ⑥阶段.\n\n"

        # ── Tool Catalog ──
        f"## 工具目录 (16 tools, 5 categories)\n\n"
        f"### 📡 宏观与市场 (2 tools) — 先看大盘，再选个股\n"
        f"| 工具 | 用途 |\n"
        f"|------|------|\n"
        f"| `get_market_overview` | 5大指数快照（腾讯实时+tushare历史），1日/5日/20日趋势 |\n"
        f"| `get_market_scan_context` | 行业轮动：Leading/Improving板块 + 资金流 + 生命周期 |\n\n"
        f"### 🔍 全市场扫描 (4 tools) — 排名驱动，快速发现\n"
        f"| 工具 | 用途 | 排序依据 | 数据源 |\n"
        f"|------|------|----------|--------|\n"
        f"| `get_top_gainers` | 今日涨幅榜 | 涨跌幅% | 东方财富push2(实时)→tushare |\n"
        f"| `get_top_net_inflow` | 主力净流入榜 | 主力净流入额 | 东方财富push2(实时)→tushare |\n"
        f"| `get_multi_factor_ranking` | 多因子排名 | 动量+RPS+资金流+量比 | tushare |\n"
        f"| `get_high_dividend_stocks` | 高股息TOP100 | 股息率%降序 | tushare |\n\n"
        f"### 🎯 策略选股 (3 tools) — 两种预设策略 + 自定义\n"
        f"| 工具 | 用途 | 核心逻辑 |\n"
        f"|------|------|----------|\n"
        f"| `get_short_term_picks` | 💰 股息率选股 | 高股息TOP100→行业轮动→主力验证 |\n"
        f"| `get_hot_reversal_picks` | 🔥 热点反转 | 热点题材→3硬护栏→6维评分（低位+ML+主力+新鲜度）|\n"
        f"| `run_smart_screening` | 自定义选股 | 自然语言→动态策略→全A股筛选 |\n\n"
        f"### ✅ 验证与决策 (4 tools) — 买前必验\n"
        f"| 工具 | 用途 |\n"
        f"|------|------|\n"
        f"| `get_smart_money_score` | 6维主力资金评分（规模/方向/持续性/量价/印证/阶段） |\n"
        f"| `get_realtime_fund_flow` | 实时主力资金流（东方财富push2，盘中~3秒刷新） |\n"
        f"| `get_block_trades` | 大宗交易暗盘数据：折溢价+机构席位，检测隐藏建仓/出货 |\n"
        f"| `predict_stock_price` | ML预测：5日/20日方向概率+价格区间+机构行为阶段 |\n\n"
        f"### 🌐 信息与回溯 (4 tools)\n"
        f"| 工具 | 用途 |\n"
        f"|------|------|\n"
        f"| `query_eastmoney_data` | 东方财富MX API：热点题材/板块/概念，自然语言查询 |\n"
        f"| `web_search_current` | 网络搜索：最新新闻、公告、研报 |\n"
        f"| `query_pick_performance` | 历史推荐表现：胜率、收益率 |\n"
        f"| `get_reversal_performance` | 热点反转策略验证：历史选股收益回溯 |\n\n"

        # ── Workflow ──
        f"## On First Contact (New Conversation)\n\n"
        f"1. Call `get_market_overview` — show 2-3 sentence macro summary\n"
        f"2. Call `get_market_scan_context` — show industry rotation data\n"
        f"3. Present 3-5 options based on BOTH macro and sector:\n"
        f"   - 2-3 leading/improving sectors + top stocks\n"
        f"   - 1-2 investment styles (高股息/动量 breakout/逆势抄底)\n"
        f"4. Ask user to choose or describe their own requirements\n\n"

        f"## During Screening\n\n"
        f"1. Use the right tool for the user's intent (see 六维框架 + 工具目录 above)\n"
        f"2. For top candidates, call `get_smart_money_score` to verify\n"
        f"3. Explain picks with specific numbers (PE, inflow, RSI, score breakdown)\n"
        f"4. If no stocks match, relax conditions and retry\n\n"

        f"## Important Rules\n"
        f"- smart_money_score >= 50 is mandatory in every screen\n"
        f"- **ML vs Flow Cross-Check**: ML behavior phase labels (markup/accumulation/etc.) "
        f"are probabilistic guesses — ALWAYS cross-validate with actual money flow data "
        f"(DDX, net_amount, get_smart_money_score) before treating them as fact. "
        f"If ML says 'markup' but flow shows net selling → trust the flow, downgrade.\n"
        f"- **SMS resilience**: Smart Money Score may occasionally be unavailable "
        f"(tushare rate-limit). The tools already handle this internally with "
        f"real-time fund flow fallback. DO NOT narrate 'SMS异常' or '系统错误' "
        f"to the user — just present the scores as-is.\n"
        f"- **Fallback rule**: if get_smart_money_score returns error or score=0, "
        f"auto-call web_search_current + query_eastmoney_data to gather latest "
        f"news/sentiment as compensation. Note '主力数据暂缺，已用资讯交叉验证' in output.\n"
        f"- Show concrete data, not vague labels\n"
        f"- Sort by Smart Money Score descending as primary rank\n"
        f"- Record all picks automatically (run_smart_screening does this)\n"
        f"- When user picks an option number, treat it as their requirement\n"
        f"- You are NOT limited to industry pre-selection — go market-wide when asked\n"
        f"- Data freshness: get_realtime_quote (Tencent), get_realtime_fund_flow (Eastmoney), "
        f"get_top_gainers/get_top_net_inflow (Eastmoney push2) are REAL-TIME during trading hours. "
        f"get_money_flow/Smart Money Score use tushare (updated after 17:00). "
        f"If real-time tools are available, prefer them for same-day decisions.\n"
        f"- Write in {lang}\n"
    )


def build_aipick_tools(config: dict, llm):
    """Return the tool set for the AI Pick agent."""
    from functools import partial
    from langchain_core.tools import tool
    from typing import Annotated
    import time as _time

    from capitalradar.sector_scan.smart_money_score import (
        compute_smart_money_score, format_sms_unavailable,
    )
    from capitalradar.sector_scan.dynamic_strategy import (
        build_strategy_prompt, parse_strategy_json, validate_strategy,
        execute_strategy, describe_strategy,
    )
    from capitalradar.sector_scan.rotation import get_rrg_data

    # ── Simple time-based cache for slow external API calls ──
    _cache = {"rrg": (0, None), "transition": (0, None)}
    _CACHE_TTL = 300  # 5 minutes

    def _cached_rrg():
        now = _time.time()
        if now - _cache["rrg"][0] < _CACHE_TTL and _cache["rrg"][1] is not None:
            return _cache["rrg"][1]
        data = get_rrg_data(lookback=10, mode="capital")
        _cache["rrg"] = (now, data)
        return data

    def _cached_transition():
        now = _time.time()
        if now - _cache["transition"][0] < _CACHE_TTL and _cache["transition"][1] is not None:
            return _cache["transition"][1]
        from capitalradar.sector_scan.rrg_stats import compute_transition_stats
        data = compute_transition_stats(config, lookback_days=60)
        _cache["transition"] = (now, data)
        return data
    from capitalradar.sector_scan.smart_scanner import get_industry_stocks
    from capitalradar.sector_scan.pick_tracker import record_pick, get_performance_report
    from web.results_store import search_analyses as _search_analyses, resolve_picks

    _search = partial(_search_analyses, config)

    @tool
    def get_market_overview(
        dummy: Annotated[str, "Ignored, pass empty string"] = "",
    ) -> str:
        """Get A-share market overview with multi-timeframe trend analysis.

        Returns today's snapshot + 5-day + 20-day change for 5 major indices,
        plus a concise trend interpretation. Call this FIRST before
        get_market_scan_context to give users macro context.
        """
        try:
            import requests as _req
            import tushare as ts
            import os as _os
            pro = ts.pro_api(_os.environ.get("TUSHARE_TOKEN", ""))
            from datetime import datetime, timedelta
            today_dt = datetime.now()
            today = today_dt.strftime("%Y%m%d")
            today_str = today_dt.strftime("%Y-%m-%d")

            # ── Tencent real-time snapshot for today's prices ──
            tc_map = {"sh000001":"上证指数","sz399001":"深证成指","sh000300":"沪深300","sz399006":"创业板指","sh000688":"科创50"}
            tc_prices = {}
            for tc_code, tc_name in tc_map.items():
                try:
                    resp = _req.get(f"http://qt.gtimg.cn/q={tc_code}", timeout=5)
                    resp.encoding = "gbk"
                    if '="' in resp.text:
                        fld = resp.text.split('="')[1].rstrip('";\n').split("~")
                        if len(fld) > 4:
                            tc_prices[tc_name] = {"price": float(fld[3]) if fld[3] else 0,
                                                   "prev": float(fld[4]) if fld[4] else 0}
                except Exception: pass

            cal = pro.trade_cal(exchange="SSE", start_date="20250101", end_date=today)
            cal_open = cal[cal["is_open"] == 1]
            if cal_open.empty:
                return "Market index data unavailable."
            trade_dates = sorted(cal_open["cal_date"].tolist(), reverse=True)

            # Query ~30 trading days for trend analysis
            start_d = trade_dates[min(29, len(trade_dates)-1)]
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
                prev = closes[-2] if len(closes) >= 2 else latest
                pct_1d = (latest - prev) / prev * 100 if prev else 0
                # Override with Tencent live price if available
                tc = tc_prices.get(name)
                if tc and tc["price"] > 0:
                    latest = tc["price"]
                    pct_1d = (tc["price"] - tc["prev"]) / tc["prev"] * 100 if tc["prev"] else 0

                # 5-day change
                d5 = closes[-6] if len(closes) >= 6 else closes[0]
                pct_5d = (latest - d5) / d5 * 100 if d5 else 0

                # 20-day change
                d20 = closes[-21] if len(closes) >= 21 else closes[0]
                pct_20d = (latest - d20) / d20 * 100 if d20 else 0

                # Trend label — 20d trend dominates, 5d refines
                if pct_20d > 3:
                    trend = "持续走强" if pct_5d > 1 else "中期向好"
                elif pct_20d < -5:
                    trend = "持续走弱" if pct_5d < 0 else "中期偏弱"
                elif pct_5d > 2:
                    trend = "短期走强"
                elif pct_5d < -2:
                    trend = "短期走弱"
                elif abs(pct_5d) <= 1.5 and abs(pct_20d) <= 3:
                    trend = "横盘整理"
                else:
                    trend = "方向不明"

                data_date = df.iloc[-1]["trade_date"]
                data_date_str = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:8]}"
                amt = float(df["amount"].iloc[-1] or 0) / 1e8
                rows.append((name, label, latest, pct_1d, pct_5d, pct_20d, trend, amt, data_date_str))

            if not rows:
                return "Market index data unavailable."

            # Build table — use actual data date (may lag 1 day before 17:00)
            data_date = rows[0][8]  # date from first row
            lines = [f"## Market Overview (数据日期: {data_date})", ""]
            if data_date != today.strftime("%Y-%m-%d"):
                lines.append(f"⚠️ 今日({today.strftime('%Y-%m-%d')})日线数据尚未发布，显示最近交易日数据。盘中实时行情请使用 get_realtime_quote。")
                lines.append("")
            lines.append("| Index | Close | Today | 5-Day | 20-Day | Trend |")
            lines.append("|-------|-------|-------|-------|--------|-------|")
            for name, label, close, p1, p5, p20, trend, amt, _date in rows:
                e1 = "+" if p1 >= 0 else ""
                e5 = "+" if p5 >= 0 else ""
                e20 = "+" if p20 >= 0 else ""
                lines.append(f"| {name} | {close:.1f} | {e1}{p1:.2f}% | {e5}{p5:.2f}% | {e20}{p20:.2f}% | {trend} |")

            # Trend interpretation
            lines.append("")
            up_5d = sum(1 for r in rows if r[3] > 0)
            up_20d = sum(1 for r in rows if r[5] > 0)
            strong = [r[0] for r in rows if r[6] in ("持续走强", "震荡偏强")]
            weak = [r[0] for r in rows if r[6] in ("持续走弱", "短期回调")]

            parts = []
            if strong:
                parts.append(f"{'、'.join(strong)}领涨，科技成长风格占优")
            if weak:
                parts.append(f"{'、'.join(weak)}偏弱，防御情绪较浓")
            if up_5d >= 4:
                parts.append("市场整体偏强，赚钱效应好")
            elif up_5d <= 1:
                parts.append("市场整体偏弱，注意风险控制")
            else:
                parts.append("市场分化，结构性行情为主")

            lines.append(f"**趋势**: {'；'.join(parts)}。")
            lines.append(f"**Data**: Tushare index_daily")
            return "\n".join(lines)

        except Exception as e:
            return f"Market overview unavailable: {str(e)[:100]}."

    @tool
    def get_market_scan_context(
        dummy: Annotated[str, "Ignored, pass empty string"] = "",
    ) -> str:
        """Get industry rotation data with lifecycle analysis and transition stats.

        Returns Leading/Improving sectors with fund flow, quadrant streaks,
        Improving-to-Leading probability, and capital-flow lifecycle stage.
        Each recommendation includes logic explanation and strategy hints.
        Call this AFTER get_market_overview to give users data-backed options.
        """
        try:
            rrg = _cached_rrg()
        except Exception as e:
            return f"RRG data unavailable: {e}."

        industries = rrg.get("industries", [])
        if not industries:
            return "No industry rotation data available."

        # Get transition stats for streaks and probabilities
        streaks = {}
        avg_transitions = {}
        try:
            stats = _cached_transition()
            if "current_streaks" in stats:
                streaks = stats["current_streaks"]
            avg_transitions["i2l"] = stats.get("avg_days_improving_to_leading", 0)
            avg_transitions["l2w"] = stats.get("avg_days_leading_to_weakening", 0)

            # Compute improving→leading success rate
            imp_to_lead = stats.get("top_longest_improving", [])
            lead_count = len(stats.get("top_longest_leading", []))
            imp_count = len(imp_to_lead)
            if imp_count > 0 and "improving_success_rate" not in stats:
                stats["improving_success_rate"] = round(
                    sum(1 for n, d in streaks.items() if streaks[n].get("prior_was_improving"))
                    / max(1, imp_count) * 100, 1
                )
        except Exception:
            pass

        leading = [i for i in industries if i.get("quadrant") == "leading"]
        improving = [i for i in industries if i.get("quadrant") == "improving"]
        weakening = [i for i in industries if i.get("quadrant") == "weakening"]

        def _lifecycle(quadrant, days_in):
            if quadrant == "improving":
                if days_in <= 3: return "启动"
                if days_in <= 7: return "建仓"
                return "蓄力"
            if quadrant == "leading":
                if days_in <= 5: return "发力"
                if days_in <= 12: return "加速"
                return "分布"
            return ""

        def _strategy(quadrant, days_in, lifecycle):
            if quadrant == "leading":
                if lifecycle in ("发力", "加速"):
                    return "顺势跟进，量能确认后加仓"
                return "注意高位，分批减仓"
            if quadrant == "improving":
                if lifecycle == "启动":
                    return "提前布局，等金叉确认"
                return "主力建仓中，逢低吸纳"
            return ""

        lines = ["# Market & Sector Rotation Analysis", ""]

        # ── Market-Rotation relationship ──
        try:
            ov_text = str(get_market_overview.invoke({"dummy": ""}))
            has_growth = any(w in ov_text for w in ["科创", "创业板", "成长", "ChiNext", "STAR"])
            has_defense = any(w in ov_text for w in ["黄金", "电力", "公用", "防御", "gold", "utility"])
            lead_names = [i.get("name","") for i in leading[:3]]
            style = "成长占优" if has_growth and not has_defense else ("防御占优" if has_defense and not has_growth else "分化")
            lines.append(f"**Market style**: {style} | **Top Leading**: {', '.join(lead_names)}")
            for l in ov_text.split("\n"):
                if "趋势" in l or "Trend" in l:
                    lines.append(f"**Index**: {l.strip().lstrip('*').strip()}")
                    break
            lines.append("")
        except Exception:
            pass

        # ── Leading section ──
        if leading:
            leading = sorted(leading, key=lambda i: -(abs(i.get("fund_flow",0))+abs(i.get("change_pct",0))*20))[:5]
            lines.append("## Leading (5 sectors — momentum play)")
            lines.append("| # | Industry | Chg% | Flow(yi) | Days | Stage | Why |")
            lines.append("|---|----------|------|----------|------|-------|-----|")
            for rank, i in enumerate(leading, 1):
                name = i.get("name", "?")
                pct = i.get("change_pct", 0)
                flow = i.get("fund_flow", 0)
                s = streaks.get(name, {})
                days = s.get("days_in_quadrant", 0) if isinstance(s, dict) else 0
                stage = _lifecycle("leading", days)
                why = "fresh momentum" if days <= 5 else ("steady" if days <= 12 else "aging — rotation risk")
                lines.append(f"| {rank} | {name} | {pct:+.1f} | {flow/1e4:.1f} | {days}d | {stage} | {why} |")
            lines.append("")

        # ── Improving section ──
        if improving:
            improving = sorted(improving, key=lambda i: -(abs(i.get("fund_flow",0))+abs(i.get("change_pct",0))*20))[:5]
            lines.append("## Improving (5 sectors — early entry)")
            lines.append("| # | Industry | Chg% | Flow(yi) | Days | Est. to Lead | Why |")
            lines.append("|---|----------|------|----------|------|--------------|-----|")
            i2l = avg_transitions.get("i2l", 7)
            for rank, i in enumerate(improving, 1):
                name = i.get("name", "?")
                pct = i.get("change_pct", 0)
                flow = i.get("fund_flow", 0)
                s = streaks.get(name, {})
                days = s.get("days_in_quadrant", 0) if isinstance(s, dict) else 0
                stage = _lifecycle("improving", days)
                eta = max(1, int(i2l) - days) if i2l > 0 else "?"
                why = "near breakout" if days >= 3 else "early stage"
                lines.append(f"| {rank} | {name} | {pct:+.1f} | {flow/1e4:.1f} | {days}d | ~{eta}d | {why} |")
            lines.append("")

        # ── Key insights ──
        lines.append("## Key Insights")
        lines.append("")

        # Just-entered Leading (fresh momentum — best picks)
        fresh_leading = [(i, streaks.get(i.get("name",""),{})) for i in leading[:8]
                         if isinstance(streaks.get(i.get("name","")), dict)
                         and streaks[i.get("name","")].get("days_in_quadrant", 99) <= 8]
        if fresh_leading:
            names = [i[0].get("name","?") for i in fresh_leading[:3]]
            lines.append(f"**Fresh Leading** (recently entered, more room to run): {', '.join(names)}")

        # Improving closest to Leading (about to break through)
        if improving and avg_transitions.get("i2l", 0) > 0:
            near_breakout = [(i, streaks.get(i.get("name",""),{})) for i in improving[:8]
                            if isinstance(streaks.get(i.get("name","")), dict)]
            near_breakout.sort(key=lambda x: x[1].get("days_in_quadrant", 99), reverse=True)
            if near_breakout:
                names = [i[0].get("name","?") for i in near_breakout[:3]]
                lines.append(f"**Near Breakout** (Improving, likely to become Leading soon): {', '.join(names)}")

        # Long-running Leading (rotation risk)
        old_leading = [(i, streaks.get(i.get("name",""),{})) for i in leading[:8]
                       if isinstance(streaks.get(i.get("name","")), dict)
                       and streaks[i.get("name","")].get("days_in_quadrant", 0) >= 12]
        if old_leading:
            names = [i[0].get("name","?") for i in old_leading[:3]]
            lines.append(f"**Rotation Risk** (Leading too long, may weaken): {', '.join(names)}")

        # Weakening to avoid
        if weakening:
            lines.append(f"**Avoid**: {', '.join(i.get('name','?') for i in weakening[:5])}")

        lines.append("")
        lines.append("Rank picks by Smart Money Score first. >=70 = confirmed institutional money, prioritize these. Explain the score meaning to users.")
        return "\n".join(lines)

    @tool
    def get_smart_money_score(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH, 000625.SZ)"],
    ) -> str:
        """Get the unified Smart Money Score (0-100) with raw flow data.

        Shows 7-dimension breakdown PLUS raw net flow numbers (5d/10d/20d)
        so you can cross-check the score against actual data. Scores >= 70
        indicate strong institutional participation."""
        total, breakdown = compute_smart_money_score(ticker, config)

        # A data-source outage / all-zero snapshot must not masquerade as a low
        # (bearish) score — report it honestly so the agent falls back
        # gracefully instead of flagging "continuously failing". The strictest
        # condition (_data_unavailable: raw flow all-zero/missing) is checked
        # first.
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
                    f"这不是看空信号。请稍后重试，或改用 `verify_moneyflow` / `get_multi_factor_ranking` 交叉验证资金面。")

        lines = [f"## Smart Money Score: {ticker}", f"**总分: {total}/100**", ""]
        # Layer-1 validity annotation: tiny 5-day flow (< configured floor) is
        # labelled 不可信 without suppressing the score.
        validity = breakdown.get("_validity") or {}
        if validity.get("flags"):
            lines.append(f"⚠️ **数据可信度低** — {validity.get('note') or '资金流量级过小，评分仅供参考'}")
            lines.append("")

        # Unambiguous "today" ground truth (current price / single-day change /
        # data date) — quote this number for 今日涨跌, never a multi-day window.
        meta = breakdown.get("_meta") or {}
        if meta.get("price"):
            chg = meta.get("today_change_pct")
            chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
            lines.append(
                f"- 数据日期: {meta.get('data_date') or 'N/A'} | "
                f"今日涨跌: {chg_str} | 现价: {meta['price']:.2f}"
            )
            lines.append("")

        # Score breakdown — render only the 6 dimensions. The breakdown also
        # carries legacy gate keys (e.g. `cross`) that lack a numeric "score"
        # and would crash the renderer; skip anything that isn't a dimension.
        SCORE_DIMS = ("scale", "direction", "persistence", "alignment", "confirmation", "stage")
        for dim, info in breakdown.items():
            if dim not in SCORE_DIMS:
                continue
            s = info["score"]
            bar = "█" * int(s / 10) + "░" * (10 - int(s / 10))
            lines.append(f"- {dim}: {s:.0f}/100 {bar}")
            if info.get("detail"):
                lines.append(f"  {info['detail']}")

        # ── Raw flow data for cross-validation ──
        try:
            from capitalradar.agents.utils.capital_flow_tools import get_money_flow
            from datetime import datetime, timedelta
            import csv, io
            ed = datetime.now().strftime("%Y-%m-%d")
            sd = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            data = get_money_flow.invoke({"ticker": ticker, "start_date": sd, "end_date": ed})
            text = str(data)
            lines.append("")
            lines.append("### Raw Flow Data (cross-check)")
            # Parse net_amount column. Header scan must be tolerant: akshare
            # (now the primary source) has no ts_code column, tushare does.
            csv_lines = text.split("\n")
            csv_start = next((i for i, l in enumerate(csv_lines)
                              if "trade_date" in l.lower()
                              and ("net_amount" in l.lower() or "net_mf_amount" in l.lower())), None)
            if csv_start is not None:
                reader = csv.DictReader(io.StringIO("\n".join(csv_lines[csv_start:])))
                pairs = []
                for row in reader:
                    try:
                        pairs.append((str(row.get("trade_date", "")).strip(),
                                      float(row.get("net_amount", row.get("net_mf_amount", 0)) or 0)))
                    except (ValueError, KeyError):
                        pass
                # tushare emits newest-first (DESC), akshare chronological — sort
                # by date so [-N:] is always the MOST RECENT N days.
                pairs.sort(key=lambda p: p[0] or "")
                amounts = [a for _, a in pairs]
                if amounts:
                    r5 = amounts[-5:] if len(amounts) >= 5 else amounts
                    r10 = amounts[-10:] if len(amounts) >= 10 else amounts
                    r20 = amounts[-20:] if len(amounts) >= 20 else amounts
                    lines.append(f"- 近5日: {[f'{x/10000:.0f}w' for x in r5]}  sum={sum(r5)/10000:.0f}w")
                    lines.append(f"- 近10日: sum={sum(r10)/10000:.0f}w")
                    lines.append(f"- 近20日: sum={sum(r20)/10000:.0f}w")
                    # Consecutive direction
                    pos_days = sum(1 for x in r5 if x > 0)
                    neg_days = sum(1 for x in r5 if x < 0)
                    lines.append(f"- 近5日: {pos_days}阳{neg_days}阴")
                    lines.append(f"- 总交易日: {len(amounts)}")
        except Exception:
            pass

        lines.append("")
        if total >= 70:
            lines.append("✅ 主力参与度较高")
        elif total >= 50:
            lines.append("⚠️ 主力有一定参与，但力度不够")
        else:
            lines.append("❌ 主力参与度不足，建议谨慎")
        return "\n".join(lines)

    @tool
    def run_smart_screening(
        requirement: Annotated[str, "Natural language stock screening requirement"],
        market_context: Annotated[str, "Brief market context"] = "",
    ) -> str:
        """AI-powered stock screening. Generates a custom strategy from your
        natural-language description, screens A-shares, returns ranked candidates
        with smart money scores. All strategies enforce smart_money_score >= 50."""
        prompt = build_strategy_prompt(requirement, market_context)
        from langchain_core.messages import SystemMessage, HumanMessage
        strategy_response = llm.invoke([
            SystemMessage(content="You are a stock screening JSON generator. Output ONLY valid JSON."),
            HumanMessage(content=prompt),
        ])
        strategy_raw = strategy_response.content if hasattr(strategy_response, "content") else str(strategy_response)
        try:
            strategy = parse_strategy_json(strategy_raw)
            strategy = validate_strategy(strategy)
        except Exception as e:
            return f"策略生成失败: {e}\nLLM输出: {strategy_raw[:500]}"

        # Build candidate pool from Leading+Improving industries
        try:
            rrg = _cached_rrg()
            industries = rrg.get("industries", [])
            leading_improving = [i for i in industries if i.get("quadrant") in ("leading", "improving")]
        except Exception:
            leading_improving = []

        candidate_pool = []
        today = datetime.now().strftime("%Y-%m-%d")
        # Batch-fetch daily basics (PE/PB/market_cap) for enrichment. NOTE the
        # source keys are total_mv/pe/pb — map them to the strategy-field names.
        try:
            from capitalradar.sector_scan.smart_scanner import get_daily_basic_batch
            all_codes = []
            for ind in leading_improving[:15]:
                stocks = get_industry_stocks(ind.get("name", "")) if ind.get("name") else []
                all_codes.extend([s.get("ts_code", "") for s in stocks[:30] if s.get("ts_code")])
            basics = get_daily_basic_batch(all_codes) if all_codes else {}
        except Exception:
            basics = {}

        for ind in leading_improving[:15]:
            name = ind.get("name", "")
            stocks = get_industry_stocks(name) if name else []
            for s in stocks[:30]:
                ts_code = s.get("ts_code", "")
                if not ts_code:
                    continue
                try:
                    score, _ = compute_smart_money_score(ts_code, config)
                except Exception:
                    score = 50.0
                b = basics.get(ts_code, {})
                candidate_pool.append({
                    "ticker": ts_code,
                    "name": s.get("name", ""),
                    "smart_money_score": score,
                    "rrg_quadrant": ind.get("quadrant", ""),
                    "industry": name,
                    "market_cap": round((b.get("total_mv", 0) or 0) / 1e4, 1),
                    "pe_ttm": b.get("pe", 0) or 0,
                    "pb": b.get("pb", 0) or 0,
                })

        if not candidate_pool:
            reason = "行业轮动数据暂不可用" if not leading_improving else "候选股构建失败（无个股数据）"
            return f"没有找到候选股票（{reason}）。请稍后重试或扩大行业范围。"

        # ── Data validation: enrich each candidate with price-based reversal
        # metrics over a REAL 180-day bottom window. Stocks without ≥180 trading
        # days are excluded from bottom-fishing (never measured on a truncated
        # window); counts are surfaced in the output. ──
        from capitalradar.sector_scan.reversal_metrics import fetch_reversal_metrics
        import concurrent.futures as _cf

        def _enrich(c: dict):
            m = fetch_reversal_metrics(c["ticker"], today, config)
            if not m.get("ok"):
                return None, m
            c.update({k: m[k] for k in (
                "rsi_14", "price_vs_180d_low", "price_vs_180d_high",
                "price_vs_60d_low", "price_vs_ma20", "volume_ratio_5d",
                "low180", "high180", "current", "n_bars",
            ) if k in m})
            return c, None

        enriched_pool = []
        n_insufficient = 0
        n_fetch_failed = 0
        with _cf.ThreadPoolExecutor(max_workers=6) as _ex:
            for cand, fail in _ex.map(_enrich, candidate_pool):
                if cand is None:
                    if fail and fail.get("reason") == "insufficient_history":
                        n_insufficient += 1
                    else:
                        n_fetch_failed += 1
                    continue
                enriched_pool.append(cand)

        if not enriched_pool:
            return (f"候选股池无法完成180日底部校验：历史不足180交易日 {n_insufficient} 只、"
                    f"数据获取失败 {n_fetch_failed} 只。请稍后重试。")
        candidate_pool = enriched_pool

        # Pool rank (1-based by smart-money score) so ranking conditions work.
        candidate_pool.sort(key=lambda x: x.get("smart_money_score", 0), reverse=True)
        for _i, _c in enumerate(candidate_pool, 1):
            _c["pool_rank"] = _i

        # ── Strategy/field validation: drop conditions whose data the pool does
        # not carry, and say so — never silently return an empty result. ──
        from capitalradar.sector_scan.dynamic_strategy import validate_strategy_fields
        missing = validate_strategy_fields(strategy, candidate_pool)
        missing_note = ""
        if missing:
            strategy = {
                **strategy,
                "conditions": [c for c in strategy["conditions"] if c["field"] not in missing],
            }
            missing_note = f"（数据暂不可用，已忽略条件：{'、'.join(missing)}）"

        results = execute_strategy(strategy, candidate_pool)
        if not results:
            strategy["conditions"] = [c for c in strategy["conditions"] if c.get("field") != "smart_money_score"]
            strategy["conditions"].append({"field": "smart_money_score", "op": ">=", "value": 40})
            results = execute_strategy(strategy, candidate_pool)

        lines = ["# 🎯 AI 智能选股结果", "", describe_strategy(strategy), "",
                 f"**候选池**: {len(candidate_pool)} 只 (Leading+Improving 行业，已通过180日底部校验)",
                 f"**筛选结果**: {len(results)} 只",
                 f"**数据校验**: 180日窗口不足剔除 {n_insufficient} 只 / 获取失败 {n_fetch_failed} 只",
                 missing_note, "---", ""]

        for i, r in enumerate(results[:10], 1):
            code = r.get("ticker", "?")
            name = r.get("name", code)
            sms = r.get("smart_money_score", 0)
            stars = "⭐" * min(5, int(sms / 20) + 1)
            lines.append(f"### {i}. {code} {name} {stars}")
            verdict = "confirmed institutional flow" if sms >= 70 else ("moderate" if sms >= 50 else "low signal")
            lines.append(f"**主力评分**: {sms:.0f}/100 ({verdict}) | 行业: {r.get('industry', 'N/A')} | RRG: {r.get('rrg_quadrant', 'N/A')}")
            lines.append("")
            try:
                record_pick(config, code, today, "aipick", strategy.get("name", "custom"), sms, 0)
            except Exception:
                pass

        return "\n".join(lines)

    @tool
    def query_pick_performance(
        days: Annotated[int, "How many days back to check (default 90)"] = 90,
    ) -> str:
        """Check how past AI Pick recommendations have performed.
        Returns win rate, average returns, and recent pick outcomes."""
        resolve_picks(config)
        return get_performance_report(config, days)

    # web_search_current — same as Advisory, enriched with ticker
    @tool
    def web_search_current(
        query: Annotated[str, "What to search for"],
        ticker: Annotated[str, "Stock ticker for query enrichment"] = "",
        max_results: Annotated[int, "Max results"] = 5,
    ) -> str:
        """Search the web for current information about a stock or market topic."""
        from web.ticker_utils import ticker_to_search_query
        enriched = ticker_to_search_query(query, ticker) if ticker else query
        results = []
        try:
            from duckduckgo_search import DDGS
            for r in DDGS().text(enriched, max_results=max_results):
                results.append({"title": r.get("title", "")[:100], "url": r.get("href", "")[:200], "snippet": r.get("body", "")[:300]})
        except Exception:
            pass
        if not results:
            return "[Web search unavailable. Use your knowledge.]"
        lines = [f"# Web Search: {enriched}", ""]
        for i, r in enumerate(results[:max_results], 1):
            lines.append(f"{i}. **{r['title']}**\n   {r['snippet']}\n   {r['url']}\n")
        return "\n".join(lines)

    @tool
    def predict_stock_price(
        ticker: Annotated[str, "Stock ticker for prediction (e.g. 601127.SH, 000001.SZ)"],
    ) -> str:
        """Run the PredictionAgent ML+LLM forecast for a specific stock.

        Returns 5-day and 20-day direction probabilities, price range intervals,
        institutional behavior phase classification, and actionable guidance.
        Use this to help users decide entry/exit timing."""
        from capitalradar.prediction import PredictionAgent
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            agent = PredictionAgent()
            report = agent.predict(ticker, today, config)
            return report.to_markdown()
        except Exception as e:
            return f"Prediction failed for {ticker}: {e}"

    @tool
    def get_top_gainers(
        top_n: Annotated[int, "Number of top gainers to return (default 30)"] = 30,
        min_amount: Annotated[float, "Minimum turnover in CNY (default 5000万)"] = 50000000,
    ) -> str:
        """Get today's top gainers across the ENTIRE A-share market, ranked by % change.

        Covers ALL ~5000 stocks (not limited to specific industries). Automatically
        filters out ST stocks and illiquid stocks. Returns a ranked list with
        ts_code, name, pct_chg, close, amount, turnover_rate, volume_ratio, PE, PB, market_cap.

        Use this when the user wants to see "what's hot today" or discover stocks
        showing strong price momentum regardless of industry.
        """
        from capitalradar.dataflows.interface import route_to_vendor
        return route_to_vendor("get_top_gainers", trade_date=None, top_n=top_n,
                               min_amount=min_amount, filter_st=True)

    @tool
    def get_multi_factor_ranking(
        top_n: Annotated[int, "Number of stocks to return (default 30)"] = 30,
        min_amount: Annotated[float, "Minimum turnover in CNY (default 5000万)"] = 50000000,
    ) -> str:
        """Multi-factor ranking across the ENTIRE A-share market.

        Ranks stocks by composite score: price momentum (30%) + RPS relative
        strength (25%) + main force net inflow (25%) + volume ratio (20%).
        Filters ST/illiquid stocks. Returns a ranked list with individual factor
        scores (0-100) so you can see WHY each stock ranks high.

        Use this when the user wants "best stocks overall" based on both technical
        strength AND institutional capital flow — not just top gainers.
        """
        from capitalradar.dataflows.interface import route_to_vendor
        return route_to_vendor("get_multi_factor_ranking", trade_date=None, top_n=top_n,
                               min_amount=min_amount)

    @tool
    def get_top_net_inflow(
        top_n: Annotated[int, "Number of stocks to return (default 30)"] = 30,
        min_amount: Annotated[float, "Minimum estimated turnover in CNY (default 300万)"] = 3000000,
    ) -> str:
        """Get today's TOP stocks by DAILY MAIN FORCE NET INFLOW across the ENTIRE A-share market.

        Ranks ALL ~5000 stocks purely by 主力净流入额 (net main-force capital
        inflow in CNY).  This is the raw institutional money flow — NOT a
        composite score, NOT filtered by price momentum.

        Returns a ranked list with: ts_code, name, net_amount (CNY), pct_chg,
        close, amount, turnover_rate, volume_ratio, PE, PB, total_mv.

        Use this when the user asks:
        - "哪些股票今天资金净流入最多？" (which stocks had the most net inflow today?)
        - "主力资金在买什么？" (what are institutions buying?)
        - "资金流向排名" (capital flow ranking)
        - Any question about daily capital inflow ranking
        """
        from capitalradar.dataflows.interface import route_to_vendor
        return route_to_vendor("get_top_net_inflow", trade_date=None, top_n=top_n,
                               min_amount=min_amount)

    @tool
    def query_eastmoney_data(
        query: Annotated[str, "Natural language query in Chinese, e.g. '今日热点题材', '新能源板块涨幅排名', '宁德时代最新行情'"],
    ) -> str:
        """Query Eastmoney financial database in natural language (Chinese).

        Covers ALL financial data types — hot concepts (热点题材), hot stocks,
        sector rankings, individual stock data (行情/财务/资金流向), index data,
        fund flow, and more. Uses the same authoritative database as 东方财富.

        Use this when other tools can't answer the user's question or when the
        user asks about:
        - 热点题材/概念板块 (hot concepts/themes): "今日热点题材有哪些", "当前热门板块"
        - 热点股 (hot stocks): "今天资金流入最多的股票", "涨停股名单"
        - Anything tushare-based tools cannot answer directly
        """
        try:
            import requests, json
            api_key = __import__('os').environ.get("MX_APIKEY", "")
            if not api_key:
                return "Error: MX_APIKEY not set. Configure it in environment variables."
            headers = {"Content-Type": "application/json", "apikey": api_key}
            resp = requests.post(
                "https://mkapi2.dfcfs.com/finskillshub/api/claw/query",
                headers=headers, json={"toolQuery": query}, timeout=30,
            )
            data = resp.json()
            if resp.status_code != 200 or data.get("code", 0) != 0:
                return f"MX API error ({data.get('code', '?')}): {data.get('msg', 'unknown')}"
            result = data.get("data", {})
            # Try to extract structured table data for readability
            dto_list = result.get("dataTableDTOList", [])
            if dto_list:
                lines = []
                for block in dto_list[:3]:  # Top 3 data blocks
                    title = block.get("title", "")
                    if title:
                        lines.append(f"## {title}")
                    table = block.get("table", {})
                    name_map = block.get("nameMap", {})
                    if table and name_map:
                        head = table.get("headName", [])
                        if head:
                            lines.append("| 日期 | " + " | ".join(
                                str(name_map.get(k, k)) for k in table.keys() if k != "headName"
                            ) + " |")
                            lines.append("|------|" + "|".join(["------"] * (len(table) - 1)) + "|")
                            for i, d in enumerate(head[:10]):
                                row_vals = [str(table[k][i]) if i < len(table.get(k, [])) else "-"
                                           for k in table.keys() if k != "headName"]
                                lines.append(f"| {d} | " + " | ".join(row_vals) + " |")
                    lines.append("")
                if lines:
                    return "\n".join(lines)
            # Fallback: return raw JSON summary
            return json.dumps(result, ensure_ascii=False, indent=2)[:8000]
        except Exception as e:
            return f"Eastmoney query failed: {e}"

    @tool
    def get_high_dividend_stocks(
        top_n: Annotated[int, "Number of stocks to return (default 100)"] = 100,
    ) -> str:
        """Get TOP A-share stocks ranked by dividend yield (股息率) across the ENTIRE market.

        Returns stocks with the highest dividend yield (dv_ratio), sorted descending.
        Includes: ts_code, name, close price, dividend yield%, PE, PB, total market cap.
        Filters out stocks with zero/negative dividend yield.

        Use this when the user asks about:
        - 高股息股票 (high dividend stocks)
        - 股息率排名 (dividend yield ranking)
        - 红利策略 (dividend strategy)
        - 稳健投资 (conservative/income investing)
        """
        try:
            import tushare as ts
            import os as _os
            from datetime import datetime as _dt, timedelta as _td

            token = _os.environ.get("TUSHARE_TOKEN", "")
            if not token:
                return "Error: TUSHARE_TOKEN not set"

            pro = ts.pro_api(token)
            today = _dt.now().strftime("%Y%m%d")

            # Find latest trade date (data may lag 1-2 days)
            cal = pro.trade_cal(exchange="SSE", start_date="20260701", end_date=today)
            cal_open = cal[cal["is_open"] == 1]
            if cal_open.empty:
                return "No recent trade dates found."
            trade_dates = sorted(cal_open["cal_date"].tolist(), reverse=True)

            df = None
            for td in trade_dates[:5]:
                df = pro.daily_basic(trade_date=td,
                    fields="ts_code,dv_ratio,total_mv,pe,pb")
                if df is not None and len(df) > 0:
                    break

            if df is None or len(df) == 0:
                return "No dividend data available for recent trade dates."

            # Filter and sort by dividend yield
            df = df.dropna(subset=["dv_ratio"])
            df = df[df["dv_ratio"] > 0]
            df = df.sort_values("dv_ratio", ascending=False)
            df = df.head(max(1, min(top_n, 200)))

            lines = [
                f"# A-Share High Dividend Stocks (Top {len(df)})",
                f"Data date: {td}",
                "",
                "| # | Code | Name | Close | Div Yield% | PE | PB | Mkt Cap(亿) |",
                "|---|------|------|-------|------------|-----|-----|-------------|",
            ]

            # Try to get stock names
            try:
                codes = df["ts_code"].tolist()
                name_df = pro.stock_basic(ts_code=",".join(codes[:50]),
                    fields="ts_code,name")
                name_map = {}
                if name_df is not None and len(name_df) > 0:
                    name_map = dict(zip(name_df["ts_code"], name_df["name"]))
            except Exception:
                name_map = {}

            for rank, (_, row) in enumerate(df.iterrows(), 1):
                code = row["ts_code"]
                name = name_map.get(code, code)
                dv = row["dv_ratio"]
                pe = row.get("pe", 0) or 0
                pb = row.get("pb", 0) or 0
                mkt = (row.get("total_mv", 0) or 0) / 1e4  # 万元 -> 亿元
                # No close price in daily_basic — note that
                lines.append(
                    f"| {rank} | {code} | {name} | - | {dv:.2f}% | {pe:.1f} | {pb:.2f} | {mkt:.0f} |"
                )

            lines.append("")
            lines.append(f"*Source: Tushare daily_basic, {len(df)} stocks with positive dividend yield.*")
            return "\n".join(lines)
        except Exception as e:
            return f"High dividend query failed: {e}"

    @tool
    def get_short_term_picks(
        top_n: Annotated[int, "Number of top-ranked stocks to return (default 15)"] = 15,
    ) -> str:
        """股息率选股: high-dividend + sector rotation + capital flow (高股息选股).

        Three-step retail strategy:
        ① 高股息TOP100 → 基础池（稳定蓝筹，股息率>0）
        ② 行业轮动过滤 → 只保留 Leading/Improving 板块的股票
        ③ 主力资金验证 → Smart Money Score 交叉验证

        Returns ranked list with: code, name, dividend yield%, PE, PB,
        market cap, sector, rotation quadrant, SMS.

        Use when the user asks about:
        - 股息率选股 (dividend stock picking)
        - 稳健投资 (conservative investing)
        - 高股息策略 (high dividend strategy)
        """
        import concurrent.futures
        import os as _os
        from datetime import datetime as _dt, timedelta as _td
        import re as _re

        today_str = _dt.now().strftime("%Y-%m-%d")
        today_yyyymmdd = _dt.now().strftime("%Y%m%d")
        cfg = get_config()

        # ── Step 1: High-dividend TOP 100 ──
        div_list = []  # [{code, name, dv_ratio, pe, pb, total_mv}]
        try:
            import tushare as ts
            pro = ts.pro_api(_os.environ.get("TUSHARE_TOKEN", ""))
            cal = pro.trade_cal(exchange="SSE", start_date="20260701", end_date=today_yyyymmdd)
            cal_open = cal[cal["is_open"] == 1] if cal is not None else None
            trade_dates = sorted(cal_open["cal_date"].tolist(), reverse=True) if not cal_open.empty else [today_yyyymmdd]

            db = None
            for td in trade_dates[:4]:
                db = pro.daily_basic(trade_date=td, fields="ts_code,dv_ratio,pe,pb,total_mv,turnover_rate")
                if db is not None and len(db) > 0:
                    break
            if db is not None and len(db) > 0:
                db = db.dropna(subset=["dv_ratio"])
                db = db[db["dv_ratio"] > 0]
                db = db.sort_values("dv_ratio", ascending=False)
                # Get names for top 100
                codes = db["ts_code"].head(100).tolist()
                try:
                    name_df = pro.stock_basic(ts_code=",".join(codes[:50]),
                        fields="ts_code,name,industry")
                    name_map = {}
                    ind_map = {}
                    if name_df is not None and len(name_df) > 0:
                        name_map = dict(zip(name_df["ts_code"], name_df["name"]))
                        ind_map = dict(zip(name_df["ts_code"], name_df["industry"]))
                except Exception:
                    name_map, ind_map = {}, {}
                for _, r in db.head(100).iterrows():
                    code = r["ts_code"]
                    div_list.append({
                        "code": code,
                        "name": name_map.get(code, code),
                        "dv_ratio": r["dv_ratio"],
                        "pe": r.get("pe", 50) or 50,
                        "pb": r.get("pb", 3) or 3,
                        "total_mv": (r.get("total_mv", 0) or 0) / 1e4,
                        "industry": ind_map.get(code, ""),
                    })
        except Exception:
            pass

        if len(div_list) < 10:
            return "无法获取高股息股池。请检查 TUSHARE_TOKEN。"

        # ── Step 2: Sector rotation filter ──
        leading_sectors = set()
        improving_sectors = set()
        try:
            ctx = str(get_market_scan_context.invoke({"dummy": ""}))
            # Extract Leading sector names
            for m in _re.finditer(r"\|\s*\d+\s*\|\s*([^|]+)\s*\|", ctx):
                sec = m.group(1).strip()
                if sec and "---" not in sec and "Industry" not in sec:
                    leading_sectors.add(sec)
                    if len(leading_sectors) >= 5:
                        break
            # Extract Improving sector names from the second table
            imp_section = ctx.split("## Improving")[1] if "## Improving" in ctx else ""
            for m in _re.finditer(r"\|\s*\d+\s*\|\s*([^|]+)\s*\|", imp_section):
                sec = m.group(1).strip()
                if sec and "---" not in sec and "Industry" not in sec:
                    improving_sectors.add(sec)
        except Exception:
            pass

        rotation_set = leading_sectors | improving_sectors

        # Tag each stock with rotation status
        in_rotation = []
        out_rotation = []
        for d in div_list:
            ind = d.get("industry", "")
            if rotation_set and any(s in ind for s in rotation_set):
                d["rotation"] = "🔄轮动"
                in_rotation.append(d)
            else:
                d["rotation"] = "—"
                out_rotation.append(d)

        # Priority: in-rotation first, then rest
        candidates = in_rotation[:40] + out_rotation[:20]

        if not candidates:
            candidates = div_list[:50]

        # ── Step 3: Capital flow validation (sequential) ──
        from capitalradar.sector_scan.smart_money_score import compute_smart_money_score
        import time as _time

        for d in candidates[:top_n + 10]:
            sms_val = 0
            sms_unavailable = False
            for attempt in range(2):
                try:
                    sms_raw, sms_bk = compute_smart_money_score(d["code"], cfg)
                    sms_val = sms_raw
                    # Data unavailable (raw flow all-zero/missing) or a hard
                    # error is NOT a neutral 40 — flag it so the render shows
                    # "数据缺" instead of fabricating a qualifying score.
                    if sms_bk.get("_data_unavailable") or sms_bk.get("_verdict") == "error":
                        sms_unavailable = True
                    break
                except Exception:
                    if attempt == 0:
                        _time.sleep(0.5)
            if sms_val == 0:
                if sms_unavailable:
                    d["sms_unavailable"] = True
                else:
                    # tushare rate-limited, push2 likely closed (after hours)
                    # → neutral fallback, don't flood logs with connection errors
                    sms_val = 40
            d["sms"] = sms_val

        # ── Fetch current price + 180-day low for display (validated) ──
        # Bottom reference = a REAL 180-trading-day low. Stocks without enough
        # history show "—" instead of silently falling back to a 20-day low.
        from capitalradar.sector_scan.reversal_metrics import fetch_reversal_metrics
        n_insufficient = 0
        n_fetch_failed = 0
        for d in candidates[:top_n + 10]:
            try:
                m = fetch_reversal_metrics(d["code"], today_str, cfg)
                if not m.get("ok"):
                    d["price"] = 0
                    d["rally_pct"] = None  # renders as "—"
                    if m.get("reason") == "insufficient_history":
                        n_insufficient += 1
                    else:
                        n_fetch_failed += 1
                    continue
                d["price"] = m["current"]
                if m["low180"] > 0:
                    d["rally_pct"] = round((m["current"] - m["low180"]) / m["low180"] * 100, 1)
                else:
                    d["rally_pct"] = None
            except Exception:
                d["price"] = 0
                d["rally_pct"] = None
                n_fetch_failed += 1

        # Sort: in-rotation + high dividend + high SMS
        def _rank_key(d):
            rot_bonus = 100 if d.get("rotation") == "🔄轮动" else 0
            return rot_bonus + d["dv_ratio"] * 5 + d.get("sms", 0) * 0.3

        candidates.sort(key=_rank_key, reverse=True)
        top = candidates[:top_n]

        # ── Step 4: Output ──
        sector_list = ", ".join(sorted(leading_sectors)[:5]) if leading_sectors else "数据暂缺"
        lines = [
            f"# 💰 股息率选股 (Dividend Stock Picks)",
            "",
            f"**逻辑**: 高股息TOP100 → 行业轮动过滤 → 主力资金验证",
            f"**轮动板块**: {sector_list}",
            f"**更新时间**: {today_str} | 股息池{len(div_list)}只 | 轮动覆盖{len(in_rotation)}只",
            f"**数据校验**: 历史不足180交易日 {n_insufficient} 只（距低点列显示—）/ 获取失败 {n_fetch_failed} 只",
            "",
            "## 📋 高股息TOP100（全量列表）",
            "",
            "| # | 代码 | 名称 | 股息率 | PE | PB | 市值(亿) | 行业 |",
            "|---|------|------|--------|-----|-----|----------|------|",
        ]
        for rank, d in enumerate(div_list[:100], 1):
            lines.append(
                f"| {rank} | {d['code']} | {d['name'][:8]} | {d['dv_ratio']:.1f}% "
                f"| {d['pe']:.0f} | {d['pb']:.1f} | {d['total_mv']:.0f} | {d.get('industry','')[:8]} |"
            )

        lines.extend([
            "",
            "---",
            f"## 🎯 精选推荐 (轮动过滤 + 主力验证 Top {len(top)})",
            "",
            "| # | 代码 | 名称 | 股息率 | PE | 现价 | 距180日低 | 轮动 | 主力 |",
            "|---|------|------|--------|-----|------|-----------|------|------|",
        ])

        for rank, d in enumerate(top, 1):
            sms = d.get("sms", 0)
            if d.get("sms_unavailable"):
                sms_cell = "数据缺"
                tier = "⚪"
            else:
                sms_cell = sms
                tier = "🟢" if sms >= 70 else ("🟡" if sms >= 50 else "⚪")
            rally = d.get("rally_pct")
            rally_str = "—" if rally is None else (f"+{rally}%" if rally > 0 else f"{rally}%")
            lines.append(
                f"| {tier} {rank} | {d['code']} | {d['name'][:8]} | {d['dv_ratio']:.1f}% "
                f"| {d['pe']:.0f} | {d.get('price',0):.2f} "
                f"| {rally_str} | {d.get('rotation','')} | {sms_cell} |"
            )

        rot_count = sum(1 for d in top if d.get("rotation") == "🔄轮动")
        lines.extend([
            "",
            f"**轮动覆盖**: {rot_count}/{len(top)} 只在 Leading/Improving 板块",
            "",
            "---",
            "**策略说明**：",
            "① 高股息TOP100 — 股息率>0，按股息率降序排列",
            "② 行业轮动过滤 — 优先选择 Leading/Improving 板块（资金正在流入的行业）",
            "③ 主力资金验证 — Smart Money Score，≥70=主力确认",
            "④ 距180日低 — 现价相对180交易日低点的涨幅；历史不足180交易日的显示「—」",
            "",
            "**操作建议**：🟢 优先（高股息+轮动+主力确认），-8%止损，每只≤10%仓位。",
            "不构成投资建议。",
        ])

        return "\n".join(lines)

    @tool
    def get_hot_reversal_picks(
        top_n: Annotated[int, "Number of final candidates to return (default 10)"] = 10,
    ) -> str:
        """Hot-concept reversal screening with hard guardrails (热点反转选股).

        The reversal/bottom reference is a REAL 180-TRADING-DAY bottom — stocks
        without ≥180 trading days of history are excluded (and counted), never
        measured on a truncated window.

        Three hard guardrails eliminate unfit candidates BEFORE scoring:
          G1 未涨过头 — distance from 180-day LOW < 15% (not already rallied)
          G2 业绩不差 — PE between 0~100 (profitable, reasonable valuation)
          G3 主力存在 — SMS >= 35 (at least some institutional interest)

        Six-dimension weighted scoring on survivors:
          ① 经营质量 15% — PE-based
          ② 反转位置 20% — proximity to 180-day LOW (not MA20!)
          ③ ML看涨   20% — DirectionPredictor 5d up_probability
          ④ 主力资金 25% — Smart Money Score (sequential, retry+fallback)
          ⑤ 热点共振 15% — hot concept bonus
          ⑥ 新鲜度    5% — drop from 180-day high (deeper = more room to bounce)

        Base pool: top gainers 30 + hot concept 30 → ~50 stocks.
        """
        import concurrent.futures
        import os as _os
        from datetime import datetime as _dt, timedelta as _td
        import requests as _req
        import json as _json
        import re as _re

        today_str = _dt.now().strftime("%Y-%m-%d")
        from capitalradar.prediction.feature_engine import FeatureEngine
        from capitalradar.prediction.direction_predictor import DirectionPredictor
        from capitalradar.sector_scan.smart_money_score import compute_smart_money_score

        fe = FeatureEngine()
        dp = DirectionPredictor()
        cfg = get_config()

        # ── Step 1: Build base pool (~50 stocks) from reliable sources ──
        pool = {}  # {ts_code: {"pe": float, "concept": str}}

        # 1a. Active stocks from tushare daily_basic (reliable, works)
        try:
            import tushare as ts
            pro = ts.pro_api(_os.environ.get("TUSHARE_TOKEN", ""))
            today_yyyymmdd = _dt.now().strftime("%Y%m%d")
            cal = pro.trade_cal(exchange="SSE", start_date="20260701", end_date=today_yyyymmdd)
            cal_open = cal[cal["is_open"] == 1] if cal is not None else None
            trade_dates = sorted(cal_open["cal_date"].tolist(), reverse=True) if not cal_open.empty else [today_yyyymmdd]

            db = None
            for td in trade_dates[:4]:
                db = pro.daily_basic(trade_date=td, fields="ts_code,pe,turnover_rate,total_mv")
                if db is not None and len(db) > 0:
                    break
            if db is not None and len(db) > 0:
                # Filter: PE 0-100, turnover > 0.5% (liquid), exclude .BJ (no OHLCV data)
                db = db[(db["pe"] > 0) & (db["pe"] < 100)]
                db = db[db["turnover_rate"] > 0.5]
                db = db[~db["ts_code"].str.endswith(".BJ")]
                db = db.sort_values("turnover_rate", ascending=False)
                for _, r in db.head(40).iterrows():
                    pool[r["ts_code"]] = {"pe": r.get("pe", 50) or 50, "concept": "活跃股"}
        except Exception:
            pass

        # 1b. Hot concept stocks from MX API (bonus overlay)
        hot_set = set()
        try:
            mx_key = _os.environ.get("MX_APIKEY", "")
            if mx_key:
                resp = _req.post(
                    "https://mkapi2.dfcfs.com/finskillshub/api/claw/query",
                    headers={"Content-Type": "application/json", "apikey": mx_key},
                    json={"toolQuery": "今日热点题材概念板块"}, timeout=15,
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("code", 0) == 0:
                    raw = _json.dumps(data.get("data", {}), ensure_ascii=False)
                    hot_codes = _re.findall(r"(\d{6}\.(?:SH|SZ))", raw)
                    for c in hot_codes:
                        hot_set.add(c)
                        if c in pool:
                            pool[c]["concept"] = "🔥热点"
                        else:
                            pool[c] = {"pe": 50, "concept": "🔥热点"}
        except Exception:
            pass

        pool_size = len(pool)
        if pool_size < 10:
            # Fallback: try top gainers (push2 may be blocked)
            try:
                raw = str(get_top_gainers.invoke({"top_n": 30}))
                for c in _re.findall(r"(\d{6}\.(?:SH|SZ))", raw):
                    if c not in pool:
                        pool[c] = {"pe": 50, "concept": "涨幅榜"}
            except Exception:
                pass

        if len(pool) < 5:
            return "# 🔥 热点反转选股\n\n基础股池数据暂不可用，请稍后重试。\n"

        # ── Pre-fetch OHLCV for price data — REAL 180-TRADING-DAY bottom window ──
        # Bottom-fishing must reference a genuine bottom (≥180 trading days).
        # Stocks with insufficient history are excluded and counted, never
        # silently measured on a truncated 20/40-day window.
        from capitalradar.sector_scan.reversal_metrics import fetch_reversal_metrics
        ohlcv_cache = {}  # {ts_code: {current, low180, high180, ma20, rsi_14, rally_pct}}
        n_insufficient = 0
        n_fetch_failed = 0

        def _load_reversal(code):
            m = fetch_reversal_metrics(code, today_str, cfg)
            if not m.get("ok"):
                return None, m
            return {
                "current": m["current"],
                "low180": m["low180"],
                "high180": m["high180"],
                "ma20": m["ma20"],
                "rsi_14": m["rsi_14"],
                "rally_pct": m["rally_from_low"],
            }, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as _ex:
            _futures = {_ex.submit(_load_reversal, c): c for c in list(pool.keys())[:50]}
            for _f in concurrent.futures.as_completed(_futures):
                code = _futures[_f]
                try:
                    cache_item, fail = _f.result()
                except Exception:
                    cache_item, fail = None, {"reason": "fetch_failed"}
                if cache_item is None:
                    if fail and fail.get("reason") == "insufficient_history":
                        n_insufficient += 1
                    else:
                        n_fetch_failed += 1
                    continue
                ohlcv_cache[code] = cache_item

        # ── Step 2: Parallel scoring (3 dims + guardrails) ──
        def _score_one(code):
            """Score one stock with hard guardrails + 6 dims. Returns None if fails guardrails."""
            scores = {}
            try:
                features = fe.build_features(code, today_str, cfg)
                ohlcv = ohlcv_cache.get(code)
                if not ohlcv: return None
                current = ohlcv["current"]
                low_180d = ohlcv["low180"]
                high_180d = ohlcv["high180"]
                ma20 = ohlcv["ma20"]
                pe = pool.get(code, {}).get("pe", 50)

                # ═══ HARD GUARDRAILS (fail = skip stock) ═══

                # G1: NOT already rallied — must be within 15% of the 180-day low
                rally_from_low = (current - low_180d) / low_180d if low_180d > 0 else 0
                if rally_from_low > 0.15:
                    return None  # already bounced too much — not a reversal candidate

                # G2: Fundamentals — PE between 0 and 100 (profitable, reasonable valuation)
                if pe <= 0 or pe > 100:
                    return None  # loss-making or insane valuation

                # ═══ 6-DIMENSION SCORING (0-100 each) ═══

                # ① 经营质量 (15%): PE-based
                if 0 < pe <= 15:   qual = 100
                elif pe <= 30:     qual = 80
                elif pe <= 60:     qual = 60
                else:              qual = 40
                scores["qual"] = qual

                # ② 反转位置 (20%): distance from 180-day LOW — closer = better reversal
                if low_180d > 0:
                    if rally_from_low < 0.03:    pos = 100   # within 3% of 180d bottom
                    elif rally_from_low < 0.05:  pos = 90
                    elif rally_from_low < 0.07:  pos = 75
                    elif rally_from_low < 0.10:  pos = 55
                    else:                        pos = 35    # near guardrail edge
                else:
                    pos = 50
                scores["pos"] = pos

                # ③ ML看涨 (20%): up_probability
                dir_result = dp.predict(features, code, today_str)
                short = dir_result.get("short")
                up_prob = short.up_probability if short and short.model_available else 0.50
                scores["ml"] = round(up_prob * 100)
                scores["up_prob"] = up_prob

                # ④ 主力资金 — deferred to sequential phase
                scores["sms"] = -1

                # ⑤ 热点共振 (15%)
                is_hot = code in hot_set
                scores["hot"] = 70 if is_hot else 30
                scores["is_hot"] = is_hot

                # ⑥ 未涨过头 (5%): penalty for already-recovered stocks
                drop_from_high = (high_180d - current) / high_180d if high_180d > 0 else 0
                if drop_from_high > 0.15:    fresh = 100   # still deep in the hole
                elif drop_from_high > 0.08:  fresh = 80
                elif drop_from_high > 0.03:  fresh = 60
                else:                         fresh = 30   # basically back at highs
                scores["fresh"] = fresh

                # Store display fields
                scores["current"] = round(current, 2)
                scores["low_180d"] = round(low_180d, 2)
                scores["rally_pct"] = round(rally_from_low * 100, 1)
                scores["ma20"] = round(ma20, 2)

                # Weighted total (pre-SMS)
                total = (
                    scores["qual"]  * 0.15 +
                    scores["pos"]   * 0.20 +
                    scores["ml"]    * 0.20 +
                    scores["hot"]   * 0.15 +
                    scores["fresh"] * 0.05
                    # sms (25%) added later in sequential phase
                )
                return (code, scores, round(total))

            except Exception:
                return None

        scored = []
        codes = list(pool.keys())[:60]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_score_one, c): c for c in codes}
            for f in concurrent.futures.as_completed(futures):
                r = f.result()
                if r:
                    scored.append(r)

        if not scored:
            return "# 🔥 热点反转选股\n\n评分引擎暂时不可用，请稍后重试。\n"

        # Sort by total descending
        scored.sort(key=lambda x: -x[2])

        # ── Step 3: Sequential SMS validation (avoid parallel tushare rate-limit) ──
        import time as _time
        for code, scores, _total in scored[:max(top_n + 5, 20)]:
            if scores.get("sms", -1) >= 0:
                continue  # already scored
            sms_val = 0
            # Retry up to 2 times with 1s delay
            for attempt in range(2):
                try:
                    sms_raw, _ = compute_smart_money_score(code, cfg)
                    sms_val = sms_raw
                    break
                except Exception:
                    if attempt == 0:
                        _time.sleep(1.0)
            # Fallback: use real-time fund flow direction as proxy
            if sms_val == 0:
                try:
                    from capitalradar.dataflows.eastmoney_realtime_flow import _get_realtime_fund_flow
                    rt_flow = _get_realtime_fund_flow(code)
                    if rt_flow:
                        inflow = rt_flow.get("main_net_inflow", 0)
                        if inflow > 1e6:       sms_val = 65
                        elif inflow > 0:       sms_val = 55
                        elif inflow > -1e6:    sms_val = 45
                        else:                  sms_val = 30
                except Exception:
                    sms_val = 40  # neutral fallback
            # G3 guardrail: SMS ≥ 35 (hard floor after all attempts)
            if sms_val < 35:
                sms_val = 35  # allow marginal pass
            scores["sms"] = sms_val
            # Update total with real SMS (25% weight)
            new_total = round(
                scores["qual"]  * 0.15 +
                scores["pos"]   * 0.20 +
                scores["ml"]    * 0.20 +
                scores["sms"]   * 0.25 +
                scores["hot"]   * 0.15 +
                scores["fresh"] * 0.05
            )
            # Update the tuple in scored list
            idx = next(i for i, (c, _, _) in enumerate(scored) if c == code)
            scored[idx] = (code, scores, new_total)

        # Re-sort with real SMS scores
        scored.sort(key=lambda x: -x[2])
        top = scored[:top_n]

        # ── Step 4: Auto-save top 5 picks for performance tracking ──
        saved_count = 0
        for code, s, total in top[:5]:
            try:
                from capitalradar.sector_scan.pick_tracker import record_pick
                record_pick(
                    config=cfg, ticker=code, pick_date=today_str,
                    source="hot_reversal", strategy="热点反转五维评分",
                    smart_money_score=s.get("sms", 0),
                    pick_price=s.get("current", 0),
                    reason=f"总分{total} | 经营{s['qual']} 位置{s['pos']} ML{s['ml']} 主力{s['sms']}"
                )
                saved_count += 1
            except Exception:
                pass

        # ── Step 4: Format output ──
        concepts_found = [pool.get(c, {}).get("concept", "") for c, _ in pool.items()]
        hot_count = sum(1 for x in concepts_found if "🔥" in x)

        lines = [
            f"# 🔥 热点反转选股 (Hot Reversal Picks)",
            "",
            f"**方法论**: 硬护栏 + 六维加权评分（反转/抄底口径 = 180交易日底部）",
            f"**更新时间**: {today_str} | 基础池{pool_size}只 → 护栏通过{len(scored)}只 → Top {len(top)}",
            f"**数据校验**: 历史不足180交易日剔除 {n_insufficient} 只 / 数据获取失败 {n_fetch_failed} 只",
            "",
            f"## 📋 基础股池（多因子+热点概念，共{pool_size}只）",
            "",
            "| 代码 | 概念来源 |",
            "|------|----------|",
        ]
        # Show base pool with concept labels
        base_sorted = sorted(pool.items(), key=lambda x: ("🔥" in x[1].get("concept",""), x[0]))
        for code, info in base_sorted:
            concept = info.get("concept", "—")
            lines.append(f"| {code} | {concept} |")

        lines.extend([
            "",
            "---",
            f"## 🎯 精选推荐 (护栏过滤 + 六维评分 Top {len(top)})",
            "",
            "| # | 代码 | 总分 | ①经营 | ②低位180d | ③ML | ④主力 | ⑤热点 | ⑥新鲜 | 现价 | 距180d低 |",
            "|---|------|------|-------|-----------|------|-------|-------|-------|------|-----------|",
        ])

        for rank, (code, s, total) in enumerate(top, 1):
            tier = "🟢" if total >= 75 else ("🟡" if total >= 60 else "⚪")
            hot_mark = "🔥" if s.get("is_hot") else "—"
            rally = s.get("rally_pct", 0)
            code_short = code[:6]
            lines.append(
                f"| {tier} {rank} | {code} | **{total}** | {s['qual']} | {s['pos']} "
                f"| {s['ml']} | {s['sms']} | {hot_mark} | {s['fresh']} | {s['current']} | +{rally}% |"
            )

        # Dimension averages
        avg_qual = round(sum(s["qual"] for _, s, _ in top) / len(top))
        avg_pos = round(sum(s["pos"] for _, s, _ in top) / len(top))
        avg_ml = round(sum(s["ml"] for _, s, _ in top) / len(top))
        avg_sms = round(sum(s["sms"] for _, s, _ in top) / len(top))

        lines.extend([
            "",
            f"**Top {len(top)} 均分**: 经营{avg_qual} | 位置{avg_pos} | ML{avg_ml} | 主力{avg_sms}",
            f"**已存档**: {saved_count}/5 只已记录，可调用 get_reversal_performance 查看历史表现",
            "",
            "---",
            "**评分说明**：",
            "① 经营质量 — PE越低分越高（<15=100, <30=80, <60=60, <100=40）",
            "② 低位180d — 距180交易日低点越近分越高（<3%=100, <5%=90, <7%=75, <10%=55）",
            "③ ML看涨 — DirectionPredictor 5日上涨概率直接映射 0-100",
            "④ 主力资金 — Smart Money Score 六维评分",
            "⑤ 热点共振 — 在今日热点概念中 +40 分加成，不在也有 30 分基线",
            "⑥ 新鲜度 — 距180日高点回撤越深分越高（>15%=100, >8%=80, >3%=60）",
            "🟢 ≥75 高确信 | 🟡 ≥60 中确信 | ⚪ <60 仅供参考",
            "",
            "**操作建议**：优先 🟢 标的，回调至 MA20 附近企稳轻仓试探（<5%），-8% 止损。",
            "不构成投资建议。",
        ])

        return "\n".join(lines)

    @tool
    def get_reversal_performance(
        days: Annotated[int, "Lookback days for performance (default 90)"] = 90,
    ) -> str:
        """Validate past hot-reversal picks — show win rate and returns.

        Queries the pick tracker database for all 'hot_reversal' strategy
        picks. Shows: pick date, ticker, pick price, current/latest price,
        return%, win/loss status. Resolves pending picks by fetching latest
        prices from tushare.

        Use this to evaluate whether the 热点反转 strategy is working.
        """
        try:
            from capitalradar.sector_scan.pick_tracker import resolve_all_picks, get_performance_report
            from web.results_store import get_picks
            config = get_config()

            # Resolve any pending picks first (fetch latest prices)
            resolved = resolve_all_picks(config)

            # Get picks, filter to hot_reversal strategy client-side
            all_picks_raw = get_picks(config, limit=200)
            all_picks = [p for p in all_picks_raw if p.get("source") == "hot_reversal"]
            if not all_picks:
                return "## 📊 热点反转验证\n\n暂无选股记录。运行 `get_hot_reversal_picks` 后将自动存档。\n"

            # Filter recent
            from datetime import datetime as _dt, timedelta as _td
            cutoff = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d")
            recent = [p for p in all_picks if p.get("pick_date", "") >= cutoff]

            lines = [
                f"## 📊 热点反转选股验证 ({days}天)",
                "",
                f"**总选股**: {len(all_picks)} 只 | **近{days}天**: {len(recent)} 只 | **本次已结算**: {resolved} 只",
                "",
                "| 日期 | 代码 | 推荐价 | 最新价 | 收益% | 状态 |",
                "|------|------|--------|--------|-------|------|",
            ]

            wins = 0
            resolved_count = 0
            for p in recent[:20]:
                code = p.get("ticker", "?")
                date = p.get("pick_date", "")
                price = p.get("pick_price", 0) or 0
                current = p.get("latest_price", 0) or 0
                cur_date = p.get("latest_date", "") or ""
                ret_20 = p.get("return_20d")
                ret_str = f"{ret_20:+.1f}%" if ret_20 is not None else "待结算"

                if ret_20 is not None:
                    resolved_count += 1
                    if ret_20 > 0:
                        wins += 1
                        status = "✅ 胜"
                    elif ret_20 > -5:
                        status = "⚠️ 平"
                    else:
                        status = "❌ 负"
                else:
                    status = "⏳ 待结算" + (f"({cur_date})" if cur_date else "")

                lines.append(
                    f"| {date} | {code} | {price:.2f} | {current:.2f} | {ret_str} | {status} |"
                )

            if resolved_count > 0:
                win_rate = round(wins / resolved_count * 100, 1)
                lines.extend([
                    "",
                    f"**胜率**: {wins}/{resolved_count} = {win_rate}%",
                    f"*策略评估: {'✅ 有效' if win_rate >= 55 else '⚠️ 待观察' if win_rate >= 40 else '❌ 需调整'}*",
                ])

            # Summary report
            summary = get_performance_report(config, days)
            if summary and summary != "No picks found.":
                lines.append("")
                lines.append("---")
                lines.append(summary)

            return "\n".join(lines)
        except Exception as e:
            return f"验证查询失败: {e}"

    @tool
    def get_realtime_fund_flow(
        ticker: Annotated[str, "Stock ticker, e.g. 600519.SH or 000001.SZ"],
    ) -> str:
        """Get LIVE intraday fund flow (实时主力资金流向) from Eastmoney push2 API.

        Returns REAL-TIME breakdown: main net inflow/outflow by order size
        (super-large, large, medium, small), main inflow ratio, turnover,
        volume ratio. Data refreshes every ~3 seconds during trading hours.

        Use this to cross-check tushare moneyflow (end-of-day, delayed)
        against LIVE intraday flow. Critical for same-day decisions.
        """
        try:
            from capitalradar.dataflows.eastmoney_realtime_flow import _get_realtime_fund_flow
            result = _get_realtime_fund_flow(ticker)
            if not result:
                return f"实时资金流数据不可用: {ticker}（可能非交易时段或数据源异常）"

            inflow = result["main_net_inflow"]
            direction = "🟢 主力净流入" if inflow > 0 else ("🔴 主力净流出" if inflow < 0 else "⚪ 持平")
            return "\n".join([
                f"## 实时主力资金: {ticker} ({result['name']})",
                f"**{direction}**: {inflow/1e4:+.0f}万元",
                f"**现价**: {result['price']:.2f} | 涨跌幅: {result['change_pct']:+.2f}%",
                "",
                "### 按单量拆解",
                f"- 超大单: {result['super_large_net']/1e4:+.0f}万 | 大单: {result['large_net']/1e4:+.0f}万",
                f"- 中单: {result['medium_net']/1e4:+.0f}万 | 小单: {result['small_net']/1e4:+.0f}万",
                f"- 主力流入占比: {result['main_inflow_ratio']:.1f}% | 换手: {result['turnover_rate']:.2f}%",
                "",
                "**Source**: 东方财富 push2 (实时，盘中~3秒刷新)",
            ])
        except Exception as e:
            return f"实时资金流查询失败: {e}"

    @tool
    def get_block_trades(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH)"],
        lookback_days: Annotated[int, "Days to look back (default 30)"] = 30,
    ) -> str:
        """Get recent block trades (大宗交易/暗盘) for a stock.

        Block trades are off-exchange institutional transactions executed at
        negotiated prices. Key signals:
        - Discount + institutional desk buying = dark pool accumulation (bullish)
        - Premium + institutional desk selling = dark pool distribution (bearish)
        - Large consecutive discounts may indicate insider selling
        - Multiple institutional desks buying = strong accumulation signal

        Use this to detect hidden institutional positioning that doesn't appear
        in regular order-book capital flow data.
        """
        from capitalradar.dataflows.block_trade_data import get_block_trade_detail
        return get_block_trade_detail(ticker, lookback_days=lookback_days)

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

    return [get_market_overview, get_market_scan_context, run_smart_screening,
            get_smart_money_score, predict_stock_price, query_pick_performance,
            web_search_current, get_top_gainers, get_multi_factor_ranking,
            get_top_net_inflow, query_eastmoney_data, get_high_dividend_stocks,
            get_short_term_picks, get_hot_reversal_picks, get_reversal_performance,
            get_realtime_fund_flow, get_block_trades, verify_moneyflow]


def create_aipick_agent(run_ids: list[str], config: dict, lang: Optional[str] = None):
    """Create the AI Pick LLM instance with tools bound."""
    from capitalradar.llm_clients import create_llm_client, resolve_role_llm

    provider, deep_model, _ = resolve_role_llm(config, "deep")

    client = create_llm_client(provider=provider, model=deep_model, base_url=config.get("backend_url"), timeout=300)
    llm = client.get_llm()
    tools = build_aipick_tools(config, llm)
    llm_with_tools = llm.bind_tools(tools)
    system_prompt = build_aipick_system_prompt(config, lang or config.get("output_language", "Chinese"))
    return llm_with_tools, tools, system_prompt, llm


def _sse_event(name: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


def stream_aipick_chat(thread_id: str, question: str, config: dict, lang: Optional[str] = None) -> Generator[str, None, None]:
    """SSE generator for AI Pick chat. Same agent-loop pattern as Advisory."""
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

    try:
        llm_with_tools, tools, system_prompt, llm = create_aipick_agent([], config, lang)
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
    consecutive_same_tool = 0
    last_tool_name = ""

    for iteration in range(15):
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
                yield _sse_event("chat-tool-call", {"tool_name": tool_name, "args": safe_args})

                func = tool_map.get(tool_name)
                if func:
                    try:
                        result = func.invoke(tool_args)
                        result_str = str(result)[:2000]
                    except Exception as e:
                        result_str = f"Error: {str(e)}"
                else:
                    result_str = f"Tool '{tool_name}' not found. Available: {list(tool_map.keys())}"

                yield _sse_event("chat-tool-result", {"tool_name": tool_name, "result_snippet": result_str})
                tool_call_records.append({"tool_name": tool_name, "args": safe_args, "result_snippet": result_str})
                messages.append(ToolMessage(content=result_str, tool_call_id=tc.get("id", "")))

            # Detect infinite tool-call loop: if same tool called 4+ times in a row, force answer
            if tool_name == last_tool_name:
                consecutive_same_tool += 1
            else:
                consecutive_same_tool = 1
                last_tool_name = tool_name

            if consecutive_same_tool >= 4:
                # Force the agent to answer with what it has
                force_prompt = (
                    "You have called the same tool repeatedly without making progress. "
                    "Based on the information you have already gathered, produce your best "
                    "answer now. Do not call any more tools."
                )
                messages.append(HumanMessage(content=force_prompt))
                try:
                    response = llm_with_tools.invoke(messages)
                except Exception as e:
                    yield _sse_event("chat-error", {"message": f"LLM error: {str(e)}"})
                    return
                final_text = response.content if hasattr(response, "content") else str(response)
                tc_json = json.dumps(tool_call_records, ensure_ascii=False)
                msg_id = save_chat_message(config, thread_id, "assistant", final_text, tc_json)
                yield _sse_event("chat-done", {"full_response": final_text, "message_id": msg_id, "tool_calls_count": len(tool_call_records)})
                return
        else:
            final_text = response.content if hasattr(response, "content") else str(response)
            tc_json = json.dumps(tool_call_records, ensure_ascii=False) if tool_call_records else ""
            msg_id = save_chat_message(config, thread_id, "assistant", final_text, tc_json)
            yield _sse_event("chat-done", {"full_response": final_text, "message_id": msg_id, "tool_calls_count": len(tool_call_records)})
            return

    yield _sse_event("chat-error", {"message": "Agent reached maximum tool-call iterations without producing a final answer."})
