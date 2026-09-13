# Stock Picking Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance CapitalRadar's static stock screening with an LLM-driven adaptive Stock Picking Agent: unified smart money score, dynamic strategy generation, natural-language pick explanations, and a performance tracking loop.

**Architecture:** Three new modules in `capitalradar/sector_scan/` — `smart_money_score.py` (institutional signal scoring), `dynamic_strategy.py` (LLM strategy JSON → execution), `pick_tracker.py` (DB persistence + performance resolution). Integrated into Advisory Agent via new `@tool` functions and into Stock Pick tab via new "AI Smart Pick" sub-tab.

**Tech Stack:** Python 3.10+, LangChain `@tool` decorator, SQLite, existing `capitalradar/dataflows/` vendors, vanilla JS frontend.

---

### Task 1: Smart Money Score

**Files:**
- Create: `capitalradar/sector_scan/smart_money_score.py`

- [ ] **Step 1: Write the file**

```python
"""Unified Smart Money Score — institutional capital flow detection for retail investors.

Synthesizes 7 dimensions of institutional participation into a single 0-100 score.
The core money-flow dimension acts as a hard gate: any stock scoring < 40 on net
inflow trend is automatically downgraded regardless of other signals.
"""

from typing import Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed


def compute_smart_money_score(ticker: str, config: dict) -> Tuple[float, Dict]:
    """Return (total_score, per_dimension_breakdown) for a single stock.

    total_score: 0-100, weighted average of sub-scores.
    per_dimension_breakdown: {dim_label: {"score": float, "detail": str}, ...}
    """
    from capitalradar.agents.utils.capital_flow_tools import (
        get_money_flow, get_hsgt_flow, get_margin_trading,
        get_institutional_holders, get_analyst_recommendations,
        get_major_holders,
    )
    from capitalradar.agents.utils.intraday_tools import get_realtime_quote

    dimensions = [
        ("主力净流入", 0.25, _score_money_flow, [ticker, config]),
        ("大单深度",   0.15, _score_big_order, [ticker, config]),
        ("北向资金",   0.15, _score_north_bound, [ticker, config]),
        ("融资趋势",   0.10, _score_margin, [ticker, config]),
        ("机构持仓",   0.15, _score_institutional, [ticker, config]),
        ("盘中异常",   0.10, _score_intraday, [ticker, config]),
        ("分析师评级", 0.10, _score_analyst, [ticker, config]),
    ]

    breakdown = {}
    total = 0.0
    money_flow_score = None
    remaining_weight = 0.0
    scores = {}

    # Evaluate all dimensions
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for label, weight, fn, args in dimensions:
            futures[pool.submit(fn, *args)] = (label, weight)
        for fut in as_completed(futures):
            label, weight = futures[fut]
            try:
                score, detail = fut.result(timeout=30)
            except Exception:
                score, detail = 0.0, "数据获取失败"
            scores[label] = (score, weight, detail)

    # Hard gate: core money flow must pass
    money_score = scores.get("主力净流入", (0, 0, ""))[0]
    if money_score < 40:
        for label in scores:
            s, w, d = scores[label]
            if label != "主力净流入":
                scores[label] = (min(s, money_score), w, d + " [主力资金不足，降级]")
        # Cap at money_score
        total = money_score * 0.5  # severely penalised
    else:
        for label, (score, weight, detail) in scores.items():
            breakdown[label] = {"score": round(score, 1), "detail": detail}
            total += score * weight

    total = max(0.0, min(100.0, total))
    breakdown["_total"] = round(total, 1)
    return round(total, 1), breakdown


# ── Sub-scoring functions ─────────────────────────────────────────

def _score_money_flow(ticker: str, config: dict) -> Tuple[float, str]:
    """Core dimension: net inflow trend over 5/10/20 days."""
    from capitalradar.agents.utils.capital_flow_tools import get_money_flow
    try:
        data = get_money_flow.invoke({"ticker": ticker, "days": 20})
        text = str(data).lower()
    except Exception:
        return 30.0, "资金流数据不可用"
    if not text or "no data" in text or "skip" in text:
        return 30.0, "无资金流数据"

    score = 50.0
    details = []

    if "净流入" in text or "inflow" in text:
        score += 15
        details.append("有净流入")
    if "连续" in text or "consecutive" in text:
        score += 10
        details.append("连续流入")
    if any(w in text for w in ["大幅", "显著", "strong", "large", "significant"]):
        score += 10
        details.append("流入显著")
    if any(w in text for w in ["流出", "outflow", "净流出"]):
        score -= 20
        details.append("出现流出")

    # Parse numeric inflow amounts if present
    import re
    amounts = re.findall(r'([\-\d,.]+)\s*(?:亿|万|百万)', text)
    if amounts:
        score += 5
        details.append("含具体金额")

    return min(100, max(0, score)), "; ".join(details) or "基础评估"


def _score_big_order(ticker: str, config: dict) -> Tuple[float, str]:
    """Large-order participation depth."""
    try:
        data = get_money_flow.invoke({"ticker": ticker, "days": 5})
        text = str(data)
    except Exception:
        return 40.0, "大单数据不可用"

    score = 50.0
    if "大单" in text or "特大单" in text:
        score += 15
    if "主力" in text or "机构" in text:
        score += 10
    return min(100, max(0, score)), "基于大单数据"


def _score_north_bound(ticker: str, config: dict) -> Tuple[float, str]:
    """North-bound (HSGT) capital flow."""
    from capitalradar.agents.utils.capital_flow_tools import get_hsgt_flow
    try:
        data = get_hsgt_flow.invoke({"days": 5})
        text = str(data).lower()
    except Exception:
        return 40.0, "北向资金数据不可用"

    score = 50.0
    if "流入" in text or "净买" in text or "inflow" in text:
        score += 15
    if "北向" in text or "north" in text:
        score += 5
    if "流出" in text or "净卖" in text:
        score -= 15
    return min(100, max(0, score)), "基于北向资金数据"


def _score_margin(ticker: str, config: dict) -> Tuple[float, str]:
    """Margin trading trend."""
    from capitalradar.agents.utils.capital_flow_tools import get_margin_trading
    try:
        data = get_margin_trading.invoke({"ticker": ticker, "days": 10})
        text = str(data).lower()
    except Exception:
        return 40.0, "融资数据不可用"

    score = 50.0
    if "增加" in text or "上升" in text or "买" in text:
        score += 10
    if "减少" in text or "下降" in text or "卖" in text:
        score -= 10
    return min(100, max(0, score)), "基于融资融券数据"


def _score_institutional(ticker: str, config: dict) -> Tuple[float, str]:
    """Institutional holdings changes."""
    from capitalradar.agents.utils.capital_flow_tools import get_institutional_holders
    try:
        data = get_institutional_holders.invoke({"ticker": ticker})
        text = str(data).lower()
    except Exception:
        return 40.0, "机构持仓数据不可用"
    if not text or "no data" in text:
        return 50.0, "无机构持仓数据"

    score = 50.0
    if "增持" in text or "新进" in text or "increase" in text:
        score += 15
    if "减持" in text or "退出" in text or "decrease" in text:
        score -= 15
    if "基金" in text or "fund" in text:
        score += 5
    return min(100, max(0, score)), "基于机构持仓数据"


def _score_intraday(ticker: str, config: dict) -> Tuple[float, str]:
    """Intraday anomaly / manipulation detection."""
    from capitalradar.agents.utils.intraday_tools import get_realtime_quote
    try:
        data = get_realtime_quote.invoke({"ticker": ticker})
        text = str(data)
    except Exception:
        return 50.0, "盘中数据不可用"

    score = 50.0
    if "涨" in text or "up" in text.lower():
        score += 5
    if any(w in text for w in ["拉升", "尾盘", "放量"]):
        score += 10  # potential manipulation signal
    if any(w in text for w in ["跌停", "跳水"]):
        score -= 15
    return min(100, max(0, score)), "基于盘中数据"


def _score_analyst(ticker: str, config: dict) -> Tuple[float, str]:
    """Analyst recommendation trends."""
    from capitalradar.agents.utils.capital_flow_tools import get_analyst_recommendations
    try:
        data = get_analyst_recommendations.invoke({"ticker": ticker})
        text = str(data).lower()
    except Exception:
        return 50.0, "分析师数据不可用"
    if not text or "no data" in text:
        return 50.0, "无分析师数据"

    score = 50.0
    if "buy" in text or "买入" in text:
        score += 10
    if "sell" in text or "卖出" in text:
        score -= 10
    if "上调" in text or "upgrade" in text:
        score += 10
    return min(100, max(0, score)), "基于分析师评级"
```

