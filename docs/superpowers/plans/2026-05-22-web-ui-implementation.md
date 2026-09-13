# Web UI for TradingAgents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interactive web dashboard (FastAPI + vanilla HTML/JS + SSE) that lets users search stocks, configure analysts, run the TradingAgents pipeline, and watch real-time agent progress with results streaming in.

**Architecture:** A FastAPI server imports the existing `CapitalRadarGraph`, calls `graph.stream()` to get per-node state chunks, diffs them to detect what changed, and emits typed SSE events to the browser. The frontend renders a split-panel dashboard: left=configuration+progress, right=streaming results.

**Tech Stack:** FastAPI + uvicorn, Jinja2 templates, vanilla HTML/CSS/JS, Server-Sent Events, yfinance (for stock search).

---

### Task 1: Install dependencies and create package structure

**Files:**
- Create: `web/__init__.py`
- Create: `web/app.py` (skeleton)
- Create: `web/stream.py` (skeleton)
- Create: `web/templates/index.html` (placeholder)
- Create: `web/static/style.css` (placeholder)
- Create: `web/static/app.js` (placeholder)

- [ ] **Step 1: Install fastapi and uvicorn**

Run: `pip install fastapi uvicorn`
Expected: Successfully installed

- [ ] **Step 2: Create directory structure**

Run: `mkdir web\static web\templates`

- [ ] **Step 3: Create web/__init__.py**

```python
"""TradingAgents Web Dashboard."""
```

- [ ] **Step 4: Create web/app.py skeleton**

```python
"""FastAPI application for the TradingAgents web dashboard."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="TradingAgents Dashboard")

_web_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_web_dir / "static")), name="static")
```

- [ ] **Step 5: Create web/stream.py skeleton**

```python
"""SSE streaming adapter for CapitalRadarGraph."""

import json
import time
from typing import Dict, Any


def format_sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

- [ ] **Step 6: Create placeholder files for templates and static assets**

Write: `web/templates/index.html`
```html
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>TradingAgents</title></head>
<body><h1>Loading...</h1></body>
</html>
```

Write: `web/static/style.css`
```css
/* placeholder */
```

Write: `web/static/app.js`
```javascript
// placeholder
```

- [ ] **Step 7: Commit**

```bash
git add web/ requirements.txt
git commit -m "feat: scaffold web package with FastAPI skeleton"
```

---

### Task 2: Stock search endpoint

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add the /api/search route to web/app.py**

Replace `web/app.py` with:

```python
"""FastAPI application for the TradingAgents web dashboard."""

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
import yfinance as yf

app = FastAPI(title="TradingAgents Dashboard")

_web_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_web_dir / "static")), name="static")

_TEMPLATE_DIR = _web_dir / "templates"


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the dashboard page."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
    template = env.get_template("index.html")
    return HTMLResponse(template.render())


@app.get("/api/search")
def search_stocks(q: str = Query(min_length=1, max_length=100)):
    """Search for stocks by ticker or company name."""
    try:
        results = yf.Search(query=q, news_count=0).quotes
    except Exception:
        return []

    items = []
    for r in (results or [])[:8]:
        symbol = r.get("symbol", "")
        if not symbol:
            continue
        items.append({
            "symbol": symbol,
            "name": r.get("shortname") or r.get("longname") or symbol,
            "exchange": r.get("exchange", ""),
            "type": r.get("quoteType", ""),
        })
    return items
```

- [ ] **Step 2: Verify it runs**

Run: `uvicorn web.app:app --host 127.0.0.1 --port 8000 --reload` (background)

Run: `curl "http://127.0.0.1:8000/api/search?q=apple"`

Expected: JSON array with entries like `{"symbol":"AAPL","name":"Apple Inc.","exchange":"NMS","type":"Equity"}`

- [ ] **Step 3: Commit**

```bash
git add web/app.py
git commit -m "feat: add stock search endpoint via yfinance"
```

---

### Task 3: SSE stream emitter

**Files:**
- Create: `web/stream.py` (full implementation)

- [ ] **Step 1: Write the full stream.py**

Replace `web/stream.py` with:

```python
"""SSE streaming adapter for CapitalRadarGraph.

Calls graph.stream() and diffs consecutive state chunks to detect
what changed, emitting typed SSE events for each detected delta.
"""

import json
import time
import logging
from typing import Dict, Any, AsyncGenerator, Optional
from collections.abc import Callable

from capitalradar.graph.trading_graph import CapitalRadarGraph
from capitalradar.default_config import DEFAULT_CONFIG
from capitalradar.agents.utils.agent_states import AgentState

logger = logging.getLogger(__name__)

# Pipeline stages in order
STAGES = [
    ("analysts", "Analyst Team"),
    ("research", "Research Debate"),
    ("trader", "Trader"),
    ("risk", "Risk Management"),
    ("portfolio", "Portfolio Manager"),
]

# Keys to check for each stage's completion
ANALYST_REPORT_KEYS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

