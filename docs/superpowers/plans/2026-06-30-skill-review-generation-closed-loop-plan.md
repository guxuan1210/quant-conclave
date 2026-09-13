# Skill Review → Generation → Versioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the closed loop for experience review → skill generation → versioning, with UI for version history and diff, auto-trigger on ≥3 approvals, and manual trigger via Mentor chat.

**Architecture:** FastAPI router for skill CRUD, auto-generation hook in experience_store.approve_experience(), new tool in Mentor, vanilla JS frontend panel.

**Tech Stack:** FastAPI, SQLite, pathlib, vanilla JS, CSS

---

### Task 1: Create `web/skill_ui.py` — FastAPI skill management routes

**Files:**
- Create: `web/skill_ui.py`

**Context:** New router providing API endpoints for listing skill versions, reading content, diffing, and triggering generation.

- [ ] **Step 1: Create the file**

```python
"""FastAPI routes for skill version management."""
from __future__ import annotations
import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skill", tags=["skill"])

SKILL_DIR = Path(".claude/skills/analyst-core")
SKILL_INDEX = SKILL_DIR / "index.json"


def _load_index() -> dict:
    if SKILL_INDEX.exists():
        try:
            return json.loads(SKILL_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"analyst": {"versions": [], "active_version": 0}}


@router.get("/versions")
def list_versions():
    """List all skill versions."""
    index = _load_index()
    return index.get("analyst", {}).get("versions", [])


@router.get("/versions/{v}")
def get_version(v: int):
    """Get the full SKILL.md content for a specific version."""
    index = _load_index()
    for entry in index.get("analyst", {}).get("versions", []):
        if entry.get("version") == v:
            fname = entry.get("file", "")
            file_path = SKILL_DIR / fname
            if file_path.exists():
                return {
                    "version": v,
                    "file": fname,
                    "content": file_path.read_text(encoding="utf-8"),
                    "metadata": entry,
                }
    raise HTTPException(404, f"Version {v} not found")


@router.get("/diff")
def diff_versions(v1: int = Query(...), v2: int = Query(...)):
    """Get diff between two skill versions. Returns added/removed lines."""
    def _get_lines(v):
        try:
            resp = get_version(v)
            return resp["content"].splitlines()
        except HTTPException:
            return []

    lines_v1 = _get_lines(v1)
    lines_v2 = _get_lines(v2)
    if not lines_v1 and not lines_v2:
        raise HTTPException(404, "Neither version found")

    added = [l for l in lines_v2 if l.strip() and l not in lines_v1]
    removed = [l for l in lines_v1 if l.strip() and l not in lines_v2]

    return {
        "v1": v1, "v2": v2,
        "v1_lines": len(lines_v1),
        "v2_lines": len(lines_v2),
        "added": added,
        "removed": removed,
        "net_change": len(added) - len(removed),
    }


@router.post("/generate")
def trigger_generate():
    """Manually trigger skill version generation."""
    from capitalradar.graph.skill_generator import generate_skill_version
    from capitalradar.default_config import DEFAULT_CONFIG as config
    result = generate_skill_version(config)
    return result


@router.get("/active")
def get_active():
    """Return current active skill version info."""
    index = _load_index()
    av = index.get("analyst", {}).get("active_version", 0)
    for entry in index.get("analyst", {}).get("versions", []):
        if entry.get("version") == av:
            return {**entry, "active": True}
    return {"version": 0, "active": False}
```

- [ ] **Step 2: Verify import**

```
python -c "from web.skill_ui import router; print('OK:', router.prefix)"
```

- [ ] **Step 3: Commit**

```
git add web/skill_ui.py
git commit -m "feat(skill): add FastAPI routes for skill version CRUD and diff"
```

---

### Task 2: Register skill_router in app.py

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add import**

After line 30 (`from web.history_agent import router as history_agent_router`), add:
```python
from web.skill_ui import router as skill_router
```

- [ ] **Step 2: Register the router**

After line 58 (`app.include_router(history_agent_router)`), add:
```python
app.include_router(skill_router)
```

- [ ] **Step 3: Verify**

```
python -c "
from web.app import app
routes = [str(r.original_router.prefix) for r in app.router.routes
          if hasattr(r, 'original_router')]
assert '/api/skill' in routes
print('skill_router registered OK')
"
```

- [ ] **Step 4: Commit**

```
git add web/app.py
git commit -m "feat(skill): register skill_router in app.py"
```

---

### Task 3: Add auto-generation on experience approval

**Files:**
- Modify: `capitalradar/advisory/experience_store.py`

**Context:** When ≥3 experiences are active, auto-trigger generate_skill_version().

- [ ] **Step 1: Read current file**

Read `capitalradar/advisory/experience_store.py`. Find the `approve_experience` function.

