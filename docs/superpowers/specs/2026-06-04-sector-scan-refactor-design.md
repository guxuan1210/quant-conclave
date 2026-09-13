# Sector Scan UI 重构设计

**日期:** 2026-06-04
**范围:** `web/templates/index.html`, `web/static/app.js`

## 动机

当前 Sector Scan 页面存在 5 个问题：
1. Quick 按钮夹在 Step 1 和 Step 2 之间，破坏 1→2→3 的线性流
2. Value Discovery 和 MACD Convergence 两套扫描代码互相抄袭（~90行重复）
3. "Select Rotation Leaders" 按钮多余——Quick 按钮已自动选取 rotation leaders
4. RRG 图表在最底部，作为决策核心依据应该更靠上
5. 手动 Scan 进度简陋——只显示文字 "Scanning xxx (1/5)..."，无进度条和逐行业状态

## 目标

重组长为 **4 段清晰分区**，从上到下线性流：

```
Quick Scans → Rotation Monitor (RRG) → Manual Scan → Results
```

JS 端提取公共 `runQuickRotationScan()` 函数，手动 Scan 也加入可视化进度。

## HTML 结构（重组后）

```html
<div id="tab-sector">
  <!-- ═══ 1. Quick Scans: 两个按钮并排 ═══ -->
  <div class="section" id="quick-scans-section">
    <label class="label">Quick Scans — Auto-select Rotation Leaders</label>
    <div style="display:flex; gap:10px;">
      <div id="quick-value-box">...</div>      <!-- Value Discovery -->
      <div id="quick-convergence-box">...</div> <!-- MACD Convergence -->
    </div>
  </div>

  <!-- ═══ 2. Rotation Monitor (RRG) ═══ -->
  <div class="section" id="rrg-section">
    ... RRG canvas + legend + table（不变）
  </div>

  <!-- ═══ 3. Manual Scan ═══ -->
  <div class="section" id="manual-scan-section">
    Step 1: Select Industries    ← 去掉 "Select Rotation Leaders" 按钮
    Step 2: Screening Strategies
    Step 3: Global Filters
    [Scan Selected Industries]   ← 改名
  </div>

  <!-- ═══ 4. Results ═══ -->
  <div class="section" id="results-section">
    <div id="scan-progress">     ← 新增：手动 Scan 也能用进度条+行业列表
    <div id="scan-results">      ← 候选卡片
  </div>
</div>
```

### Quick Scans 子区域结构

两个 Quick 卡片用 flex 并排：

```html
<div style="display:flex; gap:10px;">
  <!-- Value Discovery -->
  <div style="flex:1; border:2px solid var(--accent); ...">
    <label style="color:var(--accent);">Value Discovery</label>
    <p>Find undervalued stocks in rotation leaders</p>
    Top <input id="quick-value-top-n" value="3"> industries
    <label><input id="quick-value-leaders-only" checked> Industry Leaders Only</label>
    <button id="quick-value-rotation-btn">Find Undervalued</button>
    <div id="quick-value-progress" class="hidden">
      <!-- progress bar + step text + industry list (共用 quick-scan-progress 结构) -->
    </div>
  </div>

  <!-- MACD Convergence -->
  <div style="flex:1; border:2px solid #d97706; ...">
    <label style="color:#d97706;">MACD Convergence</label>
    <p>Find MACD convergence signals — catch entries BEFORE golden cross</p>
    Top <input id="quick-convergence-top-n" value="30"> industries
    <label><input id="quick-convergence-leaders-only" checked> Industry Leaders Only</label>
    <button id="quick-convergence-rotation-btn">Find Convergence</button>
    <div id="quick-convergence-progress" class="hidden">
      <!-- progress bar + step text + industry list -->
    </div>
  </div>
</div>
```

### Manual Scan 改动

- 删除 `sector-select-rotation` 按钮及其事件（Quick Scans 已覆盖自动选取 rotation leaders）
- Scan 按钮文字改为 "Scan Selected Industries"（原来 "Scan for Golden Cross Candidates" 过时）
- 新增 `scan-progress` 容器，支持进度条 + 行业列表

## JS 重构

### 公共函数: `runQuickRotationScan(opts)`

