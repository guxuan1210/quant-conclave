"""Portfolio Manager: two-phase agent (tool loop -> structured output).

Phase 1 - Tool Loop:
  PM may call predict_stock_price if the ML Prediction Report is empty.
  Up to 5 tool-call rounds, then falls through to Phase 2.

Phase 2 - Structured Output:
  PM receives the same context + any ToolMessages from Phase 1 and
  produces a typed PortfolioDecision.
"""

from __future__ import annotations

import re

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from capitalradar.agents.schemas import PortfolioDecision, render_pm_decision
from capitalradar.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from capitalradar.agents.utils.pm_tools import predict_stock_price
from capitalradar.agents.utils.quant_tools import (
    get_trendlines, get_chart_pattern, get_trend_score, get_atr,
)
from capitalradar.graph.adjudicator import render_adjudication_notes
from capitalradar.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_portfolio_manager(llm):
    """Create the Portfolio Manager graph node.

    Returns a callable that accepts (state) and returns dict with
    ``final_trade_decision``, ``risk_debate_state``, ``checkpoint_2_question``.
    """

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])
        ticker = state["company_of_interest"]

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]
        capital_flow_report = state.get("capital_flow_report", "")
        human_feedback = state.get("human_feedback", "")

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        adjudication_text = render_adjudication_notes(state)
        if adjudication_text:
            adjudication_text = (
                f"**Adjudicator Warnings (read carefully):**\n{adjudication_text}\n"
            )
        else:
            adjudication_text = (
                "- No adjudicator warnings for this stock.\n"
            )

        prediction_report = state.get("prediction_report", "")

        capital_flow_line = (
            f"- **Capital Flow / Smart Money Report**: {capital_flow_report}\n"
            if capital_flow_report
            else ""
        )

        human_line = (
            f"- **Human Trader's Input (from checkpoint)**: {human_feedback}\n"
            if human_feedback
            else ""
        )

        # ---- Phase 1: Tool-bound LLM (may call predict_stock_price) ----
        pm_tools = [predict_stock_price, get_trendlines, get_chart_pattern,
                    get_trend_score, get_atr]
        pm_messages = [
            SystemMessage(
                content=(
                    f"You are the Portfolio Manager for {ticker}. "
                    f"Your job: decide the final trading position (Buy/Overweight/Hold/Underweight/Sell) "
                    f"with 5-20 trading day horizon.\n\n"
                    f"If the ML Prediction Report below is empty or shows errors, "
                    f"you MUST call predict_stock_price(ticker) immediately to get fresh data. "
                    f"Do NOT proceed to your final decision without prediction data.\n\n"
                    f"Optional validation tools (call if you need precise levels):\n"
                    f"- get_trendlines(symbol) — mathematical support/resistance with channel slope\n"
                    f"- get_chart_pattern(symbol) — candlestick pattern identification\n\n"
                    f"{instrument_context}"
                )
            ),
        ]
        if prediction_report:
            pm_messages.append(HumanMessage(
                content=f"ML Prediction Report (pre-computed):\n{prediction_report}"
            ))
        pm_messages.append(HumanMessage(
            content="Proceed with the final decision. If you need prediction data, call predict_stock_price first."
        ))

        pm_tool_llm = llm.bind_tools(pm_tools)
        for _iteration in range(5):
            response = pm_tool_llm.invoke(pm_messages)
            pm_messages.append(response)
            if not hasattr(response, "tool_calls") or not response.tool_calls:
                break
            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                if tool_name == "predict_stock_price":
                    try:
                        result = predict_stock_price.invoke(tool_args)
                    except Exception as e:
                        result = f"predict_stock_price error: {e}"
                else:
                    result = f"Tool '{tool_name}' not available."
                pm_messages.append(ToolMessage(content=result, tool_call_id=tc.get("id", "")))
        else:
            # Exceeded max iterations - fall through to Phase 2 with what we have
            pass

        # ---- Phase 2: Structured output ----
        structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.
The PRIME decision horizon is 5-20 trading days. Long-term structural trends are context only; do NOT let them override short-to-mid-term signals.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**IMPORTANT -- CONFLICT SELF-CHECK (complete before selecting rating):**

Before you choose a rating, pause and audit your own analysis for contradictions:

**A. Short-term vs Medium-term divergence:**
- Are the 5d and 20d direction forecasts aligned or in conflict?
- If they CONFLICT (e.g. 5d bullish bounce, 20d bearish downtrend):
  → State which one you trust more and WHY
  → Your rating must reflect that you're trading against one of the two signals
  → If 20d is bearish with HIGH confidence, you need a STRONG reason to rate above Underweight