- [ ] **Step 2: Add auto-generation to approve_experience**

Modify `approve_experience`:
```python
def approve_experience(eid: int) -> bool:
    conn = _get_conn()
    conn.execute(
        "UPDATE advisory_experiences SET status='active' WHERE id=? AND status='pending_review'", (eid,)
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()

    # Auto-generate skill version if enough active experiences
    if affected:
        try:
            active = get_active_experiences()
            if len(active) >= 3:
                from capitalradar.graph.skill_generator import generate_skill_version
                from capitalradar.dataflows.config import get_config
                result = generate_skill_version(get_config())
                if result.get("version"):
                    logging.getLogger(__name__).info(
                        "Auto-generated skill v%d from %d active experiences",
                        result["version"], len(active)
                    )
        except Exception:
            pass

    return affected
```

- [ ] **Step 3: Verify**

```
python -c "from capitalradar.advisory.experience_store import approve_experience; print('OK')"
```

- [ ] **Step 4: Commit**

```
git add capitalradar/advisory/experience_store.py
git commit -m "feat(skill): auto-generate skill version when >=3 experiences approved"
```

---

### Task 4: Add `generate_analyst_skill` tool to Mentor

**Files:**
- Modify: `capitalradar/advisory/history_agent.py`

**Context:** Users can type "生成技能" in Mentor chat and the LLM will call this tool.

- [ ] **Step 1: Read current file**

Read `capitalradar/advisory/history_agent.py`. Find `build_history_agent_tools()`.

- [ ] **Step 2: Add new tool to the tool list**

Inside `build_history_agent_tools()`, after the existing tool definitions and before the `return` statement, add:

```python
    @tool
    def generate_analyst_skill() -> str:
        """Generate a new version of the CapitalRadar Analyst skill from
        approved experiences. Use this when the user asks to generate or
        update the skill. Returns version number and summary."""
        from capitalradar.graph.skill_generator import generate_skill_version
        from capitalradar.dataflows.config import get_config
        try:
            config = get_config()
            result = generate_skill_version(config)
            return (
                f"Skill v{result['version']} generated successfully!\n"
                f"- Rules included: {result['rule_count']}\n"
                f"- Active experiences: {result['active_exp_count']}\n"
                f"- File: {result['file_path']}"
            )
        except Exception as e:
            return f"Skill generation failed: {e}"

    # Add to result list
    result.append(generate_analyst_skill)
```

Then add `generate_analyst_skill` to the returned list (add after the existing `return result` — find where tools are assembled and append it). Look for the return statement at the end of `build_history_agent_tools()`.

- [ ] **Step 3: Verify**

```
python -c "
from capitalradar.advisory.history_agent import build_history_agent_tools
tools = build_history_agent_tools({})
names = [t.name for t in tools]
assert 'generate_analyst_skill' in names
print('generate_analyst_skill tool registered OK')
"
```

- [ ] **Step 4: Commit**

```
git add capitalradar/advisory/history_agent.py
git commit -m "feat(mentor): add generate_analyst_skill tool for Mentor agent"
```

---

### Task 5: Create skill_panel.js — frontend skill version UI

**Files:**
- Create: `web/static/skill_panel.js`

- [ ] **Step 1: Create the file**

