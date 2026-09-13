# History Enhancement & PM Chat — Design Spec

**Date**: 2026-06-02
**Status**: Draft
**Scope**: Save raw tool-call data + key metrics per analysis; display enriched History detail; multi-turn Portfolio Manager chat on historical analyses with full tool access and persistence.

---

## 1. Goals

1. **Capture raw data**: Record every tool call (function, args, result snippet) made by each analyst during the pipeline, plus extract structured key metrics from each analyst's final report.
2. **Enrich History detail**: Display key metrics as color-coded cards, tool-call traces as expandable lists, and a "Chat with PM" entry point — all within the existing History detail view.
3. **History-based PM Chat**: Let users start multi-turn conversations with the Portfolio Manager agent about a past analysis. The PM has access to:
   - All stored analyst reports from that run
   - The stored tool-call traces and key metrics
   - **Live data tools** (stock price, indicators, money flow, news, fundamentals, intraday, realtime quote) to fetch current data and compare against the historical analysis.
4. **Persist everything**: Chat threads and messages are saved to SQLite and survive restarts. Frontend loads full conversation history when revisiting.

---

## 2. What's NOT in scope

- **Backfilling existing history**: Analyses run before this change lack `analyst_tool_traces` and `key_metrics`. The frontend shows "N/A" when those fields are absent.
- **Persisting the real-time PM chat**: The existing `pm_chat` toggle during live analysis is unchanged and remains session-scoped (no persistence).
- **Multi-user / auth**: Continues the single-user assumption.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│                 LangGraph Pipeline               │
│  Analyst → ToolNode loop                         │
│    │                                              │
│    ▼  (new: capture tool calls)                  │
│  _analyst_tool_traces                            │
│    │                                              │
│    ▼  (new: extract key metrics from reports)    │
│  key_metrics                                     │
│    │                                              │
│    ▼  (existing: persist to JSON)                │
│  full_states_log_{date}.json                     │
├─────────────────────────────────────────────────┤
│                 SQLite (results.db)               │
│  result_runs  (existing)                         │
│  chat_threads  (new)                             │
│  chat_messages (new)                             │
├─────────────────────────────────────────────────┤
│              FastAPI Endpoints                    │
│  /api/results/...           (existing)           │
│  /api/results/{id}/chat/threads    (new)         │
│  /api/results/{id}/chat/stream     (new SSE)     │
├─────────────────────────────────────────────────┤
│              Frontend (app.js)                    │
│  showHistoryDetail()  → enhanced with cards      │
│  HistoryChatPanel     → new chat UI              │
└─────────────────────────────────────────────────┘
```

---

## 4. Data Capture (stream.py changes)

### 4.1 Tool-call trace capture

**Where**: `StreamEmitter._process_chunk()` in `web/stream.py`.

Track intermediate chunks from the LangGraph `stream()` call. Each `values` chunk contains the full `AgentState` including `messages`. When the messages list grows with a new `ToolMessage`, match it back to the preceding `AIMessage.tool_calls` by `tool_call_id`.

New instance variable: `_analyst_tool_traces: dict[str, list[dict]]` keyed by analyst key.

Tool trace record schema:
```python
{
    "tool_name": "get_money_flow",
    "tool_args": {"ticker": "000001.SZ", "date": "2024-05-10"},
    "result_snippet": "...",  # first 2000 chars of tool result
    "elapsed_ms": 1234,
}
```

**How to detect which analyst is active**: The LangGraph state includes the current node name. Track this via `_detect_stage()` or by inspecting the `messages[-1].name` attribute. Since analysts run sequentially, the active analyst key is known from the most recent `analyst-start` SSE event.

**Edge case — tool result too large**: Truncate to 2000 chars. Full raw data is NOT stored (keeping JSON files manageable — typical analysis with all tools could reach 100KB+ otherwise).

### 4.2 Key metrics extraction

**Where**: After each analyst report appears (same place `analyst-report` events are emitted), run regex-based extraction.

**What to extract** — deterministic patterns from each report type:

| Analyst | Metrics | Regex patterns |
|---------|---------|---------------|
| Capital Flow | main_net_inflow, hsgt_net_inflow, margin_balance, institutional_holding_pct, manipulation_risk | `**主力净流入**：...`, `**北向资金**：...`, `**Manipulation Risk Level**: HIGH\|MEDIUM\|LOW` |
| Market | current_price, ma5, ma20, macd_signal, rsi_14 | `**Current Price**：...`, `**MACD Signal**：...`, `**RSI(14)**：...` |
| Sentiment | sentiment_score, positive_count, negative_count, manipulation_risk | `**Sentiment Score**：...`, `**Positive/Negative**：...` |
| News | article_count, key_topics, sentiment_bias, manipulation_risk | `**Articles Analyzed**：...`, `**Key Topics**：...` |
| Fundamentals | pe, pb, roe, revenue_growth, eps | `**PE Ratio**：...`, `**PB Ratio**：...`, `**ROE**：...` |
| Competitor | competitor_count, relative_position, key_competitor | `**Competitors Analyzed**：...` |
| Partner | partner_count, supply_chain_risk, key_partner | `**Partners Analyzed**：...` |

**Fallback**: If the report doesn't use the expected formatting, gracefully omit metrics rather than showing wrong data. The regex is case-insensitive and handles minor whitespace variations.

**Storage**: `key_metrics` is a flat dict keyed by analyst key, stored alongside the state in the JSON log. Also accumulated in `_session_results` for the markdown download and SSE events.

### 4.3 JSON schema addition

The existing `full_states_log_{date}.json` gains two top-level keys:
```json
{
    "...existing fields...": "...",
    "analyst_tool_traces": {
        "capital_flow": [ { "tool_name": "...", ... }, ... ],
        "market": [ ... ],
        ...
    },
    "key_metrics": {
        "capital_flow": { "main_net_inflow": "+3.2亿", ... },
        "market": { "current_price": "168.50", ... },
        ...
    }
}
```

### 4.4 Pipeline graph changes

In `trading_graph.py`, the analyst subgraph's agent node needs to expose intermediate states (the tool-call loop output) through the shared state so `stream.py` can detect tool invocations. The existing `AgentState` already has `messages` which accumulates all AIMessage/ToolMessage — `stream.py` uses relative diffing against previous `messages` to detect new tool calls. No graph schema changes needed.

---

## 5. History Detail UI

### 5.1 Rendering order (top to bottom)

1. Title bar (ticker, date, rating badge, re-analyze/download buttons) — existing
2. Meta row (provider, model, elapsed, risk level, next analysis date) — existing
3. **Key metrics card grid** — NEW
4. Final decision (markdown) — existing
5. **Tool-call trace (collapsible)** — NEW
6. Analyst reports (collapsible `<details>`) — existing
7. **"Chat with PM" button** — NEW

### 5.2 Key metrics cards

Grid layout: `grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`. Each card:
- Colored left border matching the analyst's accent color
- Header: analyst name
- Body: 3-6 metric rows as `label: value` pairs
- Empty metrics are omitted; if an analyst has zero metrics, the card is hidden

### 5.3 Tool-call trace

Nested `<details>` elements:
```
Capital Flow Analyst — 5 tool calls, 8.2s total
  ├─ [expandable] get_money_flow (3.4s)
  │   args: ticker=000001.SZ, date=2024-05-10
  │   result: {"main_net_inflow": 321450000, ...} (2000 chars max)
  ├─ [expandable] get_hsgt_flow (1.2s)
  ...
