# PortfolioDecision Schema + Rating Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `time_horizon` with explicit `short_term_outlook`, `medium_term_outlook`, `confidence` fields on `PortfolioDecision`, update `render_pm_decision`, add rating consistency rules to PM prompt, and prevent "Buy" / "Sell" false-positives in `parse_rating`.

**Architecture:** Schema change in `schemas.py` → render change → prompt change → parse_rating fix. All downstream callers use `parse_rating` on rendered markdown so they adapt automatically.

**Tech Stack:** Pydantic, LangChain structured output

---

### Task 1: Update `PortfolioDecision` schema — replace `time_horizon` with `short_term_outlook`, `medium_term_outlook`, `confidence`

**Files:**
- Modify: `capitalradar/agents/schemas.py` (lines 199-213)

**Context:** Remove the `time_horizon` field and add three new required fields. The `render_pm_decision` function also needs updating in the same task since it references `decision.time_horizon`.

- [ ] **Step 1: Replace the schema fields**

In `capitalradar/agents/schemas.py`, replace lines 199-213:

**Old:**
```python
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description=(
            "The explicit trading horizon this decision targets. "
            "Must include a clear 5-day and 20-day view, e.g. "
            "'5d: neutral/slight upward bounce to 12.80–13.00 | "
            "20d: bearish bias, test 11.80 support'. "
            "Never give a flat long-term horizon without short/mid breakdown. "
            "Example: '5d: bullish (up 65%), target 12.80-13.00 | 20d: cautious, 50% probability of recovery'"
        ),
    )
```

**New:**
```python
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    short_term_outlook: str = Field(
        description=(
            "5-day outlook: direction, probability, expected price range, key levels. "
            "Example: 'bullish (57%), target 18.50-19.00, RSI=32 oversold bounce'"
        ),
    )
    medium_term_outlook: str = Field(
        description=(
            "20-day outlook: direction, confidence, price range, key risk factors. "
            "Example: 'bearish (88% confidence), test 14.74-16.49, under 200SMA=16.86'"
        ),
    )
    confidence: str = Field(
        description=(
            "Overall confidence in the decision. One of 'high', 'medium', 'low'. "
            "Based on ML prediction confidence + data quality + cross-validation."
        ),
    )
```

- [ ] **Step 2: Update `render_pm_decision`**

Replace the current function body (lines 216-235):

**Old:**
```python
def render_pm_decision(decision: PortfolioDecision) -> str:
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    return "\n".join(parts)
```

**New:**
```python
def render_pm_decision(decision: PortfolioDecision) -> str:
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
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    return "\n".join(parts)
```

- [ ] **Step 3: Verify schema and render**

```
python -c "
from capitalradar.agents.schemas import PortfolioDecision, render_pm_decision
d = PortfolioDecision.model_fields
fields = list(d.keys())
print('Fields:', fields)
assert 'short_term_outlook' in fields, 'missing short_term_outlook'
assert 'medium_term_outlook' in fields, 'missing medium_term_outlook'
assert 'confidence' in fields, 'missing confidence'
assert 'time_horizon' not in fields, 'time_horizon should be removed'

# Test render
dec = PortfolioDecision(
    rating='Hold',
    executive_summary='Test summary',
    investment_thesis='Test thesis',
    short_term_outlook='bullish (57%), target 18.50',
    medium_term_outlook='bearish (88%), test 14.74',
    confidence='medium',
)
md = render_pm_decision(dec)
print(md)
assert '**5-Day Outlook**' in md
assert '**20-Day Outlook**' in md
assert '**Confidence**' in md
assert '**Time Horizon**' not in md
print('ALL OK')
"
```
Expected: Fields verified, markdown contains **5-Day Outlook**, **20-Day Outlook**, **Confidence**, NOT **Time Horizon**.

- [ ] **Step 4: Commit**

```
git add capitalradar/agents/schemas.py
git commit -m "feat(pm): replace time_horizon with short_term_outlook, medium_term_outlook, confidence"
```

---

### Task 2: Add consistency rules to PM prompt

**Files:**
- Modify: `capitalradar/agents/managers/portfolio_manager.py` (Phase 2 prompt)

- [ ] **Step 1: Read the current Phase 2 prompt**

The Phase 2 prompt starts with `prompt = f"""As the Portfolio Manager...` and contains the "Rating Scale" section. Find it and add the consistency rules after the rating scale.

- [ ] **Step 2: Add consistency rules to prompt**

After the **Rating Scale** lines (around line 128), add:

```
**IMPORTANT — CONSISTENCY RULE:**
The `rating` field you select MUST be consistent with your own analysis:
- If 5d bearish AND 20d bearish -> rating is Sell or Underweight
- If 5d bullish AND 20d bearish -> rating is Underweight or Hold (NEVER Buy)
- If 5d bearish AND 20d bullish -> rating is Hold or Overweight (NEVER Sell)
- If 5d bullish AND 20d bullish -> rating is Buy or Overweight
The `confidence` field MUST reflect the ML prediction confidence, not your personal conviction.
```

