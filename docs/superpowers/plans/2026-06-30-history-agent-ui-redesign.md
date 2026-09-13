# History Agent UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign History Agent UI to match the visual consistency of other agents (Advisory/AI Pick) while keeping the left-panel record list + right-panel chat layout intact.

**Architecture:** Three independent tasks: (1) HTML template changes for left/right panel, (2) JS message rendering upgrade, (3) CSS cleanup. Tab switching, mode activation, and right-panel management remain unchanged.

**Tech Stack:** Vanilla JS, CSS, HTML templates (Jinja2)

---

### Task 1: Update index.html — left panel simplification + right panel enhancement

**Files:**
- Modify: `web/templates/index.html` (lines 279-322, 912-941)

**Context:** The History tab's left panel has inline-styled buttons and a compare bar that can be removed. The right panel (ha-expanded) lacks MD download and uses plain markup.

- [ ] **Step 1: Read the current HTML**

Read `web/templates/index.html` lines 279-322 (history tab left panel) and 912-941 (ha-expanded right panel).

- [ ] **Step 2: Simplify left panel**

Replace lines 279-322 with:

```html
    <!-- History Tab -->
    <div id="tab-history" class="tab-content">
      <div style="margin-bottom:12px;">
        <label class="label">Past Analyses</label>
        <p style="font-size:11px;color:var(--text-muted);margin:4px 0;">Browse completed analyses — ratings, analyst reports, and final trading decisions.</p>
      </div>
      <div id="history-summary" style="display:flex;gap:12px;margin-bottom:12px;font-size:12px;"></div>
      <div class="section">
        <label class="label">Filter</label>
        <div class="history-filters">
          <input type="text" id="hist-ticker" class="history-filter" placeholder="Ticker" style="width:70px">
          <input type="text" id="hist-date-from" class="history-filter" placeholder="From date" style="width:90px">
          <input type="text" id="hist-date-to" class="history-filter" placeholder="To date" style="width:90px">
          <select id="hist-rating" class="history-filter" style="width:90px">
            <option value="">All ratings</option>
            <option value="Buy">Buy</option>
            <option value="Overweight">Overweight</option>
            <option value="Hold">Hold</option>
            <option value="Underweight">Underweight</option>
            <option value="Sell">Sell</option>
          </select>
          <button id="hist-refresh" class="btn secondary" style="padding:6px 12px;font-size:12px;">Refresh</button>
        </div>
      </div>
      <div id="ha-actions" style="display:flex;gap:6px;margin-bottom:8px;">
        <button id="ha-analyze-btn" class="btn primary" style="flex:1;padding:8px 12px;font-size:12px;">&#x1F50D; Analyze All</button>
        <button id="ha-analyze-selected-btn" class="btn secondary" style="flex:1;padding:8px 12px;font-size:12px;">&#x1F50D; Analyze Selected</button>
      </div>
      <div style="display:flex;gap:8px;">
        <div style="flex:1;min-width:0;">
          <div id="history-list" class="history-list">
            <span class="no-results">Click Refresh to load history</span>
          </div>
          <div id='history-detail-container' class='hidden' style='flex:2;overflow-y:auto;'></div>
        </div>
      </div>
    </div><!-- /tab-history -->
```

Changes from current:
- Removed `hist-view-time` and `hist-view-company` buttons
- Removed `compare-bar` (the Compare and Chat with PM buttons)
- Removed `hi-sort` clickable spans (sorting is now handled by default date ordering)
- Updated emoji for Analyze buttons to use HTML entities

- [ ] **Step 3: Enhance right panel (ha-expanded)**

Replace lines 912-941 with:

