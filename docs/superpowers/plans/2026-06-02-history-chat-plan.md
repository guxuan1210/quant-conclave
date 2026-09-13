# History Enhancement & PM Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture raw tool-call data and key metrics during analysis, display enriched History detail with metrics cards and tool-call traces, and enable multi-turn PM chat on historical analyses with full tool access and SQLite persistence.

**Architecture:** Extend `stream.py` to capture tool-call traces and extract key metrics during the pipeline, persist to the existing JSON log. Add SQLite tables for chat threads/messages in `results_store.py`. Create `web/history_chat.py` for the PM SSE stream with full tool binding. Frontend adds metrics cards, tool traces, and a threaded chat panel to the History detail view.

**Tech Stack:** Python (LangChain tools, FastAPI SSE), SQLite, vanilla JavaScript, CSS Grid

---

## File Structure

| File | Responsibility |
|------|---------------|
| `web/stream.py` | Capture tool-call traces from LangGraph chunk messages; extract key metrics from analyst reports via regex; persist both to JSON and session_results |
| `web/history_chat.py` | **New** — `create_history_pm_agent()` builds PM LLM with tools + context; `stream_history_chat()` is the SSE generator; `build_pm_system_prompt()` constructs the full system message |
| `web/results_store.py` | `init_chat_tables()` creates `chat_threads` / `chat_messages`; CRUD: `create_chat_thread()`, `list_chat_threads()`, `delete_chat_thread()`, `get_chat_messages()`, `save_chat_message()` |
| `web/app.py` | 5 new chat endpoints; `init_chat_tables()` call in lifespan |
| `web/static/app.js` | `renderKeyMetricsCards()`, `renderToolTraces()`, history chat panel module |
| `web/static/style.css` | `.metric-card`, `.metric-grid`, `.tool-trace`, thread sidebar styles |
| `web/templates/index.html` | No structural changes needed (existing `history-detail-container` and `comparison-container` suffice) |

---

### Task 1: Capture tool-call traces during analysis

**Files:**
- Modify: `web/stream.py`

- [ ] **Step 1: Add tool-trace tracking to StreamEmitter.__init__**

In `StreamEmitter.__init__` (currently at line 161), add the traces accumulator:

```python
def __init__(self, config=None, session_id=None):
    self.config = config or DEFAULT_CONFIG.copy()
    self.session_id = session_id or ""
    self._analyst_tool_traces: dict = {}     # {analyst_key: [trace_record, ...]}
    self._prev_message_count: int = 0        # to detect new messages
```

- [ ] **Step 2: Initialize tool_traces in _session_results at stream start**

In `stream_analysis()`, after the existing `_session_results` initialization (line 199), append:

```python
_session_results[self.session_id]["analyst_tool_traces"] = {}
_session_results[self.session_id]["key_metrics"] = {}
```

- [ ] **Step 3: Add tool-call detection logic in _process_chunk**

In `_process_chunk()`, after the existing "Detect analyst reports" block (around line 592) and before the report-detection logic, add a new section that detects new ToolMessages:

```python
# Detect tool calls from new messages
messages = chunk.get("messages", [])
if len(messages) > self._prev_message_count:
    # New messages appeared — find ToolMessages and their parent AIMessages
    new_messages = messages[self._prev_message_count:]
    # Determine active analyst from the current node in state
    active_analyst = self._current_analyst_from_state(chunk, prev_state)
    if active_analyst:
        self._capture_tool_calls(active_analyst, messages, new_messages)
    self._prev_message_count = len(messages)
```

- [ ] **Step 4: Implement _current_analyst_from_state helper**

```python
def _current_analyst_from_state(self, chunk, prev_state):
    """Detect which analyst is currently active by checking which
    report field appeared most recently."""
    for analyst_key, state_key in ANALYST_REPORT_KEYS.items():
        prev_val = prev_state.get(state_key, "")
        curr_val = chunk.get(state_key, "")
        if prev_val == "" and curr_val != "":
            return analyst_key  # This analyst just finished
    # If no report just completed, check if any analyst has a partial report
    for analyst_key in ["capital_flow", "market", "social", "news",
                        "fundamentals", "competitor", "partner"]:
        state_key = ANALYST_REPORT_KEYS.get(analyst_key, "")
        if state_key and chunk.get(state_key, ""):
            return analyst_key
    return None
```

Actually, a better approach is to track the active analyst via the analyst-start events. Since `stream_analysis()` is a generator and `_process_chunk` is called per chunk, and the graph's analyst nodes run sequentially, we can track the active analyst from the node name in the graph state metadata. However, the "values" stream mode doesn't include node metadata. The simplest reliable approach: track the active analyst by watching for the analyst report key's first appearance in the state (when it goes from empty to non-empty for a given analyst, that analyst is active).

Let me revise Step 3 to a cleaner approach — capture tool calls right when an analyst report appears, since at that point we know which analyst was active and all their tool calls have accumulated in `messages`:

- [ ] **Step 3 (revised): Capture tool calls when an analyst report appears**

In `_process_chunk()`, inside the existing "Detect analyst reports" loop (around line 593-610), after the existing report-detection logic and before yielding the `analyst-report` event, add tool call extraction:

```python
# Detect analyst reports
for analyst_key, state_key in ANALYST_REPORT_KEYS.items():
    prev_val = prev_state.get(state_key, "")
    curr_val = chunk.get(state_key, "")
    if prev_val == "" and curr_val != "":
        # --- NEW: capture tool calls for this analyst ---
        self._capture_analyst_tools(analyst_key, chunk)
        # --- existing report handling ---
        elapsed = _elapsed_since(stage_times.get("analysts", pipeline_start))
        ...
```

- [ ] **Step 4 (revised): Implement _capture_analyst_tools**

```python
def _capture_analyst_tools(self, analyst_key, chunk):
    """Extract tool call records from the messages list for an analyst.
    
    The LangGraph analyst subgraph produces AIMessage (with tool_calls) 
    followed by ToolMessage (with results). We pair them by tool_call_id
    and build trace records.
    """
    messages = chunk.get("messages", [])
    if not messages:
        return
    
    # Find the range of messages belonging to this analyst's tool loop.
    # We scan backwards from the last message to find ToolMessages.
    tool_records = []
    seen_call_ids = set()
    
    for msg in messages:
        # Collect AIMessage tool_calls
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id", "")
                if tc_id and tc_id not in seen_call_ids:
                    seen_call_ids.add(tc_id)
                    tool_records.append({
                        "tool_call_id": tc_id,
                        "tool_name": tc.get("name", "unknown"),
                        "tool_args": tc.get("args", {}),
                        "result_snippet": "",  # filled below
                    })
    
    # Now match ToolMessage results to records
    tool_results = {}
    for msg in messages:
        if hasattr(msg, "tool_call_id"):
            tc_id = msg.tool_call_id
            content = str(msg.content) if hasattr(msg, "content") else str(msg)
            tool_results[tc_id] = content[:2000]
    
    # Fill in results
    for rec in tool_records:
        tc_id = rec["tool_call_id"]
        if tc_id in tool_results:
            rec["result_snippet"] = tool_results[tc_id]
        else:
            rec["result_snippet"] = "(result not captured)"
        # Remove internal id
        rec.pop("tool_call_id", None)
    
    # Accumulate
    if analyst_key not in self._analyst_tool_traces:
        self._analyst_tool_traces[analyst_key] = []
    
    # Deduplicate: only add records not already captured
    existing_names = {r.get("tool_name") for r in self._analyst_tool_traces[analyst_key]}
    for rec in tool_records:
        if rec["tool_name"] not in existing_names:
            self._analyst_tool_traces[analyst_key].append(rec)
            existing_names.add(rec["tool_name"])
    
    # Store in session results
    if self.session_id in _session_results:
        _session_results[self.session_id]["analyst_tool_traces"] = dict(
            self._analyst_tool_traces
        )
```

