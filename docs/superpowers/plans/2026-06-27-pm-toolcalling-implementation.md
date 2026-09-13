# Portfolio Manager Tool-Calling Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Portfolio Manager from pure structured-output into a two-phase agent (tool loop → structured output) so it can call `predict_stock_price` when ML prediction data is missing, and ensure the final decision is anchored to 5–20 day horizon.

**Architecture:** Phase 1 — LLM_with_tools loop (up to 5 calls). If no tool_calls, proceed to Phase 2 — structured_llm.invoke → PortfolioDecision. A single tool (`predict_stock_price`) is bound. Graph gets a new `tools_portfolio_manager` ToolNode + conditional edge. PM prompt gains explicit "call the tool if prediction_report is empty" fallback + "MEDIUM risk without ML data" rule.

**Tech Stack:** LangGraph StateGraph, LangChain with_structured_output, PredictionAgent, ToolNode

---

### Task 1: Create `pm_tools.py` — shareable prediction tool

**Files:**
- Create: `capitalradar/agents/utils/pm_tools.py`

- [ ] **Step 1: Create the file**

```python
"""Tool functions for the Portfolio Manager agent node."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def predict_stock_price(
    ticker: str,
) -> str:
    """Run the PredictionAgent ML+LLM forecast for a specific stock.

    Returns 5-day and 20-day direction probabilities, price range intervals,
    institutional behavior phase classification, and actionable guidance.
    Use this to get ML prediction data before making the final decision."""
    from capitalradar.prediction import PredictionAgent
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        agent = PredictionAgent()
        report = agent.predict(ticker, today, {})
        return report.to_markdown()
    except Exception as e:
        logger.exception("PM predict_stock_price failed for %s", ticker)
        return f"# Prediction Error\nPrediction failed for {ticker}: {e}"
```

Note: `{config}` is not available at tool-creation time because `pm_tools.py` is module-level and `config` is passed at graph-invocation time. The tool receives ticker only. The PredictionAgent initialization already handles config internally — it loads from `FeatureEngine` + `get_runtime_config()`.

- [ ] **Step 2: Verify import**

```
python -c "from capitalradar.agents.utils.pm_tools import predict_stock_price; print(predict_stock_price.name)"
```
Expected: `predict_stock_price`

- [ ] **Step 3: Commit**

```
git add capitalradar/agents/utils/pm_tools.py
git commit -m "feat(pm): add predict_stock_price tool for PM agent"
```

---

### Task 2: Add `should_continue_pm` to conditional logic

**Files:**
- Modify: `capitalradar/graph/conditional_logic.py` (after line 93)

- [ ] **Step 1: Add `max_pm_tool_calls` to __init__**

Edit line 9:
```python
def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1, max_analyst_tool_calls=12, max_pm_tool_calls=5):
```
Edit line 13:
```python
self.max_pm_tool_calls = max_pm_tool_calls
```

- [ ] **Step 2: Add `should_continue_pm` method** (after line 93, after `should_continue_partner`)

```python
def should_continue_pm(self, state: AgentState) -> str:
    """Route PM to its tool node or to END.

    PM gets up to ``max_pm_tool_calls`` tool rounds then
    falls through to structured-output phase.
    """
    messages = state["messages"]
    last_message = messages[-1]
    if last_message.tool_calls and self._count_tool_calls(state) < self.max_pm_tool_calls:
        return "tools_portfolio_manager"
    return "END"
```

- [ ] **Step 3: Commit**

```
git add capitalradar/graph/conditional_logic.py
git commit -m "feat(pm): add should_continue_pm() and max_pm_tool_calls param"
```

---

### Task 3: Refactor PM node into two-phase agent

**Files:**
- Modify: `capitalradar/agents/managers/portfolio_manager.py` (full rewrite)

The node now:
1. Exports no-structured-LLM — the factory still receives `llm`, but creates a plain tool-binding LLM for Phase 1.
2. Creates the structured_llm inside the node (lazy — same as before) for Phase 2.
3. Phase 1: LLM_with_tools.invoke(messages) with `predict_stock_price` as the only tool.
4. Phase 2: structured_llm.invoke(prompt) → PortfolioDecision → render.

- [ ] **Step 1: Rewrite portfolio_manager.py**

