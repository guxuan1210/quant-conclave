# History Ticker Click: Load Record Data into Deep Analysis

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When clicking a ticker in the History Agent list, navigate to the analyze tab with the record's analysis data displayed in the right panel, chart showing current data, and date input set to today.

**Architecture:** Modify the ticker click handler to extract `run_id` from the history item. Add a new function `renderHistoryResultInAnalyze(resultsContainer, runId, meta, state)` that renders the historical record's analysis data into the `#results-container` div (reusing rendering utility functions from `showHistoryDetail`). Update `switchToAnalyzeAndRun` to accept `runId` and, when `autoRun=false`, fetch and render the record data. Always set `dateInput` to today's date.

**Tech Stack:** Vanilla JS (ES5-compatible), no build tools. Fetch API. DOM manipulation via innerHTML.

**Files touched:**
- `web/static/app.js` — ticker handler, `switchToAnalyzeAndRun`, new render function

---

## Task 1: Modify ticker click handler to pass run_id

**File:** `web/static/app.js:1736-1746`

The `.hi-ticker` click handler currently extracts `ticker`, `date`, and `analysts` from the DOM, but does not extract the `run_id`. We need `run_id` to fetch the full analysis state.

- [ ] **Step 1: Add run_id extraction to the ticker click handler**

Current code (lines 1736-1746):
```js
  // Ticker click: switch to analyze tab with pre-filled values (no auto-run)
  list.querySelectorAll(".hi-ticker").forEach(function(el) {
    el.addEventListener("click", function(e) {
      e.stopPropagation();
      var item = el.closest(".history-item");
      if (!item) return;
      var ticker = el.textContent.trim();
      var date = item.querySelector(".hi-date").textContent.trim();
      var analysts = item.dataset.analysts || "";
      switchToAnalyzeAndRun(ticker, date, analysts, false);
    });
  });
```

Replace with:
```js
  // Ticker click: switch to analyze tab, load record's analysis results
  list.querySelectorAll(".hi-ticker").forEach(function(el) {
    el.addEventListener("click", function(e) {
      e.stopPropagation();
      var item = el.closest(".history-item");
      if (!item) return;
      var ticker = el.textContent.trim();
      var date = item.querySelector(".hi-date").textContent.trim();
      var analysts = item.dataset.analysts || "";
      var runId = item.dataset.runId || "";
      switchToAnalyzeAndRun(ticker, date, analysts, false, runId);
    });
  });
```

- [ ] **Step 2: Verify change**

