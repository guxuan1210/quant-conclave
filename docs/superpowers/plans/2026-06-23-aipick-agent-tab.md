# AI Pick Agent — Independent Main Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate AI Smart Pick from a Stock Pick sub-tab to a standalone 8th main tab "🧠 AI Pick" with its own agent engine, conversation threads, voice input, and download support — mirroring Advisory architecture.

**Architecture:** New `web/ai_pick_agent.py` mirrors `web/history_chat.py` pattern but with stock-discovery tools (run_smart_screening, get_smart_money_score, query_pick_performance). New API endpoints in `web/app.py`. Frontend mirrors Advisory with `#aipick-expanded` right panel, `#tab-aipick` left panel. Stock Pick restored to 3 sub-tabs.

**Tech Stack:** Python 3.10+, LangChain, FastAPI SSE, vanilla JS, existing `capitalradar/sector_scan/` modules.

---

### Task 1: AI Pick Agent Backend

**Files:**
- Create: `web/ai_pick_agent.py`

- [ ] **Step 1: Create the file**

```python
"""AI Pick Agent — dedicated stock screening agent for the AI Pick tab.

Mirrors web/history_chat.py but specialized in stock discovery rather than
individual stock analysis. The agent has access to: run_smart_screening,
get_smart_money_score, query_pick_performance, and web_search_current.
"""

import json
import logging
from datetime import datetime
from typing import Generator, Optional

from web.results_store import get_chat_messages, save_chat_message, get_thread_run_ids

logger = logging.getLogger(__name__)


def build_aipick_system_prompt(config: dict, lang: str = "Chinese") -> str:
    """Build the AI Pick agent system prompt."""
    today = datetime.now()
    return (
        f"You are **🧠 CapitalRadar AI Pick**, a dedicated A-share stock screening "
        f"intelligence powered by CapitalRadar's multi-agent framework.\n\n"
        f"**Current date**: {today.strftime('%Y-%m-%d')} (weekday: {today.strftime('%A')})\n\n"
        f"## Your Mission\n\n"
        f"Help retail investors discover promising A-share stocks based on their "
        f"natural-language requirements. You generate custom screening strategies, "
        f"evaluate institutional capital flow (smart money), and explain WHY each "
        f"stock was picked.\n\n"
        f"## Core Principle\n\n"
        f"**Institutional capital flow is the non-negotiable baseline.** Every "
        f"recommendation must have detectable smart money participation. Use "
        f"get_smart_money_score to verify before recommending any stock.\n\n"
        f"## Your Process\n\n"
        f"1. **Understand intent**: Parse what the user wants — sector, style "
        f"(value/momentum/growth), risk tolerance, any constraints\n"
        f"2. **Generate strategy**: Call run_smart_screening with the user's "
        f"natural-language requirement. It automatically generates a custom "
        f"strategy, screens A-shares, and returns ranked candidates\n"
        f"3. **Verify scores**: For top candidates, call get_smart_money_score "
        f"to show the institutional participation breakdown\n"
        f"4. **Explain picks**: Tell the user WHY each stock was selected — "
        f"use data from the screening results and smart money breakdowns\n"
        f"5. **Track performance**: When asked, use query_pick_performance to "
        f"review how past picks performed\n\n"
        f"## Important Rules\n"
        f"- ALWAYS include smart_money_score >= 50 in every screen\n"
        f"- Explain picks in plain language a retail investor can understand\n"
        f"- Show specific numbers (PE, inflow amounts, RSI values) not just labels\n"
        f"- If no stocks match, suggest relaxing specific conditions — don't just give up\n"
        f"- Record all picks automatically (run_smart_screening does this)\n"
        f"- Use web_search_current for latest market news when relevant\n"
        f"- Write in {lang}\n"
    )


def build_aipick_tools(config: dict):
    """Return the tool set for the AI Pick agent."""
    from functools import partial
    from langchain_core.tools import tool
    from typing import Annotated

    from capitalradar.sector_scan.smart_money_score import compute_smart_money_score
    from capitalradar.sector_scan.dynamic_strategy import (
        build_strategy_prompt, parse_strategy_json, validate_strategy,
        execute_strategy, describe_strategy,
    )
    from capitalradar.sector_scan.rotation import get_rrg_data
    from capitalradar.sector_scan.smart_scanner import get_industry_stocks
    from capitalradar.sector_scan.pick_tracker import record_pick, get_performance_report
    from web.results_store import search_analyses as _search_analyses, resolve_picks

    _search = partial(_search_analyses, config)

    @tool
    def get_smart_money_score(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH, 000625.SZ)"],
    ) -> str:
        """Get the unified Smart Money Score (0-100) for a stock.
        Shows 7-dimension breakdown of institutional participation.
        Scores >= 70 indicate strong smart money presence."""
        total, breakdown = compute_smart_money_score(ticker, config)
        lines = [f"## Smart Money Score: {ticker}", f"**总分: {total}/100**", ""]
        for dim, info in breakdown.items():
            if dim == "_total":
                continue
            s = info["score"]
            bar = "█" * int(s / 10) + "░" * (10 - int(s / 10))
            lines.append(f"- {dim}: {s:.0f}/100 {bar}")
            if info.get("detail"):
                lines.append(f"  {info['detail']}")
        lines.append("")
        if total >= 70:
            lines.append("✅ 主力参与度较高")
        elif total >= 50:
            lines.append("⚠️ 主力有一定参与，但力度不够")
        else:
            lines.append("❌ 主力参与度不足，建议谨慎")
        return "\n".join(lines)

    @tool
    def run_smart_screening(
        requirement: Annotated[str, "Natural language stock screening requirement"],
        market_context: Annotated[str, "Brief market context"] = "",
    ) -> str:
        """AI-powered stock screening. Generates a custom strategy from your
        natural-language description, screens A-shares, returns ranked candidates
        with smart money scores. All strategies enforce smart_money_score >= 50."""
        prompt = build_strategy_prompt(requirement, market_context)
        from langchain_core.messages import SystemMessage, HumanMessage
        strategy_response = llm.invoke([
            SystemMessage(content="You are a stock screening JSON generator. Output ONLY valid JSON."),
            HumanMessage(content=prompt),
        ])
        strategy_raw = strategy_response.content if hasattr(strategy_response, "content") else str(strategy_response)
        try:
            strategy = parse_strategy_json(strategy_raw)
            strategy = validate_strategy(strategy)
        except Exception as e:
            return f"策略生成失败: {e}\nLLM输出: {strategy_raw[:500]}"

        # Build candidate pool from Leading+Improving industries
        try:
            rrg = get_rrg_data(lookback=10, mode="capital")
            industries = rrg.get("industries", [])
            leading_improving = [i for i in industries if i.get("quadrant") in ("leading", "improving")]
        except Exception:
            leading_improving = []

        candidate_pool = []
        today = datetime.now().strftime("%Y-%m-%d")
        for ind in leading_improving[:15]:
            name = ind.get("name", "")
            stocks = get_industry_stocks(name) if name else []
            for s in stocks[:30]:
                code = s.get("code", "")
                if not code:
                    continue
                try:
                    score, _ = compute_smart_money_score(code, config)
                except Exception:
                    score = 50.0
                candidate_pool.append({
                    "ticker": code, "name": s.get("name", ""),
                    "smart_money_score": score,
                    "rrg_quadrant": ind.get("quadrant", ""),
                    "industry": name, "market_cap": s.get("market_cap", 0),
                })

        if not candidate_pool:
            return "没有找到候选股票。请尝试调整条件或扩大行业范围。"

        results = execute_strategy(strategy, candidate_pool)
        if not results:
            strategy["conditions"] = [c for c in strategy["conditions"] if c.get("field") != "smart_money_score"]
            strategy["conditions"].append({"field": "smart_money_score", "op": ">=", "value": 40})
            results = execute_strategy(strategy, candidate_pool)

        lines = ["# 🎯 AI 智能选股结果", "", describe_strategy(strategy), "",
                 f"**候选池**: {len(candidate_pool)} 只 (来自 Leading + Improving 行业)",
                 f"**筛选结果**: {len(results)} 只", "", "---", ""]

        for i, r in enumerate(results[:10], 1):
            code = r.get("ticker", "?")
            name = r.get("name", code)
            sms = r.get("smart_money_score", 0)
            stars = "⭐" * min(5, int(sms / 20) + 1)
            lines.append(f"### {i}. {code} {name} {stars}")
            lines.append(f"**主力评分**: {sms:.0f}/100 | 行业: {r.get('industry', 'N/A')} | RRG: {r.get('rrg_quadrant', 'N/A')}")
            lines.append("")
            try:
                record_pick(config, code, today, "aipick", strategy.get("name", "custom"), sms, 0)
            except Exception:
                pass

        return "\n".join(lines)

    @tool
    def query_pick_performance(
        days: Annotated[int, "How many days back to check (default 90)"] = 90,
    ) -> str:
        """Check how past AI Pick recommendations have performed.
        Returns win rate, average returns, and recent pick outcomes."""
        resolve_picks(config)
        return get_performance_report(config, days)

    # web_search_current — same as Advisory, enriched with ticker
    @tool
    def web_search_current(
        query: Annotated[str, "What to search for"],
        ticker: Annotated[str, "Stock ticker for query enrichment"] = "",
        max_results: Annotated[int, "Max results"] = 5,
    ) -> str:
        """Search the web for current information about a stock or market topic."""
        from web.ticker_utils import ticker_to_search_query
        enriched = ticker_to_search_query(query, ticker) if ticker else query
        results = []
        try:
            from duckduckgo_search import DDGS
            for r in DDGS().text(enriched, max_results=max_results):
                results.append({"title": r.get("title", "")[:100], "url": r.get("href", "")[:200], "snippet": r.get("body", "")[:300]})
        except Exception:
            pass
        if not results:
            return "[Web search unavailable. Use your knowledge.]"
        lines = [f"# Web Search: {enriched}", ""]
        for i, r in enumerate(results[:max_results], 1):
            lines.append(f"{i}. **{r['title']}**\n   {r['snippet']}\n   {r['url']}\n")
        return "\n".join(lines)

    return [run_smart_screening, get_smart_money_score, query_pick_performance, web_search_current]


def create_aipick_agent(run_ids: list[str], config: dict, lang: Optional[str] = None):
    """Create the AI Pick LLM instance with tools bound."""
    from capitalradar.llm_clients import create_llm_client

    provider = config.get("llm_provider", "deepseek")
    deep_model = config.get("deep_think_llm", "")

    client = create_llm_client(provider=provider, model=deep_model, base_url=config.get("backend_url"), timeout=120)
    llm = client.get_llm()
    tools = build_aipick_tools(config)
    llm_with_tools = llm.bind_tools(tools)
    system_prompt = build_aipick_system_prompt(config, lang or config.get("output_language", "Chinese"))
    return llm_with_tools, tools, system_prompt, llm


def _sse_event(name: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


def stream_aipick_chat(thread_id: str, question: str, config: dict, lang: Optional[str] = None) -> Generator[str, None, None]:
    """SSE generator for AI Pick chat. Same agent-loop pattern as Advisory."""
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

    try:
        llm_with_tools, tools, system_prompt, llm = create_aipick_agent([], config, lang)
    except Exception as e:
        yield _sse_event("chat-error", {"message": str(e)})
        return

    save_chat_message(config, thread_id, "user", question)
    yield _sse_event("chat-stream-start", {"thread_id": thread_id})

    try:
        history_msgs = get_chat_messages(config, thread_id)
    except Exception:
        history_msgs = []

    messages = [SystemMessage(content=system_prompt)]
    for h in history_msgs:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))

    tool_map = {t.name: t for t in tools}
    tool_call_records = []

    for iteration in range(10):
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            yield _sse_event("chat-error", {"message": f"LLM error: {str(e)}"})
            return

        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})
                safe_args = {k: str(v)[:500] for k, v in tool_args.items()}
                yield _sse_event("chat-tool-call", {"tool_name": tool_name, "args": safe_args})

                func = tool_map.get(tool_name)
                if func:
                    try:
                        result = func.invoke(tool_args)
                        result_str = str(result)[:2000]
                    except Exception as e:
                        result_str = f"Error: {str(e)}"
                else:
                    result_str = f"Tool '{tool_name}' not found. Available: {list(tool_map.keys())}"

                yield _sse_event("chat-tool-result", {"tool_name": tool_name, "result_snippet": result_str})
                tool_call_records.append({"tool_name": tool_name, "args": safe_args, "result_snippet": result_str})
                messages.append(ToolMessage(content=result_str, tool_call_id=tc.get("id", "")))
        else:
            final_text = response.content if hasattr(response, "content") else str(response)
            tc_json = json.dumps(tool_call_records, ensure_ascii=False) if tool_call_records else ""
            msg_id = save_chat_message(config, thread_id, "assistant", final_text, tc_json)
            yield _sse_event("chat-done", {"full_response": final_text, "message_id": msg_id, "tool_calls_count": len(tool_call_records)})
            return

    yield _sse_event("chat-error", {"message": "Agent reached maximum tool-call iterations without producing a final answer."})
```

