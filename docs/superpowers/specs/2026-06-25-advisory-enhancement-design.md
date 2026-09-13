# Advisory Agent Enhancement — Design Spec

**Date:** 2026-06-25
**Status:** Draft
**Scope:** Investment Advisor memory/reflection subsystem + Strategy Agent + Backtesting Agent

---

## 1. Architecture Overview

```
Web Dashboard (FastAPI + SSE)
├── Advisory Agent          ← 主体 (增强: 记忆+反思子系统)
├── Deep Analysis Pipeline  ← 核心 (CapitalRadarGraph, 不变)
├── Stock Pick Agent        ← 配属 (不变)
├── Prediction Agent        ← 配属 (不变)
├── Strategy Agent          ← 新增: 独立标签页
└── Backtesting Agent       ← 新增: 独立标签页
```

All four agents share the same data/vendor/LLM layer, but each has its own
System Prompt, tool set, and SQLite tables.

The Advisory Agent gains two new tools for cross-agent scheduling:
`run_strategy_backtest` and `list_strategies`.

---

## 2. Memory & Reflection Subsystem (Advisory Agent built-in)

### 2.1 Data Model — SQLite `advisory_experiences`

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | auto |
| content | TEXT | Experience description (Chinese) |
| source_ticker | TEXT | Originating ticker |
| source_date | TEXT | YYYY-MM-DD |
| outcome | TEXT | Actual outcome summary e.g. "+8.2% hold 5d" |
| raw_return | REAL | Actual raw return if tracked |
| category | TEXT | Tag for grouping: `stop_loss` / `position` / `timing` / `sector` / `risk` / `other` |
| status | TEXT | `pending_review` → `active` → `archived` |
| lesson_abstract | TEXT | One-line summary for UI list |
| created_at | TEXT | ISO timestamp |

### 2.2 Status Lifecycle

```
顾问分析 → 效果追踪 → 模式识别 → 生成经验提案
                                         ↓
                                   pending_review
                                         ↓
                           你在UI中审核 → [确认/修改/否决]
                                         ↓
                                      active
                                         ↓
                            你随时在UI中 toggle → archived
```

Only `active` entries are injected into the Advisory System Prompt.

### 2.3 Prompt Injection Mechanism

When building the Advisory System Prompt, the backend queries `advisory_experiences`
WHERE `status='active'`, appends a section at the end:

```
## 经验库（可覆盖，共{N}条）
以下是你已验证的投资经验。当前场景适用时请优先遵循；若不适用请说明理由。

{entries rendered as markdown bullet list with category tags}
```

Every injection is logged in `advisory_experience_log`:

| Column | Description |
|---|---|
| id | PK |
| thread_id | Conversation thread |
| experience_ids | Comma-separated IDs injected |
| injected_at | ISO timestamp |

### 2.4 Experience Extraction Trigger

Not automatic — initiated by:
1. **Advisor proactively suggests** at conversation start or after new outcome data arrives
2. **User commands**: "回顾一下我的交易记录" / "有什么值得总结的"
3. **Batch prompt**: every 5 new resolved outcomes trigger a UI badge

### 2.5 API Endpoints

```
GET    /api/advisory/experiences?status=active|pending_review|archived|all
GET    /api/advisory/experiences/pending           → items pending review
PUT    /api/advisory/experiences/:id/approve        → pending → active
PUT    /api/advisory/experiences/:id/archive        → active → archived
PUT    /api/advisory/experiences/:id/reactivate     → archived → active
PUT    /api/advisory/experiences/:id/reject         → pending → archived
PUT    /api/advisory/experiences/:id                → edit content/category
POST   /api/advisory/experiences/extract            → trigger pattern extraction
GET    /api/advisory/experiences/log                → injection history
```

### 2.6 UI — Experience Panel

Collapsible panel inside the **Advisory** tab, below the chat area.

