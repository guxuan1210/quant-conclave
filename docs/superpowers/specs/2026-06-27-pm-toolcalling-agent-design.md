# Design: Portfolio Manager Tool-Calling Agent (5–20 day focus)

## Problem

Portfolio Manager currently produces decision output biased toward long-term structural trends (1–3 years). It has no ability to call tools during its decision. The ML PredictionReport injected at graph start often fails silently (`prediction_report` stays empty), and PM has no fallback mechanism to fetch prediction data when that happens. Users want PM to focus on **5-20 trading days** as the primary decision horizon.

## Scope

1. **[PM node refactor]** Convert the pure-structured-output PM node into a two-phase agent (tool loop → structured output).
2. **[New tool]** Add `predict_stock_price` as a callable PM tool so it can fetch ML prediction data on demand.
3. **[Graph edge]** Register `tools_portfolio_manager` node in the LangGraph and add conditional routing from PM to its tool node.
4. **[Prompt update]** Build on the previous prompt rewrite (5d/20d structure) to add an explicit "if prediction is empty, call the tool" fallback instruction.
5. **[Fix PredictionAgent]** Diagnose why `trading_graph.py`'s pre-run of PredictionAgent produces empty `prediction_report` and fix it (likely model file path or FeatureEngine issue).

## Architecture

### 5.1 PM Node (Two-Phase)

File: `capitalradar/agents/managers/portfolio_manager.py`

```
Phase 1 — Tool Loop (up to 5 iterations):
  LLM_with_tools.invoke(messages)
  └─ if tool_calls present:
       execute tool → append ToolMessage → loop
  └─ if no tool_calls:
       go to Phase 2

Phase 2 — Structured Output:
  structured_llm.invoke(all_messages + structured_prompt) → PortfolioDecision
  render → markdown → state["final_trade_decision"]
```

The `messages` array includes the `system_prompt`, risk debate history, analyst reports, and any ToolMessages from Phase 1.

### 5.2 Tool: predict_stock_price

The tool already exists in `web/ai_pick_agent.py` and `web/history_chat.py` (reused from `capitalradar.advisory_predictor`). Extract it into a reusable form:

```
predict_stock_price(ticker: str) → str
  agent = PredictionAgent()
  report = agent.predict(ticker, today_date, config)
  return report.to_markdown()
```

Bind this single tool to the PM's LLM tool loop.

### 5.3 Graph Changes

File: `capitalradar/graph/setup.py`

Add:
- `tools_portfolio_manager` node (ToolNode with predict_stock_price bound)
- Conditional edge: `"Portfolio Manager"` → `"tools_portfolio_manager"` or `END`
- Edge: `"tools_portfolio_manager"` → `"Portfolio Manager"`

File: `capitalradar/graph/conditional_logic.py`

Add method:
```python
def should_continue_pm(self, state: AgentState) -> str:
    """Route PM to its tool node or to END."""
    if self._exceeded_limit(state):
        return "END"  # too many tool calls
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools_portfolio_manager"
    return "END"
```

### 5.4 PM Prompt Additions

Add to the existing prompt (after the "PRIMARY DECISION DRIVER" section):
```
If the ML Prediction Report above is empty or shows errors, you MUST
call predict_stock_price(ticker) immediately to get fresh prediction data.
Do not proceed with your final decision without prediction data.
```

Also add a rule for when prediction is genuinely unavailable:
```
If prediction data is genuinely unavailable (tool errors out or returns
empty), set manipulation risk to MEDIUM (never HIGH without ML data).
```

### 5.5 PredictionAgent Fix

File: `capitalradar/graph/trading_graph.py` (lines 398-405)

The try/except currently swallows all errors. Log the full traceback to diagnose:
- FeatureEngine data fetch failure (tushare/akshare)
- Model file path (`~/.capitalradar/models/*.pkl`) missing
- DirectionPredictor/PricePredictor initialization errors

Likely fix: verify model directory exists, or catch early and provide a clear error message.

## Files To Modify

| File | Change |
|------|--------|
| `capitalradar/agents/managers/portfolio_manager.py` | Convert to two-phase agent (tool loop → structured output) |
| `capitalradar/graph/setup.py` | Add `tools_portfolio_manager` node + conditional edge routing |
| `capitalradar/graph/conditional_logic.py` | Add `should_continue_pm()` method |
| `capitalradar/graph/trading_graph.py` | Fix PredictionAgent silent failure (log + diagnose) |
| `capitalradar/agents/utils/agent_utils.py` | (optional) Move `predict_stock_price` to shared utility |

## File To Create

| File | Content |
|------|--------|
| `capitalradar/agents/utils/pm_tools.py` | Shared `predict_stock_price_tool()` function bound to config |

## Test Plan

1. Unit: `python -c "from capitalradar.agents.managers.portfolio_manager import create_portfolio_manager; print('OK')"`
2. Unit: re-import schemas and confirm `time_horizon` description is updated
3. Integration: Run one full pipeline `python main.py` (any ticker) and check:
   - Graph does not crash on PM node
   - PM calls predict_stock_price if prediction_report is empty
   - Final output includes explicit `Time Horizon: 5d: ... | 20d: ...`
4. Integration: Clear prediction model cache, re-run, confirm PM still produces valid decision via tool fallback

## Edge Cases

- **Prediction tool errors**: If `predict_stock_price` fails (no model file, no data), PM still has the empty `prediction_report` in state + a ToolMessage with the error. Prompt says: set manipulation risk to MEDIUM, note that prediction data is unavailable.
- **Tool loop timeout**: 5 iteration cap. If PM can't produce a decision after 5 tool calls, fall back to structured output with whatever context it has (same as current behavior).
- **No risk debate**: For `setup_partial_graph` where there's no debate/risk stage, the PM isn't part of the graph, so no changes needed.