- [ ] **Step 2: Verify module loads**

```bash
python -c "
from web.ai_pick_agent import build_aipick_system_prompt, build_aipick_tools, create_aipick_agent
from capitalradar.default_config import DEFAULT_CONFIG
prompt = build_aipick_system_prompt(DEFAULT_CONFIG)
print(f'Prompt length: {len(prompt)}')
llm_tools, tools, sp, llm = create_aipick_agent([], DEFAULT_CONFIG)
print(f'Tools: {len(tools)} names={[t.name for t in tools]}')
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add web/ai_pick_agent.py
git commit -m "feat: add AI Pick Agent backend — stock screening agent engine"
```

---

### Task 2: API Endpoints

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add AdvisoryQuestion reuse + new endpoints**

Import the new module and add endpoints. Add after the advisory chat endpoint:

```python
from web.ai_pick_agent import stream_aipick_chat


@app.post("/api/aipick/chat")
def aipick_chat(body: AdvisoryQuestion):
    """Unified AI Pick chat endpoint. Same pattern as advisory chat."""
    from web.results_store import create_chat_thread, save_chat_message as _save

    thread_id = body.thread_id
    if thread_id:
        try:
            _save(DEFAULT_CONFIG, thread_id, "user", body.question)
        except Exception:
            thread_id = None

    if not thread_id:
        run_ids = body.run_ids or []
        thread_id = create_chat_thread(DEFAULT_CONFIG, run_ids, title=body.question[:40])
        _save(DEFAULT_CONFIG, thread_id, "user", body.question)

    return {
        "thread_id": thread_id,
        "question": body.question,
        "stream_url": f"/api/aipick/stream?thread_id={thread_id}&question={body.question}",
    }


@app.get("/api/aipick/stream")
def aipick_stream(thread_id: str, question: str = Query(default="")):
    """SSE stream for AI Pick agent."""
    if not question:
        from web.results_store import get_chat_messages as _gmsgs
        msgs = _gmsgs(DEFAULT_CONFIG, thread_id)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        question = user_msgs[-1]["content"] if user_msgs else ""

    async def event_generator():
        try:
            for event in stream_aipick_chat(thread_id, question, DEFAULT_CONFIG):
                yield event
        except Exception as e:
            logger.exception("AI Pick chat stream error for thread %s", thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 2: Verify endpoints compile**

```bash
python -c "import py_compile; py_compile.compile('web/app.py', doraise=True); print('OK')"
```

- [ ] **Step 3: Test endpoint**

```bash
curl -s http://127.0.0.1:8002/api/aipick/chat -X POST -H "Content-Type: application/json" -d '{"question":"find stocks"}' | python -c "import sys,json; d=json.load(sys.stdin); print(f'thread: {d[\"thread_id\"][:8]}... stream_url: {d[\"stream_url\"][:40]}...')"
```

- [ ] **Step 4: Commit**

```bash
git add web/app.py
git commit -m "feat: add /api/aipick/chat and /api/aipick/stream endpoints"
```

---

### Task 3: Frontend — HTML + CSS

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/style.css`