- [ ] **Step 5: Persist tool traces to JSON in pipeline-done handler**

In `stream_analysis()`, at the persistence block (around line 365-406), add `analyst_tool_traces` and `key_metrics` to the JSON log:

```python
# After existing json.dump of state_for_log:
state_for_log["analyst_tool_traces"] = dict(self._analyst_tool_traces)
state_for_log["key_metrics"] = session_data.get("key_metrics", {})
# Then write... (the existing json.dump call)
```

- [ ] **Step 6: Commit**

```bash
git add web/stream.py
git commit -m "feat: capture analyst tool-call traces during pipeline streaming

Tool-call records (tool name, args, result snippet) are captured when each
analyst report appears, accumulated in _analyst_tool_traces, and persisted
to the full state JSON log."
```

---

### Task 2: Extract key metrics from analyst reports

**Files:**
- Modify: `web/stream.py`

- [ ] **Step 1: Add _extract_key_metrics method to StreamEmitter**

Add to `StreamEmitter` class (new method, after `_capture_analyst_tools`):

```python
def _extract_key_metrics(self, analyst_key, report_text):
    """Extract structured key metrics from an analyst's final report.
    
    Returns a dict of metric_name -> value, or empty dict if nothing matched.
    Patterns are language-aware — they try English patterns first, then Chinese.
    """
    if not report_text:
        return {}
    
    text = str(report_text)
    metrics = {}
    
    patterns_map = {
        "capital_flow": [
            (r'(?:\*\*)?主力净流入(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "main_net_inflow"),
            (r'(?:\*\*)?北向资金(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "hsgt_net_inflow"),
            (r'(?:\*\*)?融资余额(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "margin_balance"),
            (r'(?:\*\*)?机构持仓比例(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "institutional_holding_pct"),
            (r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*[：:]\s*(HIGH|MEDIUM|LOW)',
             "manipulation_risk"),
            (r'(?:\*\*)?Net\s+Main\s+Inflow(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "main_net_inflow"),
            (r'(?:\*\*)?Northbound\s+Flow(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "hsgt_net_inflow"),
            (r'(?:\*\*)?Institutional\s+Holding\s*%?(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "institutional_holding_pct"),
        ],
        "market": [
            (r'(?:\*\*)?Current\s+Price(?:\*\*)?\s*[：:]\s*\$?\s*(.+?)(?:\n|$)',
             "current_price"),
            (r'(?:\*\*)?当前价格(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "current_price"),
            (r'(?:\*\*)?MA5(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "ma5"),
            (r'(?:\*\*)?MA20(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "ma20"),
            (r'(?:\*\*)?MACD\s+Signal(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "macd_signal"),
            (r'(?:\*\*)?MACD\s*信号(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "macd_signal"),
            (r'(?:\*\*)?RSI\s*\(?14\)?(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "rsi_14"),
        ],
        "social": [
            (r'(?:\*\*)?Sentiment\s+Score(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "sentiment_score"),
            (r'(?:\*\*)?情绪评分(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "sentiment_score"),
            (r'(?:\*\*)?Positive.*?Negative(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "pos_neg_ratio"),
            (r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*[：:]\s*(HIGH|MEDIUM|LOW)',
             "manipulation_risk"),
        ],
        "news": [
            (r'(?:\*\*)?Article(?:s)?\s+Analyzed(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "article_count"),
            (r'(?:\*\*)?分析文章数(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "article_count"),
            (r'(?:\*\*)?Key\s+Topics?(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "key_topics"),
            (r'(?:\*\*)?关键主题(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "key_topics"),
            (r'(?:\*\*)?Sentiment\s+Bias(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "sentiment_bias"),
            (r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*[：:]\s*(HIGH|MEDIUM|LOW)',
             "manipulation_risk"),
        ],
        "fundamentals": [
            (r'(?:\*\*)?PE\s*Ratio(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "pe"),
            (r'(?:\*\*)?PB\s*Ratio(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "pb"),
            (r'(?:\*\*)?ROE(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "roe"),
            (r'(?:\*\*)?Revenue\s+Growth(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "revenue_growth"),
            (r'(?:\*\*)?EPS(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "eps"),
            (r'(?:\*\*)?市盈率(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "pe"),
            (r'(?:\*\*)?市净率(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "pb"),
            (r'(?:\*\*)?净资产收益率(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "roe"),
        ],
        "competitor": [
            (r'(?:\*\*)?Competitor(?:s)?\s+Analyzed(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "competitor_count"),
            (r'(?:\*\*)?竞争对手(?:数|分析)(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "competitor_count"),
            (r'(?:\*\*)?Relative\s+Position(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "relative_position"),
            (r'(?:\*\*)?相对地位(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "relative_position"),
            (r'(?:\*\*)?Key\s+Competitor(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "key_competitor"),
            (r'(?:\*\*)?主要竞争对手(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "key_competitor"),
        ],
        "partner": [
            (r'(?:\*\*)?Partner(?:s)?\s+Analyzed(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "partner_count"),
            (r'(?:\*\*)?合作伙伴(?:数|分析)(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "partner_count"),
            (r'(?:\*\*)?Supply\s+Chain\s+Risk(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "supply_chain_risk"),
            (r'(?:\*\*)?供应链风险(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "supply_chain_risk"),
            (r'(?:\*\*)?Key\s+Partner(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "key_partner"),
            (r'(?:\*\*)?主要合作伙伴(?:\*\*)?\s*[：:]\s*(.+?)(?:\n|$)',
             "key_partner"),
        ],
    }
    
    patterns = patterns_map.get(analyst_key, [])
    for pattern, key in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = m.group(1).strip()
            if value and value not in ("N/A", "n/a", "-", "None", "null"):
                metrics[key] = value
    
    return metrics
```

- [ ] **Step 2: Call _extract_key_metrics when analyst report appears**

In `_process_chunk()`, inside the analyst report detection loop (same place as Task 1 Step 3), add metrics extraction:

```python
if prev_val == "" and curr_val != "":
    # Capture tool calls
    self._capture_analyst_tools(analyst_key, chunk)
    
    # --- NEW: extract key metrics ---
    report_text = str(curr_val)[:5000]
    metrics = self._extract_key_metrics(analyst_key, report_text)
    if metrics and self.session_id in _session_results:
        _session_results[self.session_id]["key_metrics"][analyst_key] = metrics
    # ---
    
    # existing: manipulation_risk, accumulation, yielding analyst-report event
    ...
```

- [ ] **Step 3: Include key_metrics in analyst-report SSE event**

Add a `metrics` field to the existing `analyst-report` SSE event data:

```python
yield _event("analyst-report", {
    "analyst": analyst_key,
    "name": ANALYST_NAMES[analyst_key],
    "report": report_text,
    "elapsed_ms": elapsed,
    "color": _STAGE_COLORS.get(analyst_key, "#888"),
    "manipulation_risk": manipulation_risk,
    "metrics": metrics,   # NEW — key metrics for this analyst
})
```

