from capitalradar.agents.utils.agent_utils import get_language_instruction


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

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

        is_first_round = (investment_debate_state.get("count", 0) == 0) and not current_response.strip()
        if is_first_round:
            prompt = f"""You are a Bear Analyst evaluating the {target_label}. This is your OPENING STATEMENT — the Bull has just made their opening argument. Your job: rebut their specific claims, then present the bear case.

Key points:
- Rebut the Bull's opening argument point by point with counter-evidence
- Present bearish risks: market saturation, financial instability, competitive threats
- Capital Flow: If money flow data contradicts the bull narrative, highlight this divergence

Bull's opening argument (REBUT THIS): {current_response}

Resources:
Market research: {market_research_report}
Sentiment: {sentiment_report}
News: {news_report}
{fundamentals_label}: {fundamentals_report}
{capital_flow_section}

Write your rebuttal + bear case now. Address the Bull's points specifically.
""" + get_language_instruction()
        else:
            prompt = f"""You are a Bear Analyst making the case against the {target_label}. This is a REBUTTAL round — the Bull has just responded. Your job: counter the Bull's latest points, then reinforce the bear case.

Key points:
- Rebut the Bull's latest argument directly (see below)
- Reinforce bearish risks with evidence
- Capital Flow alignment: if funds are selling, emphasize this; if buying, acknowledge

Debate history: {history}
Bull's latest argument (REBUT THIS): {current_response}

Write your rebuttal now.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
