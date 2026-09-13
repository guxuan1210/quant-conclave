# Skill Lifecycle Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin skill version to each analysis record, track version through memory_log, and provide active-version query for the pipeline.

**Architecture:** Add `get_active_skill_version()` to skill_generator.py, add `skill_version` field to AgentState, modify memory.py `store_decision()` to accept and store version, wire it in trading_graph.py at init and propagate time.

**Tech Stack:** Python, Pydantic, pathlib

---

### Task 1: Add `get_active_skill_version()` to skill_generator.py

**Files:**
- Modify: `capitalradar/graph/skill_generator.py`

- [ ] **Step 1: Read current file**

Read `capitalradar/graph/skill_generator.py`. Find the `get_active_skill_path` function at the end.

- [ ] **Step 2: Add new function**

After `get_active_skill_path()` at the bottom, add:

```python
def get_active_skill_version() -> int:
    """Return the version number of the currently active skill."""
    index = _load_skill_index()
    return index.get("analyst", {}).get("active_version", 0)
```

- [ ] **Step 3: Verify**

```
python -c "
from capitalradar.graph.skill_generator import get_active_skill_version
v = get_active_skill_version()
print(f'Active skill version: {v}')
assert isinstance(v, int)
"
```

Expected: prints a non-negative integer (0 if no versions exist yet, >0 if any have been generated).

- [ ] **Step 4: Commit**

```
git add capitalradar/graph/skill_generator.py
git commit -m "feat(skill): add get_active_skill_version() for pipeline use"
```

---

### Task 2: Add `skill_version` to AgentState

**Files:**
- Modify: `capitalradar/agents/utils/agent_states.py`

- [ ] **Step 1: Read current file**

Read `capitalradar/agents/utils/agent_states.py`. Find the `AgentState` TypedDict.

- [ ] **Step 2: Add `skill_version` field**

After `prediction_report` field (line 86), add:

```python
    skill_version: Annotated[int, "Skill version used for this analysis run"]
```

Also add a default to the state initializer in `propagation.py` if needed.

- [ ] **Step 3: Add default to propagation.py**

Read `capitalradar/graph/propagation.py`, find `create_initial_state()` (line 18). Add after the `prediction_report` line:
```python
            "skill_version": 0,
```

- [ ] **Step 4: Verify**

```
python -c "
from capitalradar.agents.utils.agent_states import AgentState
# Check that all required fields work
print('AgentState fields include skill_version:', 'skill_version' in AgentState.__annotations__)
"
```

- [ ] **Step 5: Commit**

```
git add capitalradar/agents/utils/agent_states.py capitalradar/graph/propagation.py
git commit -m "feat(state): add skill_version field to AgentState and state initializer"
```

---

### Task 3: Update memory.py — `store_decision` accepts `skill_version`

**Files:**
- Modify: `capitalradar/agents/utils/memory.py`

- [ ] **Step 1: Read current file**

Read `capitalradar/agents/utils/memory.py` lines 29-50. Find `store_decision()`.

- [ ] **Step 2: Add `skill_version` parameter and update tag format**

Change the function signature and logic:

```python
    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
        skill_version: int = 0,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        if not self._log_path:
            return
        # Idempotency guard: fast raw-text scan instead of full parse
        if self._log_path.exists():
            raw = self._log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("| pending]"):
                    return
        rating = parse_rating(final_trade_decision)
        tag = f"[{trade_date} | {ticker} | {rating} | v{skill_version} | pending]"
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)
```

Key changes:
- Added `skill_version: int = 0` parameter
- Tag format changed from `[{date} | {ticker} | {rating} | pending]` to `[{date} | {ticker} | {rating} | v{N} | pending]`

Also check `_parse_entry()` to ensure it can still handle the legacy format (without `v{N}`) and the new format. The `get_pending_entries()` uses `.endswith("| pending]")` which works for BOTH old and new format since both end with `| pending]`.

- [ ] **Step 3: Verify backward compatibility**

```
python -c "
from capitalradar.agents.utils.memory import CapitalRadarMemoryLog
# Check that the import works
print('memory OK')
"
```

- [ ] **Step 4: Commit**

```
git add capitalradar/agents/utils/memory.py
git commit -m "feat(memory): store_decision accepts skill_version param; tag format includes v{N}"
```

---

### Task 4: Wire skill_version in trading_graph.py

**Files:**
- Modify: `capitalradar/graph/trading_graph.py`

- [ ] **Step 1: Read current file**

Read `capitalradar/graph/trading_graph.py` around __init__ (lines 68-152) and propagate/store_decision (lines 440-446).

- [ ] **Step 2: Load skill_version in __init__**

After line 118 (`self.quick_thinking_llm = quick_client.get_llm()`), add:

```python
        # Determine current active skill version
        try:
            from capitalradar.graph.skill_generator import get_active_skill_version
            self.skill_version = get_active_skill_version()
        except Exception:
            self.skill_version = 0
        logger.info("Using skill version %d", self.skill_version)
```

- [ ] **Step 3: Set skill_version in initial state**

After line 397 (`init_agent_state = self.propagator.create_initial_state(...)`), add:

```python
        init_agent_state["skill_version"] = self.skill_version
```

- [ ] **Step 4: Pass skill_version to store_decision**

Change line 442-446 from:
```python
        self.memory_log.store_decision(
            ticker=ticker,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )
```
To:
```python
        self.memory_log.store_decision(
            ticker=ticker,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
            skill_version=self.skill_version,
        )
```

- [ ] **Step 5: Verify import and flow**

```
python -c "
from capitalradar.graph.trading_graph import CapitalRadarGraph
from capitalradar.default_config import DEFAULT_CONFIG
cfg = dict(DEFAULT_CONFIG)
cfg['llm_provider'] = 'deepseek'
cfg['deep_think_llm'] = 'deepseek-v4-flash'
g = CapitalRadarGraph(config=cfg)
print(f'Graph created with skill version: {g.skill_version}')
"
```

- [ ] **Step 6: Commit**

```
git add capitalradar/graph/trading_graph.py
git commit -m "feat(engine): load skill_version at init, pass to store_decision and AgentState"
```

---

### Task 5: Verify end-to-end

- [ ] **Step 1: All imports**

```
python -c "
from capitalradar.graph.skill_generator import get_active_skill_version
from capitalradar.agents.utils.agent_states import AgentState
from capitalradar.agents.utils.memory import CapitalRadarMemoryLog
from capitalradar.graph.trading_graph import CapitalRadarGraph
print('All imports OK')
"
```

- [ ] **Step 2: Verify skill_version flow**

```
python -c "
from capitalradar.graph.skill_generator import get_active_skill_version
v = get_active_skill_version()
print(f'Active skill version: {v}')
assert isinstance(v, int)
"
```

- [ ] **Step 3: Verify AgentState has skill_version**

```
python -c "
from capitalradar.agents.utils.agent_states import AgentState
assert 'skill_version' in AgentState.__annotations__
print('skill_version in AgentState: OK')
"
```

- [ ] **Step 4: Verify graph compiles**

```
python -c "
from capitalradar.graph.trading_graph import CapitalRadarGraph
from capitalradar.default_config import DEFAULT_CONFIG
g = CapitalRadarGraph(config=dict(DEFAULT_CONFIG))
print(f'Graph uses skill version: {g.skill_version}')
"
```

- [ ] **Step 5: Final commit**

```
git add -A
git commit -m "feat: skill lifecycle — version pinned per analysis, tracked in memory_log"
```
