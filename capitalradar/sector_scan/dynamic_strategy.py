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
        "description": "正值流入/负值流出。强度须结合流通市值与日均成交额判断——同额对小盘(如<100亿)是大动作、对大盘(如>500亿)占比极低，勿用固定绝对额当强度",
    },
    "net_inflow_20d": {
        "label": "20日主力净流入(万元)",
        "type": "number",
        "range": "可正可负",
        "description": "约一个月主力建仓/出货趋势。正值=中期净流入、负值=中期净流出；强度看占日均成交额比例与市值规模（大市值股阈值应相应上调），勿用固定绝对额",
    },
    "net_inflow_60d": {
        "label": "60日主力净流入(万元)",
        "type": "number",
        "range": "可正可负",
        "description": "季度级主力立场。正值=主力中长期做多，负值=持续撤退；与5/20日组合可筛背离（如60日净流出但近5日转正=回流初期）。强度须结合市值与日均成交额判断，勿用固定绝对额",
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
    "mf_ratio": {
        "label": "主力/散户资金比",
        "type": "number",
        "range": ">0",
        "description": "主力净额/散户净额。>2表示机构主导, >3为强控盘"
    },
    "net_inflow_ratio": {
        "label": "主力净流入占比%",
        "type": "number",
        "range": "可正可负",
        "description": "主力净流入占总成交额百分比。>5%为强烈买入信号, >8%为极强"
    },
    "pool_rank": {
        "label": "选股池内排名",
        "type": "number",
        "range": "1-N",
        "description": "在候选股池中的排名。越小越好, 1=第一名"
    },
    "moneyflow_trend": {
        "label": "资金流趋势",
        "type": "string",
        "range": "accelerating/stable/weakening",
        "description": "资金流趋势: accelerating加速流入, stable平稳, weakening流入减弱"
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
        "description": "1.05表示仅反弹5%，<1.2为低位区域（短线口径）",
    },
    "price_vs_180d_low": {
        "label": "距180日最低点",
        "type": "number",
        "range": "倍数",
        "description": "现价/180日最低点。1.05表示仅反弹5%，<1.2为180日低位区域；抄底必用180日口径",
    },
    "price_vs_180d_high": {
        "label": "距180日最高点",
        "type": "number",
        "range": "倍数",
        "description": "现价/180日最高点。0.7表示已从180日高点回撤30%（超跌反弹）",
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


def validate_strategy_fields(strategy: dict, candidate_pool: list[dict]) -> list[str]:
    """Return condition field names the candidate pool does NOT carry.

    Empty list means every condition field is present. ``execute_strategy``
    treats a missing field as a hard fail (silently dropping the whole pool), so
    callers use this first to surface unavailable data explicitly instead of
    returning an empty result — that is the data-validation contract.
    """
    if not candidate_pool:
        return sorted({c.get("field", "") for c in strategy.get("conditions", []) if c.get("field")})
    present = {k for s in candidate_pool for k in s.keys()}
    missing = {c["field"] for c in strategy.get("conditions", []) if c["field"] not in present}
    return sorted(missing)


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
