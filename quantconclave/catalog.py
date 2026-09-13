"""Domain Catalog — Capability / Role / Node.

Single source of truth that disambiguates the overloaded word "智能体"
(agent), which the codebase and docs use for four different things:

  * **Capability**  — a product feature the user sees in the UI / README
                      (Deep Analysis, AI Pick, Prediction, Strategy, ...)
  * **Role**        — an LLM agent identity in the pipeline
                      (``capital_flow``, ``market``, ``social``, ...)
  * **Node**        — a runtime LangGraph node name
                      ("Capital Flow Analyst", "Bull Researcher", ...)
  * **Report key**  — the ``AgentState`` field an analyst writes
                      (``capital_flow_report``, ``sentiment_report``, ...)

The graph builder, the analyst-execution planner, and the history-chat prompt
read from here instead of each hard-coding their own copy of the same mapping
(which previously drifted: e.g. ``social`` → "Sentiment Analyst" in the full
graph but "Social Analyst" in the partial graph).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalystRole:
    """An analyst *role* and its runtime *node* + *report key* bindings."""

    key: str          # role identity used in config / analyst selection
    label: str        # user-facing display name
    node: str         # LangGraph node name (runtime observation / debug)
    tool_node: str    # LangGraph tool node name
    clear_node: str   # LangGraph message-clear node name
    report_key: str   # AgentState report field


ANALYST_ROLES: dict[str, AnalystRole] = {
    "capital_flow": AnalystRole(
        "capital_flow", "Capital Flow Analyst", "Capital Flow Analyst",
        "tools_capital_flow", "Msg Clear Capital Flow", "capital_flow_report",
    ),
    "market": AnalystRole(
        "market", "Market Analyst", "Market Analyst",
        "tools_market", "Msg Clear Market", "market_report",
    ),
    # Wire key stays "social" for saved-config back-compat; the user-facing
    # label and node are "Sentiment Analyst" to match the v0.2.5 rename
    # (sentiment_analyst ingests news + StockTwits + Reddit, not just social).
    "social": AnalystRole(
        "social", "Sentiment Analyst", "Sentiment Analyst",
        "tools_social", "Msg Clear Sentiment", "sentiment_report",
    ),
    "news": AnalystRole(
        "news", "News Analyst", "News Analyst",
        "tools_news", "Msg Clear News", "news_report",
    ),
    "fundamentals": AnalystRole(
        "fundamentals", "Fundamentals Analyst", "Fundamentals Analyst",
        "tools_fundamentals", "Msg Clear Fundamentals", "fundamentals_report",
    ),
    "competitor": AnalystRole(
        "competitor", "Competitor Analyst", "Competitor Analyst",
        "tools_competitor", "Msg Clear Competitor", "competitor_report",
    ),
    "partner": AnalystRole(
        "partner", "Partner Analyst", "Partner Analyst",
        "tools_partner", "Msg Clear Partner", "partner_report",
    ),
}

# Canonical pipeline order — Capital Flow is the anchor and always runs first.
ANALYST_ORDER: list[str] = [
    "capital_flow", "market", "social", "news",
    "fundamentals", "competitor", "partner",
]


def ordered_roles() -> list[AnalystRole]:
    """Return analyst roles in canonical pipeline order."""
    return [ANALYST_ROLES[k] for k in ANALYST_ORDER]


@dataclass(frozen=True)
class Capability:
    """A product *capability* — what the user sees, independent of how many
    roles/nodes implement it under the hood."""

    key: str
    label: str
    description: str


CAPABILITIES: dict[str, Capability] = {
    "deep_analysis": Capability(
        "deep_analysis", "Deep Analysis",
        "全栈深度分析流水线：7 位分析师 → 裁决器 → 多空辩论 → 交易员 → 风控辩论 → PM 最终决策",
    ),
    "ai_pick": Capability(
        "ai_pick", "AI Pick Agent",
        "自然语言选股引擎，结合资金评分与快捷筛选",
    ),
    "prediction": Capability(
        "prediction", "Prediction Agent",
        "ML + LLM 混合价格方向预测，为 PM 决策提供时间维度支撑",
    ),
    "strategy": Capability(
        "strategy", "Strategy Agent",
        "LLM 生成 backtrader 回测策略代码",
    ),
    "backtest": Capability(
        "backtest", "Backtest Agent",
        "backtrader 回测引擎，7 套内置策略模板，支持自定义 + 报告输出",
    ),
    "history": Capability(
        "history", "History Agent",
        "对话式历史复盘：读取历史深度分析记录，LLM 自动萃取经验和教训",
    ),
    "advisory": Capability(
        "advisory", "Advisory Agent",
        "AI 投资顾问：对话中引用经验库、查询历史、实时触发深度分析",
    ),
    "calibration": Capability(
        "calibration", "System Calibration",
        "自动学习闭环核心：Resolve → Meta-Eval → Extract → Skill Gen",
    ),
}


def report_key_for(role_key: str) -> str:
    """Map a role key to its report field (``social`` → ``sentiment_report``)."""
    return ANALYST_ROLES[role_key].report_key


def label_for(role_key: str) -> str:
    return ANALYST_ROLES[role_key].label
