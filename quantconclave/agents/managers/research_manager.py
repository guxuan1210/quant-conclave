"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from quantconclave.agents.schemas import ResearchPlan, render_research_plan
from quantconclave.agents.utils.agent_utils import (
    build_state_instrument_context,
    get_language_instruction,
)
from quantconclave.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        from quantconclave.graph.adjudicator import render_adjudication_notes
        instrument_context = build_state_instrument_context(state)
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

---

⚠️ **Manipulation Risk Assessment (REQUIRED):**
Before finalizing your rating, you MUST evaluate whether either side's arguments could be based on institutionally-manufactured signals. Major capital (主力资金) can manipulate all five analysis dimensions: (a) technical indicators — by pushing prices to trigger false breakouts/breakdowns; (b) sentiment — by flooding social media with coordinated narratives; (c) news — by timing coverage releases to coincide with their trading; (d) fundamentals — by spinning the same data to support any narrative; (e) capital flow — by disguising distribution as accumulation through order-splitting or dark pools.

**Cross-Analyst Manipulation Detection (SYMMETRIC — evaluate BOTH trap types equally):**
If the debate arguments from the Bull or Bear side rely heavily on ONE type of signal (e.g., only technicals, only sentiment) while ignoring or dismissing the capital flow signals, suspect narrative manipulation.
- BULLISH TRAP (诱多): Bullish arguments built on sentiment + news + technicals BUT capital flow shows institutional DISTRIBUTION (net OUTFLOW by super-large orders). This pattern almost always indicates a retail trap — institutions manufacturing bullish signals to sell into.
- BEARISH TRAP (诱空): Bearish arguments built on sentiment + news + technicals BUT capital flow shows institutional ACCUMULATION (net INFLOW by super-large orders). This pattern almost always indicates a shakeout — institutions manufacturing bearish signals to buy cheap. MISSING THIS IS EQUALLY COSTLY.

**Convergent Manipulation Signals (increasingly dangerous combinations):**
- Level 1: Single-dimension anomaly (e.g., technical pattern looks manufactured)
- Level 2: Two dimensions align suspiciously (e.g., bullish news + bullish social media but both appear coordinated)
- Level 3a — Bullish Trap (诱多): Three or more dimensions align with institutional counter-trading (e.g., bullish technicals + bullish sentiment + bullish news + institutional SELLING) — classic "distribution into strength" retail trap
- Level 3b — Bearish Trap (诱空): Three or more dimensions align with institutional counter-trading (e.g., bearish technicals + bearish sentiment + bearish news + institutional BUYING) — classic "shaking out weak hands" accumulation trap

If the debate reveals a DIVERGENCE between capital flow data and the prevailing narrative, treat that as a red flag — REGARDLESS OF DIRECTION. Trust capital flow direction over narrative when they conflict. Capital flow BUYING + bearish narrative = BULLISH signal (shakeout accumulation). Capital flow SELLING + bullish narrative = BEARISH signal (distribution into strength). State your overall manipulation risk level (HIGH/MEDIUM/LOW) in your decision.

---

**Debate History:**
{history}""" + get_language_instruction() + "\n\n" + render_adjudication_notes(state)

        investment_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        checkpoint_1_question = ""
        if state.get("checkpoint_1_enabled"):
            q_prompt = (
                f"You just produced this investment plan for {state['company_of_interest']}:\n\n"
                f"{investment_plan}\n\n"
                f"Formulate ONE concise question (1-2 sentences) to ask the human user before the Trader executes. "
                f"Ask about risk tolerance, timing preferences, or any factor you genuinely need clarified. "
                f"Write ONLY the question text, no preamble, no markdown."
            )
            q_response = llm.invoke(q_prompt)
            checkpoint_1_question = q_response.content.strip()

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
            "checkpoint_1_question": checkpoint_1_question,
        }

    return research_manager_node