```javascript
(function(){
"use strict";

var panel = document.getElementById("skill-panel");
var list = document.getElementById("skill-version-list");

function loadVersions() {
  if (!list) return;
  list.innerHTML = "Loading...";
  fetch("/api/skill/versions")
    .then(function(r) { return r.json(); })
    .then(renderVersions)
    .catch(function() { list.innerHTML = '<span class="no-results">Failed to load</span>'; });
}

function renderVersions(versions) {
  if (!list) return;
  if (!versions || !versions.length) {
    list.innerHTML = '<span class="no-results">No skill versions yet</span>';
    return;
  }

  var html = "";
  // Show active version first
  fetch("/api/skill/active")
    .then(function(r) { return r.json(); })
    .then(function(activeInfo) {
      var activeV = activeInfo.version || 0;
      for (var i = versions.length - 1; i >= 0; i--) {
        var v = versions[i];
        var isActive = v.version === activeV;
        var activeTag = isActive ? ' <span class="skill-active-badge">ACTIVE</span>' : "";
        var viewBtn = '<button class="skill-view-btn" data-v="' + v.version + '" style="font-size:10px;padding:1px 6px;cursor:pointer;">View</button>';
        var diffBtn = "";
        if (i > 0) {
          diffBtn = ' <button class="skill-diff-btn" data-v1="' + versions[i-1].version + '" data-v2="' + v.version + '" style="font-size:10px;padding:1px 6px;cursor:pointer;">Diff</button>';
        }
        html += '<div class="skill-version-item' + (isActive ? ' active' : '') + '">' +
          '<span style="font-weight:600;">v' + v.version + '</span>' +
          ' <span style="font-size:10px;color:var(--text-muted);">' + (v.created || "") + '</span>' +
          activeTag +
          ' <span style="font-size:10px;">' + (v.rule_count || 0) + ' rules</span>' +
          '<div style="float:right;">' + viewBtn + diffBtn + '</div>' +
          '</div>';
      }
      list.innerHTML = html;

      // Wire view buttons
      list.querySelectorAll(".skill-view-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
          var v = btn.getAttribute("data-v");
          fetch("/api/skill/versions/" + v)
            .then(function(r) { return r.json(); })
            .then(function(data) {
              var viewer = document.getElementById("skill-viewer");
              if (viewer) {
                viewer.innerHTML = '<pre style="font-size:11px;max-height:300px;overflow:auto;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:8px;">' +
                  escapeHtml(data.content || "") + '</pre>';
                viewer.style.display = "";
              }
            });
        });
      });

      // Wire diff buttons
      list.querySelectorAll(".skill-diff-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
          var v1 = btn.getAttribute("data-v1");
          var v2 = btn.getAttribute("data-v2");
          fetch("/api/skill/diff?v1=" + v1 + "&v2=" + v2)
            .then(function(r) { return r.json(); })
            .then(function(data) {
              var viewer = document.getElementById("skill-viewer");
              if (viewer) {
                var html = '<div style="font-size:11px;max-height:300px;overflow:auto;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:8px;">';
                html += '<div style="margin-bottom:4px;font-size:10px;color:var(--text-muted);">v' + v1 + ' → v' + v2 + ' | +' + data.added.length + ' -' + data.removed.length + '</div>';
                data.added.forEach(function(l) {
                  html += '<div style="color:#22863a;">+ ' + escapeHtml(l) + '</div>';
                });
                data.removed.forEach(function(l) {
                  html += '<div style="color:#cb2431;">- ' + escapeHtml(l) + '</div>';
                });
                if (!data.added.length && !data.removed.length) {
                  html += '<div style="color:var(--text-muted);">No content changes (metadata only)</div>';
                }
                html += '</div>';
                viewer.innerHTML = html;
                viewer.style.display = "";
              }
            });
        });
      });
    });
}

function escapeHtml(s) {
  if (!s) return "";
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", loadVersions);
} else {
  loadVersions();
}
})();
```

- [ ] **Step 2: Verify syntax**

```
node -e "
const fs = require('fs');
new Function(fs.readFileSync('web/static/skill_panel.js', 'utf8'));
console.log('Syntax OK');
"
```

- [ ] **Step 3: Commit**

```
git add web/static/skill_panel.js
git commit -m "feat(ui): add skill_panel.js — version history, viewer, and diff UI"
```

---

### Task 6: Add skill panel HTML container + JS include

**Files:**
- Modify: `web/templates/index.html`

**Context:** Add a skill panel section in the right panel area (after calibration-panel or as part of the right sidebar). Also include the skill_panel.js script.

- [ ] **Step 1: Read current file**

Read `web/templates/index.html` to find where calibration-panel ends and the script includes are.

- [ ] **Step 2: Add skill panel HTML**

After the calibration-panel div (around line 783), add:

```html
    <!-- Skill Version Panel -->
    <div id="skill-panel" style="display:none;flex:0 0 auto;overflow-y:auto;padding:8px;font-size:11px;border-top:1px solid var(--border);">
      <div style="font-weight:700;font-size:12px;margin-bottom:6px;color:var(--text);">Skill Versions</div>
      <div id="skill-version-list" style="font-size:10px;color:var(--text-muted);margin-bottom:6px;">Loading...</div>
      <div id="skill-viewer" style="display:none;margin-bottom:6px;"></div>
      <div style="display:flex;gap:4px;">
        <button onclick="triggerSkillGenerate()" style="flex:1;padding:4px 6px;font-size:10px;cursor:pointer;background:var(--accent);color:#fff;border:none;border-radius:4px;">Generate Skill</button>
      </div>
    </div>
```

Add the global function for the button:
```html
<script>
function triggerSkillGenerate() {
  var btn = event.target;
  btn.textContent = "Generating..."; btn.disabled = true;
  fetch("/api/skill/generate", {method: "POST"})
    .then(function(r) { return r.json(); })
    .then(function(result) {
      alert("Skill v" + result.version + " generated with " + result.rule_count + " rules");
      if (window.skillPanelLoad) window.skillPanelLoad();
    })
    .catch(function(e) { alert("Failed: " + e); })
    .finally(function() { btn.textContent = "Generate Skill"; btn.disabled = false; });
}
</script>
```

- [ ] **Step 3: Make skill panel visible on history tab**

