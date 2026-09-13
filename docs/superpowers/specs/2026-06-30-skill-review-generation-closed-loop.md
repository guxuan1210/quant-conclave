# Skill Review → Generation → Versioning Closed Loop

## Problem

Experiences extracted by Mentor Agent are stored in `advisory_experiences` table with `pending_review` status. Users can approve/archive them via the Experience panel, but there is no way to:
1. Turn approved experiences into a versioned skill file
2. View skill version history or compare versions
3. Trigger skill generation from Mentor chat

## Scope

1. **Backend API** — Routes for listing/reading skill versions, diffing versions, triggering generation
2. **Auto-generation** — When ≥3 experiences are approved, auto-trigger `generate_skill_version()`
3. **Mentor tool** — `generate_analyst_skill` tool callable from Mentor chat
4. **UI** — Skill version history panel + diff viewer + generate button

## Architecture

### API Routes (`web/skill_ui.py`)

| Route | Method | Returns |
|-------|--------|---------|
| `/api/skill/versions` | GET | List of all versions from index.json |
| `/api/skill/versions/{v}` | GET | Raw SKILL.md content for version `v` |
| `/api/skill/diff?v1={v}&v2={v2}` | GET | Diff between two versions (JSON with add/del lines) |
| `/api/skill/generate` | POST | Trigger `generate_skill_version()` |
| `/api/skill/active` | GET | Current active version info |

### Auto-generation (`experience_store.py`)

`approve_experience()` checks: if `len(get_active_experiences()) >= 3`, calls `generate_skill_version()`.

### Mentor Tool (`history_agent.py`)

Add `generate_analyst_skill` to `build_history_agent_tools()`. When user types "生成技能", LLM calls this tool.

### UI

Skill version history panel displayed in the right sidebar (below the calibration section or as a dedicated section). Shows:
- Current active version badge
- Version timeline (clickable → shows diff vs previous)
- Generate Skill button (visible only when experiences are approved)

## Files to Modify/Create

| File | Change |
|------|--------|
| `web/skill_ui.py` | **Create** — FastAPI router for skill CRUD |
| `web/app.py` | Register skill_router |
| `capitalradar/advisory/experience_store.py` | Add auto-generation on approve |
| `capitalradar/advisory/history_agent.py` | Add `generate_analyst_skill` tool |
| `web/static/skill_panel.js` | **Create** — Frontend skill version UI |
| `web/templates/index.html` | Add skill panel container |
| `web/static/style.css` | Add skill panel styles |