Find the exact location. The current prompt has:

```
**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry
```

Add the consistency rules right after this block (before `**Your Decision Structure must include BOTH time windows:**`).

- [ ] **Step 3: Verify prompt syntax**

```
python -c "from capitalradar.agents.managers.portfolio_manager import create_portfolio_manager; print('OK')"
```

- [ ] **Step 4: Commit**

```
git add capitalradar/agents/managers/portfolio_manager.py
git commit -m "feat(pm): add rating consistency rules to Phase 2 prompt"
```

---

### Task 3: Fix `parse_rating` to skip "bearish" false match

**Files:**
- Modify: `capitalradar/agents/utils/rating.py`

**Context:** The `parse_rating` Pass 3 scans for any rating word in lowercased text. While "bearish" is not directly matched because it's not in `RATINGS_5_TIER`, the deeper issue is that the rendered markdown can trigger false matches in Pass 1 (looking for "Rating:" lines). Fix by adding a skip-words set.

- [ ] **Step 1: Add skip-words set**

After line 18 (`RATINGS_5_TIER` definition), add:
```python
# Words that should never be matched as ratings even if they appear near
# "Rating:" labels in rendered markdown.
_SKIP_WORDS: Tuple[str, ...] = ("bearish", "bullish", "rating", "ratings")
```

- [ ] **Step 2: Apply skip in Pass 1**

In Pass 1 (line 48), modify the check. Replace:
```python
            word = m.group(1).strip("*:.,").lower()
            if word in _RATING_SET:
                return word.capitalize()
```
With:
```python
            word = m.group(1).strip("*:.,").lower()
            if word in _RATING_SET and word not in _SKIP_WORDS:
                return word.capitalize()
```

- [ ] **Step 3: Apply skip in Pass 3**

In Pass 3 (line 66), replace:
```python
            if clean in _RATING_SET:
                return clean.capitalize()
```
With:
```python
            if clean in _RATING_SET and clean not in _SKIP_WORDS:
                return clean.capitalize()
```

- [ ] **Step 4: Verify fix**

```
python -c "
from capitalradar.agents.utils.rating import parse_rating

# Should return default (Hold) — not Buy
result = parse_rating('bearish (88%): test')
print(f'bearish test: {result}')
assert result != 'Buy', f'Got Buy for bearish text! Got: {result}'

result = parse_rating('bullish (57%): uptrend')
print(f'bullish test: {result}')
assert result != 'Sell', f'Got Sell for bullish text! Got: {result}'

# Should still work for real cases
result = parse_rating('**Rating**: Buy')
print(f'Rating Buy: {result}')
assert result == 'Buy'

result = parse_rating('**Rating**: Underweight')
print(f'Rating Underweight: {result}')
assert result == 'Underweight'

print('ALL OK')
"
```
Expected: bearish text → "Hold", bullish text → "Hold", "**Rating**: Buy" → "Buy", "**Rating**: Underweight" → "Underweight".

- [ ] **Step 5: Commit**

```
git add capitalradar/agents/utils/rating.py
git commit -m "fix(rating): skip bearish/bullish words in parse_rating to avoid false matches"
```

---

### Task 4: Verify end-to-end

- [ ] **Step 1: Full verification**

```
python -c "
# 1. Schema fields
from capitalradar.agents.schemas import PortfolioDecision, render_pm_decision
fields = list(PortfolioDecision.model_fields.keys())
assert 'short_term_outlook' in fields
assert 'medium_term_outlook' in fields
assert 'confidence' in fields
assert 'time_horizon' not in fields

# 2. Render
dec = PortfolioDecision(
    rating='Hold', executive_summary='E', investment_thesis='T',
    short_term_outlook='5d test', medium_term_outlook='20d test', confidence='low',
)
md = render_pm_decision(dec)
assert '**5-Day Outlook**' in md
assert '**20-Day Outlook**' in md
assert '**Confidence**' in md
assert '**Time Horizon**' not in md

# 3. parse_rating
from capitalradar.agents.utils.rating import parse_rating
assert parse_rating('bearish (88%): test') != 'Buy'
assert parse_rating('**Rating**: Underweight') == 'Underweight'

# 4. pm import ok
from capitalradar.agents.managers.portfolio_manager import create_portfolio_manager
print('ALL VERIFICATIONS PASSED')
"
```

- [ ] **Step 2: Run pipeline**

```
python -c "
from capitalradar.graph.trading_graph import CapitalRadarGraph
from capitalradar.default_config import DEFAULT_CONFIG
cfg = dict(DEFAULT_CONFIG)
cfg['llm_provider'] = 'deepseek'
cfg['deep_think_llm'] = 'deepseek-v4-flash'
g = CapitalRadarGraph(config=cfg)
print('Graph compiles OK')
"
```

- [ ] **Step 3: Final commit**

```
git add -A
git commit -m "feat(pm): schema enhancement with short_term_outlook/medium_term_outlook/confidence fields"
```
