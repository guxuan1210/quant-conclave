# Advisory Agent Enhancement -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enhance Investment Advisor with memory/reflection/experience management, and add Strategy Agent + Backtesting Agent as independent web tabs.

**Architecture:** Four sequential phases: (1) Memory & Reflection inside Advisor, (2) Backtesting Agent with backtrader, (3) Strategy Agent with template + LLM code gen, (4) Cross-agent tool integration. Detailed design in `docs/superpowers/specs/2026-06-25-advisory-enhancement-design.md`.

**Tech Stack:** FastAPI, SQLite, backtrader, Chart.js, SSE streaming, LangChain

---

## File Structure

```
capitalradar/
  advisory/experience_store.py     # SQLite CRUD for experiences
  advisory/extraction.py           # Pattern detection -> experience proposals
  backtest/engine.py               # backtrader runner
  backtest/strategies.py           # 7 strategy classes
  backtest/templates.py            # Template param schemas
  backtest/report.py               # Performance metrics
  strategy/manager.py              # CRUD strategy_templates + versions
  strategy/generator.py            # LLM -> backtrader code + validation
web/
  advisory_experience_ui.py        # SSE endpoints
  backtest_agent.py                # Backtest chat agent (SSE)
  strategy_agent.py                # Strategy chat agent (SSE)
  results_store.py                 # MODIFIED: add table init
  app.py                           # MODIFIED: register routes + tabs
  history_chat.py                  # MODIFIED: add cross-agent tools
  static/advisory_experiences.js   # UI panel
tests/                             # Test files per component
```

---

## Phase 1: Memory & Reflection

### Task 1.1: SQLite tables

**Files:** Modify `web/results_store.py`

- [ ] **Step 1: Add DDL for `advisory_experiences` and `advisory_experience_log` tables**

Add to `init_db()`:
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS advisory_experiences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL, source_ticker TEXT, source_date TEXT,
        outcome TEXT, raw_return REAL, category TEXT DEFAULT 'other',
        status TEXT DEFAULT 'pending_review'
            CHECK(status IN ('pending_review','active','archived')),
        lesson_abstract TEXT, created_at TEXT DEFAULT (datetime('now'))
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS advisory_experience_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL, experience_ids TEXT NOT NULL,
        injected_at TEXT DEFAULT (datetime('now'))
    )