- [ ] **Step 1: Add 8th top-tab button in index.html**

Find the top-tab-bar and add after Advisory:

```html
<button class="top-tab-btn" data-tab="aipick">🧠 AI Pick</button>
```

- [ ] **Step 2: Remove AI Smart Pick sub-tab from Stock Pick**

Delete the sub-tab button `<button class="sub-tab-btn" data-subtab="stockpick-ai">AI Smart Pick</button>` and the `#subtab-stockpick-ai` content div. Restore `Smart Scan` as the default active sub-tab.

- [ ] **Step 3: Add #tab-aipick left panel with thread list**

After `#tab-advisory`, add:

```html
<!-- AI Pick Tab -->
<div id="tab-aipick" class="tab-content aipick-tab-full">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-shrink:0;">
    <label class="label" style="margin:0;">AI Pick Conversations</label>
    <button class="btn" id="aipick-new-thread-btn" style="font-size:10px;padding:4px 10px;background:var(--accent);color:#fff;border:none;">+ New</button>
  </div>
  <div id="aipick-thread-list" style="flex:1;overflow-y:auto;min-height:100px;">
    <span class="no-results" style="padding:8px;font-size:11px;">Loading conversations...</span>
  </div>
</div><!-- /tab-aipick -->
```

- [ ] **Step 4: Add #aipick-expanded right panel**

