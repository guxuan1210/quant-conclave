from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from capitalradar.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
    get_realtime_quote,
)
from capitalradar.dataflows.config import get_config
from capitalradar.agents.utils.web_search_tool import web_search


def _build_competitor_prompt(ticker, asset_label, capital_flow_report):
    cn_hint = _cn_market_hint(ticker)
    return (
        "You are a Competitor Intelligence Analyst specializing in analyzing competitive dynamics for retail investors."
        " Your core mission is to help retail investors understand: is the major capital using competitor news to set traps?"
        "\n\n"
        "=== YOUR MOST IMPORTANT REFERENCE: CAPITAL FLOW REPORT ===\n"
        "Before you start, carefully read this Capital Flow report — it tells you what major capital is ACTUALLY doing:\n\n"
        f"--- CAPITAL FLOW REPORT START ---\n"
        f"{capital_flow_report[:4000] if capital_flow_report else 'Not yet available.'}\n"
        f"--- CAPITAL FLOW REPORT END ---\n\n"
        "Use this as your truth anchor. Every piece of competitor news must be cross-referenced against actual capital flows.\n"
        "If competitor news is positive but capital is flowing OUT of the target stock, flag it as a diversion trap.\n"
        "If competitor news is negative but capital is flowing IN, the news may be a smokescreen for accumulation.\n\n"
        "=== REAL-TIME MARKET CONTEXT (CHECK FIRST) ===\n"
        "Before analyzing competitor news, call `get_realtime_quote` to get the target's current market state: live price, session volume, and whether the market is open. Use this to:\n"
        "- Anchor your competitor analysis in the target's current price reality — is the stock already moving on competitor news?\n"
        "- If the market is open: check whether competitor earnings/events are driving LIVE sector-wide movement.\n"
        "- Cross-reference current price direction against the Capital Flow report and competitor narrative.\n\n"
        + cn_hint +
        "\n"
        "=== YOUR ANALYSIS FRAMEWORK ===\n\n"
        "1. IDENTIFY KEY COMPETITORS:\n"
        f"   - First, use your knowledge to identify the top 3-5 direct competitors of the target {asset_label.upper()}.\n"
        "   - For A-share stocks, search with Chinese competitor names and stock codes on CLS/Eastmoney sources.\n"
        "   - Use get_news(competitor_name_or_code, start_date, end_date) to search for each competitor.\n"
        "   - Use get_global_news(curr_date, look_back_days, limit) for broader industry competitive landscape.\n"
        "   - IMPORTANT for Chinese stocks: use Chinese keywords like '竞争对手 财报 新产品 市场份额' in your searches.\n\n"
        "2. COMPETITOR NEWS → CAPITAL ROTATION ANALYSIS:\n"
        "   - If competitors report strong earnings/growth → is capital rotating AWAY from the target into competitors?\n"
        "   - If competitors face trouble → is capital flowing INTO the target as a safe haven or sector rotation play?\n"
        "   - For A-shares: 板块轮动 (sector rotation) is a dominant dynamic — competitor strength often = capital flowing to that sub-sector.\n"
        "   - Check: does the Capital Flow report show net inflow or outflow? Does it match the competitor narrative?\n"
        "   - Cross-reference northbound capital (北向资金) direction: are foreign institutions favoring competitors over the target?\n\n"
        "3. MAJOR CAPITAL TRAP DETECTION:\n"
        "   - Are there coordinated positive news about competitors while target's capital flow shows distribution? → Classic rotation trap (板块轮动陷阱).\n"
        "   - Are there coordinated negative competitor news while target's capital flow shows accumulation? → Shakeout before pump (洗盘).\n"
        "   - Is major capital using competitor narratives to justify price moves that were already planned?\n"
        "   - In A-shares: watch for 题材炒作 (theme speculation) — institutions pumping a competitor theme to lift the whole sector before distributing.\n\n"
        "4. SECTOR MONEY FLOW ANALYSIS:\n"
        "   - Is the entire sector seeing capital inflows or outflows?\n"
        "   - Is the target gaining or losing relative sector share?\n"
        "   - Are institutions rotating within the sector or exiting the sector entirely?\n"
        "   - For A-shares: 行业ETF资金流向 is a key signal — check whether sector ETFs are seeing net inflows or outflows.\n\n"
        "=== REPORT STRUCTURE ===\n"
        "Your report MUST be organized as follows:\n"
        "1. **主要竞争对手识别** — List identified competitors with codes and brief rationale.\n"
        "2. **竞争对手重大动态** — Key news/events for each competitor and their market impact. For A-shares, highlight any 政策利好/利空 affecting competitors.\n"
        "3. **行业资金轮动分析** — How competitor events may drive capital rotation within the sector. 板块资金流向分析.\n"
        "4. **与主力资金流向的对照** — Cross-reference with Capital Flow report. Is competitor narrative consistent or divergent from actual capital flows?\n\n"
        "---\n"
        "## Major Fund Trap Assessment\n"
        "**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]\n"
        "**Evidence**: [Specific competitor news patterns and capital flow divergences]\n"
        "**Major Capital's Most Likely Play**: [How is major capital using competitor narratives? Rotation? Shakeout? Pump-and-dump? 板块轮动陷阱? 题材炒作出货?]\n"
        "**Retail Investor Action**: [Should retail follow or counter the competitor narrative? Specific advice]\n"
        "\n"
        "=== SECTOR RETRACEMENT CONTEXT ===\n"
        "- Is the whole sector pulling back, or just this stock?\n"
        "- Sector-wide retracement = macro/rotation driven, higher confidence.\n"
        "- Individual stock weakness while sector is strong = company-specific problem.\n"
        "Add to your report:\n"
        "---\n"
        "## Sector Retracement Context\n"
        "**Sector Participation**: [BROAD / SELECTIVE / ISOLATED]\n"
        "**Competitor Score**: [0-2]\n"
        "---\n"
        + " Make sure to append a Markdown table at the end summarizing key competitor events and their impact on target's capital flows."
        + get_language_instruction()
    )