In the tab-switching logic (`app.js`), the calibration panel has `display: (tabName === 'history') ? '' : 'none'`. Add similar for skill-panel:
```javascript
// After calibration panel show/hide (around line 1435)
var skillPanel = document.getElementById("skill-panel");
if (skillPanel) {
  skillPanel.style.display = (tabName === "history") ? "" : "none";
}
```

- [ ] **Step 4: Add script include**

After the calibration_panel.js include (around line 994), add:
```html
<script src="/static/skill_panel.js"></script>
<script>
function triggerSkillGenerate() {
  var btn = event.target;
  btn.textContent = "Generating..."; btn.disabled = true;
  fetch("/api/skill/generate", {method: "POST"})
    .then(function(r) { return r.json(); })
    .then(function(result) {
      alert("Skill v" + result.version + " generated with " + result.rule_count + " rules");
      if (window.location) window.location.reload();
    })
    .catch(function(e) { alert("Failed: " + e); })
    .finally(function() { btn.textContent = "Generate Skill"; btn.disabled = false; });
}
</script>
```

- [ ] **Step 5: Add CSS for skill panel**

In `web/static/style.css`, after the calibration-panel related styles (around line 2259), add:
```css
/* Skill Version Panel */
#skill-panel { display: none; }
.skill-version-item {
  display: flex; align-items: center; gap: 4px;
  padding: 4px 6px; border-radius: 4px; margin: 2px 0;
  border: 1px solid transparent; font-size: 11px;
}
.skill-version-item:hover { background: var(--panel-bg); border-color: var(--border); }
.skill-version-item.active { border-color: var(--accent); background: #e8f0fe; }
.skill-active-badge {
  font-size: 9px; background: var(--accent); color: #fff;
  padding: 1px 5px; border-radius: 8px; font-weight: 600;
}
```

Also add the skill panel to all the mode hide-rules. In the `#app.historyagent-mode` block, add:
```css
#app.historyagent-mode #skill-panel { display: none; }
```

And add a rule so skill-panel is NOT hidden by default in history tab:
```css
#app:not(.historyagent-mode) #skill-panel { display: none; }
```

- [ ] **Step 6: Verify**

```
python -c "
with open('web/templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()
assert 'skill-panel' in html
assert 'skill_panel.js' in html
assert 'Generate Skill' in html
print('Skill panel HTML present')
"
```

- [ ] **Step 7: Commit**

```
git add web/templates/index.html web/static/style.css
git commit -m "feat(ui): add skill panel HTML container, CSS styles, and tab visibility"
```

---

### Task 7: Add skill panel visibility to tab switching in app.js

**Files:**
- Modify: `web/static/app.js`

- [ ] **Step 1: Find calibration panel visibility logic**

Around line 1432-1436 in app.js:
```javascript
    var calPanel = document.getElementById("calibration-panel");
    if (calPanel) {
      calPanel.style.display = (tabName === "history") ? "" : "none";
    }
```

- [ ] **Step 2: Add skill panel visibility**

After the calibration panel block, add:
```javascript
    var skillPanel = document.getElementById("skill-panel");
    if (skillPanel) {
      skillPanel.style.display = (tabName === "history") ? "" : "none";
    }
```

- [ ] **Step 3: Verify**

```
node -e "
const fs = require('fs');
const js = fs.readFileSync('web/static/app.js', 'utf8');
console.log('skill-panel in app.js:', js.includes('skill-panel'));
new Function(js);
console.log('Syntax OK');
"
```

- [ ] **Step 4: Commit**

```
git add web/static/app.js
git commit -m "fix(ui): show skill panel on history tab, hidden on others"
```

---

### Task 8: Verify end-to-end

- [ ] **Step 1: Full import and syntax check**

```
python -c "
from web.app import app
print('App loads OK')
" && node -e "
const fs = require('fs');
new Function(fs.readFileSync('web/static/skill_panel.js', 'utf8'));
console.log('skill_panel.js syntax OK');
"
```

- [ ] **Step 2: Verify API routes**

```
python -c "
from web.app import app
# Check that skill routes exist
paths = set()
for r in app.router.routes:
    if hasattr(r, 'original_router'):
        for sr in r.original_router.routes:
            paths.add(sr.path)
skill_paths = sorted([p for p in paths if 'skill' in p.lower()])
print('Skill routes:', skill_paths)
assert len(skill_paths) >= 4  # versions, versions/{v}, diff, generate, active
"
```

- [ ] **Step 3: Verify Mentor tool is registered**

```
python -c "
from capitalradar.advisory.history_agent import build_history_agent_tools
tools = build_history_agent_tools({})
assert any(t.name == 'generate_analyst_skill' for t in tools)
print('generate_analyst_skill tool OK')
"
```

- [ ] **Step 4: Final commit**

```
git add -A
git commit -m "feat: skill review → generation → versioning closed loop"
```