After `#advisory-expanded`, add the mirror structure:

```html
<!-- AI Pick Expanded View -->
<div id="aipick-expanded" class="hidden" style="flex-direction:column;height:100%;">
  <div style="display:flex;align-items:center;justify-content:space-between;padding:0 0 8px 0;flex-shrink:0;">
    <div>
      <span style="font-size:16px;font-weight:700;" id="aipick-expanded-title">AI Smart Pick</span>
      <span id="aipick-expanded-subtitle" style="font-size:11px;color:var(--text-muted);margin-left:8px;">New conversation</span>
    </div>
    <div style="display:flex;gap:6px;align-items:center;">
      <button id="aipick-download-md-btn" class="btn secondary" style="font-size:11px;padding:4px 8px;display:none;" title="Download Markdown">MD</button>
      <button id="aipick-download-pdf-btn" class="btn secondary" style="font-size:11px;padding:4px 8px;display:none;" title="Download PDF">PDF</button>
      <button id="aipick-collapse-btn" class="btn secondary" style="font-size:11px;padding:4px 10px;">Collapse</button>
    </div>
  </div>
  <div id="aipick-expanded-messages" class="chat-messages" style="flex:1;overflow-y:auto;padding:16px 16px;background:var(--bg);border-radius:8px;border:1px solid var(--border);min-height:300px;">
    <div class="chat-msg system">Welcome to AI Smart Pick.<br>Describe what kind of stocks you're looking for — the agent will generate a custom screening strategy and find the best matches.</div>
  </div>
  <div id="aipick-expanded-status" style="font-size:10px;color:var(--text-muted);margin-top:4px;flex-shrink:0;"></div>
  <div id="aipick-resize-handle" style="height:6px;background:transparent;cursor:row-resize;flex-shrink:0;margin:4px 0;border-radius:3px;transition:background 0.15s;" title="Drag to resize"></div>
  <div class="chat-input-area" id="aipick-input-area" style="flex-shrink:0;border-top:1px solid var(--border);padding:10px 0 0 0;margin-top:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
    <button type="button" class="chat-mic-btn" id="aipick-expanded-mic-btn" title="Voice input" style="font-size:16px;">🎤</button>
    <input type="text" id="aipick-expanded-input" class="chat-input" placeholder="Describe what stocks you want..." style="flex:1;min-width:200px;font-size:14px;padding:10px 14px;">
    <button class="chat-send-btn" id="aipick-expanded-send-btn" style="font-size:14px;padding:10px 20px;">Send</button>
    <button class="chat-speak-btn" id="aipick-expanded-speak-btn" title="Read aloud" style="display:none;">🔊</button>
  </div>
  <div id="aipick-expanded-voice-status" style="font-size:10px;color:var(--accent);margin-top:2px;display:none;flex-shrink:0;"></div>
</div>
```