```

One `<details>` block per analyst, default collapsed. Individual tool calls default collapsed.

---

## 6. PM Chat on History

### 6.1 SQLite schema

```sql
CREATE TABLE IF NOT EXISTS chat_threads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL UNIQUE,
    run_id       TEXT NOT NULL,
    title        TEXT DEFAULT '',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES result_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    tool_calls   TEXT DEFAULT '',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (thread_id) REFERENCES chat_threads(thread_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_run ON chat_threads(run_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id);
```

`tool_calls` stores a JSON array of the tool invocations made during that PM response.

### 6.2 API endpoints

All under `/api/results/{run_id}/chat`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/threads` | Create new thread. Body: `{}`. Returns `{thread_id}`. |
| `GET` | `/threads` | List threads for this run. Returns `[{thread_id, title, created_at, updated_at, message_count}]`. |
| `DELETE` | `/threads/{thread_id}` | Delete a thread and all its messages. |
| `GET` | `/threads/{thread_id}/messages` | Get message history. Returns `[{role, content, tool_calls, created_at}]`. |
| `GET` | `/stream?thread_id=...&question=...` | **SSE stream**. Starts PM agent, yields events, persists messages. |

### 6.3 SSE stream (history_chat.py)

**New file**: `web/history_chat.py`

**PM Agent setup**:
```python
def create_history_pm_agent(run_id: str, config: dict) -> tuple:
    """
    Load full state from JSON, build system prompt with full analysis
    context, bind all data tools, return (llm_with_tools, tools).
    """
    state = load_full_state(config, run_id)
    # Build comprehensive system prompt from all 7 reports + traces + metrics
    # Bind tools: all dataflows tools (get_stock_data, get_indicators,
    #   get_news, get_money_flow, get_fundamentals, get_intraday_data, etc.)
```

**Agent loop** (LangGraph or manual loop):
```
1. System prompt ← full analysis context + conversation history from SQLite
2. User message ← question
3. LLM.invoke(messages) → may return tool_calls
4. If tool_calls: execute each tool, append results as ToolMessage, goto 3
5. When LLM returns final text → stream to client, persist to DB
```

**SSE events**:

| Event | Data | When |
|-------|------|------|
| `chat-stream-start` | `{thread_id}` | Stream begins |
| `chat-tool-call` | `{tool_name, args, status: "started"}` | PM starts calling a tool |
| `chat-tool-result` | `{tool_name, result_snippet}` | Tool returns |
| `chat-token` (optional) | `{token}` | Per-token streaming if model supports |
| `chat-done` | `{full_response, message_id}` | Final response complete |
| `chat-error` | `{message}` | Error occurred |

The stream is a standard SSE generator function. No threading.Event needed (unlike the live analysis checkpoints) — this is a fire-and-forget stream where each question is a single HTTP request.

### 6.4 Comparison: live PM chat vs. history PM chat

| Aspect | Live PM Chat (existing) | History PM Chat (new) |
|--------|------------------------|----------------------|
| Location | `stream.py:_generate_pm_chat_response()` | `web/history_chat.py` |
| Transport | SSE events inside analysis stream | Dedicated SSE endpoint |
| Model | Deep-thinking LLM from analysis config | Same provider/model stored in run metadata |
| Tools | None | Full dataflows tool set |
| Context | Truncated reports from memory | Full reports + tool traces + metrics from JSON |
| History | In-memory only (lost on close) | SQLite, survives restarts |
| Trigger | `pm_chat` toggle during analysis | Button in History detail |
| Concurrency | One per analysis session | Multiple threads per history record |

---

## 7. Frontend (app.js)

### 7.1 Key metrics rendering

New function `renderKeyMetricsCards(metrics)` called from `showHistoryDetail()`:
- Iterates `ANALYST_NAMES` and `_STAGE_COLORS` for consistent labeling
- Generates grid of metric cards
- Ignores analysts with no metrics

### 7.2 Tool trace rendering

New function `renderToolTraces(traces)` called from `showHistoryDetail()`:
- Per-analyst `<details>` with summary showing tool count
- Per-tool nested `<details>` with args and result snippet

### 7.3 Chat panel

New module `HistoryChatPanel` (functions scoped to avoid global pollution):

```
showHistoryChatPanel(runId, containerEl)   → opens the chat UI
closeHistoryChatPanel()                     → cleans up
loadThreadList(runId)                       → fetches GET /threads
openThread(runId, threadId)                 → loads history + opens SSE for new question
sendHistoryChatMessage(runId, threadId, text) → sends question via SSE
```

**UI structure** (reuses existing `.chat-panel` CSS where possible, extends with thread sidebar):

```
┌──────────────────────────────────────────────┐
│ Portfolio Manager Chat           [×] [+ New] │
├─────────────────┬────────────────────────────┤
│ Thread list     │ Message area                │
│ (1/4 width)     │ (3/4 width)                 │
│                 │                             │
│ ● Thread 1      │ User: ...                   │
│   "现在的价格..."  │ PM: ... (markdown)          │
│                 │                             │
│   Thread 2      │                             │
│   "评级依据"      │                             │
│                 │                             │
├─────────────────┴────────────────────────────┤
│ [input...]                          [Send]   │
└──────────────────────────────────────────────┘
```

**Thread list behavior**:
- Most recent thread at top
- Active thread highlighted
- Title auto-generated from first user question (truncated to 40 chars)
- Delete button (×) per thread with confirmation

**Error handling**:
- If SSE connection drops, append "Connection lost" message, offer retry
- If LLM errors, show error message inline (not as toast)
- Tool call failures are reported as `chat-tool-error` events, shown inline

### 7.4 Entry point in History detail

A "Chat with Portfolio Manager" button at the bottom of the detail view. On click:
1. POST `/api/results/{run_id}/chat/threads` creates a new thread
2. Opens the chat panel with the new thread active
3. User types the first question

---

## 8. Files changed

| File | Change |
|------|--------|
| `web/stream.py` | Add `_analyst_tool_traces` tracking in `_process_chunk()`; extract `key_metrics` from each analyst report; store both in `_session_results` and persist to JSON |
| `web/history_chat.py` | **New file** — SSE generator, PM agent factory with tools, context loading from stored JSON |
| `web/results_store.py` | Add `init_chat_tables()`, CRUD for chat_threads and chat_messages |
| `web/app.py` | Add 5 chat endpoints; call `init_chat_tables()` in lifespan; import history_chat module |
| `web/static/app.js` | Add `renderKeyMetricsCards()`, `renderToolTraces()`, `HistoryChatPanel` module; modify `showHistoryDetail()` |
| `web/static/style.css` | Add `.metric-card`, `.tool-trace`, `.chat-thread-sidebar` styles |
| `web/templates/index.html` | Minimal — possible container div adjustments |

---

## 9. Edge Cases & Error Handling

| Scenario | Behavior |
|----------|----------|
| Legacy history (no `key_metrics` or `tool_traces` in JSON) | Show "No metric data available" / "No tool traces recorded" in their respective sections |
| Tool result exceeds 2000 chars | Silently truncated; snippet ends with `...` |
| Chat thread has 50+ messages | Load all from DB; frontend shows last 20, scroll-up to load more |
| SSE connection drops mid-response | Frontend shows "Connection lost — retry?"; message is NOT saved (only saved on `chat-done`) |
| PM calls a tool that fails (e.g., network error) | Agent loop catches exception, injects error as ToolMessage content, lets PM decide how to respond |
| User deletes a history record | `ON DELETE CASCADE` removes associated chat threads and messages |
| Concurrent chat threads on same run | Fine — each thread has independent SSE connection, no shared state |
| Very long PM response | SSE streams in chunks (tokens or paragraphs), no truncation beyond model's natural limit |

---

## 10. Testing Strategy

- **Unit tests**: `test_history_chat.py` — context loading, PM agent prompt construction, tool binding
- **Integration tests**: `test_results_chat_api.py` — CRUD endpoints, SSE stream with mock LLM
- **Manual tests**: Run analysis → verify JSON has new fields → open History → verify cards/traces render → start chat → verify multi-turn with tool calls
- **Regression**: Existing analysis pipeline, history list/detail, live PM chat all continue to work
