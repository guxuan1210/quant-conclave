# History Agent + Skill Generator + Enhanced Experience Extraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix History Agent's summary table to use new PM schema fields (5-Day Outlook / 20-Day Outlook / Confidence), enhance the automated experience extractor with structured-output-aware pattern detection, and create a Skill Generator that turns validated experiences into versioned SKILL.md files.

**Architecture:** Three independent tasks ordered by dependency. Task 1 is a regex fix in the prompt builder. Task 2 adds new pattern detectors to the existing extractor. Task 3 creates a new file (skill_generator.py) and hooks it into the loop_coordinator.

**Tech Stack:** LangChain, SQLite, re (regex), pathlib, datetime

---

### Task 1: Update History Agent summary table — replace Time Horizon with 5-Day Outlook / 20-Day Outlook / Confidence

**Files:**
- Modify: `capitalradar/advisory/history_agent.py` (lines 73-94)

**Context:** The `final_trade_decision` markdown no longer contains `**Time Horizon**`. It now contains `**5-Day Outlook**`, `**20-Day Outlook**`, `**Confidence**`. The summary table needs updating.

- [ ] **Step 1: Read the current file**

Read `capitalradar/advisory/history_agent.py` to find the exact table headers and regex parsers (around lines 73-94).

- [ ] **Step 2: Update table header**

Change:
```python
    prompt += "| # | Date | Ticker | Rating | Signal | Price Target | Time Horizon |\n"
    prompt += "|---|---|---|---|---|---|---|\n"
```
To:
```python
    prompt += "| # | Date | Ticker | Rating | Signal | 5-Day Outlook | 20-Day Outlook | Confidence |\n"
    prompt += "|---|---|---|---|---|---|---|---|\n"
```

- [ ] **Step 3: Update column data extraction**

Change the extraction block (lines 78-94). Replace:
```python
        rating = "N/A"
        price_target = "N/A"
        time_horizon = "N/A"
        signal = "N/A"
        import re
        r_match = re.search(r'\*\*Rating\*\*[:\s]*(\w+)', final)
        if r_match:
            rating = r_match.group(1)
        pt_match = re.search(r'\*\*Price Target\*\*[:\s]*([\d.]+)', final)
        if pt_match:
            price_target = pt_match.group(1)
        th_match = re.search(r'\*\*Time Horizon\*\*[:\s]*(.+?)(?:\n|$)', final)
        if th_match:
            time_horizon = th_match.group(1).strip()[:50]
        signal = state.get("signal_processed", rating)
        prompt += f"| {i} | {date} | {ticker} | {rating} | {signal} | {price_target} | {time_horizon} |\n"
```
With:
```python
        rating = "N/A"
        outlook_5d = "N/A"
        outlook_20d = "N/A"
        confidence = "N/A"
        signal = "N/A"
        import re
        r_match = re.search(r'\*\*Rating\*\*[:\s]*(\w+)', final)
        if r_match:
            rating = r_match.group(1)
        o5_match = re.search(r'\*\*5-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)', final)
        if o5_match:
            outlook_5d = o5_match.group(1).strip()[:60]
        o20_match = re.search(r'\*\*20-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)', final)
        if o20_match:
            outlook_20d = o20_match.group(1).strip()[:60]
        conf_match = re.search(r'\*\*Confidence\*\*[:\s]*(\w+)', final)
        if conf_match:
            confidence = conf_match.group(1).strip()[:20]
        signal = state.get("signal_processed", rating)
        prompt += f"| {i} | {date} | {ticker} | {rating} | {signal} | {outlook_5d} | {outlook_20d} | {confidence} |\n"
```

- [ ] **Step 4: Verify regex parsing works**