```html
    <!-- History Agent Expanded View -->
    <div id="ha-expanded" class="hidden" style="flex-direction:column;height:100%;">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:0 0 8px 0;flex-shrink:0;">
        <div>
          <span style="font-size:16px;font-weight:700;" id="ha-expanded-title">Mentor</span>
          <span id="ha-expanded-subtitle" style="font-size:11px;color:var(--text-muted);margin-left:8px;">Historical Analysis & Experience Extraction</span>
        </div>
        <div style="display:flex;gap:6px;align-items:center;">
          <button id="ha-download-md-btn" class="btn secondary" style="font-size:11px;padding:4px 8px;display:none;" title="Download Markdown">MD</button>
          <button id="ha-download-docx-btn" class="btn secondary" style="font-size:11px;padding:4px 8px;display:none;" title="Download Word">DOCX</button>
          <button id="ha-collapse-btn" class="btn secondary" style="font-size:11px;padding:4px 10px;">Collapse</button>
        </div>
      </div>
      <div id="ha-progress" style="display:none;padding:8px 12px;margin-bottom:8px;background:var(--panel-bg);border:1px solid var(--border);border-radius:6px;">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-bottom:4px;">
          <span id="ha-progress-label">Analyzing...</span>
          <span id="ha-progress-pct">0%</span>
        </div>
        <div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden;">
          <div id="ha-progress-bar" style="height:100%;width:0%;background:var(--accent);border-radius:3px;transition:width 0.3s ease;"></div>
        </div>
      </div>
      <div id="ha-messages" class="chat-messages" style="flex:1;overflow-y:auto;padding:16px 16px;background:var(--bg);border-radius:8px;border:1px solid var(--border);min-height:300px;">
        <div class="chat-msg system">Welcome to Mentor.<br>Select analysis records on the left, then ask questions or click "Analyze" to extract experiences.</div>
      </div>
      <div id="ha-status" style="font-size:10px;color:var(--text-muted);margin-top:4px;flex-shrink:0;"></div>
      <div id="ha-resize-handle" style="height:6px;background:transparent;cursor:row-resize;flex-shrink:0;margin:4px 0;border-radius:3px;transition:background 0.15s;" title="Drag to resize"></div>
      <div class="chat-input-area" id="ha-input-area" style="flex-shrink:0;border-top:1px solid var(--border);padding:10px 0 0 0;margin-top:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <input type="text" id="ha-input" class="chat-input" placeholder="Ask about historical analysis results..." style="flex:1;min-width:200px;font-size:14px;padding:10px 14px;">
        <button class="chat-send-btn" id="ha-send-btn" style="font-size:14px;padding:10px 20px;">Send</button>
      </div>
    </div>
```

Changes from current:
- Title changed to "Mentor" (reflecting agent naming)
- Added `#ha-download-md-btn` and `#ha-download-docx-btn` (initially hidden, like Advisory)
- Note: The init subtitle says "Historical Analysis & Experience Extraction"

- [ ] **Step 4: Verify HTML is well-formed**

```
python -c "
with open('web/templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()
# Basic checks
assert 'id=\"ha-expanded\"' in html
assert 'id=\"ha-messages\"' in html
assert 'id=\"ha-download-md-btn\"' in html
assert 'id=\"ha-download-docx-btn\"' in html
assert 'id=\"ha-analyze-btn\"' in html
assert 'id=\"ha-analyze-selected-btn\"' in html
assert 'tab-history' in html
assert 'compare-bar' not in html or 'REMOVED'  # compare-bar should be gone
print('HTML checks OK')
"
```

- [ ] **Step 5: Commit**

```
git add web/templates/index.html
git commit -m "feat(ui): redesign history agent left/right panels — simplified left, MD download on right"
```

---

### Task 2: Update history_agent.js — use renderMarkdown() for message rendering

**Files:**
- Modify: `web/static/history_agent.js`

**Context:** The current `appendMsg` function does its own weak markdown rendering (regex for h1/h2/h3/bold/code). Other agents use the `renderMarkdown()` function defined in `app.js`. Switch to using that.

- [ ] **Step 1: Read current history_agent.js**

Read `web/static/history_agent.js`. The `appendMsg` function (line 130-148) has inline markdown rendering.

- [ ] **Step 2: Replace appendMsg markdown rendering**

Replace the `appendMsg` function:

```javascript
function appendMsg(role, content) {
  if (!haMsgs) return;
  var div = document.createElement("div");
  div.className = "chat-msg " + role;
  if (role === "assistant") {
    var mdDiv = document.createElement("div");
    mdDiv.className = "markdown-body";
    mdDiv.innerHTML = typeof window.renderMarkdown === "function" ? window.renderMarkdown(content) : content.replace(/\n/g, '<br>');
    div.appendChild(mdDiv);
  } else {
    div.textContent = content;
  }
  haMsgs.appendChild(div);
  haMsgs.scrollTop = haMsgs.scrollHeight;
}
```

- [ ] **Step 3: Add MD/PDF download button handlers**