Check that the `runId` extraction reads from `item.dataset.runId` which is set via `data-run-id` attribute in `_renderOneItem`:
```js
// line 1675: '<div class="history-item" data-run-id="' + esc(it.run_id) + '"' + ...
```
This confirms `runId` will be available.

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat: pass runId from history ticker click to switchToAnalyzeAndRun"
```

---

## Task 2: Update switchToAnalyzeAndRun to accept runId and show data

**File:** `web/static/app.js:1774-1855`

The function `switchToAnalyzeAndRun(ticker, date, analysts, autoRun)` needs a new 5th parameter `runId`. When `autoRun=false` and `runId` is provided, it should fetch the full analysis state and render it in the results container.

This task also changes the date: when `runId` is provided (viewing historical record), `dateInput` is set to today so the chart and future "Run" button use current data.

- [ ] **Step 1: Update function signature and date logic**

Change the function signature and early date handling:

```js
function switchToAnalyzeAndRun(ticker, date, analysts, autoRun, runId) {
  if (autoRun === undefined) autoRun = true;
```

At line 1832-1833 where date is set, change to:
```js
  // When viewing a historical record (runId provided), always use today's date
  // so chart shows current data and Run button analyzes current conditions.
  dateInput.value = (runId ? new Date().toISOString().slice(0, 10) : (date || new Date().toISOString().slice(0, 10)));
```

- [ ] **Step 2: Add fetch-and-render logic when autoRun=false and runId is provided**

After the chart loads (after line 1851 `loadChart(ticker, "max")`), add:

```js
  // Load historical record's analysis data into the right panel
  if (!autoRun && runId) {
    fetchAndDisplayRecordResults(runId);
  }
```

Place right before the auto-run block (line 1852):
```js
  loadChart(ticker, "max");
  // Load historical record's analysis data into the right panel
  if (!autoRun && runId) {
    fetchAndDisplayRecordResults(runId);
  }
  // Auto-click Run only when autoRun=true
```

Also update the `autoRun=false` (no runId) branch to clear the chart inline styles properly. The existing code at line 1812 already does `chartContainer.style.display = ""`.

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat: accept runId in switchToAnalyzeAndRun, use today's date for ticker click"
```

---

## Task 3: Create fetchAndDisplayRecordResults function

**File:** `web/static/app.js` — add new function near `showHistoryDetail` (~line 1958)

This function fetches the record metadata and full state from the API and renders it into the `results-container`, reusing the same rendering patterns as `showHistoryDetail`.

- [ ] **Step 1: Add the new function**

Add after `switchToAnalyzeAndRun` (before `updateCompareBar`):

```js
function fetchAndDisplayRecordResults(runId) {
  resultsContainer.innerHTML = '<div class="result-card" style="border-left-color:#888;padding:16px;"><div style="text-align:center;color:var(--text-muted);">Loading record...</div></div>';
  Promise.all([
    fetch("/api/results/" + runId).then(function(r) { return r.json(); }),
    fetch("/api/results/" + runId + "/full").then(function(r) { return r.json(); }).catch(function() { return null; }),
  ]).then(function(results) {
    var meta = results[0];
    var state = results[1];
    if (!meta) {
      resultsContainer.innerHTML = '<div class="placeholder-hint">Record not found.</div>';
      return;
    }
    var html = renderRecordResultsHtml(meta, state, runId);
    resultsContainer.innerHTML = html;
    // Re-wire the Re-Analyze button inside the rendered content
    var reBtn = document.getElementById("detail-reanalyze-btn");
    if (reBtn) {
      reBtn.addEventListener("click", function() {
        switchToAnalyzeAndRun(reBtn.dataset.ticker, new Date().toISOString().slice(0, 10), reBtn.dataset.analysts);
      });
    }
    // Re-wire Chat with PM button
    var chatBtn = document.getElementById("history-chat-btn");
    if (chatBtn) {
      chatBtn.addEventListener("click", function() {
        _selectedTickers[runId] = meta.ticker;
        _selectedRunMeta[runId] = { ticker: meta.ticker, date: meta.date };
        showHistoryChatPanel(runId);
      });
    }
  }).catch(function() {
    resultsContainer.innerHTML = '<div class="placeholder-hint">Failed to load record.</div>';
  });
}
```

- [ ] **Step 2: Create renderRecordResultsHtml helper**

This extracts the rendering HTML from `showHistoryDetail` into its own reusable function. Add it right before `fetchAndDisplayRecordResults`:

```js
function renderRecordResultsHtml(meta, state, runId) {
  var html = '<div class="result-card" style="border-left-color:#888;">';
  html += '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">';
  html += '<span class="card-title">' + esc(meta.ticker) + ' — ' + esc(meta.date) + '</span>';
  html += '<div style="display:flex;gap:8px;align-items:center;">';
  var rtg = (meta.rating || "Hold").toLowerCase();
  html += '<span class="rating-badge ' + rtg + '">' + esc(meta.rating || "Hold") + '</span>';
  html += '<button class="btn primary" style="padding:4px 12px;font-size:12px;" id="detail-reanalyze-btn" data-ticker="' + esc(meta.ticker) + '" data-date="' + esc(meta.date) + '" data-analysts="' + esc(meta.analysts || '') + '">Re-Analyze</button>';
  html += '<a href="/api/results/' + esc(runId) + '/download?format=md" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">MD</a>';
  html += '<a href="/api/results/' + esc(runId) + '/download?format=json" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">JSON</a>';
  html += '</div></div>';

  // Meta row
  html += '<div class="card-body" style="font-size:12px;color:var(--text-muted);padding-bottom:0;">';
  html += 'Provider: ' + esc(meta.provider || "N/A") + ' | ';
  html += 'Deep: ' + esc(meta.deep_model || "N/A") + ' | ';
  html += 'Quick: ' + esc(meta.quick_model || "N/A") + ' | ';
  html += 'Elapsed: ' + ((meta.total_elapsed_ms || 0) / 1000).toFixed(1) + 's | ';
  html += 'Risk: ' + esc(meta.risk_level || "N/A");
  if (meta.next_analysis_date) {
    html += ' | <b>Next Analysis: ' + esc(meta.next_analysis_date) + '</b>';
  }
  html += '</div>';

  // Key metrics
  if (state && state.key_metrics) {
    html += '<div class="card-body" style="padding-top:0;">' + renderKeyMetricsCards(state.key_metrics) + '</div>';
  }
  if (state && state.key_metrics && state.key_metrics.retracement) {
    html += '<div class="card-body" style="padding-top:0;">' + renderRetracementCard(state.key_metrics.retracement) + '</div>';
  }

  // Full decision
  html += '<div class="card-body markdown-body">';
  if (state && state.final_trade_decision) {
    html += renderMarkdown(String(state.final_trade_decision).substring(0, 12000));
  } else {
    html += "<p>No detailed report available</p>";
  }
  html += '</div>';

  // Tool-call traces
  if (state && state.analyst_tool_traces) {
    html += '<div class="card-body" style="padding-top:0;">' + renderToolTraces(state.analyst_tool_traces) + '</div>';
  }

  // Analyst reports (collapsible)
  if (state) {
    var reportKeys = [
      {key: "capital_flow_report", label: "Capital Flow Analyst"},
      {key: "market_report", label: "Market Analyst"},
      {key: "sentiment_report", label: "Sentiment Analyst"},
      {key: "news_report", label: "News Analyst"},
      {key: "fundamentals_report", label: "Fundamentals Analyst"},
      {key: "competitor_report", label: "Competitor Analyst"},
      {key: "partner_report", label: "Partner Analyst"},
    ];
    var hasReports = false;
    html += '<div class="card-body" style="padding-top:0;">';
    html += '<details style="margin-top:12px;"><summary style="cursor:pointer;font-weight:600;font-size:13px;">Analyst Reports</summary>';
    html += '<div class="markdown-body" style="margin-top:8px;">';
    reportKeys.forEach(function(rk) {
      var report = state[rk.key];
      if (report && String(report).trim()) {
        hasReports = true;
        html += '<h4>' + rk.label + '</h4>';
        html += renderMarkdown(String(report).substring(0, 5000));
        html += '<hr>';
      }
    });
    if (!hasReports) html += '<p>No analyst reports stored</p>';
    html += '</div></details></div>';
  }

  // Chat with PM button
  html += '<div class="card-body" style="padding-top:8px;border-top:1px solid var(--border);margin-top:12px;">';
  html += '<button class="btn primary" id="history-chat-btn" data-run-id="' + esc(runId) + '" style="padding:8px 16px;font-size:13px;">Chat with Portfolio Manager</button>';
  html += '</div>';
  html += '</div>';

  return html;
}
```

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat: fetch and render historical analysis results when clicking ticker"
```

---

## Verification

1. Open the web UI at `http://127.0.0.1:8001`
2. Click the **History** tab — wait for the list to load
3. Click any ticker text (`.hi-ticker`) in the history list
4. **Expected:** Switches to Analyze tab, right panel shows the record's full analysis results (rating, key metrics, decision, analyst reports), K-line chart loads with current data, date input shows today's date
5. Click the **Re-Analyze** button inside the rendered record — it should re-run analysis with today's date
6. Click the **Chat with Portfolio Manager** button — it should open the chat panel for that record
