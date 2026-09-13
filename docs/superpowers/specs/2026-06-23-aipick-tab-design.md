# AI Pick Agent — Independent Main Tab Design Spec

**Date**: 2026-06-23  
**Status**: Approved

## Overview

Elevate AI Smart Pick from a Stock Pick sub-tab to a standalone 8th main tab "🧠 AI Pick" — a dedicated stock screening agent with its own conversation threads, voice input, and download support. Mirrors the Advisory (💬) tab architecture but specialized in stock discovery rather than individual stock analysis.

## Tab Layout

```
🔍 Analyze │📋 History │⭐ Shortlist │⏰ Scheduled │🔄 Rotation │🎯 Stock Pick │💬 Advisory │🧠 AI Pick
```

## Agent Differentiation

| | Advisory 💬 | AI Pick 🧠 |
|---|---|---|
| Core task | Analyze known stocks | Discover new stocks |
| Key tools | query_past_analyses, run_full_analysis, get_money_flow, ... | run_smart_screening, get_smart_money_score, query_pick_performance |
| Conversation style | "帮我分析 000625" | "找低位启动的新能源票" |
| Output | Analysis reports with download links | Ranked candidate lists with reasons + smart money scores |

## Backend: `web/ai_pick_agent.py`

New module, mirrors `web/history_chat.py` pattern:

- `build_aipick_system_prompt()` — specialized prompt emphasizing: stock discovery methodology, smart money score as baseline, strategy generation from natural language, explaining picks in retail-investor language
- `build_aipick_tools(config)` — returns: `run_smart_screening`, `get_smart_money_score`, `query_pick_performance`, `web_search_current`
- `create_aipick_agent(run_ids, config, lang)` — creates LLM + tools, same pattern as `create_history_pm_agent`
- `stream_aipick_chat(thread_id, question, config)` — SSE generator, same agent loop pattern
- Reuses `chat_threads` table, stores `source="aipick"` (new column or use metadata)
- New API endpoint: `GET /api/aipick/stream?thread_id=...&question=...`

## Frontend: `index.html` + `app.js`

### HTML
- 8th top-tab button: `<button class="top-tab-btn" data-tab="aipick">🧠 AI Pick</button>`
- Right panel: `#aipick-expanded` div, mirrors `#advisory-expanded` structure exactly
- Left panel: `#tab-aipick` with thread list, mirrors Advisory thread list
- Header with Collapse + MD/DOCX/PDF download buttons
- Messages area + resize handle + input area with mic/send/speak

### JS
- `aipickThreadId`, `aipickStreaming`, `aipickThreads` — parallel to advisory state
- `aipickSend()` — POST to `/api/aipick/chat`, then SSE stream
- Same voice input, download, thread management as Advisory
- Tab switching: `aipick` → add `aipick-mode` class to `#app`

## Stock Pick Revert
- Remove AI Smart Pick sub-tab button and content
- Restore 3 sub-tabs: Smart Scan / Quick Scans / Sector Scanner (Smart Scan default active)

## Files Changed

| File | Action |
|------|--------|
| `web/ai_pick_agent.py` | NEW — AI Pick agent engine |
| `web/app.py` | Add `/api/aipick/chat` + `/api/aipick/stream` endpoints |
| `web/templates/index.html` | 8th top-tab, `#tab-aipick` left panel, `#aipick-expanded` right panel, remove AI Smart Pick sub-tab |
| `web/static/app.js` | Wire aipick tab switching, aipickSend(), thread management, download buttons |
| `web/static/style.css` | Add aipick-mode styles (mirror advisory-mode) |