```python
"""Portfolio Manager: two-phase agent (tool loop → structured output).

Phase 1 — Tool Loop:
  PM may call predict_stock_price if the ML Prediction Report is empty.
  Up to 5 tool-call rounds, then falls through to Phase 2.

Phase 2 — Structured Output:
  PM receives the same context + any ToolMessages from Phase 1 and
  produces a typed PortfolioDecision.
"""

from __future__ import annotations

import re

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from capitalradar.agents.schemas import PortfolioDecision, render_pm_decision
from capitalradar.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from capitalradar.agents.utils.pm_tools import predict_stock_price
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
        from capitalradar.graph.adjudicator import render_adjudication_notes
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

        prediction_report = state.get("prediction_report", "")
        prediction_line = (
            f"- **ML Prediction Report**: {prediction_report}\n"
            if prediction_report
            else ""
        )

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

        # ── Phase 1: Tool-bound LLM (may call predict_stock_price) ──
        pm_tools = [predict_stock_price]
        # Clear messages from prior graph nodes to avoid tainting tool loop
        # (same pattern as analyst nodes)
        pm_messages = [
            SystemMessage(
                content=(
                    f"You are the Portfolio Manager for {ticker}. "
                    f"Your job: decide the final trading position (Buy/Overweight/Hold/Underweight/Sell) "
                    f"with 5–20 trading day horizon.\n\n"
                    f"If the ML Prediction Report below is empty or shows errors, "
                    f"you MUST call predict_stock_price(ticker) immediately to get fresh data. "
                    f"Do NOT proceed to your final decision without prediction data.\n\n"
                    f"{instrument_context}"
                )
            ),
        ]
        if prediction_report:
            pm_messages.append(HumanMessage(
                content=f"ML Prediction Report (pre-computed):\n{prediction_report}"
            ))
        pm_messages.append(HumanMessage(
            content=f"Proceed with the final decision. If you need prediction data, call predict_stock_price first."
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
            # Exceeded max iterations — fall through to Phase 2 with what we have
            pass

        # ── Phase 2: Structured output ──
        structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.
The PRIME decision horizon is 5–20 trading days. Long-term structural trends are context only; do NOT let them override short-to-mid-term signals.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Your Decision Structure must include BOTH time windows:**

1. **5-day outlook**: What does the ML prediction + capital flow + technicals say about the next 5 trading days? Direction probability, expected price range, key levels.
2. **20-day outlook**: What does the picture look like over the next month? Is the ML prediction vs fundamental analysis aligned or divergent?
3. **Final Rating**: Which rating fits the balance of 5d and 20d views? If short-term signals contradict long-term trends, prioritize short-term for the rating and note the long-term risk.

**Context:**
- Research Manager's investment plan: **{research_plan}**
- **Analysis date**: {state['trade_date']} — always reference this date, never invent future dates
- Trader's transaction proposal: **{trader_plan}**
{human_line}{capital_flow_line}{lessons_line}
**Risk Analysts Debate History:**
{history}

---

⚠️ **PRIMARY DECISION DRIVER — ML PREDICTION DATA**

The ML Prediction Report (either pre-computed or fetched via predict_stock_price tool) is your SINGLE MOST IMPORTANT INPUT. It contains:
- **Direction Forecast** (5d and 20d): up/down probabilities with calibrated confidence
- **Price Range Forecast** (5d and 20d): lower bound, median, upper bound
- **Institutional Behavior Phase**: accumulation / shakeout / markup / distribution
- **Cross-validation**: whether models agree or conflict
- **Overall confidence**: high / medium / low

**Rules for using the ML Prediction:**
1. If the prediction shows a **high-confidence directional probability** (>= 65%), weight it heavily—the ML is calibrated and has historical accuracy.
2. If the prediction and the analyst debate CONFLICT, explain which you trust more and why.
3. If the prediction's 5d and 20d outlooks diverge (e.g., 5d bullish bounce but 20d bearish), your rating must account for the near-term opportunity while noting the medium-term risk.
4. The prediction's **price range** should directly inform your entry/exit levels and stop-loss placement.
5. **If prediction data is genuinely unavailable** (tool errors out or returns empty), set manipulation risk to MEDIUM — never HIGH without ML data.

---

⚠️ **MANIPULATION RISK ASSESSMENT (REQUIRED):**

Synthesize the analysts' capital flow assessments:

1. **Tally**: How many analysts flagged HIGH / MEDIUM / LOW manipulation risk?
2. **Pattern**: Is money flow + narrative creating a trap? (distribution into strength = 诱多; shaking out weak hands = 诱空)
3. **Trust the money**: If institutions buy while narrative is bearish → likely shakeout, not a crash. If institutions sell while narrative is bullish → likely distribution, not a breakout.
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
```

- [ ] **Step 2: Verify import + syntax**

```
python -c "from capitalradar.agents.managers.portfolio_manager import create_portfolio_manager; print('OK')"
```

- [ ] **Step 3: Verify the pm_tools import is resolved**

