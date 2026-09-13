# Web UI for TradingAgents — Design Spec

**Date**: 2026-05-22
**Status**: Approved

## Overview

Add an interactive web dashboard to TradingAgents that lets users configure and run a trading analysis pipeline, watch real-time agent progress via SSE, and review intermediate and final results in a split-panel layout.

## Architecture

```
Browser (HTML/CSS/JS)  ←──SSE──→  FastAPI Backend  ←──→  CapitalRadarGraph (LangGraph)
                                          │
                                          └── yfinance (stock search)
```

- **Frontend**: Single-page HTML with vanilla JS, served as a FastAPI static route plus a Jinja2 template for the main page.
- **Streaming**: SSE (Server-Sent Events). The backend calls `graph.stream()` and translates each LangGraph state delta into typed SSE events the frontend renders incrementally.
- **Stock search**: A new `/api/search?q=` endpoint wraps `yfinance.Search` and returns matching tickers.

## Tech Stack

| Layer     | Choice                      | Rationale                                                          |
|-----------|-----------------------------|--------------------------------------------------------------------|
| Backend   | FastAPI                     | Integrates with existing Python codebase; native SSE support.      |
| Frontend  | Vanilla HTML/CSS/JS + Jinja2| Zero build step, no npm. `EventSource` API consumes SSE natively.  |
| Streaming | Server-Sent Events          | Unidirectional is sufficient; simpler than WebSocket, HTTP-native. |

## Backend API

### `GET /` — Main page
Serves the dashboard HTML (Jinja2 template).

### `GET /api/search?q=<query>`
Returns up to 8 matching tickers from yfinance Search.

**Response**: `[{"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NMS", "type": "Equity"}]`

### `GET /api/analyze` (SSE)
Launches the full pipeline and streams results.

**Query params**: `ticker`, `date`, `analysts` (comma-separated), `llm_provider`, `language`

**Response**: `text/event-stream`

#### SSE Event Protocol

| Event              | Data                                                            | Emitted when                       |
|--------------------|-----------------------------------------------------------------|------------------------------------|
| `pipeline-start`   | `{ticker, date, analysts, provider, model}`                     | Stream begins                      |
| `stage-start`      | `{stage, label}`                                                | A pipeline stage begins            |
| `tool-call`        | `{stage, tool_name, args}`                                      | Analyst invokes a tool             |
| `tool-result`      | `{stage, tool_name, result_preview}`                            | Tool returns (summary, not full)   |
| `analyst-report`   | `{stage, analyst, report, elapsed_ms}`                          | Analyst completes its report       |
| `debate-round`     | `{stage, side, round, content}`                                 | Bull/bear or risk debate round     |
| `debate-decision`  | `{stage, decision}`                                             | Research manager or risk judge     |
| `trader-proposal`  | `{proposal}`                                                    | Trader emits structured proposal   |
| `final-decision`   | `{decision, rating, summary, thesis, target_price, horizon}`    | Portfolio manager completes        |
| `pipeline-error`   | `{stage, message}`                                              | Error at any stage                 |
| `pipeline-done`    | `{total_elapsed_ms, token_stats}`                               | Pipeline complete                  |

Events are emitted as JSON, e.g.: `event: analyst-report\ndata: {"analyst":"market","report":"...","elapsed_ms":2300}\n\n`

## Frontend Layout (Split Panel)

```
┌────────────────────┬──────────────────────────────────┐
│ LEFT (35%)         │ RIGHT (65%)                       │
│                    │                                   │
│ [Stock Search ▾]   │  Market Analyst Report     2.3s   │
│ [AAPL - Apple Inc.]│  ─────────────────────────────   │
│ [Date Picker    ]  │  AAPL shows bullish momentum...   │
│ [Analyst Toggles]  │                                   │
│ [LLM Info       ]  │  Sentiment Analyst Report  1.8s   │
│                    │  ────────────────────────────────│
│ [Run Analysis]     │  Social sentiment 72% positive... │
│                    │                                   │
│ ── Progress ────── │  ◉ News Analyst (Running...)     │
│ ✓ Market    2.3s   │  Fetching articles...            │
│ ✓ Sentiment 1.8s   │                                   │
│ ◉ News      Run..  │  ◌ Research Debate (Waiting)     │
│ ◌ Research  Wait   │  ◌ Trader Proposal  (Waiting)    │
│ ◌ Trader    Wait   │  ◌ Risk + PM        (Waiting)    │
│ ◌ Risk+PM   Wait   │                                   │
└────────────────────┴──────────────────────────────────┘
```

### Left Panel Components

1. **Stock Search** — Text input with autocomplete dropdown. Debounced (300ms) call to `/api/search`. Selecting a ticker shows a confirmation card with company name and exchange. A clear button resets the selection.
2. **Date Picker** — Text input with YYYY-MM-DD validation.
3. **Analyst Toggles** — Checkbox-style buttons for Market/Sentiment/News/Fundamentals. All selected by default. Crypto mode auto-hides Fundamentals.
4. **LLM Info** — Read-only display of current provider and model from config.
5. **Run/Reset buttons** — Run starts the pipeline, Reset clears all results.
6. **Pipeline Progress** — Ordered list of stages. Each shows an icon (✓ done, ◉ active, ◌ pending), elapsed time, and a thin progress bar for the active stage.

### Right Panel Components