ANALYST_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

_STAGE_COLORS = {
    "market": "#3fb950",
    "social": "#a371f7",
    "news": "#ffd700",
    "fundamentals": "#53d8fb",
}


def _elapsed_since(start: float) -> int:
    return int((time.time() - start) * 1000)


class StreamEmitter:
    """Wraps a CapitalRadarGraph run and emits SSE events."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or DEFAULT_CONFIG.copy()

    async def stream_analysis(
        self,
        ticker: str,
        date: str,
        analysts: list[str],
        provider: str = "",
        language: str = "English",
    ) -> AsyncGenerator[str, None]:
        """Run the analysis pipeline and yield SSE-formatted events."""
        pipeline_start = time.time()
        stage_times: Dict[str, float] = {}

        cfg = self.config.copy()
        if provider:
            cfg["llm_provider"] = provider
        cfg["output_language"] = language

        # Emit pipeline-start
        yield _event("pipeline-start", {
            "ticker": ticker,
            "date": date,
            "analysts": analysts,
            "provider": cfg.get("llm_provider", ""),
            "model": cfg.get("deep_think_llm", ""),
        })

        try:
            ta = CapitalRadarGraph(
                selected_analysts=analysts,
                debug=False,
                config=cfg,
            )
        except Exception as e:
            yield _event("pipeline-error", {"stage": "init", "message": str(e)})
            return

        # Build stream args from Propagator defaults
        args = {
            "stream_mode": "values",
            "config": {"recursion_limit": cfg.get("max_recur_limit", 100)},
        }

        past_context = ta.memory_log.get_past_context(ticker)
        init_state = ta.propagator.create_initial_state(
            ticker, date, asset_type="stock", past_context=past_context
        )

        current_stage_idx = -1
        seen_tool_calls: set[tuple] = set()

        try:
            prev_state: Dict[str, Any] = {}
            for chunk in ta.graph.stream(init_state, **args):
                # Detect stage transitions
                new_stage_idx = _detect_stage(chunk)
                if new_stage_idx > current_stage_idx:
                    for si in range(current_stage_idx + 1, new_stage_idx + 1):
                        stage_key, stage_label = STAGES[si]
                        stage_times[stage_key] = time.time()
                        yield _event("stage-start", {
                            "stage": stage_key,
                            "label": stage_label,
                            "elapsed": _elapsed_since(pipeline_start),
                        })
                    current_stage_idx = new_stage_idx

                # Detect analyst reports
                for analyst_key, state_key in ANALYST_REPORT_KEYS.items():
                    prev_val = prev_state.get(state_key, "")
                    curr_val = chunk.get(state_key, "")
                    if prev_val == "" and curr_val != "":
                        elapsed = _elapsed_since(stage_times.get("analysts", pipeline_start))
                        yield _event("analyst-report", {
                            "analyst": analyst_key,
                            "name": ANALYST_NAMES[analyst_key],
                            "report": str(curr_val)[:5000],
                            "elapsed_ms": elapsed,
                            "color": _STAGE_COLORS.get(analyst_key, "#888"),
                        })

                # Detect debate rounds
                deb_state = chunk.get("investment_debate_state", {})
                prev_deb = prev_state.get("investment_debate_state", {})
                if isinstance(deb_state, dict):
                    history = deb_state.get("history", "")
                    prev_history = prev_deb.get("history", "") if isinstance(prev_deb, dict) else ""
                    if len(history) > len(prev_history):
                        new_content = history[len(prev_history):].strip()
                        # Determine side from relation to bull/bear history
                        bull_h = deb_state.get("bull_history", "")
                        bear_h = deb_state.get("bear_history", "")
                        side = ""
                        if new_content in bull_h:
                            side = "bull"
                        elif new_content in bear_h:
                            side = "bear"
                        round_num = deb_state.get("count", 0)
                        yield _event("debate-round", {
                            "stage": "research",
                            "side": side,
                            "round": round_num,
                            "content": new_content[:3000],
                        })

                    judge = deb_state.get("judge_decision", "")
                    prev_judge = prev_deb.get("judge_decision", "") if isinstance(prev_deb, dict) else ""
                    if prev_judge == "" and judge != "":
                        yield _event("debate-decision", {
                            "stage": "research",
                            "decision": str(judge)[:3000],
                        })

                # Detect trader proposal
                trader = chunk.get("trader_investment_plan")
                prev_trader = prev_state.get("trader_investment_plan")
                if prev_trader is None and trader is not None:
                    yield _event("trader-proposal", {
                        "proposal": str(trader)[:3000],
                    })

                # Detect risk debate
                risk_state = chunk.get("risk_debate_state", {})
                prev_risk = prev_state.get("risk_debate_state", {})
                if isinstance(risk_state, dict):
                    risk_history = risk_state.get("history", "")
                    prev_risk_history = prev_risk.get("history", "") if isinstance(prev_risk, dict) else ""
                    if len(risk_history) > len(prev_risk_history):
                        new_content = risk_history[len(prev_risk_history):].strip()
                        agg_h = risk_state.get("aggressive_history", "")
                        con_h = risk_state.get("conservative_history", "")
                        side = ""
                        if new_content in agg_h:
                            side = "aggressive"
                        elif new_content in con_h:
                            side = "conservative"
                        else:
                            side = "neutral"
                        yield _event("debate-round", {
                            "stage": "risk",
                            "side": side,
                            "round": risk_state.get("count", 0),
                            "content": new_content[:3000],
                        })

                    risk_judge = risk_state.get("judge_decision", "")
                    prev_risk_judge = prev_risk.get("judge_decision", "") if isinstance(prev_risk, dict) else ""
                    if prev_risk_judge == "" and risk_judge != "":
                        yield _event("debate-decision", {
                            "stage": "risk",
                            "decision": str(risk_judge)[:3000],
                        })

                # Detect investment plan (PM output)
                inv_plan = chunk.get("investment_plan")
                prev_inv = prev_state.get("investment_plan")
                if prev_inv is None and inv_plan is not None:
                    yield _event("debate-decision", {
                        "stage": "portfolio",
                        "decision": str(inv_plan)[:3000],
                    })

                # Detect final decision
                final = chunk.get("final_trade_decision")
                prev_final = prev_state.get("final_trade_decision")
                if prev_final is None and final is not None:
                    signal_processor = ta.signal_processor
                    rating = signal_processor.process_signal(str(final))
                    yield _event("final-decision", {
                        "decision": str(final)[:5000],
                        "rating": str(rating),
                    })

                prev_state = chunk

        except Exception as e:
            logger.exception("Pipeline error")
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": str(e),
            })

        total_elapsed = _elapsed_since(pipeline_start)
        yield _event("pipeline-done", {
            "total_elapsed_ms": total_elapsed,
        })


def _detect_stage(state: Dict[str, Any]) -> int:
    """Return the index of the furthest-reached pipeline stage (0-4)."""
    has_analyst_report = any(
        state.get(k) for k in ANALYST_REPORT_KEYS.values()
    )
    if not has_analyst_report:
        return -1

    deb_state = state.get("investment_debate_state", {})
    has_debate_decision = bool(
        isinstance(deb_state, dict) and deb_state.get("judge_decision")
    )
    if not has_debate_decision:
        return 0  # analysts stage

    has_trader = state.get("trader_investment_plan") is not None
    if not has_trader:
        return 1  # research stage

    risk_state = state.get("risk_debate_state", {})
    has_risk_decision = bool(
        isinstance(risk_state, dict) and risk_state.get("judge_decision")
    )
    if not has_risk_decision:
        return 2  # trader stage

    has_final = state.get("final_trade_decision") is not None
    if not has_final:
        return 3  # risk stage

    return 4  # portfolio manager / complete


def _event(name: str, data: Any) -> str:
    """Format an SSE event string."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