""")
```

- [ ] **Step 2: Verify tables exist**
Run: `python -c "from web.results_store import init_db; init_db(config)"`
Expected: no error

- [ ] **Step 3: Commit**

### Task 1.2: Experience store CRUD

**Files:** Create `capitalradar/advisory/__init__.py`, Create `capitalradar/advisory/experience_store.py`

- [ ] **Step 1: Create `__init__.py`** with docstring `"""Advisory memory, experience extraction, and prompt injection."""`

- [ ] **Step 2: Create `experience_store.py`** with methods: `set_db_path`, `create_experience`, `list_experiences`, `approve_experience`, `archive_experience`, `reactivate_experience`, `update_experience`, `get_active_experiences`, `log_injection`, `get_injection_log`

Each method wraps a simple SQLite query against the advisory_experiences/log tables.

Key design: `get_active_experiences()` queries `status='active'` -- this is what the prompt injector calls.
`create_experience()` defaults `status='pending_review'`.

- [ ] **Step 3: Write test** in `tests/test_advisory_experience.py` -- create an in-memory SQLite table, test lifecycle: create -> list_pending -> approve -> active -> archive -> verify empty

Run: `pytest tests/test_advisory_experience.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

### Task 1.3: Experience extraction (pattern detection)

**Files:** Create `capitalradar/advisory/extraction.py`

- [ ] **Step 1: Create `extraction.py`** with pattern detection logic:
  - Pattern: "chase high without stop loss -> drawdown" (matches entries with raw_return < -0.05 and keywords in decision text)
  - Pattern: "gap up on policy news -> fade" (matches entries with raw_return < -0.03 and gap/policy keywords)
  - Each pattern produces a dict with `pattern_type`, `tickers`, `proposed_content`, `proposed_category`
  - Function `extract_patterns_from_log(memory_log, min_entries=3)` returns list of proposals

- [ ] **Step 2: Write test** -- mock `CapitalRadarMemoryLog.get_resolved_entries()`, verify empty log returns [], verify patterns are detected

Run: `pytest tests/test_advisory_experience.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

### Task 1.4: Experience API routes

**Files:** Create `web/advisory_experience_ui.py`, Modify `web/app.py`

- [ ] **Step 1: Create `advisory_experience_ui.py`** with FastAPI router at `/api/advisory/experiences`:
  - `GET /experiences?status=` -- list
  - `GET /experiences/active` -- active only
  - `PUT /experiences/{id}/approve` -- pending -> active
  - `PUT /experiences/{id}/archive` -- active -> archived
  - `PUT /experiences/{id}/reactivate` -- archived -> active
  - `PUT /experiences/{id}` -- update content/category
  - `POST /experiences/extract` -- run pattern detection, create entries, return list
  - `GET /experiences/log` -- injection history

- [ ] **Step 2: Register router in `web/app.py`**: `from web.advisory_experience_ui import router; app.include_router(router)`

- [ ] **Step 3: Commit**

### Task 1.5: Experience UI panel (JS)

**Files:** Create `web/static/js/advisory_experiences.js`

- [ ] **Step 1: Create JS panel** -- fetches `/api/advisory/experiences`, renders experience list grouped by status with approve/archive/reactivate buttons. Uses fetch() calls for each action.

- [ ] **Step 2: Add `<details>` element** to Advisory tab HTML with id `experience-panel-container` and child `experience-panel`, plus script include for `advisory_experiences.js`

- [ ] **Step 3: Commit**

### Task 1.6: Experience injection into prompt

**Files:** Modify `web/history_chat.py`

- [ ] **Step 1: Add injection before return in `build_pm_system_prompt()`:**
```python
from capitalradar.advisory.experience_store import get_active_experiences, log_injection
active = get_active_experiences()
if active:
    prompt += "\n## Experience Vault (overridable, {N} entries)\n".format(N=len(active))
    prompt += "Use when applicable; explain if not applicable.\n\n"
    for exp in active:
        cat = exp.get("category", "other")
        prompt += "- [{cat}] {content}\n".format(cat=cat, content=exp.get("content",""))
    prompt += "\n"
    log_injection(...)
```

- [ ] **Step 2: Commit**

---

## Phase 2: Backtesting Agent

### Task 2.1: Backtest engine

**Files:** Create `capitalradar/backtest/__init__.py`, `templates.py`, `strategies.py`, `engine.py`, `report.py`

- [ ] **Step 1: Create `templates.py`** -- 7 template definitions as list of dicts (id, name, description, param schema). Templates: ma_cross, macd, rsi, bollinger, turtle, ma_arrange, volume_breakout. Each param has key/label/type/default/min/max.

- [ ] **Step 2: Create `strategies.py`** -- 7 backtrader `SignalStrategy` classes, one per template. All use `bt.ind.*` (SMA, MACD, RSI, BollingerBands, Highest/Lowest, ATR). Map dict `STRATEGY_MAP` maps template_id -> class.

- [ ] **Step 3: Create `engine.py`** -- `run_backtest(ticker, strategy_class, params, start_date, end_date)` -> dict with `results` (metrics), `equity_curve` ([{date, value}]), `trade_log`. Uses yfinance for data, backtrader Cerebro with analyzers (Returns, SharpeRatio, DrawDown, TradeList).

- [ ] **Step 4: Create `report.py`** -- `compute_performance(results)` -> markdown string. `render_equity_chart_data(equity_curve)` -> Chart.js-compatible list.

- [ ] **Step 5: Write tests** in `tests/test_backtest_engine.py` -- verify all 7 strategy classes import, verify template metadata, verify engine can instantiate Cerebro (no data run is OK).

- [ ] **Step 6: Commit**

### Task 2.2: Backtest runs table

**Files:** Modify `web/results_store.py`

- [ ] **Step 1: Add DDL for `backtest_runs`** in `init_db()`:
```sql
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, strategy_name TEXT, strategy_id INTEGER,
    start_date TEXT, end_date TEXT, parameters_used TEXT,
    initial_capital REAL DEFAULT 100000,
    results TEXT, equity_curve TEXT, trade_log TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
```

- [ ] **Step 2: Commit**

### Task 2.3: Backtest agent chat (SSE)

**Files:** Create `web/backtest_agent.py`, Modify `web/app.py`

- [ ] **Step 1: Create `web/backtest_agent.py`** following same SSE pattern as `web/ai_pick_agent.py`:
  - `build_backtest_system_prompt()` -- lists available templates, instructs agent to ask user for parameters
  - `build_backtest_tools()` -- provides `list_templates_tool` and `run_backtest_tool`
  - `create_backtest_agent()` -- binds LLM + tools
  - `stream_backtest_chat()` -- SSE generator (chat-tool-call -> chat-tool-result -> chat-done)

- [ ] **Step 2: Register SSE endpoint** in `web/app.py`:
```python
@app.post("/api/backtest/chat")
async def backtest_chat(thread_id=Form(...), question=Form(...), lang=Form("Chinese")):
    return StreamingResponse(stream_backtest_chat(...), media_type="text/event-stream")
```

- [ ] **Step 3: Commit**

---

## Phase 3: Strategy Agent

### Task 3.1: Strategy manager

**Files:** Create `capitalradar/strategy/__init__.py`, Create `capitalradar/strategy/manager.py`

- [ ] **Step 1: Create `__init__.py`** with docstring

- [ ] **Step 2: Create `manager.py`** -- full CRUD: `create_strategy`, `list_strategies`, `get_strategy`, `search_strategies`, `delete_strategy`, `add_version`, `get_versions`. Connects to SQLite, serializes parameters as JSON.

- [ ] **Step 3: Write test** in `tests/test_strategy_manager.py` -- create/search/get/delete cycle + version tracking

- [ ] **Step 4: Commit**

### Task 3.2: LLM code generator

**Files:** Create `capitalradar/strategy/generator.py`

- [ ] **Step 1: Create `generator.py`** -- function `generate_strategy_code(description, config)`:
  1. Sends LLM a system prompt requesting backtrader code only
  2. Extracts Python from markdown fences via regex
  3. Validates with `ast.parse()`
  4. Checks for `bt.Strategy` subclass
  5. Returns validated code string

- [ ] **Step 2: Write test** in `tests/test_strategy_generator.py` -- test `_extract_code()` with/without fences, test `_validate_code()` valid/invalid cases

- [ ] **Step 3: Commit**

### Task 3.3: Strategy agent chat

**Files:** Create `web/strategy_agent.py`, Modify `web/app.py`

- [ ] **Step 1: Create `web/strategy_agent.py`** -- same SSE pattern as backtest agent. Tools: `list_templates`, `generate_strategy` (LLM gen), `save_strategy`, `get_strategy`, `delete_strategy`.

- [ ] **Step 2: Register SSE endpoint** in `web/app.py`

- [ ] **Step 3: Commit**

### Task 3.4: Web tab UI for both agents

**Files:** Modify `web/app.py` (tab navigation)

- [ ] **Step 1: Add "Strategy" and "Backtest" tabs** to the web dashboard navigation bar, with click handlers that show the corresponding chat UI components

- [ ] **Step 2: Commit**

---

## Phase 4: Cross-Agent Integration

### Task 4.1: Advisory Agent tools for strategy/backtest

**Files:** Modify `web/history_chat.py`

- [ ] **Step 1: Add `list_strategies` tool** -- queries `capitalradar.strategy.manager.search_strategies()`, returns formatted list

- [ ] **Step 2: Add `run_backtest` tool** -- loads strategy by ID, calls backtest engine, returns performance summary

- [ ] **Step 3: Wire experience injection** -- Task 1.6 already modifies `build_pm_system_prompt()`; verify it works during integration

- [ ] **Step 4: Commit**

### Task 4.2: Integration tests

**Files:** Create `tests/test_advisory_integration.py`

- [ ] **Step 1: Write test** for cross-agent tool invocation (mock backtrader engine, verify `run_backtest` tool returns expected format)

- [ ] **Step 2: Run `pytest tests/ -v`** -- all existing + new tests pass

- [ ] **Step 3: Commit**

---

## Self-Review

1. **Spec coverage:** All sections from the design spec are covered. Memory (2.1-2.6) -> Ph1. Backtest (4.1-4.6) -> Ph2. Strategy (3.1-3.6) -> Ph3. Integration (5) -> Ph4.
2. **Placeholder scan:** No TBD/TODO. All steps have concrete actions.
3. **Type consistency:** Function signatures and API paths match across phases. `get_active_experiences()` used consistently by both the API routes and the prompt injector.
4. **Encoding:** Pure ASCII. No special Unicode characters.

---

## Execution Options

**1. Subagent-Driven (recommended)** -- I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** -- Execute tasks in this session with checkpoints for review

Which approach do you prefer?