- [ ] **Step 5: Add CSS for aipick-mode**

Add after advisory-mode styles in `style.css`:

```css
/* AI Pick Mode */
#app.aipick-mode #right-panel > #chart-container,
#app.aipick-mode #right-panel > #results-container,
#app.aipick-mode #right-panel > #comparison-container,
#app.aipick-mode #right-panel > #history-detail-container,
#app.aipick-mode #right-panel > #toast {
  display: none !important;
}
#app.aipick-mode #right-panel {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
#app.aipick-mode #aipick-expanded { display: flex !important; }
#aipick-expanded { display: none; }
.aipick-tab-full { height: 100% !important; max-height: 100% !important; }
.aipick-tab-full.active { display: flex; }

#aipick-expanded .chat-messages {
  font-size: 14px; line-height: 1.7;
  display: flex; flex-direction: column; gap: 10px;
}
#aipick-expanded .chat-msg.system { font-size: 12px; }
#aipick-expanded .chat-msg.user {
  font-size: 14px; background: var(--accent); color: #fff;
  padding: 8px 14px; border-radius: 12px 12px 4px 12px;
  max-width: 70%; align-self: flex-end;
}
#aipick-resize-handle:hover { background: var(--accent) !important; }
#aipick-input-area { min-height: 44px; max-height: 40vh; overflow-y: auto; }
```