- [ ] **Step 2: Commit**

```bash
git add web/stream.py
git commit -m "feat: add SSE stream emitter with state delta detection"
```

---

### Task 4: FastAPI SSE endpoint and main page wiring

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add the /api/analyze SSE endpoint and /api/config endpoint to web/app.py**

Replace `web/app.py` with:

```python
"""FastAPI application for the TradingAgents web dashboard."""

from fastapi import FastAPI, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
import yfinance as yf

from capitalradar.default_config import DEFAULT_CONFIG

app = FastAPI(title="TradingAgents Dashboard")

_web_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_web_dir / "static")), name="static")

_TEMPLATE_DIR = _web_dir / "templates"


def _render_template(name: str, **context) -> str:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
    template = env.get_template(name)
    return template.render(**context)


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the dashboard page."""
    cfg = DEFAULT_CONFIG
    return HTMLResponse(_render_template("index.html",
        llm_provider=cfg.get("llm_provider", ""),
        deep_model=cfg.get("deep_think_llm", ""),
        quick_model=cfg.get("quick_think_llm", ""),
        language=cfg.get("output_language", "English"),
    ))


@app.get("/api/config")
def get_config():
    """Return current LLM config for the UI."""
    cfg = DEFAULT_CONFIG
    return {
        "llm_provider": cfg.get("llm_provider", ""),
        "deep_think_llm": cfg.get("deep_think_llm", ""),
        "quick_think_llm": cfg.get("quick_think_llm", ""),
        "output_language": cfg.get("output_language", "English"),
        "max_debate_rounds": cfg.get("max_debate_rounds", 1),
        "max_risk_discuss_rounds": cfg.get("max_risk_discuss_rounds", 1),
    }


@app.get("/api/search")
def search_stocks(q: str = Query(min_length=1, max_length=100)):
    """Search for stocks by ticker or company name."""
    try:
        results = yf.Search(query=q, news_count=0).quotes
    except Exception:
        return []

    items = []
    for r in (results or [])[:8]:
        symbol = r.get("symbol", "")
        if not symbol:
            continue
        items.append({
            "symbol": symbol,
            "name": r.get("shortname") or r.get("longname") or symbol,
            "exchange": r.get("exchange", ""),
            "type": r.get("quoteType", ""),
        })
    return items


@app.get("/api/analyze")
async def analyze(
    ticker: str = Query(min_length=1),
    date: str = Query(min_length=10, max_length=10),
    analysts: str = Query(default="market,social,news,fundamentals"),
    provider: str = Query(default=""),
    language: str = Query(default="English"),
):
    """Run the analysis pipeline and stream results via SSE."""
    analyst_list = [a.strip() for a in analysts.split(",") if a.strip()]

    from web.stream import StreamEmitter
    emitter = StreamEmitter()

    async def event_stream():
        async for event in emitter.stream_analysis(
            ticker=ticker,
            date=date,
            analysts=analyst_list,
            provider=provider,
            language=language,
        ):
            yield event

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

- [ ] **Step 2: Commit**

```bash
git add web/app.py
git commit -m "feat: add SSE analyze endpoint and config endpoint"
```

---

### Task 5: Dashboard HTML template

**Files:**
- Modify: `web/templates/index.html` (full implementation)
- Modify: `web/static/style.css` (full implementation)

- [ ] **Step 1: Write the index.html template**

Replace `web/templates/index.html` with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TradingAgents Dashboard</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div id="app">
  <!-- Left Panel -->
  <aside id="left-panel">
    <h1 class="logo">TradingAgents</h1>

    <!-- Stock Search -->
    <div class="section">
      <label class="label">Stock Ticker</label>
      <div class="search-container">
        <input type="text" id="ticker-input" placeholder="Search by symbol or name..."
               autocomplete="off" />
        <div id="search-dropdown" class="dropdown hidden"></div>
      </div>
      <div id="selected-ticker" class="selected-card hidden">
        <span id="selected-symbol"></span>
        <span id="selected-name"></span>
        <span id="selected-exchange" class="badge"></span>
        <button id="clear-ticker" class="clear-btn">&times;</button>
      </div>
    </div>

    <!-- Date -->
    <div class="section">
      <label class="label" for="date-input">Analysis Date</label>
      <input type="text" id="date-input" placeholder="YYYY-MM-DD" />
      <span id="date-error" class="error hidden">Invalid date format</span>
    </div>

    <!-- Analysts -->
    <div class="section">
      <label class="label">Analysts</label>
      <div id="analyst-toggles" class="toggle-group">
        <button class="toggle active" data-analyst="market">Market</button>
        <button class="toggle active" data-analyst="social">Sentiment</button>
        <button class="toggle active" data-analyst="news">News</button>
        <button class="toggle active" data-analyst="fundamentals">Fundamentals</button>
      </div>
    </div>

    <!-- LLM Info -->
    <div class="section">
      <label class="label">LLM Configuration</label>
      <div id="llm-info" class="info-text">
        {{ llm_provider }} / {{ deep_model }}
      </div>
      <div class="info-text small">Language: {{ language }}</div>
    </div>

    <!-- Buttons -->
    <div class="section">
      <button id="run-btn" class="btn primary" disabled>Run Analysis</button>
      <button id="reset-btn" class="btn secondary">Reset</button>
    </div>

    <!-- Pipeline Progress -->
    <div class="section">
      <label class="label">Pipeline Progress</label>
      <div id="progress-list">
        <div class="progress-item" data-stage="analysts">
          <span class="stage-icon">◌</span>
          <span class="stage-label">Analyst Team</span>
          <span class="stage-time"></span>
        </div>
        <div class="progress-item" data-stage="research">
          <span class="stage-icon">◌</span>
          <span class="stage-label">Research Debate</span>
          <span class="stage-time"></span>
        </div>
        <div class="progress-item" data-stage="trader">
          <span class="stage-icon">◌</span>
          <span class="stage-label">Trader</span>
          <span class="stage-time"></span>
        </div>
        <div class="progress-item" data-stage="risk">
          <span class="stage-icon">◌</span>
          <span class="stage-label">Risk Management</span>
          <span class="stage-time"></span>
        </div>
        <div class="progress-item" data-stage="portfolio">
          <span class="stage-icon">◌</span>
          <span class="stage-label">Portfolio Manager</span>
          <span class="stage-time"></span>
        </div>
      </div>
    </div>
  </aside>

  <!-- Right Panel -->
  <main id="right-panel">
    <div id="results-container">
      <div id="placeholder-hint" class="placeholder-hint">
        Configure and run an analysis to see results here.
      </div>
    </div>

    <!-- Toast -->
    <div id="toast" class="toast hidden"></div>
  </main>
</div>

<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write style.css**

Replace `web/static/style.css` with:

```css
:root {
  --bg: #0d1117;
  --panel-bg: #161b22;
  --border: #30363d;
  --text: #c9d1d9;
  --text-muted: #8b949e;
  --accent: #53d8fb;
  --green: #3fb950;
  --purple: #a371f7;
  --gold: #ffd700;
  --red: #f85149;
  --blue: #53d8fb;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  overflow: hidden;
}