```
python -c "
import re
from capitalradar.agents.schemas import PortfolioDecision, render_pm_decision

# Create a sample PM decision with new schema fields
dec = PortfolioDecision(
    rating='Underweight',
    executive_summary='Test',
    investment_thesis='Test',
    short_term_outlook='bullish (57%), target 18.50-19.00',
    medium_term_outlook='bearish (88%), test 14.74-16.49',
    confidence='medium',
)
md = render_pm_decision(dec)
print('Rendered markdown:')
print(md)
print()
# Verify regex patterns match
test = str(md)
r = re.search(r'\*\*Rating\*\*[:\s]*(\w+)', test)
print('Rating match:', r.group(1) if r else 'FAIL')
o5 = re.search(r'\*\*5-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)', test)
print('5-Day match:', o5.group(1).strip()[:60] if o5 else 'FAIL')
o20 = re.search(r'\*\*20-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)', test)
print('20-Day match:', o20.group(1).strip()[:60] if o20 else 'FAIL')
c = re.search(r'\*\*Confidence\*\*[:\s]*(\w+)', test)
print('Confidence match:', c.group(1) if c else 'FAIL')
"
```
Expected: All 4 regexes match correctly.

- [ ] **Step 5: Commit**

```
git add capitalradar/advisory/history_agent.py
git commit -m "fix(history_agent): update summary table to use 5-Day/20-Day Outlook + Confidence fields"
```

---

### Task 2: Enhance experience extractor with structured-output-aware patterns

**Files:**
- Modify: `capitalradar/graph/experience_extractor.py`

**Context:** The current extractor only detects 3 basic patterns (sector bias, ticker streak, bearish bias). Add 4 new patterns that leverage the PM's structured output (5d/20d outlook + confidence) from the memory log's `final_trade_decision` markdown.

- [ ] **Step 1: Read the current file**

Read `capitalradar/graph/experience_extractor.py` to understand existing patterns.

- [ ] **Step 2: Add helper regexes to parse new fields**

After line 8 (`logger = logging.getLogger(__name__)`), add:
```python
# Precompiled regexes for PM structured output fields
_RATING_RE = re.compile(r'\*\*Rating\*\*[:\s]*(\w+)')
_OUTLOOK_5D_RE = re.compile(r'\*\*5-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)')
_OUTLOOK_20D_RE = re.compile(r'\*\*20-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)')
_CONFIDENCE_RE = re.compile(r'\*\*Confidence\*\*[:\s]*(\w+)')
```

- [ ] **Step 3: Add helper function to parse decision text**

At module level (after the regexes), add:
```python
def _parse_decision_fields(text: str) -> dict:
    """Extract structured fields from PM decision markdown."""
    result = {}
    m = _RATING_RE.search(text)
    if m:
        result["rating"] = m.group(1)
    m = _OUTLOOK_5D_RE.search(text)
    if m:
        result["outlook_5d"] = m.group(1).strip()[:80]
    m = _OUTLOOK_20D_RE.search(text)
    if m:
        result["outlook_20d"] = m.group(1).strip()[:80]
    m = _CONFIDENCE_RE.search(text)
    if m:
        result["confidence"] = m.group(1).strip().lower()
    # Detect direction from outlook text
    result["5d_bullish"] = any(k in (result.get("outlook_5d","")).lower() for k in ["bullish", "up", "涨", "看涨", "买入"])
    result["20d_bearish"] = any(k in (result.get("outlook_20d","")).lower() for k in ["bearish", "down", "跌", "看跌", "卖出"])
    result["divergence"] = result.get("5d_bullish", False) and result.get("20d_bearish", False)
    return result
```

- [ ] **Step 4: Add 4 new pattern detectors to `extract_experiences()`**

After the existing Pattern 3 block (line 82), add:

