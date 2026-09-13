from quantconclave.agents.utils.agent_utils import get_language_instruction


def create_conservative_debator(llm):
    def conservative_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        conservative_history = risk_debate_state.get("conservative_history", "")

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
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

        prompt = f"""You are the Conservative Risk Analyst. The Aggressive analyst has just spoken. Rebut their points, then present your conservative case.

Your role: Prioritize capital preservation, risk mitigation, and steady returns. Challenge overly optimistic assumptions.

Trader's plan: {trader_decision}

Aggressive's argument (REBUT THIS): {current_aggressive_response}
Neutral's argument (if any): {current_neutral_response}
History: {history}

Key points:
- Point out risks the Aggressive analyst overlooked
- Capital Flow: if funds are selling, emphasize distribution risk; if buying, acknowledge
- Propose safer alternatives if the current plan is too risky

Resources:
Market: {market_research_report}
Sentiment: {sentiment_report}
News: {news_report}
Fundamentals: {fundamentals_report}
{capital_flow_section}
{human_line}

Write your rebuttal + conservative case now.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Conservative Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": conservative_history + "\n" + argument,
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Conservative",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": argument,
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return conservative_node