- [ ] **Step 2: Verify module loads**

```bash
python -c "from capitalradar.sector_scan.smart_money_score import compute_smart_money_score; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/sector_scan/smart_money_score.py
git commit -m "feat: add Smart Money Score — unified institutional flow detection (0-100)"
```

---

### Task 2: Dynamic Strategy Engine

**Files:**
- Create: `capitalradar/sector_scan/dynamic_strategy.py`

- [ ] **Step 1: Write the file**

```python
"""Dynamic strategy engine — LLM generates screening strategies from natural language.

The LLM receives a list of available fields (with descriptions and value ranges)
and outputs a JSON strategy that the engine executes against the A-share universe.
All strategies MUST include a smart_money_score floor — institutional participation
is the non-negotiable baseline.
"""

import json
import re
from typing import Any


# ── Field catalogue exposed to the LLM ────────────────────────────

STRATEGY_FIELDS = {
    "smart_money_score": {
        "label": "主力资金综合评分",
        "type": "number",
        "range": "0-100",
        "description": "综合主力资金评分。建议最低门槛 >= 50，好的标的 >= 70",
    },
    "net_inflow_5d": {
        "label": "5日主力净流入(万元)",
        "type": "number",
        "range": "可正可负",
        "description": "正值表示流入，负值表示流出。>5000万为强流入",
    },
    "big_order_ratio": {
        "label": "大单成交占比",
        "type": "number",
        "range": "0-1",
        "description": "大单和特大单占总成交比例。>0.4表示大资金活跃",
    },
    "north_bound_flow": {
        "label": "北向资金变化",
        "type": "number",
        "range": "可正可负(万元)",
        "description": "沪深港通资金变化。>0表示外资流入",
    },
    "pe_ttm": {
        "label": "市盈率(TTM)",
        "type": "number",
        "range": ">0",
        "description": "滚动市盈率。A股均值约18x，<15为低估，>50为高估",
    },
    "pb": {
        "label": "市净率",
        "type": "number",
        "range": ">0",
        "description": "<1为破净，金融股通常<1.5",
    },
    "pe_vs_industry": {
        "label": "PE vs 行业均值",
        "type": "number",
        "range": "比率",
        "description": "<1 表示低于行业平均估值",
    },
    "rsi_14": {
        "label": "RSI(14)",
        "type": "number",
        "range": "0-100",
        "description": "<30超卖，30-50低位，50-70健康，>70超买",
    },
    "macd_dif": {
        "label": "MACD DIF方向",
        "type": "string",
        "range": "positive/negative/flat",
        "description": "positive=多头排列, negative=空头, flat=即将拐点",
    },
    "price_vs_ma20": {
        "label": "价格 vs 20日均线",
        "type": "number",
        "range": "比率",
        "description": ">1表示站上20日线，<1表示线下",
    },
    "price_vs_60d_low": {
        "label": "距60日最低点",
        "type": "number",
        "range": "倍数",
        "description": "1.05表示仅反弹5%，<1.2为低位区域",
    },
    "golden_cross_days": {
        "label": "金叉后天数",
        "type": "number",
        "range": "0-N",
        "description": "0-5天内金叉为最佳介入窗口",
    },
    "volume_ratio_5d": {
        "label": "5日均量比",
        "type": "number",
        "range": "比率",
        "description": ">1.5为放量，<0.7为缩量",
    },
    "industry": {
        "label": "行业板块",
        "type": "string",
        "range": "A股行业名称列表",
        "description": "可以是具体行业名列表",
    },
    "rrg_quadrant": {
        "label": "RRG象限",
        "type": "string",
        "range": "leading/improving/weakening/lagging",
        "description": "推荐限制在 leading 或 improving",
    },
    "rps_120": {
        "label": "RPS(120日)",
        "type": "number",
        "range": "0-100",
        "description": "O'Neil相对强度。>85为强势，>90为领涨股",
    },
    "market_cap": {
        "label": "总市值(亿)",
        "type": "number",
        "range": ">0",
        "description": "市值筛选。大盘股>500亿，中盘100-500亿，小盘<100亿",
    },
}


STRATEGY_PROMPT = """You are a stock screening expert for Chinese A-share retail investors.

Available screening fields:
{field_catalogue}

CRITICAL: Every strategy MUST include `smart_money_score >= 50` as a baseline filter.
This ensures we only recommend stocks with detectable institutional participation.

User request: {user_intent}

Market context (for reference): {market_context}

Output a JSON strategy object:
{{
  "name": "策略名称",
  "conditions": [
    {{"field": "field_name", "op": ">= or <= or in or ==", "value": "value"}},
    ...
  ],
  "order_by": "smart_money_score",  // always sort by this unless user specifies otherwise
  "limit": 10
}}

Rules:
1. Minimum 3 conditions, maximum 8
2. smart_money_score >= 50 is MANDATORY in every strategy
3. Use the field names EXACTLY as listed in the catalogue
4. Use appropriate operators: ">=", "<=", "in" (for string lists), "==" 
5. For industry, always use "in" with a list of industry names
6. For rrg_quadrant, use "in" with quadrant names
7. limit should be 5-15 based on how specific the request is
8. Output ONLY the JSON, no explanation text

JSON:"""


def build_strategy_prompt(user_intent: str, market_context: str = "") -> str:
    """Build the LLM prompt for strategy generation."""
    field_lines = []
    for name, meta in STRATEGY_FIELDS.items():
        field_lines.append(
            f"  {name}: {meta['label']} ({meta['type']}, {meta['range']}) — {meta['description']}"
        )
    return STRATEGY_PROMPT.format(
        field_catalogue="\n".join(field_lines),
        user_intent=user_intent,
        market_context=market_context or "当前市场状态未知，正常分析即可",
    )


def parse_strategy_json(raw: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    # Try to find JSON block
    m = re.search(r'\{[\s\S]*\}', raw)
    if not m:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(m.group())


def validate_strategy(strategy: dict) -> dict:
    """Validate and normalise a strategy dict. Raises ValueError on invalid."""
    if "conditions" not in strategy:
        raise ValueError("Strategy must have 'conditions' list")

    has_smart_money = False
    for c in strategy["conditions"]:
        if "field" not in c or "op" not in c or "value" not in c:
            raise ValueError(f"Condition missing required key: {c}")
        if c["field"] not in STRATEGY_FIELDS:
            raise ValueError(f"Unknown field: {c['field']}")
        if c["op"] not in (">=", "<=", "in", "==", "!=", ">", "<"):
            raise ValueError(f"Unknown operator: {c['op']}")
        if c["field"] == "smart_money_score" and c["op"] == ">=":
            has_smart_money = True

    if not has_smart_money:
        raise ValueError("Strategy MUST include smart_money_score >= threshold")

    strategy.setdefault("order_by", "smart_money_score")
    strategy.setdefault("limit", 10)
    return strategy


def execute_strategy(strategy: dict, candidate_pool: list[dict]) -> list[dict]:
    """Apply strategy conditions to filter and rank candidates.

    Each candidate dict MUST have keys matching the strategy field names.
    Returns ranked list with added '_score' and '_explanation' keys.
    """
    results = []
    for stock in candidate_pool:
        passed = True
        for cond in strategy["conditions"]:
            field = cond["field"]
            op = cond["op"]
            value = cond["value"]

            if field not in stock:
                passed = False
                break

            stock_val = stock[field]

            if op == ">=" and not (stock_val >= value):
                passed = False
            elif op == "<=" and not (stock_val <= value):
                passed = False
            elif op == ">" and not (stock_val > value):
                passed = False
            elif op == "<" and not (stock_val < value):
                passed = False
            elif op == "==" and not (stock_val == value):
                passed = False
            elif op == "!=" and not (stock_val == value):
                passed = False
            elif op == "in":
                if isinstance(value, str):
                    value = [v.strip() for v in value.split(",")]
                if not isinstance(value, list):
                    passed = False
                elif stock_val not in value:
                    passed = False

            if not passed:
                break

        if passed:
            results.append(stock)

    # Sort by order_by field descending (higher = better)
    order_field = strategy.get("order_by", "smart_money_score")
    results.sort(key=lambda s: s.get(order_field, 0), reverse=True)

    return results[: strategy.get("limit", 10)]


def describe_strategy(strategy: dict) -> str:
    """Human-readable Chinese description of what the strategy does."""
    lines = [f"# 策略: {strategy.get('name', '自定义策略')}", ""]
    for i, c in enumerate(strategy["conditions"], 1):
        field_name = STRATEGY_FIELDS.get(c["field"], {}).get("label", c["field"])
        op_map = {">=": "≥", "<=": "≤", ">": ">", "<": "<", "==": "=", "!=": "≠", "in": "包含"}
        op_display = op_map.get(c["op"], c["op"])
        val_display = str(c["value"])
        if c["op"] == "in" and isinstance(c["value"], list):
            val_display = "、".join(str(v) for v in c["value"])
        lines.append(f"{i}. {field_name} {op_display} {val_display}")
    lines.append(f"\n排序: {STRATEGY_FIELDS.get(strategy.get('order_by', ''), {}).get('label', strategy.get('order_by', ''))} 降序")
    lines.append(f"数量: 最多 {strategy.get('limit', 10)} 只")
    return "\n".join(lines)
```