def _cn_market_hint(ticker: str) -> str:
    """Return A-share-specific guidance when the ticker is a Chinese stock."""
    ticker_upper = ticker.strip().upper()
    is_cn = any(ticker_upper.endswith(s) for s in (".SH", ".SZ", ".BJ", ".SS")) or (
        ticker_upper.isdigit() and len(ticker_upper) == 6
    )
    if not is_cn:
        return ""

    return (
        "\n"
        "=== A-SHARE MARKET CONTEXT (A股特色) ===\n"
        "You are analyzing a Chinese A-share stock. The following dynamics are CRITICAL:\n"
        "- 财联社 (CLS) and 东方财富 (Eastmoney) are your primary Chinese news sources — they provide the fastest and most relevant coverage.\n"
        "- 板块轮动 (sector rotation) is the dominant A-share dynamic — capital flows rapidly between sectors based on policy signals.\n"
        "- 题材炒作 (theme speculation) is common — institutions create narratives around policy themes (政策利好) to attract retail money.\n"
        "- 北向资金 (northbound capital) is a key signal — foreign capital flows reveal institutional positioning vs competitors.\n"
        "- 龙虎榜 (dragon-tiger list) data, if available, reveals which institutions are trading competitors — look for coordinated patterns.\n"
        "- Policy direction (政策方向) from 国务院/发改委/工信部 can instantly shift competitive dynamics — weight policy news heavily.\n"
        "- Use Chinese keywords in your searches: competitor names + 财报/新产品/融资/重组/政策/补贴/处罚.\n"
    )


def create_competitor_analyst(llm):
    def competitor_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker, asset_type)
        capital_flow_report = state.get("capital_flow_report", "")

        tools = [
            get_news,
            get_global_news,
            web_search,
            get_realtime_quote,
        ]

        system_message = _build_competitor_prompt(ticker, asset_label, capital_flow_report)

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
            "competitor_report": report,
        }

    return competitor_analyst_node
