# CapitalRadar Web UI Redesign — Design Doc

**Date**: 2026-06-22  
**Status**: Approved

## Overview

Restructure the web dashboard layout: move the 7 main navigation tabs from the left panel to a full-width top-level tab bar. Add branding header with project name and tagline. Keep the existing left panel + right panel layout below the tabs for content display.

## Layout Before/After

**Before:**
```
┌──────────┬──────────────────────────────┐
│ Left Panel (380px)                      │
│  [Tab Bar] (inside left panel)          │
│  [Controls]                             │
│  [Progress]                             │
└──────────┴──────────────────────────────┘
```

**After:**
```
┌──────────────────────────────────────────────────────────┐
│  🛡️ CapitalRadar — AI Institutional Flow Detection        │
│  Empowering retail investors to track smart-money moves  │
├──────────────────────────────────────────────────────────┤
│  🔍 Analyze │📋 History │⭐ Shortlist │... │💬 Advisory  │
├─────────────────────┬────────────────────────────────────┤
│ Left Panel (380px)  │ Right Panel (flex:1)               │
│ Controls / Filters  │ Chart / Results / Chat             │
└─────────────────────┴────────────────────────────────────┘
```

## Design Sections

### 1. Branding Header

- Single row: **🛡️ CapitalRadar** (bold, accent color) + tagline in muted text
- Tagline: "AI Institutional Capital Flow Detection — Empowering Retail Investors"
- Height: ~40px, padding: 8px 20px

### 2. Top Tab Bar (full-width)

- 7 tabs spanning full page width, equal `flex: 1`
- Each tab: emoji icon + text label, centered
- Active tab: blue background + white text
- Inactive tabs: light gray background + dark text
- Bottom border: 2px solid accent on active tab
- Height: ~40px

### 3. Content Area (below tabs)

- Same left panel (380px) + right panel (flex:1) + resizer
- Left panel content changes per tab (Analyze: config form; History: filters+list; etc.)
- Right panel content changes per tab (chart+results, history detail, advisory chat, etc.)
- Advisory mode: right panel becomes full-height chat (existing behavior preserved)

## Tab Icons

| Tab | Icon | Label |
|-----|------|-------|
| Analyze | 🔍 | Analyze |
| History | 📋 | History |
| Shortlist | ⭐ | Shortlist |
| Scheduled | ⏰ | Scheduled |
| Rotation | 🔄 | Rotation |
| Stock Pick | 🎯 | Stock Pick |
| Advisory | 💬 | Advisory |

## Files Changed

| File | Changes |
|------|---------|
| `web/templates/index.html` | Add header + top tab bar above `#app`; move `#app` to contain only panels+resizer |
| `web/static/style.css` | New `.header`, `.top-tab-bar`, `.top-tab-btn` styles; remove old `.tab-bar` inside left panel |
| `web/static/app.js` | Update tab switching to use `.top-tab-btn`; update Advisory mode CSS class target |

## Scope

- **In scope**: HTML restructuring, CSS for header/top-tabs, keep existing content panels
- **Out of scope**: New features, backend changes, responsive/mobile layout

## Risks

- HTML restructuring may introduce tag balance issues — use Python validation after edits
- JS tab switching references `.tab-btn` — need to update to `.top-tab-btn`
- BOM can be introduced by Edit tool — verify with `xxd | head -1` after each edit