- [ ] **Step 2: Verify module loads**

```bash
python -c "from capitalradar.sector_scan.dynamic_strategy import build_strategy_prompt, validate_strategy, execute_strategy; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/sector_scan/dynamic_strategy.py
git commit -m "feat: add Dynamic Strategy Engine — LLM generates screening strategies from natural language"
```

---

### Task 3: Pick Tracker (DB + persistence)

**Files:**
- Modify: `web/results_store.py` — add `stock_picks` table + CRUD
- Create: `capitalradar/sector_scan/pick_tracker.py`

- [ ] **Step 1: Add stock_picks table to init_db in results_store.py**

Add after the `chat_thread_runs` table creation block:

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_picks (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        pick_id      TEXT NOT NULL UNIQUE,
        ticker       TEXT NOT NULL,
        pick_date    TEXT NOT NULL,
        source       TEXT DEFAULT 'advisory',
        strategy     TEXT DEFAULT '',
        smart_money_score REAL DEFAULT 0,
        pick_price   REAL DEFAULT 0,
        reason       TEXT DEFAULT '',
        return_5d    REAL,
        return_20d   REAL,
        return_60d   REAL,
        resolved_at  TEXT,
        notes        TEXT DEFAULT ''
    )
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_ticker ON stock_picks(ticker)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_date ON stock_picks(pick_date)")
```

Also add these functions at the end of `results_store.py` (before the last line):

```python
def save_pick(config: dict, pick_data: dict) -> str:
    """Save a stock pick. Returns pick_id."""
    import uuid
    pick_id = pick_data.get("pick_id") or uuid.uuid4().hex[:12]
    conn = _get_conn(config)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO stock_picks
                (pick_id, ticker, pick_date, source, strategy,
                 smart_money_score, pick_price, reason, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pick_id,
            pick_data.get("ticker", ""),
            pick_data.get("pick_date", ""),
            pick_data.get("source", "advisory"),
            pick_data.get("strategy", ""),
            pick_data.get("smart_money_score", 0),
            pick_data.get("pick_price", 0),
            pick_data.get("reason", ""),
            pick_data.get("notes", ""),
        ))
        conn.commit()
    finally:
        conn.close()
    return pick_id


