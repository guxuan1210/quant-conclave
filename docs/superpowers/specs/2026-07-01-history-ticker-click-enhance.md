# 历史列表 Ticker 点击跳转增强设计

## 背景

深度分析完成后，结果记录会出现在历史智能体列表中。当前点击列表行会展示分析详情，但用户需要一种更快捷的方式：**点击股票代码直接跳回深度分析页面**，同时更新 K 线图、预填表单，但保留上次的分析结果（不重新运行分析、不擦除结果卡片）。

## 设计目标

1. 在历史列表的股票代码（`.hi-ticker`）上添加独立的点击交互
2. 切回 Deep Analysis 页面，更新 K 线图到该股票
3. 预填分析日期和分析师配置（来自历史记录的字段）
4. **不触发新分析**，保留现有结果卡片不动
5. 切回时清理其他标签页在 `#right-panel` 上残留的内联样式

## 方案

### 方案选择

推荐 **方案 B**：为一个现有函数增加 `autoRun` 参数，而非创建重复的新函数。

### 改动范围

涉及 [web/static/app.js](web/static/app.js)，共 3 处改动：

#### 1. `switchToAnalyzeAndRun` 函数签名（第 ~1717 行）

新增第四个参数 `autoRun`，默认 `true` 保持向后兼容：

```javascript
function switchToAnalyzeAndRun(ticker, date, analysts, autoRun = true) {
```

将末尾的 `setTimeout(() => runBtn.click(), 500)` 包裹在 `if (autoRun)` 条件内。

#### 2. 历史列表中 ticker 点击事件（在 `renderHistoryList` 函数内，第 ~1691 行附近）

`.hi-ticker` 元素添加独立 click 监听器，阻止冒泡（避免触发行级别的 `showHistoryDetail`）：

```javascript
list.querySelectorAll(".hi-ticker").forEach(function(el) {
  el.addEventListener("click", function(e) {
    e.stopPropagation();
    var item = el.closest(".history-item");
    var ticker = item.querySelector(".hi-ticker").textContent.trim();
    var date = item.querySelector(".hi-date").textContent.trim();
    // analysts 字段存储在 hi-ticker 或 history-item 的 data-* 属性中
    var analysts = item.dataset.analysts || "";
    switchToAnalyzeAndRun(ticker, date, analysts, false); // autoRun=false
  });
});
```

需要给 `.history-item` 增加 `data-analysts` 属性以在渲染时存储分析师配置。

#### 3. 样式微调（可选）

`.hi-ticker` 增加 `cursor: pointer` 和 `text-decoration: underline on hover` 以提示可点击性。已有 `.hi-ticker { cursor: pointer; }` 但可增强 hover 效果。

### 数据流

```
用户点击 .hi-ticker
  → e.stopPropagation() 防止触发行级 detail 展示
  → 从 DOM 提取 ticker / date / analysts
  → switchToAnalyzeAndRun(ticker, date, analysts, false)
    → 清除 app 上的所有 mode class
    → 切换到 analyze tab
    → 填充搜索框、日期
    → 设置分析师 toggle
    → loadChart(ticker, "max") 更新 K 线图
    → 跳过 runBtn.click()
    → 先前 _savedResultsHtml 在 showRightContent("results") 中保留
```

### 结果保留机制

现有的 `_savedResultsHtml` 机制已经工作：切出 Analyze 标签时保存 `resultsContainer.innerHTML`（第 ~1408 行），切回时恢复（第 ~1476 行）。本次改动不涉及这部分。

## 边界情况

- **无分析师字段**：历史记录可能没有存储 analysts，此时使用默认全选状态
- **重复点击**：在同一历史记录行多次点击 ticker 是安全的（幂等操作）
- **已选中其他股票后点击**：会替换当前选中的股票并更新图表
- **ticker Date 格式异常**：`loadChart` 和 `validateForm` 已有防御逻辑

## 测试建议

手动验证：
1. 完成一次分析 → 切到 History 标签
2. 点击某条记录的股票代码 → 应切到 Analyze 标签，K线图更新，结果不消失
3. 点击非 ticker 区域 → 应正常打开详情（现有行为不变）
4. 切换多个标签后回到 Analyze → K线图仍在，结果仍在
