# Stock Picking Agent — Design Spec

**Date**: 2026-06-23  
**Status**: Approved

## Overview

Enhance CapitalRadar's stock screening from static hardcoded strategies to an LLM-driven adaptive stock picking agent. Four integrated modules centered on institutional capital flow detection — the project's core differentiator for retail investors.

## Module 1: Smart Money Score

**File**: `capitalradar/sector_scan/smart_money_score.py`

Unified 0-100 score synthesizing institutional participation signals from existing dataflows.

**Signal Weights:**

| Signal | Source | Weight |
|--------|--------|--------|
| Net inflow trend (5/10/20d) | `get_money_flow` | 25% |
| Large-order ratio | `get_money_flow` | 15% |
| North-bound flow change | `get_hsgt_flow` | 15% |
| Margin balance trend | `get_margin_trading` | 10% |
| Institutional holdings delta | `get_institutional_holders` | 15% |
| Intraday anomalies | `get_intraday_data` | 10% |
| Analyst rating changes | `get_analyst_recommendations` | 10% |

**Hard rule**: any stock with core money-flow sub-score < 40 is downgraded regardless of other signals.

**Returns**: total score + per-dimension breakdown dict.

## Module 2: Dynamic Strategy Engine

**File**: `capitalradar/sector_scan/dynamic_strategy.py`

LLM generates screening strategies as JSON from natural-language user intent. Engine executes against A-share universe.

**Available fields** (LLM can freely combine):

| Category | Field | Type |
|----------|-------|------|
| Smart Money | `smart_money_score`, `net_inflow_5d`, `big_order_ratio`, `north_bound_flow` | numeric |
| Valuation | `pe_ttm`, `pb`, `pe_vs_industry` | numeric |
| Technical | `rsi_14`, `macd_dif`, `price_vs_ma20`, `price_vs_60d_low`, `golden_cross_days`, `volume_ratio_5d` | numeric |
| Sector | `industry`, `rrg_quadrant`, `rps_120` | string/numeric |
| Scale | `market_cap` | numeric |

**Flow**: User intent → LLM generates strategy JSON → Engine executes → Returns ranked candidates.

**Entry points**: Advisory tool `run_smart_screening(requirement)` + Stock Pick "AI Smart Pick" sub-tab.

## Module 3: Stock Pick Explanation

Agent produces retail-investor-readable explanations for each candidate.

**Template per stock:**
- Why selected (data-backed bullet points)
- Risk warnings (pressure levels, conflicting signals)
- Observation suggestion (entry trigger price/condition)

Implementation: `build_pick_explanation(stock_data, smart_money_breakdown, market_context)` with LLM language polish.

## Module 4: Pick Tracking Loop

**File**: `capitalradar/sector_scan/pick_tracker.py`

**DB Table**: `stock_picks` with columns: pick_id, ticker, pick_date, source, strategy, smart_money_score, pick_price, reason, return_5d, return_20d, return_60d, resolved_at.

**Workflow**:
1. On pick: insert row with price/score/reason
2. `resolve_picks()` called periodically: fetches latest prices, computes returns at 5/20/60d
3. Agent query: "how did my picks do?" → returns performance report with win rate

## Integration

- **Stock Pick tab**: new "AI Smart Pick" sub-tab alongside Smart Scan/Quick Scans/Sector Scanner
- **Advisory Agent**: new tools `run_smart_screening`, `query_pick_performance`, `get_smart_money_score`
- **Market state detection**: LLM judges bull/bear/ranging from macro context for strategy weighting

## Files Changed

| File | Action |
|------|--------|
| `capitalradar/sector_scan/smart_money_score.py` | NEW |
| `capitalradar/sector_scan/dynamic_strategy.py` | NEW |
| `capitalradar/sector_scan/pick_tracker.py` | NEW |
| `web/history_chat.py` | Add tools: `run_smart_screening`, `query_pick_performance`, `get_smart_money_score` |
| `web/results_store.py` | Add `stock_picks` table + CRUD |
| `web/app.py` | Add `/api/smart-pick` endpoint, pick tracking endpoints |
| `web/templates/index.html` | Add "AI Smart Pick" sub-tab in Stock Pick |
| `web/static/app.js` | Wire AI Smart Pick UI, pick performance display |
