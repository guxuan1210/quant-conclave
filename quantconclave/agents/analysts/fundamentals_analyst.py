from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from quantconclave.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
    get_realtime_quote,
)
from quantconclave.dataflows.config import get_config
from quantconclave.agents.utils.web_search_tool import web_search
from quantconclave.agents.utils.eastmoney_tools import (
    get_eastmoney_fundamentals,
    get_eastmoney_data,
)


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        capital_flow_report = state.get("capital_flow_report", "")

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            get_realtime_quote,
            # 妙想(MX) real-time fundamentals from 东方财富 (CN stocks)
            get_eastmoney_fundamentals,
            get_eastmoney_data,
        ]

        system_message = (
            "=== YOUR MOST IMPORTANT REFERENCE: CAPITAL FLOW REPORT ===\n"
            "Before you start, carefully read this Capital Flow report — it tells you what major capital is ACTUALLY doing with this stock:\n\n"
            f"--- CAPITAL FLOW REPORT START ---\n"
            f"{capital_flow_report[:4000] if capital_flow_report else 'Not yet available.'}\n"
            f"--- CAPITAL FLOW REPORT END ---\n\n"
            "Use this as your truth anchor for fundamental analysis. Every fundamental finding must be cross-referenced against actual capital flows.\n"
            "If fundamentals appear strong but capital is flowing OUT, the fundamental narrative is a distribution smokescreen.\n"
            "If fundamentals appear weak but capital is flowing IN, major capital may be accumulating ahead of a turnaround.\n"
            "Your job: determine whether the fundamental narrative is being USED by major capital to justify their positioning to retail.\n\n"
            "=== REAL-TIME MARKET CONTEXT (CHECK FIRST) ===\n"
            "Before diving into fundamentals, call `get_realtime_quote` to get the current market pulse: live price, bid/ask spread, session volume, and market state. This matters for your valuation analysis:\n"
            "- The current price is your valuation anchor — compare it against book value, earnings, and cash flow metrics you fetch.\n"
            "- If the current price has moved significantly since the last financial statement date, your P/E, P/B, and other multiples must be calculated against the LIVE price, not a stale historical close.\n"
            "- If the market is open: real-time price movement may reflect breaking fundamental news (earnings releases, guidance changes, analyst reports).\n"
            "- Cross-reference the current price against the Capital Flow report: is the market pricing the fundamentals correctly, or is major capital moving the price away from fair value?\n\n"
            + "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements. For Chinese A-share stocks, `get_eastmoney_fundamentals` returns real-time 东方财富 financials and `get_eastmoney_data` answers arbitrary data questions (hot sectors, rankings, 大宗交易 etc.)."
            + """

⚠️ CRITICAL — Major Fund Movement Assessment:

Before concluding, you MUST analyze whether major funds / institutions are manipulating fundamental narratives to trap retail investors. The same financial data can be spun bullishly or bearishly — institutions can emphasize different metrics, change valuation frameworks, or shift narrative focus to justify whatever positioning they hold. Key manipulation signals to detect:

- **Metric cherry-picking**: When the market narrative suddenly shifts from one valuation metric to another (e.g., from P/E to "adjusted EBITDA") without clear reason, this may enable institutions to justify overvalued positions
- **Non-GAAP exaggeration**: Excessive reliance on adjusted/non-GAAP figures that mask deteriorating GAAP performance — a classic institutional tool for maintaining bullish narratives during distribution
- **"Kitchen sink" reporting**: Companies taking unusually large write-downs or charges in a single quarter to create artificially low comparables, setting up "beat" quarters where institutions can sell into strength
- **Insider trading divergence**: If fundamentals appear strong but insiders are selling heavily, the fundamental narrative may be being used as a smokescreen for institutional exit
- **Guidance gaming**: Companies issuing conservative guidance to create low bars they can easily beat, enabling institutions to accumulate before the "surprise"

**CROSS-REFERENCE WITH CAPITAL FLOW REPORT**: Compare your fundamental analysis against the Capital Flow report above. If fundamentals appear strong but capital flow shows net OUTFLOW, the fundamental narrative is being used to justify distribution. If fundamentals appear weak but capital flow shows net INFLOW, major capital is accumulating despite (or because of) the negative narrative.

At the end of your report, add this REQUIRED section:

---
## Major Fund Movement Assessment
**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]
**Evidence**: [Specific fundamental patterns that suggest genuine value vs. manufactured narrative; flag metric shifts, GAAP/non-GAAP divergences, insider trading conflicts]
**Capital Flow vs Fundamentals Alignment**: [ALIGNED / DIVERGENT — are fundamentals and capital flows telling the same story?]
**Major Capital's Most Likely Play**: [Are they using fundamentals narrative to distribute or accumulate?]
**Retail Investor Action**: [Should retail trust the fundamental narrative or counter it?]

=== RETRACEMENT VALUATION ANCHOR ===
- After the retracement, is PE/PB below historical median? Below = value support.
- Is ROE stable? Declining ROE + price dip = value trap, not opportunity.
Add to your report:
---
## Retracement Valuation Anchor
**Valuation Zone**: [UNDERVALUED / FAIR / OVERVALUED]
**ROE Stability**: [STABLE / DECLINING / IMPROVING]
**Fundamentals Score**: [0-2]
---
"""
            + get_language_instruction(),
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
