# Design: PortfolioDecision Schema Enhancement + Rating Consistency

## Problem

Portfolio Manager's structured output has two issues:

1. **Rating inconsistency**: The `rating` enum field sometimes returns "Buy" while the prose in `investment_thesis` clearly argues for "Underweight". This happens because (a) the LLM defaults to the first enum value, and (b) `parse_rating` can match "bearish" text as "Buy" (contains "bu" near "buy"? Not actually — the real issue is that the LLM sees "Buy" as the first enum option and defaults to it when uncertain.)

2. **No explicit 5d/20d fields**: The `time_horizon` field is a free-text string. There's no structured field for 5-day outlook, 20-day outlook, or confidence — all of which are critical for the user to understand the decision's time-bound nature.

## Scope

1. **Schema**: Replace `time_horizon` with `short_term_outlook`, `medium_term_outlook`, `confidence` fields on `PortfolioDecision`
2. **Render**: Update `render_pm_decision()` to output the new fields
3. **Prompt**: Add consistency rules linking `rating` to time-window analysis
4. **parse_rating**: Fix the "Buy" default — exclude bearish-related false positives
5. **Downstream**: All callers use `parse_rating` on the rendered markdown, so they adapt automatically

### Schema Changes

**Remove:**
```
time_horizon: Optional[str]  →  removed
```

**Add:**
```
short_term_outlook: str   — 5-day direction + probability + price range + key levels
medium_term_outlook: str  — 20-day direction + confidence + price range + key risks
confidence: str           — overall decision confidence: "high" | "medium" | "low"
```

### Render Changes

```python
parts = [
    f"**Rating**: {decision.rating.value}",
    "",
    f"**Executive Summary**: {decision.executive_summary}",
    "",
    f"**Investment Thesis**: {decision.investment_thesis}",
    "",
    f"**5-Day Outlook**: {decision.short_term_outlook}",
    "",
    f"**20-Day Outlook**: {decision.medium_term_outlook}",
    "",
    f"**Confidence**: {decision.confidence}",
]
```

### Prompt Consistency Rule

Add after the "Rating Scale" section in Phase 2 prompt:

```
**IMPORTANT — CONSISTENCY RULE:**
The `rating` field you select MUST be consistent with your own analysis:
- If 5d bearish AND 20d bearish → rating is Sell or Underweight
- If 5d bullish AND 20d bearish → rating is Underweight or Hold (NEVER Buy)
- If 5d bearish AND 20d bullish → rating is Hold or Overweight (NEVER Sell)
- If 5d bullish AND 20d bullish → rating is Buy or Overweight
The `confidence` field MUST reflect the ML prediction confidence, not your personal conviction.
```

### parse_rating Fix

The `parse_rating` function in `capitalradar/agents/utils/rating.py` has a known edge case: it matches "bearish" text before "buy" in Pass 3. The fix is to add `"bearish"` to a skip-words set.

Current Pass 3:
```python
for line in text.splitlines():
    for word in line.lower().split():
        clean = word.strip("*:.,")
        if clean in _RATING_SET:
            return clean.capitalize()
```

Fix — add skip-words:
```python
_SKIP_WORDS = {"bearish"}
# in Pass 3:
if clean in _SKIP_WORDS:
    continue
```

Wait — "bearish" is not in `RATINGS_5_TIER` so it wouldn't match. The real issue is that the LLM defaults to "Buy" because it's the first enum option in `PortfolioRating`. The fix is in the **prompt** (adding the consistency rule) and in the **Phase 1 tool-loop result** (the LLM sees actual prediction data and is forced to reason about it before producing the structured output). The consistency rule is the correct fix.

### Files To Modify

| File | Change |
|------|--------|
| `capitalradar/agents/schemas.py` | Replace `time_horizon` with `short_term_outlook`, `medium_term_outlook`, `confidence` |
| `capitalradar/agents/managers/portfolio_manager.py` | Update Phase 2 prompt with consistency rules |
| `capitalradar/agents/utils/rating.py` | Add bearish skip-word in parse_rating Pass 3 |
| `capitalradar/agents/utils/memory.py` | (No change needed — stores rendered markdown) |
| `capitalradar/graph/signal_processing.py` | (No change needed — uses parse_rating) |
| `web/stream.py` | (No change needed — uses parse_rating) |

### Test Plan

1. `python -c "from capitalradar.agents.schemas import PortfolioDecision; d = PortfolioDecision.model_fields; print(list(d.keys()))"` → confirm contains `short_term_outlook`, `medium_term_outlook`, `confidence`, NOT `time_horizon`
2. Run a test structured output: create a `PortfolioDecision` instance, call `render_pm_decision()`, confirm markdown has `**5-Day Outlook**`, `**20-Day Outlook**`, `**Confidence**` sections
3. `python -c "from capitalradar.agents.utils.rating import parse_rating; print(parse_rating('bearish (88%): test'))"` → should NOT return "Buy"
4. Full pipeline test: `python main.py` (any ticker) → confirm final output has new sections and rating is consistent