```
┌─ ⚙ 经验库 ──────────────────────────────────┐
│                                                │
│  全部(8)  │  ● 已启用(3)  │  ○ 待审核(2)  │  归档(3)  │
│                                                │
│  ┌──────────────────────────────────────────┐ │
│  │ ✅ [止损] 追高热门股必须设-5%止损线       │ │
│  │    601127 → 回撤12%  06/10              │ │
│  │                                    [归档] │ │
│  ├──────────────────────────────────────────┤ │
│  │ ✅ [时机] 政策跳空等3日确认承接后再入场    │ │
│  │    688981 → 被套  06/08                 │ │
│  │                                    [归档] │ │
│  ├──────────────────────────────────────────┤ │
│  │ ☐ [风险] 放量下跌不抄底等缩量企稳         │ │
│  │    000858 → 继续下跌  06/15              │ │
│  │                              [审核✓] [✗]  │ │
│  └──────────────────────────────────────────┘ │
│                                                │
└────────────────────────────────────────────────┘
```

---

## 3. Strategy Agent (独立标签页)

**Location:** `web/strategy_agent.py` + `capitalradar/strategy/`
**Tab label:** 📝 策略

### 3.1 System Prompt Role

> You are the CapitalRadar Strategy Agent, specialized in creating, managing,
> and refining trading strategies for A-share stocks.

### 3.2 Capabilities

| Capability | Tool | Description |
|---|---|---|
| Template library | `list_strategy_templates` | 7+ built-in templates with docs |
| LLM strategy generation | `generate_strategy_code` | NL → backtrader code; user previews before saving |
| Custom strategy CRUD | `save_strategy` / `load_strategy` / `delete_strategy` | Manage user's strategy library |
| Strategy comparison | `compare_strategy_params` | Show how param changes affect logic |
| Strategy export | `export_strategy_code` | Export as standalone Python file |

### 3.3 Built-in Templates

| Template ID | Name | Key Parameters |
|---|---|---|
| ma_cross | 双均线金叉/死叉 | fast_period, slow_period |
| macd | MACD 金叉/背离 | fast, slow, signal |
| rsi | RSI 超买超卖 | period, overbought, oversold |
| bollinger | 布林带突破 | period, std_dev |
| turtle | 海龟交易法则 | entry_period, stop_atr |
| ma_arrange | 均线多头排列 | short_ma, mid_ma, long_ma |
| volume_breakout | 成交量突破 | volume_mult, price_period |

### 3.4 Data Model — `strategy_templates`

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | auto |
| name | TEXT | Human-readable name |
| description | TEXT | What the strategy does |
| type | TEXT | `builtin` / `custom_llm` / `custom_manual` |
| template_id | TEXT | Identifies which builtin template (null for custom) |
| parameters | JSON | Default parameter values + constraints |
| code | TEXT | Full backtrader strategy Python code |
| tags | TEXT | Comma-separated tags for search |
| is_favorite | INTEGER | 0/1 for quick access |
| parent_id | INTEGER | null for originals, points to parent for derived strategies |
| created_at | TEXT | |
| updated_at | TEXT | |

`strategy_versions` table records code changes:

| Column | Description |
|---|---|
| id | PK |
| strategy_id | FK → strategy_templates |
| parameters | JSON snapshot |
| code | Full code snapshot |
| version_notes | User notes on what changed |
| created_at | |

### 3.5 Workflow

```
用户进入"策略"标签页
       ↓
  ① 顾问问候：展示模板库 + 已有策略
       ↓
  ② 用户选择
     ├─ "我要用双均线模板" → 填参 → 保存
     └─ "帮我写个策略：5日涨幅<10%且20日线向上..."
          → LLM 生成代码 → 用户预览/修改 → 确认 → 保存
       ↓
  结果：策略存入「我的策略库」
```

### 3.6 Investment Advisor Integration

```python
@tool
def list_strategies(query: str = "") -> str:
    """Search and list saved strategies from the strategy library."""

@tool
def get_strategy_detail(strategy_id: int) -> str:
    """Get full strategy code and parameters."""
```

---

## 4. Backtesting Agent (独立标签页)

