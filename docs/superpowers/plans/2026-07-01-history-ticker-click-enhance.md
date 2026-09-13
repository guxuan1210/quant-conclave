# 历史列表 Ticker 点击跳转增强 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 点击历史智能体列表中的股票代码（`.hi-ticker`），跳转到深度分析页面，更新 K 线图、预填表单，但保留现有的分析结果（不重新运行）。

**Architecture:** 在已有 `switchToAnalyzeAndRun` 函数基础上加第四个参数 `autoRun`（默认 true），历史 ticker 点击时传 `false` 跳过自动分析。同时为 `.history-item` 元素补上 `data-analysts` 属性，并在 `.hi-ticker` 上绑定独立点击事件（阻止冒泡避免触发行级详情）。

**Tech Stack:** 纯前端改动，仅涉及 [web/static/app.js](web/static/app.js)

---

### Task 1: 修改 `switchToAnalyzeAndRun` 函数，增加 `autoRun` 参数

**Files:**
- Modify: `web/static/app.js:1736-1770`

- [ ] **Step 1: 修改函数签名并添加条件逻辑**

将原函数（第 1736-1770 行）替换为以下代码。关键变化：
- 第 4 个参数 `autoRun`，默认 `true`（向后兼容）
- `autoRun=false` 时不调用 `showRightContent("results")`（否则会清空结果），改为从 `_savedResultsHtml` 恢复
- 自动点击 Run 按钮的逻辑包裹在 `if (autoRun)` 内

```javascript
function switchToAnalyzeAndRun(ticker, date, analysts, autoRun) {
  if (autoRun === undefined) autoRun = true;
  // Clean up any expanded modes
  var app = document.getElementById("app");
  if (app) app.classList.remove("advisory-mode", "aipick-mode", "prediction-mode", "strategy-mode", "backtest-mode");
  currentTab = "analyze";
  document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
  var analyzeTab = document.querySelector('[data-tab="analyze"]');
  if (analyzeTab) analyzeTab.classList.add("active");
  var analyzeContent = document.getElementById("tab-analyze");
  if (analyzeContent) analyzeContent.classList.add("active");

  // When autoRun=false (ticker click from history), preserve existing results
  if (autoRun) {
    showRightContent("results");
  } else {
    // Still ensure the right panel is visible and chart/result containers show
    var rp = document.getElementById("right-panel");
    if (rp) {
      rp.style.display = "";
      rp.style.flexDirection = "";
      rp.style.height = "";
      rp.style.overflow = "";
    }
    var cc = document.getElementById("comparison-container");
    var hd = document.getElementById("history-detail-container");
    if (cc) { cc.innerHTML = ""; cc.classList.add("hidden"); }
    if (hd) { hd.innerHTML = ""; hd.classList.add("hidden"); }
    resultsContainer.classList.remove("hidden");
    if (_savedResultsHtml.trim()) {
      resultsContainer.innerHTML = _savedResultsHtml;
      // Rebuild nav and re-wire discuss button
      updateAnalyzeNav();
      var discussBtn = document.getElementById("discuss-with-pm-btn");
      if (discussBtn && sessionId) {
        discussBtn.addEventListener("click", function() { discussWithPM(); });
      }
    } else {
      // No saved results — show placeholder
      resultsContainer.innerHTML = '<div id="placeholder-hint" class="placeholder-hint">Configure and run an analysis to see results here.</div>';
    }
  }

  tickerInput.value = ticker;
  dateInput.value = date || new Date().toISOString().slice(0, 10);

  if (analysts) {
    var analystList = analysts.split(",");
    analystToggles.querySelectorAll(".toggle").forEach(function(t) {
      var key = t.dataset.analyst;
      if (analystList.indexOf(key) >= 0) { t.classList.add("active"); }
      else { t.classList.remove("active"); }
    });
  }

  // Set ticker directly and load chart
  selectedTicker = { symbol: ticker, name: ticker, exchange: "" };
  selectedSymbol.textContent = ticker;
  selectedName.textContent = ticker;
  selectedExchange.textContent = "";
  selectedCard.classList.remove("hidden");
  runBtn.disabled = false;
  loadChart(ticker, "max");
  // Auto-click Run only when autoRun=true
  if (autoRun) {
    setTimeout(function() { runBtn.click(); }, 500);
  }
}
```

- [ ] **Step 2: 验证修改**

检查项：
1. 现有调用 `switchToAnalyzeAndRun(ticker, date, analysts)` —— 第 4 个参数为 undefined，函数内 `autoRun = true`，自动运行 ✅
2. 新调用 `switchToAnalyzeAndRun(ticker, date, analysts, false)` —— autoRun=false，不自动运行 ✅
3. `autoRun=false` 时，只要 `_savedResultsHtml` 非空就恢复结果卡片 ✅

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat: add autoRun parameter to switchToAnalyzeAndRun