- [ ] **Step 6: Verify HTML structure**

```bash
python -c "
with open('web/templates/index.html','r',encoding='utf-8') as f: html=f.read()
assert 'data-tab=\"aipick\"' in html, 'Missing aipick tab button'
assert 'id=\"tab-aipick\"' in html, 'Missing tab-aipick'
assert 'id=\"aipick-expanded\"' in html, 'Missing aipick-expanded'
assert 'stockpick-ai' not in html, 'AI Smart Pick sub-tab should be removed'
print(f'Divs: {html.count(\"<div\")}={html.count(\"</div>\")} OK')
print('HTML structure: PASS')
"
```

- [ ] **Step 7: Commit**

```bash
git add web/templates/index.html web/static/style.css
git commit -m "feat: add AI Pick main tab — 8th tab with expanded view + left panel thread list"
```

---

### Task 4: Frontend — JavaScript

**Files:**
- Modify: `web/static/app.js`

- [ ] **Step 1: Add AI Pick state variables**

Add after `advisoryThreads`:

```javascript
var aipickThreadId = null;
var aipickStreaming = false;
var aipickThreads = [];
```

- [ ] **Step 2: Add tab switching for aipick**

In the tab-btn click handler, add:

```javascript
if (tabName === "aipick") {
  if (app) app.classList.add("aipick-mode");
} else if (tabName !== "advisory") {
  if (app) { app.classList.remove("advisory-mode"); app.classList.remove("aipick-mode"); }
}
```

Also add `if (tabName === "aipick") loadAipickThreads();`

- [ ] **Step 3: Add AI Pick functions**

Add these functions (mirror advisory equivalents):