def get_picks(config: dict, ticker: str = None, limit: int = 20) -> list[dict]:
    """Get past picks, optionally filtered by ticker."""
    conn = _get_conn(config)
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM stock_picks WHERE ticker = ? ORDER BY pick_date DESC LIMIT ?",
                (ticker, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock_picks ORDER BY pick_date DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_picks(config: dict) -> int:
    """Update unresolved picks with latest price data. Returns count resolved."""
    import yfinance as yf
    conn = _get_conn(config)
    try:
        pending = conn.execute(
            "SELECT * FROM stock_picks WHERE return_60d IS NULL"
        ).fetchall()
        resolved = 0
        for p in pending:
            try:
                stock = yf.Ticker(p["ticker"])
                hist = stock.history(period="3mo")
                if len(hist) < 2:
                    continue
                pick_dt = p["pick_date"]
                last_price = float(hist["Close"].iloc[-1])
                pick_price = p["pick_price"] or 0
                if pick_price <= 0:
                    # Try to get price at pick date
                    pick_hist = stock.history(start=pick_dt, end=pick_dt)
                    if len(pick_hist) > 0:
                        pick_price = float(pick_hist["Close"].iloc[0])
                total_return = (last_price - pick_price) / pick_price if pick_price > 0 else 0

                from datetime import datetime, timedelta
                pick_d = datetime.strptime(pick_dt, "%Y-%m-%d")
                days_ago = (datetime.now() - pick_d).days

                updates = {"resolved_at": datetime.now().strftime("%Y-%m-%d")}
                if days_ago >= 5:
                    updates["return_5d"] = round(total_return * 100, 2)
                if days_ago >= 20:
                    updates["return_20d"] = round(total_return * 100, 2)
                if days_ago >= 60:
                    updates["return_60d"] = round(total_return * 100, 2)

                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE stock_picks SET {set_clause} WHERE pick_id = ?",
                    list(updates.values()) + [p["pick_id"]]
                )
                resolved += 1
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()
    return resolved