**B. Fundamentals vs Technicals / Sentiment:**
- Does the fundamentals report contain extreme risk signals?
  (severe FCF drain, debt spiral, persistent losses, revenue collapse)
- If YES, your rating MUST address them explicitly in the investment thesis
- Do NOT let growth narrative or short-term price action drown out structural risk

**C. Capital Flow reliability:**
{adjudication_text}
- If the adjudicator flags capital flow data as potentially unreliable, cross-check
  against Market technicals and Fundamentals before using flow as your primary driver

**D. Cross-Run Consistency:**
{lessons_line}
- If a prior analysis of this ticker rated differently, explain what NEW data justifies the change
- If fundamentals (PE, FCF, revenue trend) are unchanged since last analysis,
  a rating flip requires specific, named evidence — not just "momentum shifted"

The `confidence` field MUST reflect the ML prediction confidence, not your personal conviction.

**Your Decision Structure must include BOTH time windows:**

1. **5-day outlook**: What does the ML prediction + capital flow + technicals say about the next 5 trading days? Direction probability, expected price range, key levels.
2. **20-day outlook**: What does the picture look like over the next month? Is the ML prediction vs fundamental analysis aligned or divergent?
3. **Final Rating**: Which rating fits the balance of 5d and 20d views? If 5d and 20d signals conflict, explain the trade-off explicitly — do not silently pick one side. Your rating must account for both timelines.

**Context:**
- Research Manager's investment plan: **{research_plan}**
- **Analysis date**: {state['trade_date']} -- always reference this date, never invent future dates
- Trader's transaction proposal: **{trader_plan}**
{human_line}{capital_flow_line}
**Risk Analysts Debate History:**
{history}

---

**PRIMARY DECISION DRIVER -- ML PREDICTION DATA**

The ML Prediction Report (either pre-computed or fetched via predict_stock_price tool) is your SINGLE MOST IMPORTANT INPUT. It contains:
- **Direction Forecast** (5d and 20d): up/down probabilities with calibrated confidence
- **Price Range Forecast** (5d and 20d): lower bound, median, upper bound
- **Institutional Behavior Phase**: accumulation / shakeout / markup / distribution
- **Cross-validation**: whether models agree or conflict
- **Overall confidence**: high / medium / low

**Rules for using the ML Prediction:**
1. If the prediction shows a **high-confidence directional probability** (>= 65%), weight it heavily -- the ML is calibrated and has historical accuracy.
2. If the prediction and the analyst debate CONFLICT, explain which you trust more and why.
3. If the prediction's 5d and 20d outlooks diverge (e.g., 5d bullish bounce but 20d bearish), your rating must account for the near-term opportunity while noting the medium-term risk.
4. The prediction's **price range** should directly inform your entry/exit levels and stop-loss placement.
5. **If prediction data is genuinely unavailable** (tool errors out or returns empty), set manipulation risk to MEDIUM -- never HIGH without ML data.

---

**MANIPULATION RISK ASSESSMENT (REQUIRED):**

Synthesize the analysts' capital flow assessments:

1. **Tally**: How many analysts flagged HIGH / MEDIUM / LOW manipulation risk?
2. **Pattern**: Is money flow + narrative creating a trap? (distribution into strength = 诱多; shaking out weak hands = 诱空)
3. **Trust the money**: If institutions buy while narrative is bearish -> likely shakeout, not a crash. If institutions sell while narrative is bullish -> likely distribution, not a breakout.
4. **Verdict**: State final manipulation risk (HIGH/MEDIUM/LOW) and the specific trap retail should avoid.

Be decisive. Ground every conclusion in specific evidence. Remember: your user cares about the next 5-20 trading days, not the next 5-20 years.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        checkpoint_2_question = ""
        if state.get("checkpoint_2_enabled"):
            q_prompt = (
                f"You just produced this final trading decision for {state['company_of_interest']}:\n\n"
                f"{final_trade_decision}\n\n"
                f"Formulate ONE concise question (1-2 sentences) to ask the human user before this decision is finalized. "
                f"Ask about position sizing comfort, time horizon preference, or any last concern. "
                f"Write ONLY the question text, no preamble, no markdown."
            )
            q_response = llm.invoke(q_prompt)
            checkpoint_2_question = q_response.content.strip()

        # Brief risk-debate closeout
        rating_match = re.search(r'\*\*Rating\*\*[:\s]*(\w+)', final_trade_decision)
        rating_str = rating_match.group(1) if rating_match else "N/A"
        new_risk_debate_state = {
            "judge_decision": f"The Portfolio Manager has evaluated all risk perspectives and produced the final investment decision. Final rating: {rating_str}. See Final Decision section for full details.",
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "checkpoint_2_question": checkpoint_2_question,
        }

    return portfolio_manager_node