#app { display: flex; height: 100vh; }

/* Left Panel */
#left-panel {
  width: 380px;
  min-width: 380px;
  background: var(--panel-bg);
  border-right: 1px solid var(--border);
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.logo {
  font-size: 18px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: -0.5px;
}

.section { display: flex; flex-direction: column; gap: 6px; }
.label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  font-weight: 600;
}

/* Search */
.search-container { position: relative; }
#ticker-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  outline: none;
}
#ticker-input:focus { border-color: var(--accent); }

.dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: var(--panel-bg);
  border: 1px solid var(--border);
  border-radius: 0 0 6px 6px;
  max-height: 280px;
  overflow-y: auto;
  z-index: 100;
}
.dropdown.hidden { display: none; }
.dropdown-item {
  padding: 8px 12px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  border-bottom: 1px solid var(--border);
}
.dropdown-item:hover, .dropdown-item.active { background: #1f2937; }
.dropdown-item .sym { color: var(--accent); font-weight: 600; }
.dropdown-item .name { color: var(--text); flex: 1; margin: 0 10px; }
.dropdown-item .exch { color: var(--text-muted); font-size: 11px; }
.dropdown-item .no-results { color: var(--text-muted); font-style: italic; }

.selected-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #0d3b66;
  border: 1px solid #1f6feb;
  border-radius: 6px;
  font-size: 13px;
}
.selected-card.hidden { display: none; }
.selected-card .badge { font-size: 10px; background: #1f6feb33; padding: 2px 6px; border-radius: 4px; color: var(--accent); }
.clear-btn { margin-left: auto; background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; }

/* Date */
#date-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  outline: none;
}
#date-input:focus { border-color: var(--accent); }
#date-input.invalid { border-color: var(--red); }
.error { color: var(--red); font-size: 11px; }
.error.hidden { display: none; }