```
python -c "from capitalradar.agents.utils.pm_tools import predict_stock_price; print(predict_stock_price.name, predict_stock_price.args)"
```
Expected: `predict_stock_price {'ticker': {'title': 'Ticker', 'type': 'string'}}`

- [ ] **Step 4: Commit**

```
git add capitalradar/agents/managers/portfolio_manager.py
git commit -m "feat(pm): convert PM to two-phase agent (tool loop → structured output)"
```

---

### Task 4: Update GraphSetup to register tools_portfolio_manager node

**Files:**
- Modify: `capitalradar/graph/setup.py` (lines 62-68 + 138-169)

- [ ] **Step 1: Import ToolNode and pm_tool**

At top of setup.py, add:
```python
from langgraph.prebuilt import ToolNode
from capitalradar.agents.utils.pm_tools import predict_stock_price
```

(Note: `ToolNode` is already imported at the top of setup.py — line 5. Only the `predict_stock_price` import is new.)

- [ ] **Step 2: Create tools_portfolio_manager node after portfolio_manager_node**

After line 68 (`portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)`), add:
```python
pm_tool_node = ToolNode([predict_stock_price])
```

- [ ] **Step 3: Register nodes in the graph**

After line 142 (`workflow.add_node("Portfolio Manager", portfolio_manager_node)`), add:
```python
workflow.add_node("tools_portfolio_manager", pm_tool_node)
```

- [ ] **Step 4: Replace the flat edge `workflow.add_edge("Portfolio Manager", END)`**

Replace line 169 (`workflow.add_edge("Portfolio Manager", END)`) with:
```python
workflow.add_conditional_edges(
    "Portfolio Manager",
    self.conditional_logic.should_continue_pm,
    {
        "tools_portfolio_manager": "tools_portfolio_manager",
        "END": END,
    },
)
workflow.add_edge("tools_portfolio_manager", "Portfolio Manager")
```

- [ ] **Step 5: Verify graph compiles**

```
python -c "from capitalradar.graph.trading_graph import CapitalRadarGraph; g = CapitalRadarGraph({}); print('Graph OK')"
```

- [ ] **Step 6: Commit**

```
git add capitalradar/graph/setup.py
git commit -m "feat(graph): register tools_portfolio_manager node + PM conditional edge"
```

---

### Task 5: Fix PredictionAgent silent failure in trading_graph.py

**Files:**
- Modify: `capitalradar/graph/trading_graph.py` (lines 398-405)

- [ ] **Step 1: Add traceback logging to the silent-raise block**

Replace:
```python
        except Exception:
            init_agent_state["prediction_report"] = ""
```
With:
```python
        except Exception as e:
            import traceback
            logger.warning(
                "PredictionAgent pre-run failed for %s/%s (PM will call predict_stock_price as fallback): %s\ntraceback:\n%s",
                ticker, trade_date, e, traceback.format_exc(),
            )
            init_agent_state["prediction_report"] = ""
```

- [ ] **Step 2: Verify logging**

```
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from capitalradar.graph.trading_graph import CapitalRadarGraph
print('Import OK')
"
```

- [ ] **Step 3: Commit**

```
git add capitalradar/graph/trading_graph.py
git commit -m "fix(prediction): log PredictionAgent failure with full traceback for diagnostics"
```

---

### Task 6: Verify end-to-end

- [ ] **Step 1: Run full pipeline test**

```
python -c "
from capitalradar.graph.trading_graph import CapitalRadarGraph
from capitalradar.default_config import DEFAULT_CONFIG
cfg = dict(DEFAULT_CONFIG)
cfg['deep_think_llm'] = 'deepseek-v4-flash'
cfg['llm_provider'] = 'deepseek'
g = CapitalRadarGraph(cfg)
result = g.run('600519.SH', '2026-06-26')
print('Run complete')
print('Decision:', result.get('final_trade_decision', 'N/A')[:300])
"
```

Expected: Pipeline runs to completion. PM either uses pre-computed prediction_report or calls predict_stock_price during Phase 1. Final decision includes `Time Horizon: 5d: ... | 20d: ...`.

- [ ] **Step 2: Verify time_horizon in structured output**

Check that `PortfolioDecision.time_horizon` description was updated:
```
python -c "from capitalradar.agents.schemas import PortfolioDecision; print(PortfolioDecision.model_fields['time_horizon'].description)"
```
Expected: Contains "5-day" and "20-day"

- [ ] **Step 3: Verify test suite**

```
python -m pytest tests/graph/ -v --timeout=60 --no-header -q 2>&1 | tail -20
```

- [ ] **Step 4: Final commit**

```
git add -A
git commit -m "feat(pm): tool-calling agent with predict_stock_price fallback and 5-20d horizon"
```
