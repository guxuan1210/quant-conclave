from quantconclave.agents.utils.agent_utils import get_language_instruction


def create_neutral_debator(llm):
    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_conservative_response = risk_debate_state.get("current_conservative_response", "")

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

        prompt = f"""You are the Neutral Risk Analyst. Both Aggressive and Conservative have spoken. Weigh both sides, then provide a balanced assessment.

Your role: Find the middle ground. Critique over-optimism AND over-caution. Propose a balanced adjustment.

Trader's plan: {trader_decision}

Aggressive's argument: {current_aggressive_response}
Conservative's argument: {current_conservative_response}
History: {history}

Key points:
- Where is the Aggressive analyst too optimistic?
- Where is the Conservative analyst too cautious?
- Capital Flow: what does the actual money flow direction suggest?
- Propose a moderate strategy that captures upside while managing downside

Resources:
Market: {market_research_report}
Sentiment: {sentiment_report}
News: {news_report}
Fundamentals: {fundamentals_report}
{capital_flow_section}
{human_line}

Write your balanced assessment now. Address both sides specifically.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return neutral_node