/* Toggles */
.toggle-group { display: flex; gap: 4px; flex-wrap: wrap; }
.toggle {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.toggle.active {
  color: var(--accent);
  border-color: var(--accent);
  background: #1f6feb22;
}
.toggle:hover { border-color: var(--text-muted); }

/* LLM Info */
.info-text { font-size: 13px; color: var(--text); }
.info-text.small { font-size: 11px; color: var(--text-muted); }

/* Buttons */
.btn {
  padding: 10px 20px;
  border-radius: 6px;
  border: none;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn.primary { background: #238636; color: white; }
.btn.primary:hover:not(:disabled) { background: #2ea043; }
.btn.secondary { background: var(--border); color: var(--text); margin-left: 8px; }
.btn.secondary:hover { background: #484f58; }

/* Progress */
#progress-list { display: flex; flex-direction: column; gap: 4px; }
.progress-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 4px 0;
}
.progress-item .stage-icon {
  width: 16px;
  text-align: center;
  font-size: 14px;
}
.progress-item .stage-icon.done { color: var(--green); }
.progress-item .stage-icon.active { color: var(--gold); animation: pulse 1s ease-in-out infinite; }
.progress-item .stage-icon.error { color: var(--red); }
.progress-item .stage-label { flex: 1; }
.progress-item .stage-time { color: var(--text-muted); font-size: 11px; }
.progress-item.dimmed { opacity: 0.4; }
.progress-item .progress-bar {
  width: 100%;
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  margin-top: 2px;
}
.progress-item .progress-bar-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--gold);
  transition: width 0.3s;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* Right Panel */
#right-panel {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

#results-container {
  max-width: 900px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.placeholder-hint {
  text-align: center;
  color: var(--text-muted);
  margin-top: 100px;
  font-size: 15px;
}

/* Result Cards */
.result-card {
  background: var(--panel-bg);
  border-radius: 8px;
  border-left: 3px solid var(--text-muted);
  padding: 14px 18px;
  animation: slideIn 0.3s ease;
}

.result-card .card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.result-card .card-title { font-size: 14px; font-weight: 600; }
.result-card .card-time { font-size: 11px; color: var(--text-muted); }
.result-card .card-body {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  max-height: 400px;
  overflow-y: auto;
}

.result-card.placeholder-card { opacity: 0.25; }
.result-card.placeholder-card .card-body { font-style: italic; color: var(--text-muted); }

@keyframes slideIn {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Debate */
.debate-round {
  padding: 10px 14px;
  margin: 6px 0;
  border-radius: 6px;
  font-size: 13px;
}
.debate-round.bull { background: #3fb95011; border-left: 3px solid var(--green); }
.debate-round.bear { background: #f8514911; border-left: 3px solid var(--red); }
.debate-round.aggressive { background: #ffd70011; border-left: 3px solid var(--gold); }
.debate-round.conservative { background: #53d8fb11; border-left: 3px solid var(--blue); }
.debate-round.neutral { background: #a371f711; border-left: 3px solid var(--purple); }
.debate-round .round-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }

/* Final Decision */
.final-decision {
  background: linear-gradient(135deg, #1a2332, #0d3b66);
  border: 1px solid #1f6feb;
  border-radius: 10px;
  padding: 20px 24px;
}
.final-decision .rating-badge {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: 700;
}
.rating-badge.buy { background: #3fb95033; color: var(--green); }
.rating-badge.overweight { background: #53d8fb33; color: var(--blue); }
.rating-badge.hold { background: #ffd70033; color: var(--gold); }
.rating-badge.underweight { background: #f0883e33; color: #f0883e; }
.rating-badge.sell { background: #f8514933; color: var(--red); }

/* Toast */
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: #f8514922;
  border: 1px solid var(--red);
  color: var(--red);
  padding: 12px 20px;
  border-radius: 8px;
  font-size: 13px;
  max-width: 400px;
  z-index: 200;
  animation: slideIn 0.3s ease;
}
.toast.hidden { display: none; }
```

- [ ] **Step 3: Commit**

```bash
git add web/templates/index.html web/static/style.css
git commit -m "feat: add dashboard HTML template and dark theme CSS"
```

---

### Task 6: Frontend JavaScript

**Files:**
- Modify: `web/static/app.js` (full implementation)

- [ ] **Step 1: Write app.js**

Replace `web/static/app.js` with:

```javascript
// --- State ---
let selectedTicker = null;
let activeStage = null;
let eventSource = null;
let pipelineStart = 0;

// --- DOM refs ---
const tickerInput = document.getElementById("ticker-input");
const searchDropdown = document.getElementById("search-dropdown");
const selectedCard = document.getElementById("selected-ticker");
const selectedSymbol = document.getElementById("selected-symbol");
const selectedName = document.getElementById("selected-name");
const selectedExchange = document.getElementById("selected-exchange");
const clearTickerBtn = document.getElementById("clear-ticker");
const dateInput = document.getElementById("date-input");
const dateError = document.getElementById("date-error");
const analystToggles = document.getElementById("analyst-toggles");
const runBtn = document.getElementById("run-btn");
const resetBtn = document.getElementById("reset-btn");
const resultsContainer = document.getElementById("results-container");
const placeholderHint = document.getElementById("placeholder-hint");
const progressList = document.getElementById("progress-list");
const toast = document.getElementById("toast");

// --- Stock Search ---
let searchTimer = null;
tickerInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = tickerInput.value.trim();
  if (q.length < 1) { searchDropdown.classList.add("hidden"); return; }
  searchTimer = setTimeout(() => searchStocks(q), 300);
});

async function searchStocks(q) {
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const results = await resp.json();
    renderSearchResults(results);
  } catch {
    searchDropdown.innerHTML = '<div class="dropdown-item"><span class="no-results">Search unavailable</span></div>';
    searchDropdown.classList.remove("hidden");
  }
}

function renderSearchResults(items) {
  if (!items.length) {
    searchDropdown.innerHTML = '<div class="dropdown-item"><span class="no-results">No results found</span></div>';
  } else {
    searchDropdown.innerHTML = items.map((r, i) =>
      `<div class="dropdown-item" data-idx="${i}">
        <span class="sym">${esc(r.symbol)}</span>
        <span class="name">${esc(r.name)}</span>
        <span class="exch">${esc(r.exchange)}</span>
      </div>`
    ).join("");
    searchDropdown.querySelectorAll(".dropdown-item[data-idx]").forEach(el => {
      el.addEventListener("click", () => selectTicker(items[parseInt(el.dataset.idx)]));
    });
  }
  searchDropdown.classList.remove("hidden");
}

function selectTicker(item) {
  selectedTicker = item;
  tickerInput.value = "";
  searchDropdown.classList.add("hidden");
  selectedSymbol.textContent = item.symbol;
  selectedName.textContent = item.name;
  selectedExchange.textContent = item.exchange;
  selectedCard.classList.remove("hidden");
  validateForm();
}

clearTickerBtn.addEventListener("click", () => {
  selectedTicker = null;
  selectedCard.classList.add("hidden");
  validateForm();
});

document.addEventListener("click", (e) => {
  if (!searchDropdown.contains(e.target) && e.target !== tickerInput) {
    searchDropdown.classList.add("hidden");
  }
});

// --- Analyst Toggles ---
analystToggles.querySelectorAll(".toggle").forEach(btn => {
  btn.addEventListener("click", () => {
    const toggles = analystToggles.querySelectorAll(".toggle.active");
    if (toggles.length === 1 && btn.classList.contains("active")) return;
    btn.classList.toggle("active");
  });
});

function getSelectedAnalysts() {
  return [...analystToggles.querySelectorAll(".toggle.active")]
    .map(b => b.dataset.analyst);
}

// --- Date Validation ---
dateInput.addEventListener("input", validateForm);

function validateForm() {
  const valid = selectedTicker && /^\d{4}-\d{2}-\d{2}$/.test(dateInput.value.trim());
  runBtn.disabled = !valid;
  if (dateInput.value && !/^\d{4}-\d{2}-\d{2}$/.test(dateInput.value.trim())) {
    dateInput.classList.add("invalid");
    dateError.classList.remove("hidden");
  } else {
    dateInput.classList.remove("invalid");
    dateError.classList.add("hidden");
  }
}

// --- Run Analysis ---
runBtn.addEventListener("click", startAnalysis);
resetBtn.addEventListener("click", resetAll);

function startAnalysis() {
  if (!selectedTicker) return;
  const date = dateInput.value.trim();
  const analysts = getSelectedAnalysts().join(",");
  const params = new URLSearchParams({ ticker: selectedTicker.symbol, date, analysts });
  const url = `/api/analyze?${params.toString()}`;

  runBtn.disabled = true;
  resetProgress();
  clearResults();
  pipelineStart = Date.now();

  eventSource = new EventSource(url);

  eventSource.addEventListener("pipeline-start", (e) => {
    const d = JSON.parse(e.data);
    addResultCard("system", `Analyzing ${d.ticker} on ${d.date} with ${d.provider}/${d.model}`, "");
  });

  eventSource.addEventListener("stage-start", (e) => {
    const d = JSON.parse(e.data);
    activateStage(d.stage, d.elapsed);
  });

  eventSource.addEventListener("analyst-report", (e) => {
    const d = JSON.parse(e.data);
    addResultCard(d.analyst, d.report, d.elapsed_ms, d.color);
    completeStage("analysts", d.elapsed_ms);
  });

  eventSource.addEventListener("debate-round", (e) => {
    const d = JSON.parse(e.data);
    addDebateRound(d.stage, d.side, d.round, d.content);
  });

  eventSource.addEventListener("debate-decision", (e) => {
    const d = JSON.parse(e.data);
    addResultCard("decision-" + d.stage, d.decision, null, "#888");
  });

  eventSource.addEventListener("trader-proposal", (e) => {
    const d = JSON.parse(e.data);
    addResultCard("trader", d.proposal, null, "#f0883e");
  });

  eventSource.addEventListener("final-decision", (e) => {
    const d = JSON.parse(e.data);
    addFinalDecision(d.decision, d.rating);
  });

  eventSource.addEventListener("pipeline-done", (e) => {
    const d = JSON.parse(e.data);
    completeAllStages(d.total_elapsed_ms);
    runBtn.disabled = false;
    eventSource.close();
    eventSource = null;
  });

  eventSource.addEventListener("pipeline-error", (e) => {
    const d = JSON.parse(e.data);
    showToast(`Error at ${d.stage}: ${d.message}`);
    markStageError(d.stage);
    runBtn.disabled = false;
    if (eventSource) { eventSource.close(); eventSource = null; }
  });

  eventSource.onerror = () => {
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      runBtn.disabled = false;
    }
  };
}

// --- Progress Helpers ---
function resetProgress() {
  activeStage = null;
  progressList.querySelectorAll(".progress-item").forEach(item => {
    item.classList.add("dimmed");
    item.querySelector(".stage-icon").textContent = "◌";
    item.querySelector(".stage-icon").className = "stage-icon";
    item.querySelector(".stage-time").textContent = "";
    const bar = item.querySelector(".progress-bar");
    if (bar) bar.remove();
  });
}

function activateStage(stageKey, elapsed) {
  const item = progressList.querySelector(`[data-stage="${stageKey}"]`);
  if (!item) return;
  item.classList.remove("dimmed");
  item.querySelector(".stage-icon").textContent = "◉";
  item.querySelector(".stage-icon").className = "stage-icon active";
  item.querySelector(".stage-time").textContent = fmtMs(elapsed);
  if (!item.querySelector(".progress-bar")) {
    const bar = document.createElement("div");
    bar.className = "progress-bar";
    bar.innerHTML = '<div class="progress-bar-fill" style="width:0%"></div>';
    item.appendChild(bar);
  }
  activeStage = stageKey;
  // Simulate progress bar animation
  let w = 10;
  const iv = setInterval(() => {
    const fill = item.querySelector(".progress-bar-fill");
    if (fill && activeStage === stageKey && w < 90) { w += 5; fill.style.width = w + "%"; }
    else clearInterval(iv);
  }, 400);
}

function completeStage(stageKey, elapsed) {
  const item = progressList.querySelector(`[data-stage="${stageKey}"]`);
  if (!item) return;
  item.querySelector(".stage-icon").textContent = "✓";
  item.querySelector(".stage-icon").className = "stage-icon done";
  item.querySelector(".stage-time").textContent = fmtMs(elapsed);
  const fill = item.querySelector(".progress-bar-fill");
  if (fill) fill.style.width = "100%";
}

function completeAllStages(totalElapsed) {
  progressList.querySelectorAll(".progress-item").forEach(item => {
    if (item.querySelector(".stage-icon").textContent === "◌") {
      item.querySelector(".stage-icon").textContent = "✓";
      item.querySelector(".stage-icon").className = "stage-icon done";
    }
    item.querySelector(".stage-time").textContent = fmtMs(totalElapsed);
    item.classList.remove("dimmed");
  });
  const fill = document.querySelector(`[data-stage="${activeStage}"] .progress-bar-fill`);
  if (fill) fill.style.width = "100%";
}

function markStageError(stageKey) {
  const item = progressList.querySelector(`[data-stage="${stageKey}"]`);
  if (!item) return;
  item.querySelector(".stage-icon").textContent = "✗";
  item.querySelector(".stage-icon").className = "stage-icon error";
}

// --- Results Helpers ---
function clearResults() {
  resultsContainer.innerHTML = "";
}

function addResultCard(id, content, elapsedMs, color) {
  if (placeholderHint) placeholderHint.remove();
  // Remove placeholder for this id if exists
  const existing = document.getElementById("card-" + id);
  if (existing) existing.remove();
  const elapsedHtml = elapsedMs != null ? `<span class="card-time">${fmtMs(elapsedMs)}</span>` : "";
  const card = document.createElement("div");
  card.id = "card-" + id;
  card.className = "result-card";
  card.style.borderLeftColor = color || "#888";
  card.innerHTML = `<div class="card-header"><span class="card-title">${idLabel(id)}</span>${elapsedHtml}</div>
    <div class="card-body">${esc(content)}</div>`;
  resultsContainer.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function addDebateRound(stage, side, round, content) {
  if (placeholderHint) placeholderHint.remove();
  const el = document.createElement("div");
  el.className = `debate-round ${side}`;
  el.innerHTML = `<div class="round-label">${side} &middot; Round ${round}</div>
    <div>${esc(content)}</div>`;
  resultsContainer.appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function addFinalDecision(content, rating) {
  if (placeholderHint) placeholderHint.remove();
  const ratingClass = (rating || "hold").toLowerCase();
  const el = document.createElement("div");
  el.className = "final-decision";
  el.innerHTML = `<span class="rating-badge ${ratingClass}">${rating || "N/A"}</span>
    <div class="card-body" style="margin-top:12px">${esc(content)}</div>`;
  resultsContainer.appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 6000);
}

function resetAll() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  clearResults();
  resetProgress();
  resultsContainer.innerHTML = '<div id="placeholder-hint" class="placeholder-hint">Configure and run an analysis to see results here.</div>';
  runBtn.disabled = !selectedTicker || !/^\d{4}-\d{2}-\d{2}$/.test(dateInput.value.trim());
}

// --- Utils ---
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmtMs(ms) { return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms"; }

function idLabel(id) {
  const labels = {
    market: "Market Analyst Report",
    social: "Sentiment Analyst Report",
    news: "News Analyst Report",
    fundamentals: "Fundamentals Analyst Report",
    trader: "Trader Proposal",
    "decision-research": "Research Manager Decision",
    "decision-risk": "Risk Management Decision",
    "decision-portfolio": "Portfolio Manager Decision",
    system: "System",
  };
  return labels[id] || id;
}
```

- [ ] **Step 2: Commit**

```bash
git add web/static/app.js
git commit -m "feat: add frontend JS for SSE consumption, search, and rendering"
```

---

### Task 7: Verify end-to-end

- [ ] **Step 1: Start the server**

Run: `uvicorn web.app:app --host 127.0.0.1 --port 8000`

- [ ] **Step 2: Test stock search**

Open browser at `http://127.0.0.1:8000`, type "Apple" in the search box.
Expected: Dropdown appears with AAPL and other matching tickers.

- [ ] **Step 3: Test the analysis pipeline**

Select AAPL, set date to "2024-05-10", keep all analysts selected, click "Run Analysis".
Expected: Progress indicators update in real-time, report cards appear in right panel with SSE streaming, final decision banner appears at end.

- [ ] **Step 4: Test error handling**

Enter date "bad-date", click Run.
Expected: Button stays disabled, inline validation error shown.

- [ ] **Step 5: Commit any final fixes**

```bash
git add -A && git commit -m "chore: final adjustments from e2e verification"
```

---

### Task 8: Add a startup script

**Files:**
- Create: `run_web.py` (convenience script at project root)

- [ ] **Step 1: Create run_web.py**

Write: `run_web.py`

```python
"""Quick-start script for the TradingAgents web dashboard."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=True)
```

- [ ] **Step 2: Commit**

```bash
git add run_web.py
git commit -m "feat: add run_web.py convenience launcher"
```
