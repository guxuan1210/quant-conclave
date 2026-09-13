# Skill Lifecycle Management — Versioned Analysis with Manual Calibration

## Problem

Deep analysis runs happen at irregular intervals (user-driven, any ticker any time). Pending decisions need enough price data before they can be resolved. Current resolvers are auto-triggered based on pending count threshold, but this doesn't account for the inconsistent timing of price data availability. Skill versions need to be pinned to analysis records so we can measure skill improvement per version.

## Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| When to resolve outcomes? | Manual only | Pending entries need price data that may not be available yet. User triggers via Calibration Run or Mentor chat when they're ready. |
| Which skill version to use? | Pin version per analysis record | Each deep analysis stores the `skill_version` it was run with. Future versions can be compared. |
| Version pinning storage | In memory_log tag and `AgentState` | Tag format: `[date \| ticker \| rating \| v{N} \| pending]`. Also stored in `result_runs` table. |
| Active skill | Always latest generated version | `get_active_skill_version()` reads `index.json`. New analyses always use this. |

## Lifecycle Flow

```
Manual trigger (Calibration Run / Mentor "run calibration")
  → resolve_all_pending() — fetch price data for pending entries
  → extract_experiences() — detect patterns in resolved entries
  → User approves/archives experiences in UI
  → generate_skill_version() — writes v{N+1}.md
  → index.json updated, active_version advanced
  → Next deep analysis picks up new active_version
```

## Files to Modify

| File | Change |
|------|--------|
| `capitalradar/graph/skill_generator.py` | Add `get_active_skill_version()` function |
| `capitalradar/agents/utils/memory.py` | `store_decision()` accepts `skill_version` param, stores in tag |
| `capitalradar/agents/utils/agent_states.py` | Add `skill_version` field to `AgentState` |
| `capitalradar/graph/trading_graph.py` | Read skill_version at init; pass to `store_decision`; record in state |

## No Changes Needed

| Module | Why |
|--------|-----|
| `web/skill_ui.py` | Already supports manual trigger via `POST /api/skill/generate` |
| `capitalradar/advisory/experience_store.py` | Already auto-generates on >=3 approvals (this is a UI-triggered event, not an analysis-triggered one) |
| `capitalradar/advisory/history_agent.py` | Already has `generate_analyst_skill` tool for Mentor chat |
| `web/calibration_ui.py` | Already has `POST /api/calibration/run` which calls `run_full_loop()` |
| `web/static/skill_panel.js` | Already renders versions and triggers generate |
| `cli/main.py` | No changes — CLI runs trigger `propagate()` which handles everything internally |