```python
    # Pattern 4: time-horizon divergence (5d bullish + 20d bearish)
    divergence = [e for e in resolved_entries
                  if _parse_decision_fields(e.get("decision", "")).get("divergence")]
    if len(divergence) >= 3:
        correct = [e for e in divergence if e.get("raw", 0) < 0]
        if correct:
            eid = create_experience(
                content=f"Time-horizon divergence detected {len(divergence)} times: 5d bullish + 20d bearish. "
                        f"In {len(correct)} cases the 20d bearish view was correct. "
                        f"Rule: when 5d and 20d ML predictions diverge, weight the 20d more heavily.",
                category="time_horizon",
                lesson_abstract="time_horizon_divergence",
            )
            created.append({"id": eid, "divergence_count": len(divergence), "correct_count": len(correct)})

    # Pattern 5: confidence calibration — does confidence level match actual outcome?
    for level in ["high", "medium", "low"]:
        level_entries = [
            e for e in resolved_entries
            if _parse_decision_fields(e.get("decision", "")).get("confidence") == level
        ]
        if len(level_entries) >= 5:
            correct = sum(1 for e in level_entries if e.get("raw", 0) < 0)
            correct_rate = correct / len(level_entries) * 100
            if level == "high" and correct_rate < 65:
                eid = create_experience(
                    content=f"Overconfidence detected: 'high' confidence decisions only {correct_rate:.0f}% correct "
                            f"({correct}/{len(level_entries)}). Consider calibrating confidence down.",
                    category="confidence_calibration",
                    lesson_abstract=f"overconfidence_{level}",
                )
                created.append({"id": eid, "level": level, "correct_rate": correct_rate})
            elif level == "low" and correct_rate > 50:
                eid = create_experience(
                    content=f"Underconfidence detected: 'low' confidence decisions achieved {correct_rate:.0f}% correct "
                            f"({correct}/{len(level_entries)}). Consider raising confidence when pattern is clear.",
                    category="confidence_calibration",
                    lesson_abstract=f"underconfidence_{level}",
                )
                created.append({"id": eid, "level": level, "correct_rate": correct_rate})

    # Pattern 6: 5d rebound success rate
    bullish_5d = [e for e in resolved_entries
                  if _parse_decision_fields(e.get("decision", "")).get("5d_bullish")]
    if len(bullish_5d) >= 5:
        profitable = sum(1 for e in bullish_5d if e.get("raw", 0) > 0)
        profit_rate = profitable / len(bullish_5d) * 100
        eid = create_experience(
            content=f"5d bullish calls: {profit_rate:.0f}% profitable ({profitable}/{len(bullish_5d)}). "
                    f"Use this to calibrate the actual win rate of short-term bounce predictions.",
            category="short_term_accuracy",
            lesson_abstract=f"5d_rebound_rate_{profit_rate:.0f}",
        )
        created.append({"id": eid, "profit_rate": profit_rate, "total": len(bullish_5d)})

    # Pattern 7: rating-specific accuracy
    rating_entries = defaultdict(list)
    for e in resolved_entries:
        parsed = _parse_decision_fields(e.get("decision", ""))
        rating = parsed.get("rating", "")
        if rating:
            rating_entries[rating].append(e)
    for rating, entries in rating_entries.items():
        if len(entries) >= 4:
            correct = sum(1 for e in entries if e.get("raw", 0) < 0)
            accuracy = correct / len(entries) * 100
            if accuracy < 40:
                eid = create_experience(
                    content=f"Low {rating} accuracy: only {accuracy:.0f}% correct ({correct}/{len(entries)}). "
                            f"Consider reviewing the decision criteria for this rating level.",
                    category="rating_accuracy",
                    lesson_abstract=f"low_accuracy_{rating}",
                )
                created.append({"id": eid, "rating": rating, "accuracy": accuracy})
```

- [ ] **Step 5: Verify**

```
python -c "
from capitalradar.graph.experience_extractor import _parse_decision_fields
import re

# Test with sample PM output containing new fields
sample = '''**Rating**: Underweight

**Executive Summary**: Test

**Investment Thesis**: Test

**5-Day Outlook**: bullish (57%), target 18.50-19.00

**20-Day Outlook**: bearish (88%), test 14.74-16.49

**Confidence**: medium'''

result = _parse_decision_fields(sample)
print('Parsed:', result)
assert result['rating'] == 'Underweight'
assert result['5d_bullish'] == True
assert result['20d_bearish'] == True
assert result['divergence'] == True
assert result['confidence'] == 'medium'
print('ALL OK')
"
```

- [ ] **Step 6: Commit**

```
git add capitalradar/graph/experience_extractor.py
git commit -m "feat(extractor): add structured-output-aware patterns (divergence, confidence, rebound, rating)"
```

---

### Task 3: Create Skill Generator — experiences → versioned SKILL.md

**Files:**
- Create: `capitalradar/graph/skill_generator.py`
- Modify: `capitalradar/graph/loop_coordinator.py` (add Phase 4 hook)
- Modify: `.gitignore` (if needed)

**Context:** After the loop_coordinator finishes Phases 1-3 (resolve → meta-evaluate → extract experiences), add a Phase 4 that turns new experiences into a versioned SKILL.md file in `.claude/skills/analyst-core/`.

- [ ] **Step 1: Create `skill_generator.py`**

