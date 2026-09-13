from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from quantconclave.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
    get_realtime_quote,
)
from quantconclave.dataflows.config import get_config
from quantconclave.agents.utils.web_search_tool import web_search


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = build_instrument_context(
            state["company_of_interest"], asset_type
        )

        capital_flow_report = state.get("capital_flow_report", "")

        tools = [
            get_news,
            get_global_news,
            get_realtime_quote,
        ]

        system_message = (
            "=== YOUR MOST IMPORTANT REFERENCE: CAPITAL FLOW REPORT ===\n"
            "Before you start, carefully read this Capital Flow report — it tells you what major capital is ACTUALLY doing with this stock:\n\n"
            f"--- CAPITAL FLOW REPORT START ---\n"
            f"{capital_flow_report[:4000] if capital_flow_report else 'Not yet available.'}\n"
            f"--- CAPITAL FLOW REPORT END ---\n\n"
            "Use this as your truth anchor for news analysis. Every piece of news must be cross-referenced against actual capital flows.\n"
            "If news is overwhelmingly positive but capital is flowing OUT, the news is being used as a distribution tool (利好出货).\n"
            "If news is overwhelmingly negative but capital is flowing IN, the news is being used for accumulation (利空吸筹).\n"
            "Your job: determine whether the news is genuine information or a tool major capital uses to manipulate retail.\n\n"
            "=== REAL-TIME MARKET CONTEXT (CHECK FIRST) ===\n"
            "Before analyzing news, call `get_realtime_quote` to get the current market state: live price, session volume, and whether the market is open/closed. This is critical context:\n"
            "- If the market is currently open: recent news may be driving LIVE price action — check if the current price move aligns with news sentiment or diverges from it.\n"
            "- If the market is closed: the real-time quote shows the closing price and after-hours movement, giving you the latest market anchor to evaluate news against.\n"
            "- Price-news divergence (e.g., bullish news + declining current price) is a strong manipulation signal that must be flagged.\n"
            "- Cross-reference the current price/volume against the Capital Flow report: does the real-time picture match what capital flows suggest?\n\n"
            +
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for {asset_label}-specific or targeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. For Chinese A-share stocks, CLS (财联社) and Eastmoney (东方财富) are your primary news sources — they provide the fastest and most relevant Chinese-language coverage. Use Chinese keywords when searching for Chinese stock news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + """

⚠️ CRITICAL — Major Fund Movement Assessment:

Before concluding, you MUST analyze whether major funds / institutions are using news and media to manipulate retail investors. Institutions control access to sell-side analysts, can time the release of positive/negative coverage to coincide with their trading, and can plant stories to create buying or selling pressure. Key manipulation signals to detect:

- **Coverage timing relative to price moves**: If overwhelmingly positive news appears AFTER a stock has already risen significantly, institutions may be using the news to attract retail buyers while they distribute
- **Coordinated media campaigns**: Multiple outlets publishing similar bullish/bearish narratives simultaneously, especially from sources tied to specific institutions
- **Selective disclosure**: Companies releasing favorable information while burying negative details — check if the news timing aligns with insider selling or institutional outflows
- **Analyst report clustering**: Multiple analyst upgrades/downgrades within a tight window may indicate coordinated institutional messaging rather than independent analysis
- **News-flow divergence**: If news tone is uniformly bullish but stock price is declining (or vice versa), the news may be a smokescreen for institutional trading

**CROSS-REFERENCE WITH CAPITAL FLOW REPORT**: Compare your news analysis against the Capital Flow report above. If news is bullish but capital flow shows net OUTFLOW, this is 利好出货 (distribution via good news) — the most dangerous retail trap. If news is bearish but capital flow shows net INFLOW, this is 利空吸筹 (accumulation via bad news).

At the end of your report, add this REQUIRED section:

---
## Major Fund Movement Assessment
**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]
**Evidence**: [Specific news patterns that suggest genuine coverage vs. manufactured narrative; flag coverage timing anomalies, coordinated campaigns, news-price divergences]
**Capital Flow vs News Alignment**: [ALIGNED / DIVERGENT — explain: are major funds trading in the same direction the news narrative suggests?]
**Major Capital's Most Likely Play**: [Are they pumping via news to distribute, or suppressing via news to accumulate?]
**Retail Investor Action**: [Should retail follow the news narrative or counter it?]

=== RETRACEMENT CATALYST CHECK ===
- Did the retracement have a concrete negative catalyst, or is it purely technical?
- Technical pullback without negative news = healthy, buyable dip.
- Retracement driven by material bad news (earnings miss, regulatory action) = avoid.
Add to your report:
---
## Retracement Catalyst Check
**Catalyst Type**: [TECHNICAL / NEWS-DRIVEN / MIXED]
**News Severity**: [NONE / MILD / MATERIAL]
**News Score**: [0-2]
---
"""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
