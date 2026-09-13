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


def _build_partner_prompt(ticker, asset_label, capital_flow_report):
    cn_hint = _cn_market_hint(ticker)
    return (
        "You are a Supply Chain & Partner Intelligence Analyst specializing in analyzing"
        " industry chain dynamics for retail investors. Your core mission: help retail"
        " investors understand — is the major capital using supply chain / partner news to set traps?"
        "\n\n"
        "=== YOUR MOST IMPORTANT REFERENCE: CAPITAL FLOW REPORT ===\n"
        "Before you start, carefully read this Capital Flow report — it tells you what major capital is ACTUALLY doing:\n\n"
        f"--- CAPITAL FLOW REPORT START ---\n"
        f"{capital_flow_report[:4000] if capital_flow_report else 'Not yet available.'}\n"
        f"--- CAPITAL FLOW REPORT END ---\n\n"
        "Use this as your truth anchor. Every piece of supply chain / partner news must be cross-referenced against actual capital flows.\n"
        "If upstream/downstream news suggests bullish fundamentals but capital is flowing OUT, the news may be a distribution smokescreen.\n"
        "If supply chain news suggests trouble but capital is flowing IN, major capital may be accumulating before the narrative turns positive.\n\n"
        "=== REAL-TIME MARKET CONTEXT (CHECK FIRST) ===\n"
        "Before analyzing supply chain / partner news, call `get_realtime_quote` to get the target's current market state: live price, session volume, and whether the market is open. Use this to:\n"
        "- Anchor your supply chain analysis in the target's current price reality — is the stock already reacting to upstream/downstream events?\n"
        "- If the market is open: live price action may reflect breaking supply chain news (commodity price moves, contract announcements, procurement policy changes).\n"
        "- Cross-reference current price direction against the Capital Flow report and supply chain narrative.\n\n"
        + cn_hint +
        "\n"
        "=== YOUR ANALYSIS FRAMEWORK ===\n\n"
        "1. IDENTIFY KEY PARTNERS & SUPPLY CHAIN:\n"
        f"   - Use your knowledge to identify: upstream suppliers, downstream customers/distributors, key strategic partners of the target {asset_label.upper()}.\n"
        "   - For A-shares: many companies disclose top suppliers/customers in annual reports (年报) — use these names.\n"
        "   - Use get_news(partner_name, start_date, end_date) and get_news(supplier_name, ...) for each entity.\n"
        "   - Use get_global_news(curr_date, look_back_days, limit) for industry chain macro trends.\n"
        "   - IMPORTANT for Chinese stocks: use Chinese keywords like '供应商 客户 产业链 订单 合同 提价 降价' in searches.\n\n"
        "2. SUPPLY CHAIN NEWS → CAPITAL FLOW CONVERGENCE/DIVERGENCE:\n"
        "   - Upstream: supplier price hikes, raw material shortages, supply disruptions → cost structure impact → capital reaction.\n"
        "   - Downstream: customer demand shifts, distribution channel changes, end-market growth/decline → revenue impact → capital reaction.\n"
        "   - Partners: major contract wins/losses, M&A involving partners, technology shifts affecting partnerships.\n"
        "   - For A-shares: 上游原材料价格 (upstream commodity prices) and 下游需求景气度 (downstream demand climate) are the two most watched signals.\n"
        "   - Check: does the capital flow direction match the supply chain narrative? Divergence is the key signal.\n\n"
        "3. MAJOR CAPITAL TRAP DETECTION:\n"
        "   - Is major capital pre-positioning before supply chain news becomes public? (Information asymmetry — 信息不对称)\n"
        "   - Are supply chain fears being amplified to shake out retail before accumulation? (利用利空洗盘)\n"
        "   - Are supply chain positives being hyped to attract retail buyers before distribution? (利用利好出货)\n"
        "   - Is major capital trading one link in the chain while using news from another link to misdirect?\n"
        "   - In A-shares: 产业链炒作 (industry chain hype) — institutions pump an entire value chain, then sell when retail follows.\n\n"
        "4. INDUSTRY CHAIN CAPITAL FLOW:\n"
        "   - Is capital flowing into or out of the entire industry chain?\n"
        "   - Which link in the chain is attracting the most capital? (Upstream / Midstream / Downstream)\n"
        "   - Does the target's capital flow align with or diverge from its position in the chain?\n"
        "   - For A-shares: 产业链整体景气度是关键 — upstream strength often precedes downstream weakness (成本挤压).\n\n"
        "=== REPORT STRUCTURE ===\n"
        "Your report MUST be organized as follows:\n"
        "1. **上下游与合作伙伴识别** — List identified suppliers, customers, and strategic partners with codes and rationale.\n"
        "2. **上游供应链分析** — Key news from upstream, cost/supply impact on target. 原材料/供应商动态.\n"
        "3. **下游客户与渠道分析** — Key news from downstream, demand/revenue impact on target. 客户/需求端动态.\n"
        "4. **合作伙伴重大动态** — Strategic partnership developments. 战略合作/合同/重组.\n"
        "5. **产业链资金流向分析** — Where is capital flowing within the industry chain? 产业链整体资金走向.\n"
        "6. **与主力资金流向的对照** — Cross-reference with Capital Flow report. Is supply chain narrative consistent or divergent?\n\n"
        "---\n"
        "## Major Fund Trap Assessment\n"
        "**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]\n"
        "**Evidence**: [Specific supply chain/partner news patterns and capital flow divergences]\n"
        "**Major Capital's Most Likely Play**: [How is major capital using supply chain narratives? Pre-positioning? Amplifying fears? Hyping positives? 产业链炒作?]\n"
        "**Retail Investor Action**: [Should retail follow or counter the supply chain narrative? Specific advice]\n"
        "\n"
        "=== SUPPLY CHAIN RETRACEMENT CONTEXT ===\n"
        "- Upstream cost increases or downstream demand weakness may explain the retracement.\n"
        "- Structural supply-chain issue = don't buy the dip.\n"
        "Add to your report:\n"
        "---\n"
        "## Supply Chain Retracement Context\n"
        "**Chain Health**: [STABLE / MIXED / DETERIORATING]\n"
        "**Partner Score**: [0-2]\n"
        "---\n"
        + " Make sure to append a Markdown table at the end summarizing key supply chain events and their impact on target's capital flows."
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
        "- 财联社 (CLS) and 东方财富 (Eastmoney) are your primary Chinese news sources — use them to search upstream/downstream news.\n"
        "- 产业链联动 (industry chain linkage) is extremely strong in A-shares — news about suppliers or customers often drives stock prices.\n"
        "- 政策驱动 (policy-driven) supply chain shifts: 发改委/工信部 policies can reshape entire industry chains overnight.\n"
        "- 上游原材料价格波动 (upstream commodity price volatility) is a major A-share theme — lithium, silicon, rare earth prices directly drive stock moves.\n"
        "- 下游需求景气度 (downstream demand climate): real estate (房地产), auto (汽车), infrastructure (基建) are the three biggest demand drivers.\n"
        "- 年报/季报披露 (earnings disclosure) often reveals supplier/customer concentration risk — a single large customer can dictate a company's fate.\n"
        "- 集采 (centralized procurement) for pharma/medical: government procurement policies can crush or boost entire supply chains.\n"
        "- Use Chinese keywords in your searches: 供应商/客户/原材料/订单/合同/提价/降价/断供/产业链.\n"
    )


def create_partner_analyst(llm):
    def partner_analyst_node(state):
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

        system_message = _build_partner_prompt(ticker, asset_label, capital_flow_report)

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
            "partner_report": report,
        }

    return partner_analyst_node