- autoRun=true (default): existing behavior, auto-click Run after pre-fill
- autoRun=false: skip auto-run, restore saved analysis results from _savedResultsHtml
- Preserve chart, results, and form state when clicking a history ticker

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 在历史列表的 `.history-item` 上加入 `data-analysts` 属性

**Files:**
- Modify: `web/static/app.js:1649`

- [ ] **Step 1: 修改 `_renderOneItem` 函数**

在 `_renderOneItem` 的 `.history-item` div 上加上 `data-analysts` 属性（第 1649 行），使 ticker 点击时可以读取分析师配置：

```javascript
    var companyName = it.company_name || "";
    return '<div class="history-item" data-run-id="' + esc(it.run_id) + '"' +
      (it.analysts ? ' data-analysts="' + esc(it.analysts) + '"' : '') + '>' +
```

即：在 `data-run-id` 属性之后，有条件地追加 `data-analysts` 属性。

- [ ] **Step 2: Commit**

```bash
git add web/static/app.js
git commit -m "feat: add data-analysts to history-item for ticker-click pre-fill

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 在 `.hi-ticker` 上绑定独立点击事件

**Files:**
- Modify: `web/static/app.js:1695-1734`（在删除按钮和重新分析按钮的绑定代码之间）

- [ ] **Step 1: 添加 `.hi-ticker` 点击监听器**

在删除按钮监听器（第 1695-1708 行）和重新分析按钮监听器（第 1709-1718 行）之间，插入 ticker 点击代码：

```javascript
  // Ticker click: switch to analyze tab with pre-filled values (no auto-run)
  list.querySelectorAll(".hi-ticker").forEach(function(el) {
    el.addEventListener("click", function(e) {
      e.stopPropagation();
      e.preventDefault(); // prevent any default link behavior
      var item = el.closest(".history-item");
      if (!item) return;
      var ticker = item.querySelector(".hi-ticker").textContent.trim();
      var date = item.querySelector(".hi-date").textContent.trim();
      var analysts = item.dataset.analysts || "";
      switchToAnalyzeAndRun(ticker, date, analysts, false);
    });
  });
```

- [ ] **Step 2: 给 `.hi-ticker` 添加视觉提示（可选）**

在 `_renderOneItem` 函数中，给 `.hi-ticker` span 增加一个 title 属性和 cursor 样式提示：

```javascript
      '<span class="hi-ticker" style="cursor:pointer;" title="Click to view chart and pre-fill analysis form">' + esc(it.ticker) + '</span>' +
```

或者直接在已有行（第 1652 行）上修改。

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "feat: add click handler on .hi-ticker to jump to analyze tab

- Clicking a stock code in history list switches to Deep Analysis page
- Pre-fills ticker, date, and analyst configuration
- autoRun=false so existing results are preserved, no new analysis triggered
- e.stopPropagation() prevents triggering row-level showHistoryDetail

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 验证完整流程

- [ ] **Step 1: 重启 Web 服务**

```bash
# 终止当前 run_web.py 后重启
python run_web.py
```

确认服务在 `http://127.0.0.1:8001` 正常启动。

- [ ] **Step 2: 手动测试流程**

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 输入股票代码（如 AAPL），填写日期，点击 Run Analysis | 分析正常完成，结果显示在右侧 |
| 2 | 点击 History 标签 | 历史列表中出现该记录 |
| 3 | 点击历史记录的 AAPL 股票代码（不是行，是代码文字）| 自动切回 Deep Analysis 标签，K线图更新为 AAPL，之前的分析结果卡片保留可见 |
| 4 | 点击历史列表中另一条不同股票记录的代码 | 切回 Analyze 标签，K线图更新为另一股票，结果卡片保留 |
| 5 | 点击行的空白区域（非 ticker 文字）| 正常打开分析详情（现有行为不变） |
| 6 | 多次点击同一个 ticker | 每次都是幂等的——切换 tab、更新图表，不重复触发新分析 |

- [ ] **Step 3: 验证无回归**

- 检查 History 标签的「重新分析」按钮（🔁）—— 仍然触发 `switchToAnalyzeAndRun(ticker, date, analysts)`（第 4 个参数为 undefined，autoRun 默认为 true）→ 自动运行 ✅
- 检查 tab 切换后的图表 resize 逻辑 —— 切回 Analyze 标签时 K线图正常渲染 ✅
- 检查 tab 切换后内联样式清理 —— 之前的修复（清理 rightPanel 内联样式）依然生效 ✅