```javascript
function runQuickRotationScan(opts) {
  // opts = {
  //   strategyName: "Value Discovery",
  //   strategyGroups: [...],
  //   topNInputId: "quick-value-top-n",
  //   leadersOnlyCbId: "quick-value-leaders-only",
  //   progressContainerId: "quick-value-progress",  // or null if no container
  //   barFillId: ..., stepTextId: ..., industryListId: ...,
  //   buttonId: "quick-value-rotation-btn",
  // }
  //
  // 流程:
  // 1. GET /api/rotation/rrg → filter leading+improving → top N
  // 2. 初始化进度条 + 行业状态列表（调用 buildIndustryList / updateIndustryStatus）
  // 3. 逐个 GET /api/sector/scan/{industry}
  //    → updateIndustryStatus() 实时更新
  // 4. 完成后去重 → 可选行业龙头过滤 → renderCandidates()
}
```

### 两个 Quick 按钮简化为一行调用

```javascript
quickValueBtn.addEventListener("click", function() {
  runQuickRotationScan({
    strategyName: "Value Discovery",
    strategyGroups: [{logic: "AND", conditions: [
      {name: "low_valuation", must: true},
      {name: "fund_turnaround", must: true, param: {days: 5}},
      {name: "bottom_breakout", must: false, param: {volume_mult: 1.3}},
      {name: "macd_golden_cross", must: false},
    ]}],
    topNInputId: "quick-value-top-n",
    leadersOnlyCbId: "quick-value-leaders-only",
    progressElId: "quick-value-progress",
    buttonId: "quick-value-rotation-btn",
  });
});

quickConvergenceBtn.addEventListener("click", function() {
  runQuickRotationScan({
    strategyName: "MACD Convergence",
    strategyGroups: [{logic: "AND", conditions: [
      {name: "macd_convergence", must: true},
      {name: "main_net_inflow", must: true, param: {min_amount: 3000}},
      {name: "volume_breakout", must: false, param: {multiple: 1.5}},
    ]}],
    topNInputId: "quick-convergence-top-n",
    leadersOnlyCbId: "quick-convergence-leaders-only",
    progressContainerId: "quick-convergence-progress",
    barFillId: "quick-convergence-bar-fill",
    stepTextId: "quick-convergence-step-text",
    industryListId: "quick-convergence-industry-list",
    buttonId: "quick-convergence-rotation-btn",
  });
});
```

### 手动 Scan 也加进度

手动 Scan 按钮（`scanBtn`）的点击逻辑改为：
1. 在 `scanResults` 顶部插入 `scan-progress` 容器（进度条 + 行业列表）
2. 逐行业扫描时调用 `updateIndustryStatus()`
3. 完成后移除进度容器，保留候选卡片

### 辅助函数提取

```javascript
// 构建行业状态列表 HTML（Quick 和 Manual 共用）
function buildIndustryList(industries, containerId) {...}

// 更新单个行业状态（Quick 和 Manual 共用）
function updateIndustryStatus(name, quadrant, status, found, containerId) {...}

// 从 scan results 中收集所有已渲染的候选（去重用）
function collectAndDedupe(candidates) {...}
```

## 删除项

| 元素 | 原因 |
|------|------|
| `sector-select-rotation` 按钮 (HTML + JS) | Quick Scans 已自动选取 rotation leaders |
| 旧版 `quick-value-progress` `<span>` | 替换为带进度条的 `div` 容器 |
| 手动 Scan 的 `scanSpinner` | 替换为进度条 + 行业列表 |

## 不变项

- `STRATEGY_OPTIONS` / `STRATEGY_PRESETS` — 不变
- `renderStrategyGroups()` / `renderCandidates()` / `renderCandidateList()` — 不变
- `runPreliminaryAnalysis()` / `batchAnalyzeAll()` — 不变
- RRG 渲染逻辑 (`drawRRG`, `renderRRGTable`) — 不变
- 后端 API — 零改动

## 边界情况

- RRG 返回空数据 → Quick 按钮提示 "No leading/improving industries found"
- 某行业扫描 HTTP 失败 → `updateIndustryStatus(name, quadrant, "error", 0)`，继续下一个
- 全部行业 0 候选 → 显示 "No signals found in rotation leader industries"
- Quick Scan 过程中按钮 disabled，防止重复点击
- 同一只股票出现在多个行业 → 按 `ts_code` 去重
- 手动 Scan 选中 0 个行业 → 按钮 disabled

## 验证

1. 打开 Web UI → Sector Scan 标签 → 确认 4 段分区从上到下排列
2. 点击 "Find Convergence" → 进度条 + 行业列表实时更新
3. 点击 "Find Undervalued" → 同上
4. 手动选中 3 个行业 → 点 "Scan Selected Industries" → 进度条 + 行业列表
5. "Run Preliminary Analysis" → 候选卡片不消失
6. `pytest -m unit -q` 94 pass