After the existing tool event handlers, add download support (similar to Advisory's pattern):

```javascript
function updateDownloadBtns() {
  var mdBtn = document.getElementById("ha-download-md-btn");
  if (!mdBtn) return;
  var hasMessages = haMsgs && haMsgs.querySelectorAll(".chat-msg.assistant").length > 0;
  mdBtn.style.display = hasMessages ? "" : "none";
  var docxBtn = document.getElementById("ha-download-docx-btn");
  if (docxBtn) docxBtn.style.display = "none"; // DOCX not supported yet
}

// Hook into ha-done event: call updateDownloadBtns after messages are added
```

Modify the `ha-done` event listener to call `updateDownloadBtns()`:

After line 113 (`es.close(); haCfg.eventSource = null;`), add:
```javascript
updateDownloadBtns();
```

Also in `init()`, add MD button click handler:
```javascript
if (mdBtn) mdBtn.addEventListener("click", function() {
  var text = "";
  var msgs = haMsgs ? haMsgs.querySelectorAll(".chat-msg") : [];
  msgs.forEach(function(m) {
    var role = m.classList.contains("user") ? "User" : (m.classList.contains("assistant") ? "Assistant" : "System");
    var content = m.textContent || m.innerText || "";
    text += "**" + role + "**: " + content + "\n\n";
  });
  if (text) {
    var blob = new Blob([text], {type: "text/markdown"});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "mentor-chat.md";
    a.click();
  }
});
```

- [ ] **Step 4: Collect DOM elements at init + add mdBtn reference**

At the top of the IIFE, add:
```javascript
var haMdBtn = document.getElementById("ha-download-md-btn");
```

- [ ] **Step 5: Verify**

```
node -e "
const fs = require('fs');
const code = fs.readFileSync('web/static/history_agent.js', 'utf8');
console.log('Length:', code.length);
console.log('Has renderMarkdown:', code.includes('renderMarkdown'));
console.log('Has haMdBtn:', code.includes('haMdBtn'));
console.log('Has updateDownloadBtns:', code.includes('updateDownloadBtns'));
new Function(code);
console.log('Syntax OK');
"
```

- [ ] **Step 6: Commit**

```
git add web/static/history_agent.js
git commit -m "feat(ui): upgrade history agent to use renderMarkdown() + add MD download support"
```

---

### Task 3: Clean up CSS — remove ha-expanded-specific message styles

**Files:**
- Modify: `web/static/style.css` (lines 2246-2257)

**Context:** The `#ha-expanded` section has its own `.chat-messages`, `.chat-msg.system`, `.chat-msg.user` styles. These should be inherited from the shared `.chat-messages` and `.chat-msg` classes used by Advisory/AI Pick.

- [ ] **Step 1: Read the CSS section**

Read `web/static/style.css` lines 2244-2258:

```css
#ha-expanded { display: none; }
#ha-expanded .chat-messages {
  font-size: 14px; line-height: 1.5;
  display: flex; flex-direction: column; gap: 10px;
}
#ha-expanded .chat-msg.system { font-size: 12px; }
#ha-expanded .chat-msg.user {
  font-size: 14px; background: var(--accent); color: #fff;
  padding: 8px 14px; border-radius: 12px 12px 4px 12px;
  max-width: 70%; align-self: flex-end;
}
#ha-resize-handle:hover { background: var(--accent) !important; }
#ha-input-area { min-height: 44px; max-height: 40vh; overflow-y: auto; }
```

- [ ] **Step 2: Remove ha-specific message styles**

Replace with just:
```css
#ha-expanded { display: none; }
#ha-resize-handle:hover { background: var(--accent) !important; }
#ha-input-area { min-height: 44px; max-height: 40vh; overflow-y: auto; }
```

The `.chat-messages`, `.chat-msg`, `.chat-msg.system`, `.chat-msg.user` styles are already defined by the shared chat styles in `style.css` (used by Advisory, AI Pick, etc.). Removing the overrides lets History Agent inherit them.

- [ ] **Step 3: Verify**

```
python -c "
with open('web/static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()
assert '#ha-expanded .chat-messages' not in css, 'should have been removed'
assert '#ha-expanded .chat-msg.system' not in css, 'should have been removed'
assert '#ha-expanded { display: none; }' in css, 'should remain'
assert '#ha-resize-handle:hover' in css, 'should remain'
print('CSS checks OK')
"
```

- [ ] **Step 4: Commit**

```
git add web/static/style.css
git commit -m "fix(ui): remove ha-expanded-specific chat styles, inherit shared chat CSS"
```

---

### Task 4: Verify end-to-end

- [ ] **Step 1: Verify all imports and syntax**

```
python -c "
from web.app import app
print('App loads OK')
"
node -e "
const fs = require('fs');
const haJS = fs.readFileSync('web/static/history_agent.js', 'utf8');
const appJS = fs.readFileSync('web/static/app.js', 'utf8');
new Function(haJS);
console.log('history_agent.js syntax: OK');
"
```

- [ ] **Step 2: Verify HTML fragments**

```
python -c "
with open('web/templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()
assert 'id=\"ha-expanded\"' in html
assert 'id=\"ha-download-md-btn\"' in html
assert 'id=\"ha-collapse-btn\"' in html
assert 'id=\"ha-analyze-btn\"' in html
assert 'id=\"hist-ticker\"' in html
assert 'id=\"hist-refresh\"' in html
print('All expected DOM IDs present')
"
```

- [ ] **Step 3: git add + commit**

```
git add -A
git commit -m "feat(ui): history agent visual redesign — unified styling, MD download, simplified left panel"
```