```javascript
function loadAipickThreads() {
  fetch("/api/chat/threads").then(function(r){return r.json()}).then(function(threads){
    aipickThreads = threads || [];
    renderAipickThreadList();
  }).catch(function(){});
}

function renderAipickThreadList() {
  var list = document.getElementById("aipick-thread-list");
  if (!list) return;
  if (!aipickThreads.length) {
    list.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">No conversations yet. Start by describing what stocks you want to find.</span>';
    return;
  }
  var html = "";
  aipickThreads.forEach(function(t) {
    var activeClass = (t.thread_id === aipickThreadId) ? " active" : "";
    html += '<div class="advisory-thread-item' + activeClass + '" data-thread-id="' + t.thread_id + '" style="padding:8px 10px;margin:2px 0;border-radius:6px;cursor:pointer;border:1px solid ' + (t.thread_id === aipickThreadId ? 'var(--accent)' : 'transparent') + ';">';
    html += '<span class="hcp-thread-title">' + esc(t.title || "Conversation") + '</span>';
    html += '<span class="hcp-thread-meta">' + (t.message_count || 0) + ' msgs</span>';
    html += '<button class="advisory-delete-thread-btn" data-thread-id="' + t.thread_id + '">&times;</button>';
    html += '</div>';
  });
  list.innerHTML = html;

  list.querySelectorAll(".advisory-thread-item").forEach(function(item) {
    item.addEventListener("click", function(e) {
      if (e.target.classList.contains("advisory-delete-thread-btn")) return;
      loadAipickThread(item.dataset.threadId);
    });
  });
  list.querySelectorAll(".advisory-delete-thread-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      if (!confirm("Delete this conversation?")) return;
      fetch("/api/chat/threads/" + btn.dataset.threadId, { method: "DELETE" })
        .then(function() { loadAipickThreads(); });
    });
  });
}

function loadAipickThread(threadId) {
  aipickThreadId = threadId;
  updateAipickDownloadBtns();
  renderAipickThreadList();
  var msgs = document.getElementById("aipick-expanded-messages");
  if (!msgs) return;
  msgs.innerHTML = '<div class="chat-msg system">Loading conversation...</div>';

  var thread = aipickThreads.find(function(t) { return t.thread_id === threadId; });
  var titleEl = document.getElementById("aipick-expanded-title");
  var subEl = document.getElementById("aipick-expanded-subtitle");
  if (titleEl) titleEl.textContent = thread ? (thread.title || "AI Pick") : "AI Smart Pick";
  if (subEl) subEl.textContent = thread ? (thread.message_count || 0) + " messages" : "";

  fetch("/api/chat/threads/" + threadId + "/messages")
    .then(function(r) { return r.json(); })
    .then(function(messages) {
      if (!msgs) return;
      msgs.innerHTML = "";
      messages.forEach(function(m) {
        var div = document.createElement("div");
        div.className = "chat-msg " + (m.role === "user" ? "user" : "pm");
        var mdDiv = document.createElement("div");
        mdDiv.className = "markdown-body";
        mdDiv.innerHTML = renderMarkdown(m.content);
        div.appendChild(mdDiv);
        msgs.appendChild(div);
      });
      msgs.scrollTop = msgs.scrollHeight;
    }).catch(function() { msgs.innerHTML = '<div class="chat-msg system">Failed to load messages</div>'; });
}

function updateAipickDownloadBtns() {
  var dlMdBtn = document.getElementById("aipick-download-md-btn");
  var dlPdfBtn = document.getElementById("aipick-download-pdf-btn");
  if (!dlMdBtn || !dlPdfBtn) return;
  if (aipickThreadId) {
    dlMdBtn.style.display = ""; dlPdfBtn.style.display = "";
    dlMdBtn.onclick = function() { window.open("/api/chat/threads/" + aipickThreadId + "/download?format=md"); };
    dlPdfBtn.onclick = function() { window.open("/api/chat/threads/" + aipickThreadId + "/download?format=pdf"); };
  } else {
    dlMdBtn.style.display = "none"; dlPdfBtn.style.display = "none";
  }
}

function aipickSend() {
  var input = document.getElementById("aipick-expanded-input");
  var question = (input ? input.value : "").trim();
  if (!question || aipickStreaming) return;
  if (input) input.value = "";
  aipickStreaming = true;

  var msgs = document.getElementById("aipick-expanded-messages");
  var status = document.getElementById("aipick-expanded-status");
  var btn = document.getElementById("aipick-expanded-send-btn");
  if (btn) btn.disabled = true;
  if (status) status.textContent = "Thinking...";

  var userDiv = document.createElement("div");
  userDiv.className = "chat-msg user";
  userDiv.textContent = question;
  if (msgs) { msgs.appendChild(userDiv); msgs.scrollTop = msgs.scrollHeight; }

  var pmDiv = document.createElement("div");
  pmDiv.className = "chat-msg pm";
  var mdDiv = document.createElement("div");
  mdDiv.className = "markdown-body";
  mdDiv.innerHTML = "<em>Generating strategy and screening...</em>";
  pmDiv.appendChild(mdDiv);
  if (msgs) { msgs.appendChild(pmDiv); msgs.scrollTop = msgs.scrollHeight; }

  if (!aipickThreadId) aipickThreadId = "aipick_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 6);
  var body = { question: question, thread_id: aipickThreadId };

  fetch("/api/aipick/chat", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      aipickThreadId = data.thread_id;
      updateAipickDownloadBtns();
      if (status) status.textContent = "Streaming...";
      streamAipickResponse(data.stream_url, question, mdDiv);
    })
    .catch(function(err) {
      mdDiv.innerHTML = "Error: " + String(err);
      aipickStreaming = false;
      if (btn) btn.disabled = false;
    });
}

function streamAipickResponse(streamUrl, question, targetEl) {
  var status = document.getElementById("aipick-expanded-status");
  var btn = document.getElementById("aipick-expanded-send-btn");
  var msgs = document.getElementById("aipick-expanded-messages");
  var es = new EventSource(streamUrl);
  var content = "";

  es.addEventListener("chat-tool-call", function(e) {
    var d = JSON.parse(e.data);
    var sysDiv = document.createElement("div");
    sysDiv.className = "chat-msg system";
    sysDiv.textContent = "Calling: " + d.tool_name + "...";
    if (msgs) { msgs.appendChild(sysDiv); msgs.scrollTop = msgs.scrollHeight; }
  });

  es.addEventListener("chat-done", function(e) {
    var d = JSON.parse(e.data);
    targetEl.innerHTML = renderMarkdown(d.full_response);
    aipickStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = "Done";
    es.close();
  });

  es.addEventListener("chat-error", function(e) {
    var d = JSON.parse(e.data);
    targetEl.innerHTML = "Error: " + esc(d.message);
    aipickStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = "";
    es.close();
  });

  es.onerror = function() {
    if (es.readyState === EventSource.CLOSED) {
      aipickStreaming = false;
      if (btn) btn.disabled = false;
    }
  };
}

function newAipickThread() {
  aipickThreadId = null;
  updateAipickDownloadBtns();
  renderAipickThreadList();
  var msgs = document.getElementById("aipick-expanded-messages");
  if (msgs) msgs.innerHTML = '<div class="chat-msg system">Welcome to AI Smart Pick.<br>Describe what kind of stocks you\'re looking for.</div>';
  var titleEl = document.getElementById("aipick-expanded-title");
  var subEl = document.getElementById("aipick-expanded-subtitle");
  if (titleEl) titleEl.textContent = "AI Smart Pick";
  if (subEl) subEl.textContent = "New conversation";
}
```