- [ ] **Step 4: Commit**

```bash
git add web/stream.py
git commit -m "feat: extract key metrics from analyst reports using regex

Parses structured fields (PE, MACD signal, net inflow, etc.) from each
analyst's final report using bilingual regex patterns. Metrics are stored
in _session_results, emitted in SSE analyst-report events, and persisted
to the state JSON log."
```

---

### Task 3: Add chat tables and CRUD to results_store.py

**Files:**
- Modify: `web/results_store.py`

- [ ] **Step 1: Add init_chat_tables function**

Append to the end of `web/results_store.py`:

```python
# ---- Chat Threads & Messages ----

def init_chat_tables(config: dict) -> None:
    conn = _get_conn(config)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id    TEXT NOT NULL UNIQUE,
                run_id       TEXT NOT NULL,
                title        TEXT DEFAULT '',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES result_runs(run_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id    TEXT NOT NULL,
                role         TEXT NOT NULL,
                content      TEXT NOT NULL,
                tool_calls   TEXT DEFAULT '',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_threads_run
            ON chat_threads(run_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
            ON chat_messages(thread_id)
        """)
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Add create_chat_thread**

```python
def create_chat_thread(config: dict, run_id: str, title: str = "") -> str:
    """Create a new chat thread. Returns the thread_id."""
    import uuid
    thread_id = uuid.uuid4().hex[:12]
    conn = _get_conn(config)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO chat_threads (thread_id, run_id, title) VALUES (?, ?, ?)",
            (thread_id, run_id, title)
        )
        conn.commit()
    finally:
        conn.close()
    return thread_id
```

- [ ] **Step 3: Add list_chat_threads**

```python
def list_chat_threads(config: dict, run_id: str) -> list[dict]:
    """List all chat threads for a run, newest first."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """SELECT t.thread_id, t.title, t.created_at, t.updated_at,
                      COUNT(m.id) as message_count
               FROM chat_threads t
               LEFT JOIN chat_messages m ON t.thread_id = m.thread_id
               WHERE t.run_id = ?
               GROUP BY t.thread_id
               ORDER BY t.updated_at DESC""",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: Add delete_chat_thread**

```python
def delete_chat_thread(config: dict, thread_id: str) -> bool:
    """Delete a thread and its messages (CASCADE handles messages)."""
    conn = _get_conn(config)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 5: Add get_chat_messages and save_chat_message**

```python
def get_chat_messages(config: dict, thread_id: str) -> list[dict]:
    """Get all messages for a thread, oldest first."""
    conn = _get_conn(config)
    try:
        rows = conn.execute(
            """SELECT role, content, tool_calls, created_at
               FROM chat_messages WHERE thread_id = ?
               ORDER BY created_at ASC""",
            (thread_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_chat_message(config: dict, thread_id: str, role: str,
                      content: str, tool_calls: str = "") -> int:
    """Save a message. Returns the message id."""
    conn = _get_conn(config)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            """INSERT INTO chat_messages (thread_id, role, content, tool_calls)
               VALUES (?, ?, ?, ?)""",
            (thread_id, role, content, tool_calls)
        )
        # Update thread's updated_at
        conn.execute(
            "UPDATE chat_threads SET updated_at = CURRENT_TIMESTAMP WHERE thread_id = ?",
            (thread_id,)
        )
        # Auto-set thread title from first user message
        conn.execute(
            """UPDATE chat_threads SET title = ?
               WHERE thread_id = ? AND (title IS NULL OR title = '')""",
            (content[:40], thread_id)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
```

- [ ] **Step 6: Commit**

```bash
git add web/results_store.py
git commit -m "feat: add chat_threads and chat_messages tables with CRUD functions"
```

---

### Task 4: Create history_chat.py — PM agent with tools

**Files:**
- Create: `web/history_chat.py`

- [ ] **Step 1: Create the file with imports and build_pm_system_prompt**

```python
"""History-based Portfolio Manager chat with full data tool access.

Provides the SSE generator and PM agent factory for multi-turn conversations
about past CapitalRadar analyses. The PM agent is bound with all dataflows
tools and receives the full analysis context from stored JSON.
"""

import json
import logging
import time
from typing import Generator, Optional

from web.results_store import load_full_state, get_result, get_chat_messages, save_chat_message

logger = logging.getLogger(__name__)


def build_pm_system_prompt(state: dict, config: dict, lang: str = "English") -> str:
    """Build the Portfolio Manager system prompt from stored analysis state."""
    ticker = state.get("ticker", "N/A")
    date = state.get("trade_date", "N/A")
    
    prompt = (
        f"You are the Portfolio Manager from the CapitalRadar trading analysis system. "
        f"The user is reviewing a past analysis and wants to ask follow-up questions.\n\n"
        f"**Analysis Context:**\n"
        f"- Ticker: {ticker}\n"
        f"- Analysis Date: {date}\n\n"
    )
    
    # Final decision (most important)
    final = state.get("final_trade_decision", "")
    if final:
        prompt += f"**Your Final Decision (from the analysis):**\n{str(final)[:4000]}\n\n"
    
    # All analyst reports
    report_keys = [
        ("capital_flow_report", "Capital Flow Analyst"),
        ("market_report", "Market Analyst"),
        ("sentiment_report", "Sentiment Analyst"),
        ("news_report", "News Analyst"),
        ("fundamentals_report", "Fundamentals Analyst"),
        ("competitor_report", "Competitor Analyst"),
        ("partner_report", "Partner Analyst"),
    ]
    
    for key, label in report_keys:
        report = state.get(key, "")
        if report:
            prompt += f"**{label} Report:**\n{str(report)[:2000]}\n\n"
    
    # Key metrics (if available)
    metrics = state.get("key_metrics", {})
    if metrics:
        prompt += "**Extracted Key Metrics:**\n"
        for analyst, kv in metrics.items():
            if kv:
                prompt += f"- {analyst}: {json.dumps(kv, ensure_ascii=False)}\n"
        prompt += "\n"
    
    # Instructions
    prompt += (
        "---\n"
        "You have access to live data tools (stock prices, indicators, news, "
        "money flow, fundamentals, intraday data, real-time quotes). Use them "
        "when the user asks about current market conditions or wants to compare "
        "the analysis with the present situation.\n\n"
        "Guidelines:\n"
        "- Reference specific data from the analysis reports when relevant.\n"
        "- Call tools to fetch current data when comparing past vs present.\n"
        "- Be concise but thorough. Acknowledge limitations honestly.\n"
        "- If tool calls fail, explain what you tried to fetch and why.\n"
        f"- Write in {lang}.\n"
    )
    
    return prompt


def build_tool_set():
    """Return the full set of data tools available to the PM.
    
    These are the same LangChain @tool-decorated functions used by analysts.
    """
    from capitalradar.agents.utils.core_stock_tools import get_stock_data
    from capitalradar.agents.utils.technical_indicators_tools import get_indicators
    from capitalradar.agents.utils.fundamental_data_tools import (
        get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
    )
    from capitalradar.agents.utils.news_data_tools import (
        get_news, get_insider_transactions, get_global_news,
    )
    from capitalradar.agents.utils.capital_flow_tools import (
        get_money_flow, get_hsgt_flow, get_market_flow, get_margin_trading,
        get_institutional_holders, get_major_holders, get_analyst_recommendations,
    )
    from capitalradar.agents.utils.intraday_tools import (
        get_intraday_data, get_realtime_quote,
    )
    
    return [
        get_stock_data, get_indicators,
        get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
        get_news, get_insider_transactions, get_global_news,
        get_money_flow, get_hsgt_flow, get_market_flow, get_margin_trading,
        get_institutional_holders, get_major_holders, get_analyst_recommendations,
        get_intraday_data, get_realtime_quote,
    ]
```

- [ ] **Step 2: Add create_history_pm_agent**

```python
def create_history_pm_agent(run_id: str, config: dict):
    """Create a PM LLM instance with tools bound and full analysis context.
    
    Returns:
        (llm_with_tools, tools, system_prompt): The tool-bound LLM, the tool
        list for manual execution, and the system prompt string.
    """
    from capitalradar.llm_clients import create_llm_client
    
    # Load analysis metadata to get provider/model
    meta = get_result(config, run_id)
    if not meta:
        raise ValueError(f"Analysis run {run_id} not found")
    
    provider = meta.get("provider") or config.get("llm_provider", "deepseek")
    deep_model = meta.get("deep_model") or config.get("deep_think_llm", "")
    
    # Load full analysis state
    state = load_full_state(config, run_id)
    if not state:
        raise ValueError(f"Full state for run {run_id} not found")
    
    # Create LLM client with the same provider/model used in the analysis
    client = create_llm_client(
        provider=provider,
        model=deep_model,
        base_url=config.get("backend_url"),
    )
    llm = client.get_llm()
    
    # Build tools
    tools = build_tool_set()
    llm_with_tools = llm.bind_tools(tools)
    
    # Build system prompt
    lang = meta.get("language") or config.get("output_language", "English")
    system_prompt = build_pm_system_prompt(state, config, lang)
    
    return llm_with_tools, tools, system_prompt, meta
```

- [ ] **Step 3: Commit**

```bash
git add web/history_chat.py
git commit -m "feat: add history_chat.py with PM agent factory and tool binding"
```

---

### Task 5: Add SSE stream generator for history chat

**Files:**
- Modify: `web/history_chat.py`

- [ ] **Step 1: Add _sse_event helper and stream_history_chat generator**

Append to `web/history_chat.py`:

```python
import re as _re


def _escape_newlines(s: str) -> str:
    """Collapse actual newlines in SSE data values so each event stays on one line."""
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _event(name: str, data: dict) -> str:
    """Format an SSE event string."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