```python
"""Skill Generator: convert resolved experiences into versioned SKILL.md files.

Called by loop_coordinator.run_full_loop() after Phase 3 (extract experiences).
Reads active experiences from the advisory_experience table, formats them as
rules/sections, and writes v{N+1} to .claude/skills/analyst-core/.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_DIR = Path(".claude/skills/analyst-core")
SKILL_INDEX = SKILL_DIR / "index.json"


def _parse_version(fname: str) -> int:
    """Extract version number from filename like 'v3-2026-06-27-name.md'."""
    m = re.match(r"v(\d+)", fname)
    return int(m.group(1)) if m else 0


def _next_version() -> int:
    """Determine the next version number."""
    if not SKILL_DIR.exists():
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        return 1
    versions = [0]
    for f in SKILL_DIR.iterdir():
        if f.suffix == ".md":
            v = _parse_version(f.name)
            if v:
                versions.append(v)
    return max(versions) + 1


def _load_skill_index() -> dict:
    """Load existing skill index, or return default."""
    if SKILL_INDEX.exists():
        try:
            return json.loads(SKILL_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"analyst": {"versions": []}}


def _save_skill_index(index: dict) -> None:
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    SKILL_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _experiences_to_rules(experiences: list[dict]) -> list[str]:
    """Convert experience entries into SKILL rule text."""
    rules = []
    for exp in experiences:
        cat = exp.get("category", "other")
        content = exp.get("content", "")
        abstract = exp.get("lesson_abstract", "")
        if not content:
            continue
        # Format as a skill section
        lines = [
            f"### Rule: {abstract or cat}",
            "",
            content,
            "",
        ]
        rules.append("\n".join(lines))
    return rules


def _build_skill_md(version: int, rules: list[str], stats: dict | None = None) -> str:
    """Build the full SKILL.md content for this version."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Frontmatter
    lines = [
        "---",
        f"name: capitalradar-analyst-core",
        f"description: Auto-generated skill v{version} from resolved backtest experiences",
        f"version: {version}",
        f"created: {today}",
    ]
    if stats:
        lines.append(f"source_loop: calibration run | bearish_acc={stats.get('accuracy', '?')}%")
    lines.extend([
        "---",
        "# CapitalRadar Analyst Core Skill",
        "",
    ])
    
    if stats:
        lines.extend([
            f"*Auto-generated v{version} on {today}*",
            "",
            "## Current Stats",
            f"- Total resolved entries: {stats.get('total', '?')}",
            f"- Bearish accuracy: {stats.get('accuracy', '?')}%",
            f"- Bearish rate: {stats.get('bearish_rate', '?')}%",
            "",
        ])
    
    if rules:
        lines.append("## Experience-Derived Rules")
        lines.append("")
        lines.extend(rules)
        lines.append("")
    
    # Always include core principles
    lines.extend([
        "## Core Principles",
        "",
        "1. **Smart money first** — Capital flow (主力资金) is the primary signal.",
        "2. **Trust money, not narrative** — When capital flow contradicts sentiment, trust the flow.",
        "3. **Symmetric manipulation** — Bull traps AND bear traps are equally dangerous.",
        "4. **5-20 day horizon** — Long-term structural trends are context only.",
        "5. **Chinese retail first** — Highlight actionable price levels.",
        "",
    ])
    
    return "\n".join(lines)


def generate_skill_version(config: dict) -> dict:
    """Generate or update the Analyst SKILL.md based on resolved experiences.
    
    Returns dict with version, file_path, rule_count.
    """
    # Load active experiences
    from capitalradar.advisory.experience_store import list_experiences
    all_exp = list_experiences()
    active = [e for e in all_exp if e.get("status") == "active"]
    
    # Load latest calibration stats if available
    stats = None
    try:
        results_dir = config.get("results_dir", "")
        if results_dir:
            import sqlite3
            db_path = str(Path(results_dir) / "results.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                row = conn.execute(
                    "SELECT stats FROM calibration_runs ORDER BY run_date DESC LIMIT 1"
                ).fetchone()
                conn.close()
                if row and row[0]:
                    stats = json.loads(row[0])
    except Exception as e:
        logger.warning("Could not load calibration stats: %s", e)
    
    # Determine version and build files
    version = _next_version()
    rules = _experiences_to_rules(active)
    
    md_content = _build_skill_md(version, rules, stats)
    
    # Write files
    today = datetime.now().strftime("%Y-%m-%d")
    fname = f"v{version}-{today}-auto-generated.md"
    file_path = SKILL_DIR / fname
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    file_path.write_text(md_content, encoding="utf-8")
    
    # Update index
    index = _load_skill_index()
    index["analyst"]["versions"].append({
        "version": version,
        "file": fname,
        "created": today,
        "rule_count": len(rules),
        "active_exp_count": len(active),
    })
    index["analyst"]["active_version"] = version
    _save_skill_index(index)
    
    logger.info("Generated skill v%d (%s) with %d rules", version, fname, len(rules))
    return {
        "version": version,
        "file_path": str(file_path),
        "rule_count": len(rules),
        "active_exp_count": len(active),
    }


def get_active_skill_path() -> str | None:
    """Return the file path of the currently active skill version."""
    index = _load_skill_index()
    av = index.get("analyst", {}).get("active_version")
    versions = index.get("analyst", {}).get("versions", [])
    for v in versions:
        if v.get("version") == av:
            return str(SKILL_DIR / v["file"])
    return None
```