**Location:** `web/backtest_agent.py` + `capitalradar/backtest/`
**Tab label:** 📊 回测

### 4.1 System Prompt Role

> You are the CapitalRadar Backtesting Agent, specialized in running and
> analyzing strategy backtests on A-share stocks using backtrader.

### 4.2 Capabilities

| Capability | Tool | Description |
|---|---|---|
| Import strategy | `load_strategy` | Load from strategy library |
| Manual backtest | `run_backtest` | Backtest a strategy on a ticker + date range |
| Sensitivity analysis | `run_sensitivity` | Sweep a parameter across values, show performance impact |
| Comparison | `compare_backtests` | Side-by-side metric comparison of multiple runs |
| View details | `get_backtest_detail` | Full trade log + equity curve |
| Export report | `export_backtest_report` | PDF / MD / JSON |

### 4.3 Data Model — `backtest_runs`

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | auto |
| strategy_id | INTEGER | FK → strategy_templates |
| ticker | TEXT | Backtested ticker |
| start_date | TEXT | YYYY-MM-DD |
| end_date | TEXT | YYYY-MM-DD |
| parameters_used | JSON | Actual parameters used |
| initial_capital | REAL | Default 100000 |
| results | JSON | annual_return, sharpe, max_drawdown, win_rate, profit_loss_ratio, total_trades |
| equity_curve | JSON | [{date, value}, ...] |
| trade_log | JSON | [{entry_date, exit_date, entry_price, exit_price, pnl, pnl_pct}, ...] |
| created_at | TEXT | |

### 4.4 Backtest Report Output

```
📊 回测报告
═══
══标的: 601127.SH 赛力斯
══策略: 双均线金叉(5,20)
══区间: 2025-01-01 → 2026-06-01
───
══年化收益率      23.5%
══夏普比率        1.42
══最大回撤       -12.3%
══胜率          58.7%
══盈亏比        2.1:1
══交易次数        46
───
[Chart.js equity curve]
[Trade log table]
```

### 4.5 Workflow

```
用户进入"回测"标签页
       ↓
① 用户选择策略来源
   ├─ 从「我的策略库」选一个
   └─ 直接填模板+参数
       ↓
② 选择标的 + 日期区间
       ↓
③ 回测引擎运行 → 展示报告
       ↓
④ 迭代
   ├─ 改参数 → 重新回测
   ├─ 换策略 → 横向对比
   └─ 敏感性分析 → 找最优参数
```

### 4.6 Investment Advisor Integration

```python
@tool
def run_backtest(
    ticker: str,
    strategy_id: int,
    parameters: dict = {},
    start_date: str = "",
    end_date: str = "",
) -> str:
    """Run a strategy backtest for a ticker, return performance summary."""

@tool
def compare_backtests(run_ids: list[int]) -> str:
    """Compare multiple backtest results side-by-side."""
```

---

## 5. Implementation Order

| Phase | Scope | Dependencies |
|---|---|---|
| Phase 1 | Memory & Reflection subsystem: SQLite tables, API endpoints, extraction logic, experience UI panel | None |
| Phase 2 | Backtesting Agent: backtrader engine wrapper, `backtest_runs` table, backtest agent agent+UI tab | None (standalone) |
| Phase 3 | Strategy Agent: strategy CRUD, template library, LLM code generation, `strategy_templates` table, UI tab | Phase 2 (shares engine) |
| Phase 4 | Cross-agent integration: Advisory tools (`list_strategies`, `run_backtest`, `compare_backtests`), experience-aware prompt injection | Phase 1 + 2 + 3 |

---

## 6. Non-Goals

- Real-time paper trading or live execution — out of scope
- Multi-asset / portfolio-level backtesting — single stock only in v1
- Automated strategy optimization (genetic algo, grid search) — manual sensitivity sweep only
- Backtrader datafeed for non-A-share / non-US markets — use existing dataflow layer
- LLM strategy code is previewed by the user before execution; the system does not auto-run generated code