def get_pick_performance_summary(config: dict, days: int = 90) -> dict:
    """Return aggregate performance stats for picks in the last N days."""
    conn = _get_conn(config)
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        picks = conn.execute(
            "SELECT * FROM stock_picks WHERE pick_date >= ?",
            (cutoff,)
        ).fetchall()

        resolved = [p for p in picks if p["return_20d"] is not None]
        if not resolved:
            return {"total_picks": len(picks), "resolved": 0, "message": "No resolved picks yet"}

        wins = len([p for p in resolved if (p["return_20d"] or 0) > 0])
        avg_return = sum(p["return_20d"] or 0 for p in resolved) / len(resolved)
        return {
            "total_picks": len(picks),
            "resolved": len(resolved),
            "win_rate": round(wins / len(resolved) * 100, 1),
            "avg_return_20d": round(avg_return, 2),
            "top_pick": max(resolved, key=lambda p: p["return_20d"] or 0),
        }
    finally:
        conn.close()
```

- [ ] **Step 2: Create pick_tracker.py**

```python
"""Pick tracker — record stock picks and resolve performance over time."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def record_pick(config: dict, ticker: str, pick_date: str, source: str,
                strategy: str, smart_money_score: float, pick_price: float,
                reason: str = "") -> str:
    """Record a stock pick. Returns pick_id."""
    from web.results_store import save_pick
    return save_pick(config, {
        "ticker": ticker,
        "pick_date": pick_date,
        "source": source,
        "strategy": strategy,
        "smart_money_score": smart_money_score,
        "pick_price": pick_price,
        "reason": reason,
    })


def resolve_all_picks(config: dict) -> int:
    """Resolve all pending picks — fetch latest prices, compute returns."""
    from web.results_store import resolve_picks
    count = resolve_picks(config)
    logger.info("Resolved %d picks", count)
    return count


def get_performance_report(config: dict, days: int = 90) -> str:
    """Generate a human-readable performance report for the Advisory agent."""
    from web.results_store import get_pick_performance_summary, get_picks
    summary = get_pick_performance_summary(config, days)
    if summary.get("message"):
        return summary["message"]

    picks = get_picks(config, limit=20)
    lines = [
        f"## 📊 选股跟踪报告 (近{days}天)",
        "",
        f"**总选股**: {summary['total_picks']} 只",
        f"**已结算**: {summary['resolved']} 只",
        f"**胜率**: {summary['win_rate']}%",
        f"**平均20日收益**: {summary['avg_return_20d']}%",
        "",
        "### 最近选股表现",
        "",
    ]
    for p in picks[:10]:
        ret_20 = p.get("return_20d")
        emoji = "✅" if ret_20 and ret_20 > 0 else ("⚠️" if ret_20 and ret_20 > -5 else "❌")
        ret_str = f"{ret_20:+.1f}%" if ret_20 is not None else "待结算"
        lines.append(
            f"{emoji} **{p['ticker']}** — {p['pick_date']} | "
            f"主力评分 {p['smart_money_score']:.0f} | 20日 {ret_str}"
        )
    return "\n".join(lines)
```

- [ ] **Step 3: Verify**

```bash
python -c "
from web.results_store import save_pick, get_picks, get_pick_performance_summary, resolve_picks
from capitalradar.sector_scan.pick_tracker import record_pick, get_performance_report
from capitalradar.default_config import DEFAULT_CONFIG
# Quick test
pid = record_pick(DEFAULT_CONFIG, '000625.SZ', '2026-06-23', 'advisory', 'test', 78.0, 15.50, 'test pick')
print(f'pick_id: {pid}')
picks = get_picks(DEFAULT_CONFIG)
print(f'total picks: {len(picks)}')
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add web/results_store.py capitalradar/sector_scan/pick_tracker.py
git commit -m "feat: add Pick Tracker — DB persistence + performance resolution for stock picks"
```

---

### Task 4: Advisory Agent Tools

**Files:**
- Modify: `web/history_chat.py` — add `run_smart_screening`, `get_smart_money_score`, `query_pick_performance` tools

- [ ] **Step 1: Add new tools to build_tool_set**

Add these tools inside `build_tool_set()` before the `return [...]` statement:

```python
@tool
def get_smart_money_score(
    ticker: Annotated[str, "Stock ticker (e.g. 600519.SH, 000625.SZ)"],
) -> str:
    """Get the unified Smart Money Score (0-100) for a stock.

    Score synthesizes 7 dimensions: money flow trend, large-order depth,
    north-bound flow, margin trend, institutional holdings, intraday anomalies,
    and analyst ratings. Scores >= 70 are strong institutional participation.
    Core money flow score < 40 results in automatic downgrade.
    """
    from capitalradar.sector_scan.smart_money_score import compute_smart_money_score
    total, breakdown = compute_smart_money_score(ticker, config)
    lines = [f"## Smart Money Score: {ticker}", f"**总分: {total}/100**", ""]
    for dim, info in breakdown.items():
        if dim == "_total":
            continue
        s = info["score"]
        bar = "█" * int(s / 10) + "░" * (10 - int(s / 10))
        lines.append(f"- {dim}: {s:.0f}/100 {bar}")
        if info.get("detail"):
            lines.append(f"  {info['detail']}")
    lines.append("")
    if total >= 70:
        lines.append("✅ 主力参与度较高，值得关注")
    elif total >= 50:
        lines.append("⚠️ 主力有一定参与，但力度不够强")
    else:
        lines.append("❌ 主力参与度不足，建议谨慎")
    return "\n".join(lines)


@tool
def run_smart_screening(
    requirement: Annotated[str, "Natural language stock screening requirement, e.g. '帮我找低位启动的新能源票，要有主力建仓信号'"],
    market_context: Annotated[str, "Brief market context, e.g. '大盘震荡，成交量萎缩'"] = "",
) -> str:
    """AI-powered stock screening. Describe what you want in natural language and
    the agent will: 1) generate a custom screening strategy, 2) execute it against
    A-share data, 3) return ranked candidates with explanations.

    ALL strategies automatically include smart_money_score >= 50 baseline.
    """
    from capitalradar.sector_scan.dynamic_strategy import (
        build_strategy_prompt, parse_strategy_json, validate_strategy,
        execute_strategy, describe_strategy, STRATEGY_FIELDS,
    )
    from capitalradar.sector_scan.rotation import get_rrg_data
    from capitalradar.sector_scan.smart_scanner import get_industry_stocks, _score_one_stock
    from capitalradar.sector_scan.smart_money_score import compute_smart_money_score
    from capitalradar.sector_scan.pick_tracker import record_pick
    from datetime import datetime

    # ── Phase 1: Generate strategy ──
    prompt = build_strategy_prompt(requirement, market_context)
    from langchain_core.messages import SystemMessage, HumanMessage
    strategy_response = llm_with_tools.invoke([
        SystemMessage(content="You are a stock screening JSON generator. Output ONLY valid JSON."),
        HumanMessage(content=prompt),
    ])
    strategy_raw = strategy_response.content if hasattr(strategy_response, "content") else str(strategy_response)
    try:
        strategy = parse_strategy_json(strategy_raw)
        strategy = validate_strategy(strategy)
    except Exception as e:
        return f"策略生成失败: {e}\nLLM输出: {strategy_raw[:500]}"

    # ── Phase 2: Build candidate pool ──
    try:
        rrg = get_rrg_data(lookback=10, mode="capital")
        industries = rrg.get("industries", [])
        leading_improving = [
            i for i in industries
            if i.get("quadrant") in ("leading", "improving")
        ]
        if not leading_improving:
            leading_improving = industries[:10]  # fallback
    except Exception:
        leading_improving = []

    candidate_pool = []
    today = datetime.now().strftime("%Y-%m-%d")
    for ind in leading_improving[:15]:
        name = ind.get("name", "")
        stocks = get_industry_stocks(name) if name else []
        for s in stocks[:30]:
            code = s.get("code", "")
            if not code:
                continue
            try:
                score, _ = compute_smart_money_score(code, config)
            except Exception:
                score = 50.0
            candidate_pool.append({
                "ticker": code,
                "name": s.get("name", ""),
                "smart_money_score": score,
                "rrg_quadrant": ind.get("quadrant", ""),
                "industry": name,
                "market_cap": s.get("market_cap", 0),
            })

    if not candidate_pool:
        return "没有找到候选股票。请尝试调整条件或扩大行业范围。"

    # ── Phase 3: Execute strategy ──
    results = execute_strategy(strategy, candidate_pool)
    if not results:
        strategy["conditions"] = [c for c in strategy["conditions"]
                                   if c.get("field") != "smart_money_score"]
        strategy["conditions"].append({"field": "smart_money_score", "op": ">=", "value": 40})
        results = execute_strategy(strategy, candidate_pool)

    # ── Phase 4: Build output ──
    lines = ["# 🎯 AI 智能选股结果", "", describe_strategy(strategy), "", f"**候选池**: {len(candidate_pool)} 只 (来自 Leading + Improving 行业)", f"**筛选结果**: {len(results)} 只", "", "---", ""]

    for i, r in enumerate(results[:10], 1):
        code = r.get("ticker", "?")
        name = r.get("name", code)
        sms = r.get("smart_money_score", 0)
        stars = "⭐" * min(5, int(sms / 20) + 1)
        lines.append(f"### {i}. {code} {name} {stars}")
        lines.append(f"**主力评分**: {sms:.0f}/100 | 行业: {r.get('industry', 'N/A')} | RRG: {r.get('rrg_quadrant', 'N/A')}")
        lines.append("")

        # Record pick
        try:
            record_pick(config, code, today, "advisory",
                        strategy.get("name", "custom"), sms,
                        s.get("close", 0))
        except Exception:
            pass

    if not results:
        lines.append("⚠️ 没有股票满足当前策略条件。已放宽主力资金门槛重试，仍无结果。建议扩大行业范围或降低估值/技术指标要求。")

    return "\n".join(lines)


@tool
def query_pick_performance(
    days: Annotated[int, "How many days back to check (default 90)"] = 90,
) -> str:
    """Check how past stock picks have performed. Returns win rate, average returns,
    and a list of recent picks with their outcomes."""
    from capitalradar.sector_scan.pick_tracker import get_performance_report
    from web.results_store import resolve_picks
    resolve_picks(config)
    return get_performance_report(config, days)
```

Also add the tools to the return list:

```python
return [
    query_past_analyses,
    run_full_analysis,
    get_smart_money_score,       # NEW
    run_smart_screening,          # NEW
    query_pick_performance,       # NEW
    get_stock_data, get_indicators,
    ...
]
```

- [ ] **Step 2: Verify module loads**

```bash
python -c "
from web.history_chat import create_history_pm_agent
from capitalradar.default_config import DEFAULT_CONFIG
llm, tools, prompt, meta = create_history_pm_agent([], DEFAULT_CONFIG)
names = [t.name for t in tools]
assert 'get_smart_money_score' in names
assert 'run_smart_screening' in names
assert 'query_pick_performance' in names
print(f'Tools: {len(names)}')
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add web/history_chat.py
git commit -m "feat: add Advisory tools — run_smart_screening, get_smart_money_score, query_pick_performance"
```

---

### Task 5: Frontend — AI Smart Pick sub-tab

**Files:**
- Modify: `web/templates/index.html` — add sub-tab button + content
- Modify: `web/static/app.js` — wire sub-tab + API calls

- [ ] **Step 1: Add sub-tab button in index.html**

Find the Stock Pick sub-tab buttons and add:

```html
<button class="sub-tab-btn" data-subtab="stockpick-ai">AI Smart Pick</button>
```

Insert as the FIRST sub-tab button (before Smart Scan).

- [ ] **Step 2: Add sub-tab content in index.html**

After the sub-tab buttons div, add:

```html
<!-- Sub-tab: AI Smart Pick -->
<div id="subtab-stockpick-ai" class="sub-tab-content">
  <div class="section">
    <label class="label">AI Smart Pick — Natural Language Stock Screening</label>
    <p style="font-size:11px;color:var(--text-muted);margin:4px 0;">
      Describe what kind of stocks you want in plain language.
      The AI agent generates a custom strategy, screens A-shares, and explains each pick.
      All strategies include institutional capital flow detection as baseline.
    </p>
  </div>

  <div class="section">
    <label class="label">What are you looking for?</label>
    <textarea id="ai-pick-requirement" placeholder="e.g. 帮我找低位启动的新能源票，要有主力建仓信号，PE低于30，近5日放量"
              style="width:100%;height:80px;padding:10px;border:1px solid var(--border);border-radius:6px;font-size:13px;resize:vertical;background:var(--bg);color:var(--text);"></textarea>
    <input type="text" id="ai-pick-context" placeholder="Market context (optional): e.g. 大盘震荡，成交量萎缩"
           style="width:100%;padding:8px 10px;margin-top:6px;border:1px solid var(--border);border-radius:6px;font-size:12px;background:var(--bg);color:var(--text);">
  </div>

  <div class="section">
    <button type="button" id="ai-pick-btn" class="btn prelim-start-btn" style="width:100%;">
      🎯 Start AI Screening
    </button>
    <div id="ai-pick-spinner" class="spinner hidden"></div>
    <div id="ai-pick-status" style="font-size:11px;color:var(--text-muted);margin-top:4px;"></div>
  </div>

  <div class="section">
    <label class="label">Results</label>
    <div id="ai-pick-results" class="scan-results">
      <span class="no-results">Enter your requirements above and click Start AI Screening</span>
    </div>
  </div>
</div><!-- /subtab-stockpick-ai -->
```

- [ ] **Step 3: Wire JS in app.js**

Add after existing Stock Pick init code:

```javascript
// AI Smart Pick
var aiPickBtn = document.getElementById("ai-pick-btn");
if (aiPickBtn) {
  aiPickBtn.addEventListener("click", function() {
    var req = document.getElementById("ai-pick-requirement").value.trim();
    if (!req) { showToast("Please describe what you're looking for"); return; }
    var ctx = document.getElementById("ai-pick-context").value.trim();
    var status = document.getElementById("ai-pick-status");
    var results = document.getElementById("ai-pick-results");
    var spinner = document.getElementById("ai-pick-spinner");
    if (status) status.textContent = "Generating strategy + screening...";
    if (spinner) spinner.classList.remove("hidden");
    if (results) results.innerHTML = '<span class="no-results">Screening in progress...</span>';

    // Use Advisory chat as backend
    var threadId = "aipick_" + Date.now().toString(36);
    var question = req + (ctx ? " [市场背景: " + ctx + "]" : "");
    
    fetch("/api/advisory/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: question, thread_id: threadId})
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      var streamUrl = data.stream_url;
      var es = new EventSource(streamUrl);
      var mdContent = "";
      es.addEventListener("chat-done", function(e) {
        var d = JSON.parse(e.data);
        if (results) results.innerHTML = '<div class="markdown-body">' + renderMarkdown(d.full_response) + '</div>';
        if (status) status.textContent = "Done";
        if (spinner) spinner.classList.add("hidden");
        es.close();
      });
      es.addEventListener("chat-error", function(e) {
        if (results) results.innerHTML = '<span class="no-results">Screening failed. Please try again.</span>';
        if (status) status.textContent = "";
        if (spinner) spinner.classList.add("hidden");
        es.close();
      });
    }).catch(function() {
      if (results) results.innerHTML = '<span class="no-results">Failed to start screening</span>';
      if (spinner) spinner.classList.add("hidden");
    });
  });
}
```

- [ ] **Step 4: Commit**

```bash
git add web/templates/index.html web/static/app.js
git commit -m "feat: add AI Smart Pick sub-tab — natural language stock screening UI"
```

---

### Task 6: System prompt update

**Files:**
- Modify: `web/history_chat.py` — update `build_pm_system_prompt` advisory mode prompt

- [ ] **Step 1: Update system prompt**

In the advisory mode prompt (the `if not states` branch), update the "Step 2" section to mention new tools:

```
f"2. **Quick Assessment**: use get_money_flow FIRST (core), then web_search_current + get_indicators + get_realtime_quote\n"
f"3. **Smart Screening**: use run_smart_screening when the user wants to discover stocks matching specific criteria — just pass their natural language request\n"
f"4. **Check Smart Money Score**: use get_smart_money_score(ticker) to see a stock's institutional participation score (0-100)\n"
f"5. **Review Past Picks**: use query_pick_performance to see how previous recommendations performed\n\n"
f"## Important Rules\n"
f"- ALWAYS check history (query_past_analyses) before answering stock questions\n"
f"- ALWAYS ask for user consent before calling run_full_analysis\n"
f"- When user asks 'what stocks should I buy' or similar, suggest run_smart_screening with their criteria\n"
f"- Get smart money score before recommending any stock\n"
```

- [ ] **Step 2: Verify and commit**

```bash
python -c "import py_compile; py_compile.compile('web/history_chat.py', doraise=True); print('OK')"
git add web/history_chat.py
git commit -m "feat: update Advisory system prompt for stock picking tools"
```