- [ ] **Step 2: Hook skill_generator into loop_coordinator**

In `capitalradar/graph/loop_coordinator.py`, modify `run_full_loop()`:

After line 69 (`experience_result = extract_experiences(config)`), add:
```python
    # Phase 4: Generate skill version if experiences changed
    skill_result = None
    if experience_result:
        from capitalradar.graph.skill_generator import generate_skill_version
        try:
            skill_result = generate_skill_version(config)
            logger.info("Phase 4 done: skill v%d generated", skill_result.get("version", 0))
        except Exception as e:
            logger.error("Skill generation failed: %s", e)
```

In the return dict (line 74-82), add:
```python
        "skill_version": skill_result.get("version", 0) if skill_result else 0,
```

Also add the import at the top of the file (after line 1):
```python
"""Skill Generator: convert resolved experiences into versioned SKILL.md files."""

- [ ] **Step 3: Verify generator works**

```
python -c "
from capitalradar.graph.skill_generator import generate_skill_version, get_active_skill_path
# Test with a minimal config (generator handles errors gracefully)
result = generate_skill_version({})
print('Generate result:', result)
path = get_active_skill_path()
print('Active skill path:', path)
"
```

- [ ] **Step 4: Verify the generated file exists**

```
ls -la .claude/skills/analyst-core/
cat .claude/skills/analyst-core/index.json 2>/dev/null || echo "No index yet"
```

- [ ] **Step 5: Commit**

```
git add capitalradar/graph/skill_generator.py capitalradar/graph/loop_coordinator.py .claude/skills/analyst-core/
git commit -m "feat(mentor): add skill_generator — converts experiences into versioned SKILL.md files"
```

---

### Task 4: Verify end-to-end

- [ ] **Step 1: Verify all imports**

```
python -c "
from capitalradar.advisory.history_agent import build_history_agent_prompt
from capitalradar.graph.experience_extractor import extract_experiences, _parse_decision_fields
from capitalradar.graph.skill_generator import generate_skill_version
print('All imports OK')
"
```

- [ ] **Step 2: Verify experience_extractor regex with actual PM output**

```
python -c "
from capitalradar.graph.experience_extractor import _parse_decision_fields
sample = '''**Rating**: Underweight\n\n**5-Day Outlook**: bullish (57%), target 18.50\n\n**20-Day Outlook**: bearish (88%), test 14.74\n\n**Confidence**: medium'''
r = _parse_decision_fields(sample)
assert r['rating'] == 'Underweight'
assert r['divergence'] == True
assert r['confidence'] == 'medium'
print('Field parsing OK')
"
```

- [ ] **Step 3: Verify skill_generator produces valid SKILL.md**

```
python -c "
from capitalradar.graph.skill_generator import generate_skill_version
import json
result = generate_skill_version({})
print(json.dumps(result, indent=2))
assert 'version' in result
assert result['rule_count'] >= 0
"
```

- [ ] **Step 4: Final commit**

```
git add -A
git commit -m "feat: history-agent table fix + enhanced extractor + skill generator v1"
```
