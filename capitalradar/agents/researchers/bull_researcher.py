from capitalradar.agents.utils.agent_utils import get_language_instruction


def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        capital_flow_report = state.get("capital_flow_report", "")
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        capital_flow_section = (
            f"Capital Flow / Smart Money Report: {capital_flow_report}"
            if capital_flow_report
            else ""
        )

        is_first_round = (investment_debate_state.get("count", 0) == 0)
        if is_first_round:
            prompt = f"""You are a Bull Analyst advocating for investing in the {target_label}. This is your OPENING STATEMENT — you are the first speaker. No bear argument exists yet.

Your task: Build a strong, independent bull case based on the research data. Do NOT reference or anticipate bear arguments.

Key points:
- Growth Potential: Market opportunities, revenue projections, scalability
- Competitive Advantages: Unique products, branding, market positioning
- Positive Indicators: Financial health, industry trends, positive news
- Capital Flow Check: If the Capital Flow Report shows major funds buying, highlight it. If selling, acknowledge the risk honestly.

Resources:
Market research: {market_research_report}
Sentiment report: {sentiment_report}
News: {news_report}
{fundamentals_label}: {fundamentals_report}
{capital_flow_section}

Write your opening bull case now. Be evidence-based and concise.
""" + get_language_instruction()
        else:
            prompt = f"""You are a Bull Analyst advocating for investing in the {target_label}. This is a REBUTTAL round.

The Bear has just made the following argument. Your job: rebut the Bear's specific points with evidence, then reinforce your bull case.

Key points:
- Rebuttal: Address the Bear's specific claims. Where is their data wrong or incomplete?
- Reinforcement: After rebutting, restate why the bull case remains stronger
- Capital Flow: If money flow contradicts bearish narrative, highlight this

Debate history: {history}
Bear's latest argument (REBUT THIS): {current_response}

Write your rebuttal now. Be direct — address the Bear's points specifically.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