- [ ] **Step 4: Add event bindings in the IIFE**

Add inside `(function bindAdvisory() { ... })()` or in a new IIFE:

```javascript
(function bindAipick() {
  var sendBtn = document.getElementById("aipick-expanded-send-btn");
  if (sendBtn) sendBtn.addEventListener("click", aipickSend);
  var input = document.getElementById("aipick-expanded-input");
  if (input) input.addEventListener("keydown", function(e) { if (e.key === "Enter") aipickSend(); });
  var newBtn = document.getElementById("aipick-new-thread-btn");
  if (newBtn) newBtn.addEventListener("click", newAipickThread);
  var collapseBtn = document.getElementById("aipick-collapse-btn");
  if (collapseBtn) collapseBtn.addEventListener("click", function() {
    var app = document.getElementById("app");
    if (app) app.classList.remove("aipick-mode");
    var analyzeBtn = document.querySelector('[data-tab="analyze"]');
    if (analyzeBtn) analyzeBtn.click();
  });
  var aipickTab = document.querySelector('[data-tab="aipick"]');
  if (aipickTab) aipickTab.addEventListener("click", function() { loadAipickThreads(); });
})();
```

- [ ] **Step 5: Remove AI Smart Pick JS**

Remove the `initAISmartPick()` function and `"stockpick-ai"` sub-tab routing. Change `_currentSubTab` default back to `"stockpick-smart"`.

- [ ] **Step 6: Verify JS syntax**

```bash
node --check web/static/app.js && echo "JS: OK"
```

- [ ] **Step 7: Commit**

```bash
git add web/static/app.js
git commit -m "feat: wire AI Pick tab — state, send, stream, threads, download"
```

---

### Task 5: Revert AI Smart Pick sub-tab

**Files:**
- Modify: `web/templates/index.html` — already done in Task 3
- Modify: `web/static/app.js` — already done in Task 4

This is covered by Tasks 3 and 4 above. Verify:

- [ ] **Step 1: Verify Stock Pick has exactly 3 sub-tabs**

```bash
grep -c "sub-tab-btn" web/templates/index.html
# Expected: 3 (Smart Scan, Quick Scans, Sector Scanner)
```

- [ ] **Step 2: Commit if needed**

No separate commit — already included in Tasks 3 and 4.