def stream_history_chat(
    run_id: str,
    thread_id: str,
    question: str,
    config: dict,
) -> Generator[str, None, None]:
    """SSE generator for a single Q&A turn with the PM.
    
    Loads the full analysis context, conversation history, creates a tool-bound
    PM agent, and runs the agent loop (LLM -> tool calls -> tool results -> LLM).
    Yields SSE events for each step. Persists both user question and PM response
    to the database.
    
    Args:
        run_id: The analysis run identifier.
        thread_id: The chat thread identifier.
        question: The user's question text.
        config: Application config dict.
    """
    try:
        llm_with_tools, tools, system_prompt, meta = create_history_pm_agent(
            run_id, config
        )
    except Exception as e:
        yield _event("chat-error", {"message": str(e)})
        return
    
    # Save user message
    save_chat_message(config, thread_id, "user", question)
    
    ticker = meta.get("ticker", "")
    date = meta.get("date", "")
    
    yield _event("chat-stream-start", {"thread_id": thread_id})
    
    # Load conversation history
    try:
        history_msgs = get_chat_messages(config, thread_id)
    except Exception:
        history_msgs = []
    
    # Build messages list: system + history + current question
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
    
    messages = [SystemMessage(content=system_prompt)]
    
    for h in history_msgs:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))
    
    # The user question is already saved as the last message, included above
    # But we need to make sure it's there:
    messages.append(HumanMessage(content=question))
    
    # Tool execution map (name -> function)
    tool_map = {t.name: t for t in tools}
    
    # Agent loop: LLM -> tools -> LLM -> tools -> ... -> final answer
    tool_call_records = []
    max_iterations = 10
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            logger.exception("PM LLM invocation failed")
            yield _event("chat-error", {"message": f"LLM error: {e}"})
            return
        
        # Check for tool calls
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            
            for tc in response.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})
                
                # Sanitize args for display
                safe_args = {}
                for k, v in tool_args.items():
                    safe_args[k] = str(v)[:500]
                
                yield _event("chat-tool-call", {
                    "tool_name": tool_name,
                    "args": safe_args,
                })
                
                # Execute tool
                func = tool_map.get(tool_name)
                if func:
                    try:
                        result = func.invoke(tool_args)
                        result_str = str(result)[:2000]
                    except Exception as e:
                        result_str = f"Error executing {tool_name}: {e}"
                else:
                    result_str = f"Tool '{tool_name}' not found. Available: {list(tool_map.keys())}"
                
                yield _event("chat-tool-result", {
                    "tool_name": tool_name,
                    "result_snippet": _escape_newlines(result_str),
                })
                
                tool_call_records.append({
                    "tool_name": tool_name,
                    "args": safe_args,
                    "result_snippet": result_str,
                })
                
                messages.append(ToolMessage(content=result_str, tool_call_id=tc.get("id", "")))
        else:
            # No more tool calls — this is the final response
            final_text = response.content if hasattr(response, "content") else str(response)
            
            # Save PM response
            tc_json = json.dumps(tool_call_records, ensure_ascii=False) if tool_call_records else ""
            msg_id = save_chat_message(config, thread_id, "assistant", final_text, tc_json)
            
            yield _event("chat-done", {
                "full_response": _escape_newlines(final_text),
                "message_id": msg_id,
                "tool_calls_count": len(tool_call_records),
            })
            return
    
    # Max iterations reached
    yield _event("chat-error", {
        "message": "Agent reached maximum tool-call iterations without producing a final answer."
    })
```

- [ ] **Step 2: Commit**

```bash
git add web/history_chat.py
git commit -m "feat: add SSE stream generator for history chat with tool-call loop"
```

---

### Task 6: Add chat API endpoints to app.py

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Import history_chat module and init chat tables in lifespan**

In `web/app.py`, add to imports (near line 20):

```python
from web.history_chat import stream_history_chat
```

In the `lifespan` function (line 28-38), add chat table init after existing `init_shortlist`:

```python
from web.results_store import init_chat_tables
init_chat_tables(DEFAULT_CONFIG)
```

- [ ] **Step 2: Add the 5 chat endpoints**

Add before the `# ---- Shortlist API ----` section (around line 601):