1. **Report Cards** — Each analyst report renders as a card with left-border color coding (market=green, sentiment=purple, news=gold, fundamentals=blue). Cards appear as they arrive via SSE, replacing placeholder slots.
2. **Debate View** — Bull/bear debate rounds shown as alternating chat-style messages with side labels. Risk debate shows three speakers.
3. **Trader Proposal Card** — Structured display: action (Buy/Sell/Hold), entry price, stop loss, position size.
4. **Final Decision Banner** — Prominent card at the bottom: rating (5-tier scale with colored badge), executive summary, investment thesis, optional price target and time horizon.

### Visual Design

- Dark theme (matches the CLI aesthetic).
- Color coding per agent: Market=#3fb950, Sentiment=#a371f7, News=#ffd700, Fundamentals=#53d8fb.
- Pipeline stage icons: ✓ (done, green), ◉ (active, gold with pulse animation), ◌ (pending, gray).
- The final decision rating gets a color badge: Buy=green, Overweight=teal, Hold=gold, Underweight=orange, Sell=red.

## Data Flow

```
1. User fills form → clicks "Run Analysis"
2. Frontend: GET /api/analyze?ticker=AAPL&date=2024-05-10&analysts=market,social,news,fundamentals&llm_provider=deepseek&language=English
3. Backend creates CapitalRadarGraph, calls graph.stream(state)
4. For each chunk from the stream:
   a. Detect what changed (new report? debate round? final decision?)
   b. Map to the appropriate SSE event type
   c. Yield via StreamingResponse
5. Frontend EventSource receives events, updates DOM:
   - stage-start → highlight stage in progress panel, start timer
   - analyst-report → populate report card, mark stage done
   - debate-round → append to debate view
   - final-decision → render decision banner, mark pipeline complete
   - pipeline-error → show error toast
6. On pipeline-done, Run button re-enables, total elapsed displayed
```

## File Structure (New Files)

```
web/                          # New top-level directory
  __init__.py
  app.py                      # FastAPI app, routes, SSE endpoint
  stream.py                   # SSE event emitter: translates graph.stream() → SSE events
  templates/
    index.html                # Jinja2 template for the dashboard
  static/
    style.css                 # Dark theme, layout, animations
    app.js                    # EventSource consumer, DOM updates, search autocomplete
```

No changes to existing `tradingagents/` code. The web layer imports `CapitalRadarGraph` and `DEFAULT_CONFIG` as a consumer, same as `main.py` and the CLI already do.

### New Dependencies

- `fastapi` + `uvicorn` — web server
- `jinja2` — template rendering (likely already installed as a FastAPI transitive dep)

## State Delta Detection (Stream → SSE)

`graph.stream(state, stream_mode="values")` returns complete state dicts after each node. The emitter diffs consecutive chunks to detect what changed:

| AgentState key                         | Detection rule                     | Emits                     |
|----------------------------------------|------------------------------------|---------------------------|
| `market_report`                        | was `""`, now populated            | `analyst-report` (market) |
| `sentiment_report`                     | was `""`, now populated            | `analyst-report` (social) |
| `news_report`                          | was `""`, now populated            | `analyst-report` (news)   |
| `fundamentals_report`                  | was `""`, now populated            | `analyst-report` (fund.)  |
| `investment_debate_state.history`      | grew (new round appended)          | `debate-round`            |
| `investment_debate_state.judge_decision` | was `""`, now populated          | `debate-decision`         |
| `trader_investment_plan`               | was not in state, now present      | `trader-proposal`         |
| `risk_debate_state.history`            | grew                               | `debate-round` (risk)     |
| `risk_debate_state.judge_decision`     | was `""`, now populated            | `debate-decision` (risk)  |
| `investment_plan`                      | was not in state, now present      | `debate-decision` (pm)    |
| `final_trade_decision`                 | was not in state, now present      | `final-decision`          |

Additionally, `stage-start` events are emitted when the emitter detects the pipeline has entered a new stage (Analyst → Research → Trader → Risk → PM) by tracking which fields have been populated so far.

## Error Handling

- **Stock search errors**: Show inline "No results found" or "Search unavailable" message inside the dropdown.
- **Pipeline errors**: Emit `pipeline-error` SSE event with stage and message. Frontend shows a red toast notification and marks the failing stage with ✗. The run button remains enabled for retry.
- **SSE connection drop**: Frontend `EventSource` auto-reconnects (built-in browser behavior). On reconnect, if the pipeline is still running, the backend resumes the stream from the current state.
- **Invalid input**: Validate ticker (non-empty) and date (YYYY-MM-DD format) before enabling the Run button. Show inline validation errors.

## Testing

- **Backend**: Unit tests for `/api/search` and SSE event generation. Mock `yfinance.Search` and `CapitalRadarGraph`.
- **Frontend**: Manual testing in browser for layout, SSE rendering, search UX. The visual nature of the dashboard makes automated frontend tests low-value for now.
- **Integration**: End-to-end test with a real `CapitalRadarGraph` instance running against a mock LLM to verify full SSE stream.

## Scope Boundaries

**In scope**:
- Single-ticker analysis dashboard with SSE streaming
- Stock search via yfinance
- Real-time progress tracking for all 5 pipeline stages
- Display of all analyst reports, debate rounds, trader proposal, final decision

**Out of scope** (future):
- Multi-ticker comparison
- Historical analysis log browser
- User authentication
- Backtest visualization
- Configuration editing UI (use .env file for now)
