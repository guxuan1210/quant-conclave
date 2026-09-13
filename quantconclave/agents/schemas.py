"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    short_term_outlook: str = Field(
        description=(
            "5-day outlook: direction, probability, expected price range, key levels. "
            "Example: 'bullish (57%), target 18.50-19.00, RSI=32 oversold bounce'"
        ),
    )
    medium_term_outlook: str = Field(
        description=(
            "20-day outlook: direction, confidence, price range, key risk factors. "
            "Example: 'bearish (88% confidence), test 14.74-16.49, under 200SMA=16.86'"
        ),
    )
    confidence: str = Field(
        description=(
            "Overall confidence in the decision. One of 'high', 'medium', 'low'. "
            "Based on ML prediction confidence + data quality + cross-validation."
        ),
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
        "",
        f"**5-Day Outlook**: {decision.short_term_outlook}",
        "",
        f"**20-Day Outlook**: {decision.medium_term_outlook}",
        "",
        f"**Confidence**: {decision.confidence}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Watchlist Analysis (Eastmoney self-select per-stock LLM analysis)
# ---------------------------------------------------------------------------


class WatchlistVerdict(str, Enum):
    """3-tier verdict for per-stock capital-flow analysis."""

    BULLISH = "看多"
    BEARISH = "看空"
    NEUTRAL = "观望"


class WatchlistSetupType(str, Enum):
    """Which bull/bear setup pattern the stock matched, per the path-classification
    framework in the watchlist prompt (web/app.py 分析要点区).

    The model must pick the single best-matching path. 观望 is the residual:
    it is chosen only when none of the six paths' conditions are met.
    """

    A_LOW_ACCUM = "低位吸筹启动"
    B_BREAKOUT = "放量突破新高"
    C_SAME_DIR_INFLOW = "主力同向持续流入"
    D_OVERSOLD_REVERSAL = "超跌修复拐点"
    E_HIGH_DISTRIBUTION = "高位放量派发"
    F_BREAKDOWN = "破位下跌"
    NO_SETUP = "观望-无明确路径"


class WatchlistAnalysis(BaseModel):
    """Structured output for the eastmoney watchlist per-stock LLM analysis.

    Uses function-calling to enforce that the verdict and analysis sections
    are always consistent — the model cannot claim 看多 in one sentence and
    describe 观望 conditions in another.
    """

    verdict: WatchlistVerdict = Field(
        description=(
            "最终结论，与 setup_type 一致：看多=命中看多路径A~D；"
            "看空=命中看空路径E/F；观望=路径A~F全不命中（setup_type=NO_SETUP）。"
            "必须三选一，不得在观望时命中看多路径或在看多时命中看空路径。"
        ),
    )
    setup_type: WatchlistSetupType = Field(
        description=(
            "命中的主力资金路径（按提示词【路径判定框架】逐条核对）："
            "A低位吸筹启动=位置低+主力由负转正+放量启动；B放量突破新高=距区间最高近+放量+超大单进+换手正常；"
            "C主力同向持续流入=同向流入日多+12日主力为正；D超跌修复拐点=位置极低+急跌后放量反弹+超大单转正；"
            "E高位放量派发=位置高+放量但超大单流出或高换手对倒；F破位下跌=跌破均线+放量下跌+主力流出。"
            "观望时必须填 观望-无明确路径。"
        ),
    )
    main_force_behavior: str = Field(
        description=(
            "【主力行为】超大单和大单的净流向与持续性分析。"
            "判断是吸筹还是派发，必须引用具体的超大单净额和大单净额数据。2-3句话。"
        ),
    )
    volume_price_analysis: str = Field(
        description=(
            "【量价关系】结合每日量价数据与资金流向，分析涨时放量还是缩量、"
            "跌时有无主力抵抗。必须引用具体的日涨幅和成交量数据。2-3句话。"
        ),
    )
    divergence_check: str = Field(
        description=(
            "【背离检查】是否存在价涨钱出（诱多）或价跌钱进（吸筹）的背离。"
            "对比每日涨幅方向与当日主力净额方向。2-3句话。"
        ),
    )
    conclusion_detail: str = Field(
        description=(
            "【综合结论】总结核心依据，引用超大单、大单净额及关键量价数据，"
            "明确给出最终判断和建议。2-4句话。"
        ),
    )


def render_watchlist_analysis(wa: WatchlistAnalysis) -> str:
    """Render a WatchlistAnalysis back to the markdown format the frontend expects.

    The first line is the verdict line (backward-compatible with the old
    free-text prompt format), followed by the structured sections.
    """
    verdict_emoji = {"看多": "🟢", "看空": "🔴", "观望": "🟡"}
    emoji = verdict_emoji.get(wa.verdict.value, "⚪")
    return "\n".join([
        f"结论：{wa.verdict.value} {emoji}",
        f"命中路径：{wa.setup_type.value}",
        "",
        f"1.【主力行为】{wa.main_force_behavior}",
        "",
        f"2.【量价关系】{wa.volume_price_analysis}",
        "",
        f"3.【背离检查】{wa.divergence_check}",
        "",
        f"4.【综合结论】{wa.conclusion_detail}",
    ])