```python
# ---- History Chat API ----

class CreateThreadBody(BaseModel):
    pass


@app.post("/api/results/{run_id}/chat/threads")
def create_chat_thread_endpoint(run_id: str, body: CreateThreadBody = CreateThreadBody()):
    """Create a new chat thread for a historical analysis."""
    from web.results_store import create_chat_thread, get_result
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")
    thread_id = create_chat_thread(DEFAULT_CONFIG, run_id)
    return {"thread_id": thread_id}


@app.get("/api/results/{run_id}/chat/threads")
def list_chat_threads_endpoint(run_id: str):
    """List all chat threads for a historical analysis."""
    from web.results_store import get_result, list_chat_threads
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")
    threads = list_chat_threads(DEFAULT_CONFIG, run_id)
    return threads


@app.delete("/api/results/{run_id}/chat/threads/{thread_id}")
def delete_chat_thread_endpoint(run_id: str, thread_id: str):
    """Delete a chat thread and all its messages."""
    from web.results_store import delete_chat_thread
    ok = delete_chat_thread(DEFAULT_CONFIG, thread_id)
    if not ok:
        raise HTTPException(404, "Thread not found")
    return {"acknowledged": True}


@app.get("/api/results/{run_id}/chat/threads/{thread_id}/messages")
def get_chat_messages_endpoint(run_id: str, thread_id: str):
    """Get all messages for a chat thread."""
    from web.results_store import get_chat_messages as get_msgs
    msgs = get_msgs(DEFAULT_CONFIG, thread_id)
    return msgs


@app.get("/api/results/{run_id}/chat/stream")
def history_chat_stream(
    run_id: str,
    thread_id: str = Query(min_length=1),
    question: str = Query(min_length=1),
):
    """SSE stream for a PM chat Q&A turn on a historical analysis."""
    from web.results_store import get_result, get_chat_messages
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")
    
    def event_stream():
        yield from stream_history_chat(run_id, thread_id, question, DEFAULT_CONFIG)
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 3: Commit**

```bash
git add web/app.py
git commit -m "feat: add 5 REST + SSE endpoints for history-based PM chat"
```

---

### Task 7: Frontend — render key metrics cards in History detail

**Files:**
- Modify: `web/static/app.js`
- Modify: `web/static/style.css`

- [ ] **Step 1: Add renderKeyMetricsCards function to app.js**

Add after the existing `renderComparison()` function (around line 1466). Insert:

```javascript
var METRIC_LABELS = {
  // Capital Flow
  "main_net_inflow": "Main Net Inflow",
  "hsgt_net_inflow": "Northbound Flow",
  "margin_balance": "Margin Balance",
  "institutional_holding_pct": "Institutional Holding %",
  // Market
  "current_price": "Current Price",
  "ma5": "MA5",
  "ma20": "MA20",
  "macd_signal": "MACD Signal",
  "rsi_14": "RSI(14)",
  // Sentiment
  "sentiment_score": "Sentiment Score",
  "pos_neg_ratio": "Positive/Negative",
  // News
  "article_count": "Articles Analyzed",
  "key_topics": "Key Topics",
  "sentiment_bias": "Sentiment Bias",
  // Fundamentals
  "pe": "PE Ratio",
  "pb": "PB Ratio",
  "roe": "ROE",
  "revenue_growth": "Revenue Growth",
  "eps": "EPS",
  // Competitor
  "competitor_count": "Competitors Analyzed",
  "relative_position": "Relative Position",
  "key_competitor": "Key Competitor",
  // Partner
  "partner_count": "Partners Analyzed",
  "supply_chain_risk": "Supply Chain Risk",
  "key_partner": "Key Partner",
  // Common
  "manipulation_risk": "Manipulation Risk",
};

var ANALYST_CARD_ORDER = [
  "capital_flow", "market", "social", "news",
  "fundamentals", "competitor", "partner"
];

var ANALYST_CARD_NAMES = {
  "capital_flow": "Capital Flow",
  "market": "Market",
  "social": "Sentiment",
  "news": "News",
  "fundamentals": "Fundamentals",
  "competitor": "Competitor",
  "partner": "Partner",
};

var ANALYST_CARD_COLORS = {
  "capital_flow": "#f0883e",
  "market": "#3fb950",
  "social": "#a371f7",
  "news": "#ffd700",
  "fundamentals": "#53d8fb",
  "competitor": "#ff6b6b",
  "partner": "#48dbfb",
};

function renderKeyMetricsCards(metrics) {
  if (!metrics || typeof metrics !== "object") {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No key metrics available for this analysis.</div>';
  }

  var html = '<div class="metric-grid">';
  var hasAny = false;

  ANALYST_CARD_ORDER.forEach(function(analystKey) {
    var kv = metrics[analystKey];
    if (!kv || typeof kv !== "object" || Object.keys(kv).length === 0) return;
    hasAny = true;
    var color = ANALYST_CARD_COLORS[analystKey] || "#888";
    var name = ANALYST_CARD_NAMES[analystKey] || analystKey;
    html += '<div class="metric-card" style="border-left: 3px solid ' + color + ';">';
    html += '<div class="metric-card-header">' + esc(name) + '</div>';
    html += '<div class="metric-card-body">';
    Object.keys(kv).forEach(function(k) {
      var label = METRIC_LABELS[k] || k;
      var val = kv[k];
      html += '<div class="metric-row">';
      html += '<span class="metric-label">' + esc(label) + '</span>';
      html += '<span class="metric-value">' + esc(String(val)) + '</span>';
      html += '</div>';
    });
    html += '</div></div>';
  });

  if (!hasAny) {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No key metrics available for this analysis.</div>';
  }

  html += '</div>';
  return html;
}
```

- [ ] **Step 2: Integrate metrics cards into showHistoryDetail**

In `showHistoryDetail()` (lines 1347-1429), after the meta row and before the final decision, insert the metrics section.

Find this line (around 1380):
```javascript
    // Full decision
    html += '<div class="card-body markdown-body">';
```

Insert BEFORE it:
```javascript
    // Key metrics cards
    if (state && state.key_metrics) {
      html += '<div class="card-body" style="padding-top:0;">';
      html += renderKeyMetricsCards(state.key_metrics);
      html += '</div>';
    }
```

- [ ] **Step 3: Add CSS styles for metric cards**

Append to `web/static/style.css`:

```css
/* ---- Key Metrics Cards ---- */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
  margin-top: 8px;
}

