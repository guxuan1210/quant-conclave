from capitalradar.agents.utils.agent_utils import get_language_instruction


def create_aggressive_debator(llm):
    def aggressive_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        aggressive_history = risk_debate_state.get("aggressive_history", "")

        current_conservative_response = risk_debate_state.get("current_conservative_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        capital_flow_report = state.get("capital_flow_report", "")
        human_feedback = state.get("human_feedback", "")

        trader_decision = state["trader_investment_plan"]

        capital_flow_section = (
            f"Capital Flow / Smart Money Report: {capital_flow_report}"
            if capital_flow_report
            else ""
        )

        human_line = (
            f"Human Trader's Input (from checkpoint): {human_feedback}"
            if human_feedback
            else ""
        )

        has_opponents = bool(current_conservative_response.strip() or current_neutral_response.strip())
        if not has_opponents:
            prompt = f"""You are the Aggressive Risk Analyst. This is your OPENING STATEMENT — you are the first speaker. No opposing views exist yet.

Your role: Champion high-reward opportunities. Evaluate the trader's plan below and argue for bold action. Do NOT reference conservative or neutral views (they haven't spoken yet).

Trader's plan: {trader_decision}

Key points:
- Upside potential: growth, innovation, competitive advantage
- Capital Flow check: if major funds align with the trader's direction, emphasize this; if opposing, acknowledge the risk honestly

Resources:
Market: {market_research_report}
Sentiment: {sentiment_report}
News: {news_report}
Fundamentals: {fundamentals_report}
{capital_flow_section}
{human_line}

Write your opening aggressive argument now.
""" + get_language_instruction()
        else:
            prompt = f"""You are the Aggressive Risk Analyst. This is a REBUTTAL round — other analysts have spoken. Rebut their points, then reinforce your aggressive stance.

Your role: Counter conservative/neutral caution. Argue for high-reward action.

Trader's plan: {trader_decision}
History: {history}
Conservative's latest: {current_conservative_response}
Neutral's latest: {current_neutral_response}

Address their concerns directly. Why does their caution miss the opportunity? What data supports aggressive action?
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Aggressive Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": aggressive_history + "\n" + argument,
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Aggressive",
            "current_aggressive_response": argument,
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return aggressive_node