.metric-card {
  background: var(--panel-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.metric-card-header {
  font-size: 12px;
  font-weight: 600;
  padding: 6px 10px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  color: var(--text);
}

.metric-card-body {
  padding: 6px 10px;
}

.metric-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
  font-size: 12px;
}

.metric-row + .metric-row {
  border-top: 1px solid rgba(128, 128, 128, 0.1);
}

.metric-label {
  color: var(--text-muted);
  font-size: 11px;
}

.metric-value {
  font-weight: 600;
  color: var(--text);
  font-size: 12px;
  text-align: right;
}
```

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js web/static/style.css
git commit -m "feat: add key metrics cards to History detail view

Renders a responsive grid of metric cards color-coded by analyst type.
Metrics are extracted from stored key_metrics JSON using bilingual labels."
```

---

### Task 8: Frontend — render tool-call traces in History detail

**Files:**
- Modify: `web/static/app.js`
- Modify: `web/static/style.css`

- [ ] **Step 1: Add renderToolTraces function to app.js**

Add after `renderKeyMetricsCards()`:

```javascript
function renderToolTraces(traces) {
  if (!traces || typeof traces !== "object") {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No tool-call traces recorded.</div>';
  }

  var hasAny = false;
  var html = '<div style="padding-top:0;">';
  html += '<details class="tool-traces-details">';
  html += '<summary style="cursor:pointer;font-weight:600;font-size:13px;">Tool-Call Traces</summary>';
  html += '<div class="tool-traces-body" style="margin-top:8px;">';

  ANALYST_CARD_ORDER.forEach(function(analystKey) {
    var records = traces[analystKey];
    if (!records || !Array.isArray(records) || records.length === 0) return;
    hasAny = true;
    var name = ANALYST_CARD_NAMES[analystKey] || analystKey;
    var color = ANALYST_CARD_COLORS[analystKey] || "#888";
    html += '<details class="tool-analyst-details" style="margin-bottom:6px;">';
    html += '<summary style="cursor:pointer;font-size:12px;color:' + color + ';font-weight:600;">';
    html += esc(name) + ' — ' + records.length + ' tool call(s)';
    html += '</summary>';
    html += '<div style="padding-left:14px;margin-top:4px;">';
    records.forEach(function(rec) {
      var argsStr = "";
      if (rec.tool_args && typeof rec.tool_args === "object") {
        var parts = [];
        Object.keys(rec.tool_args).forEach(function(k) {
          var v = rec.tool_args[k];
          if (typeof v === "string" && v.length > 80) v = v.substring(0, 80) + "...";
          parts.push(k + "=" + String(v));
        });
        argsStr = parts.join(", ");
      }
      html += '<details class="tool-item-details" style="margin-bottom:4px;">';
      html += '<summary style="cursor:pointer;font-size:11px;color:var(--text-muted);font-family:monospace;">';
      html += esc(rec.tool_name || "unknown") + '(' + esc(argsStr) + ')';
      if (rec.result_snippet) {
        var snippetLen = rec.result_snippet.length;
        html += ' <span style="opacity:0.6;">(' + snippetLen + ' chars)</span>';
      }
      html += '</summary>';
      if (rec.result_snippet) {
        html += '<pre class="tool-result-pre">' + esc(rec.result_snippet) + '</pre>';
      }
      html += '</details>';
    });
    html += '</div></details>';
  });

  html += '</div></details></div>';

  if (!hasAny) {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No tool-call traces recorded.</div>';
  }
  return html;
}
```

- [ ] **Step 2: Integrate tool traces into showHistoryDetail**

In `showHistoryDetail()`, after the final decision section and before the analyst reports `<details>`, insert the tool traces.

Find this section (around 1400):
```javascript
      html += '<details style="margin-top:12px;"><summary ...
```

Insert BEFORE it:
```javascript
    // Tool-call traces
    if (state && state.analyst_tool_traces) {
      html += '<div class="card-body" style="padding-top:0;">';
      html += renderToolTraces(state.analyst_tool_traces);
      html += '</div>';
    }
```

- [ ] **Step 3: Add CSS for tool traces**

Append to `web/static/style.css`:

```css
/* ---- Tool-Call Traces ---- */
.tool-traces-details > summary {
  padding: 0;
}

.tool-traces-body {
  font-size: 12px;
}

.tool-analyst-details > summary:hover {
  opacity: 0.85;
}

.tool-item-details > summary {
  padding: 2px 0;
}

.tool-item-details > summary:hover {
  color: var(--text) !important;
}

.tool-result-pre {
  font-size: 10px;
  line-height: 1.4;
  background: #1a1a2e;
  color: #7ecb9a;
  padding: 8px 10px;
  border-radius: 4px;
  margin: 4px 0 6px;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
```

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js web/static/style.css
git commit -m "feat: add tool-call trace display to History detail

Renders nested <details> elements showing each analyst's tool invocations,
arguments, and result snippets. Styled with monospace code blocks."
```

---

### Task 9: Frontend — add "Chat with PM" entry point in History detail

**Files:**
- Modify: `web/static/app.js`

- [ ] **Step 1: Add chat button to showHistoryDetail**

In `showHistoryDetail()`, at the end of the detail HTML (before the final `</div>` closing the result-card), add a chat button:

Find this near line 1415:
```javascript
    html += '</div>';
    showRightContent("history", {html: html});
```

Insert BEFORE `html += '</div>';`:
```javascript
    // Chat with PM button
    html += '<div class="card-body" style="padding-top:8px;border-top:1px solid var(--border);margin-top:12px;">';
    html += '<button class="btn primary" id="history-chat-btn" data-run-id="' + esc(runId) + '" style="padding:8px 16px;font-size:13px;">Chat with Portfolio Manager</button>';
    html += '</div>';
```

- [ ] **Step 2: Add event listener binding in showHistoryDetail**

After `showRightContent("history", {html: html});` and the existing re-analyze button binding, add the chat button binding:

```javascript
    // Bind Chat with PM button
    var chatBtn = document.getElementById("history-chat-btn");
    if (chatBtn) {
      chatBtn.addEventListener("click", function() {
        showHistoryChatPanel(chatBtn.dataset.runId);
      });
    }
```

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat: add 'Chat with PM' button to History detail view"
```

---

### Task 10: Frontend — history chat panel (thread list + message area)

**Files:**
- Modify: `web/static/app.js`
- Modify: `web/static/style.css`

- [ ] **Step 1: Add history chat panel state and functions to app.js**

Add before the `// ---- Init ----` section (around line 2921). This is a large addition — add it as a self-contained module:

```javascript
// ---- History Chat Panel ----
var hcpRunId = null;
var hcpActiveThreadId = null;
var hcpPanel = null;
var hcpMessages = null;
var hcpThreadList = null;
var hcpEventSource = null;

function showHistoryChatPanel(runId) {
  if (hcpPanel) closeHistoryChatPanel();
  hcpRunId = runId;

  // Create panel
  hcpPanel = document.createElement("div");
  hcpPanel.id = "history-chat-panel";
  hcpPanel.className = "history-chat-panel";
  hcpPanel.innerHTML =
    '<div class="history-chat-header">' +
    '<span>Portfolio Manager Chat</span>' +
    '<div style="display:flex;gap:8px;">' +
    '<button id="hcp-new-thread" class="btn secondary" style="padding:3px 10px;font-size:11px;">+ New</button>' +
    '<button id="hcp-close" class="chat-close-btn">&times;</button>' +
    '</div>' +
    '</div>' +
    '<div class="history-chat-body">' +
    '<div id="hcp-thread-list" class="hcp-thread-list"></div>' +
    '<div class="hcp-main">' +
    '<div id="hcp-messages" class="hcp-messages">' +
    '<span class="no-results" style="padding:20px;">Select a thread or create a new one to start chatting.</span>' +
    '</div>' +
    '<div class="hcp-input-area">' +
    '<input type="text" id="hcp-input" class="chat-input" placeholder="Ask the Portfolio Manager about this analysis..." disabled />' +
    '<button id="hcp-send" class="chat-send-btn" disabled>Send</button>' +
    '</div>' +
    '</div>' +
    '</div>';

  document.getElementById("right-panel").appendChild(hcpPanel);

  hcpMessages = document.getElementById("hcp-messages");
  hcpThreadList = document.getElementById("hcp-thread-list");

  // Event bindings
  document.getElementById("hcp-close").addEventListener("click", closeHistoryChatPanel);
  document.getElementById("hcp-new-thread").addEventListener("click", function() {
    createAndOpenThread(runId);
  });
  document.getElementById("hcp-send").addEventListener("click", sendHistoryChatMessage);
  document.getElementById("hcp-input").addEventListener("keydown", function(e) {
    if (e.key === "Enter") sendHistoryChatMessage();
  });

  // Load threads
  loadHistoryChatThreads(runId);
}

function closeHistoryChatPanel() {
  if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }
  if (hcpPanel) { hcpPanel.remove(); hcpPanel = null; }
  hcpMessages = null;
  hcpThreadList = null;
  hcpRunId = null;
  hcpActiveThreadId = null;
}

function loadHistoryChatThreads(runId) {
  fetch("/api/results/" + runId + "/chat/threads")
    .then(function(r) { return r.json(); })
    .then(function(threads) {
      renderHistoryChatThreads(threads);
    })
    .catch(function() {
      if (hcpThreadList) hcpThreadList.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">Failed to load</span>';
    });
}

function renderHistoryChatThreads(threads) {
  if (!hcpThreadList) return;
  if (!threads || !threads.length) {
    hcpThreadList.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">No conversations yet</span>';
    return;
  }
  var html = "";
  threads.forEach(function(t) {
    var title = t.title || "New conversation";
    if (title.length > 30) title = title.substring(0, 30) + "...";
    var activeClass = (t.thread_id === hcpActiveThreadId) ? " active" : "";
    html +=
      '<div class="hcp-thread-item' + activeClass + '" data-thread-id="' + esc(t.thread_id) + '">' +
      '<span class="hcp-thread-title">' + esc(title) + '</span>' +
      '<span class="hcp-thread-meta">' + esc(t.message_count || "0") + ' msgs</span>' +
      '<button class="hcp-thread-delete" data-thread-id="' + esc(t.thread_id) + '" title="Delete">&times;</button>' +
      '</div>';
  });
  hcpThreadList.innerHTML = html;

  // Bind thread clicks
  hcpThreadList.querySelectorAll(".hcp-thread-item").forEach(function(el) {
    el.addEventListener("click", function(e) {
      if (e.target.classList.contains("hcp-thread-delete")) return;
      var tid = el.dataset.threadId;
      openHistoryChatThread(hcpRunId, tid);
    });
  });

  // Bind delete buttons
  hcpThreadList.querySelectorAll(".hcp-thread-delete").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      var tid = btn.dataset.threadId;
      if (!confirm("Delete this conversation?")) return;
      fetch("/api/results/" + hcpRunId + "/chat/threads/" + tid, { method: "DELETE" })
        .then(function(r) {
          if (r.ok) {
            if (hcpActiveThreadId === tid) {
              hcpActiveThreadId = null;
              if (hcpMessages) hcpMessages.innerHTML = '<span class="no-results" style="padding:20px;">Select a thread or create a new one.</span>';
            }
            loadHistoryChatThreads(hcpRunId);
          }
        });
    });
  });
}

function createAndOpenThread(runId) {
  fetch("/api/results/" + runId + "/chat/threads", { method: "POST", headers: {"Content-Type": "application/json"}, body: "{}" })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      openHistoryChatThread(runId, data.thread_id);
      loadHistoryChatThreads(runId);
    });
}

function openHistoryChatThread(runId, threadId) {
  hcpActiveThreadId = threadId;
  // Enable input
  var input = document.getElementById("hcp-input");
  var sendBtn = document.getElementById("hcp-send");
  if (input) input.disabled = false;
  if (sendBtn) sendBtn.disabled = false;

  // Load message history
  fetch("/api/results/" + runId + "/chat/threads/" + threadId + "/messages")
    .then(function(r) { return r.json(); })
    .then(function(msgs) {
      if (!hcpMessages || hcpActiveThreadId !== threadId) return;
      hcpMessages.innerHTML = "";
      if (!msgs || !msgs.length) {
        hcpMessages.innerHTML = '<span class="no-results" style="padding:20px;">Start the conversation by asking a question.</span>';
      } else {
        msgs.forEach(function(m) {
          appendHistoryChatMessage(m.role, m.content, m.tool_calls);
        });
      }
    });

  // Update thread list highlight
  loadHistoryChatThreads(runId);
}

function sendHistoryChatMessage() {
  if (!hcpRunId || !hcpActiveThreadId) return;
  var input = document.getElementById("hcp-input");
  var sendBtn = document.getElementById("hcp-send");
  if (!input) return;
  var text = input.value.trim();
  if (!text) return;

  appendHistoryChatMessage("user", text);
  input.value = "";
  input.disabled = true;
  if (sendBtn) sendBtn.disabled = true;

  // Show thinking indicator
  appendHistoryChatThinking();

  // Close existing SSE
  if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }

  var url = "/api/results/" + hcpRunId + "/chat/stream?thread_id=" +
    encodeURIComponent(hcpActiveThreadId) + "&question=" + encodeURIComponent(text);
  hcpEventSource = new EventSource(url);

  hcpEventSource.addEventListener("chat-tool-call", function(e) {
    var d = JSON.parse(e.data);
    removeHistoryChatThinking();
    appendHistoryChatToolCall(d.tool_name);
  });

  hcpEventSource.addEventListener("chat-tool-result", function(e) {
    // Tool completed — thinking indicator resumes
    appendHistoryChatThinking();
  });

  hcpEventSource.addEventListener("chat-done", function(e) {
    var d = JSON.parse(e.data);
    removeHistoryChatThinking();
    appendHistoryChatMessage("assistant", d.full_response || "", "");
    if (input) input.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
    if (input) input.focus();
    if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }
    // Refresh thread list to update titles
    loadHistoryChatThreads(hcpRunId);
  });

  hcpEventSource.addEventListener("chat-error", function(e) {
    var d = JSON.parse(e.data);
    removeHistoryChatThinking();
    appendHistoryChatMessage("system", "Error: " + (d.message || "Unknown error"));
    if (input) input.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
    if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }
  });

  hcpEventSource.onerror = function() {
    if (hcpEventSource && hcpEventSource.readyState === EventSource.CLOSED) {
      if (input) input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  };
}

function appendHistoryChatMessage(role, content, toolCallsJson) {
  if (!hcpMessages) return;
  var div = document.createElement("div");
  div.className = "chat-msg " + role;
  if (role === "assistant" || role === "pm") {
    div.innerHTML = '<div class="markdown-body">' + renderMarkdown(content || "") + '</div>';
    // Show tool calls if present
    if (toolCallsJson) {
      try {
        var tcs = JSON.parse(toolCallsJson);
        if (tcs && tcs.length) {
          var tcHtml = '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">Tools used: ';
          tcHtml += tcs.map(function(tc) { return esc(tc.tool_name || "unknown"); }).join(", ");
          tcHtml += '</div>';
          div.innerHTML += tcHtml;
        }
      } catch(e) {}
    }
  } else if (role === "user") {
    div.textContent = content;
  } else {
    div.textContent = content;
  }
  hcpMessages.appendChild(div);
  hcpMessages.scrollTop = hcpMessages.scrollHeight;
}

function appendHistoryChatThinking() {
  if (!hcpMessages) return;
  var div = document.createElement("div");
  div.className = "chat-msg pm thinking hcp-thinking";
  div.textContent = "Thinking...";
  hcpMessages.appendChild(div);
  hcpMessages.scrollTop = hcpMessages.scrollHeight;
}

function removeHistoryChatThinking() {
  if (!hcpMessages) return;
  var el = hcpMessages.querySelector(".hcp-thinking");
  if (el) el.remove();
}

function appendHistoryChatToolCall(toolName) {
  if (!hcpMessages) return;
  var div = document.createElement("div");
  div.className = "chat-msg system";
  div.innerHTML = '<span style="opacity:0.7;">Calling <code>' + esc(toolName) + '</code>...</span>';
  hcpMessages.appendChild(div);
  hcpMessages.scrollTop = hcpMessages.scrollHeight;
}
```

- [ ] **Step 2: Add CSS for history chat panel**

Append to `web/static/style.css`:

```css
/* ---- History Chat Panel ---- */
.history-chat-panel {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  height: 520px;
  background: var(--bg);
  overflow: hidden;
}

.history-chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  font-weight: 600;
  background: var(--panel-bg);
  flex-shrink: 0;
}

.history-chat-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.hcp-thread-list {
  width: 180px;
  min-width: 160px;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  background: var(--panel-bg);
  flex-shrink: 0;
}

.hcp-thread-item {
  padding: 8px 10px;
  cursor: pointer;
  border-bottom: 1px solid rgba(128, 128, 128, 0.08);
  position: relative;
  transition: background 0.15s;
}

.hcp-thread-item:hover {
  background: var(--bg);
}

.hcp-thread-item.active {
  background: var(--bg);
  border-left: 3px solid var(--accent);
}

.hcp-thread-title {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.hcp-thread-meta {
  font-size: 10px;
  color: var(--text-muted);
}

.hcp-thread-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  padding: 0 4px;
  opacity: 0;
  transition: opacity 0.15s;
}

.hcp-thread-item:hover .hcp-thread-delete {
  opacity: 1;
}

.hcp-thread-delete:hover {
  color: var(--red);
}

.hcp-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.hcp-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg);
}

.hcp-input-area {
  display: flex;
  border-top: 1px solid var(--border);
  padding: 8px 10px;
  gap: 8px;
  background: var(--panel-bg);
  flex-shrink: 0;
}

/* Tool call message styling */
.chat-msg.system code {
  background: var(--panel-bg);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
```

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js web/static/style.css
git commit -m "feat: add history chat panel with thread list and SSE message streaming

Full chat UI: thread sidebar, message area, input, SSE streaming with
tool-call visualization. Persists all conversations to SQLite."
```

---

### Task 11: Integration test and end-to-end verification

**Files:**
- Create: `tests/test_history_chat.py`

- [ ] **Step 1: Write basic unit test for key_metrics extraction**

```python
"""Tests for history chat and metrics extraction."""
import pytest
from web.stream import StreamEmitter


class TestMetricsExtraction:
    def test_extract_capital_flow_metrics_english(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **Main Net Inflow**: +3.2 billion
        **Northbound Flow**: +1.5 billion
        **Institutional Holding %**: 42.3%
        **Manipulation Risk Level**: LOW
        """
        metrics = emitter._extract_key_metrics("capital_flow", report)
        assert metrics.get("main_net_inflow") == "+3.2 billion"
        assert metrics.get("hsgt_net_inflow") == "+1.5 billion"
        assert metrics.get("institutional_holding_pct") == "42.3%"
        assert metrics.get("manipulation_risk") == "LOW"

    def test_extract_capital_flow_metrics_chinese(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **主力净流入**：+3.2亿
        **北向资金**：+1.5亿
        **融资余额**：85.6亿
        """
        metrics = emitter._extract_key_metrics("capital_flow", report)
        assert metrics.get("main_net_inflow") == "+3.2亿"
        assert metrics.get("hsgt_net_inflow") == "+1.5亿"
        assert metrics.get("margin_balance") == "85.6亿"

    def test_extract_market_metrics(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **Current Price**: $168.50
        **MA5**: $165.30
        **MA20**: $158.90
        **MACD Signal**: Bullish Golden Cross
        **RSI(14)**: 62.5
        """
        metrics = emitter._extract_key_metrics("market", report)
        assert metrics.get("current_price") == "$168.50"
        assert metrics.get("macd_signal") == "Bullish Golden Cross"
        assert metrics.get("rsi_14") == "62.5"

    def test_extract_empty_report_returns_empty_dict(self):
        emitter = StreamEmitter(session_id="test")
        metrics = emitter._extract_key_metrics("capital_flow", "")
        assert metrics == {}

    def test_extract_unknown_analyst_returns_empty_dict(self):
        emitter = StreamEmitter(session_id="test")
        metrics = emitter._extract_key_metrics("unknown_type", "**PE**: 15")
        assert metrics == {}
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_history_chat.py -v
```

Expected: 5 tests pass.

- [ ] **Step 3: Write integration test for chat tables CRUD**

```python
class TestChatTables:
    def test_create_and_list_thread(self):
        from web.results_store import (
            init_db, init_chat_tables, create_chat_thread,
            list_chat_threads, delete_chat_thread, save_chat_message,
            get_chat_messages,
        )
        from capitalradar.default_config import DEFAULT_CONFIG

        init_db(DEFAULT_CONFIG)
        init_chat_tables(DEFAULT_CONFIG)

        # Create a test run first (needed for FK)
        from web.results_store import save_result
        run_id = save_result(DEFAULT_CONFIG, {
            "ticker": "TEST", "date": "2024-01-15", "rating": "Hold",
        })

        thread_id = create_chat_thread(DEFAULT_CONFIG, run_id, "Test Thread")
        assert thread_id
        assert len(thread_id) == 12

        threads = list_chat_threads(DEFAULT_CONFIG, run_id)
        assert len(threads) == 1
        assert threads[0]["thread_id"] == thread_id
        assert threads[0]["message_count"] == 0

        # Save messages
        save_chat_message(DEFAULT_CONFIG, thread_id, "user", "Hello PM")
        save_chat_message(DEFAULT_CONFIG, thread_id, "assistant", "Hello investor!")

        threads = list_chat_threads(DEFAULT_CONFIG, run_id)
        assert threads[0]["message_count"] == 2

        msgs = get_chat_messages(DEFAULT_CONFIG, thread_id)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

        # Delete
        assert delete_chat_thread(DEFAULT_CONFIG, thread_id) is True
        assert list_chat_threads(DEFAULT_CONFIG, run_id) == []

        # Cleanup
        from web.results_store import delete_result
        delete_result(DEFAULT_CONFIG, run_id)

    def test_list_empty_threads(self):
        from web.results_store import init_chat_tables, list_chat_threads
        from capitalradar.default_config import DEFAULT_CONFIG

        init_chat_tables(DEFAULT_CONFIG)
        threads = list_chat_threads(DEFAULT_CONFIG, "nonexistent_run")
        assert threads == []
```

- [ ] **Step 4: Run integration tests**

```bash
pytest tests/test_history_chat.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Manual verification checklist**

1. Run a full analysis from the web UI (any ticker, e.g., NVDA)
2. Check the results JSON file at `~/.capitalradar/results/NVDA/CapitalRadarStrategy_logs/full_states_log_*.json` — verify `analyst_tool_traces` and `key_metrics` fields exist and contain data
3. Open History tab, refresh, click the analysis — verify metrics cards render, tool traces are expandable
4. Click "Chat with Portfolio Manager" — verify chat panel opens with empty thread
5. Ask a question like "What was the main reason for your rating?" — verify PM responds with analysis-specific content
6. Ask a question that triggers a tool call, e.g., "What's the current price of NVDA?" — verify tool-call indicator appears
7. Close and reopen the History detail — verify the chat thread persists and messages reload
8. Run "Compare Selected" with 2 results — verify still works

- [ ] **Step 6: Commit**

```bash
git add tests/test_history_chat.py
git commit -m "test: add unit and integration tests for metrics extraction and chat tables"
```

---

## Post-Implementation Verification

After all tasks are complete, run the full test suite:

```bash
pytest tests/ -v --ignore=tests/test_memory_log.py
```

Then manually verify the checklist from Task 11 Step 5.
