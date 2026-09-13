"""Calibration tool for the Advisory Agent.

Analyzes the advisory experience store for systematic biases (chase-fade patterns,
missed entries, etc.) and proposes threshold adjustments to improve future
recommendations.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_calibration(config: dict[str, Any]) -> str:
    """Run system calibration to analyze biases and propose threshold adjustments.

    Scans the advisory experience database for patterns in resolved outcomes,
    computes bias metrics, and returns human-readable recommendations for
    smart-money-score thresholds, entry timing, or stop-loss parameters.

    Args:
        config: QuantConclave runtime config dict.

    Returns:
        Markdown-formatted calibration report.
    """
    lines = ["# System Calibration Report", ""]

    # ── 1. Load active experiences ──
    try:
        from quantconclave.advisory.experience_store import (
            get_active_experiences,
            list_experiences,
        )
        active = get_active_experiences()
        all_exp = list_experiences()
    except Exception as e:
        logger.warning("Cannot load experiences: %s", e)
        return "\n".join(lines + ["", "⚠️ Experience store unavailable.", f"Error: {e}"])

    lines.append(f"**Active experiences**: {len(active)}")
    lines.append(f"**Total experiences**: {len(all_exp)}")
    lines.append("")

    if not all_exp:
        lines.append(
            "No experiences in the database yet. "
            "Calibration requires at least 10 resolved outcomes to produce "
            "meaningful recommendations. Continue using the advisory agent "
            "and revisit this after more decisions have been logged."
        )
        return "\n".join(lines)

    # ── 2. Analyze by category ──
    categories: dict[str, list[dict]] = {}
    for exp in all_exp:
        cat = exp.get("category", "other") or "other"
        categories.setdefault(cat, []).append(exp)

    lines.append("## Category Distribution")
    lines.append("")
    for cat, exps in sorted(categories.items(), key=lambda x: -len(x[1])):
        lines.append(f"- **{cat}**: {len(exps)} experiences")
    lines.append("")

    # ── 3. Outcome analysis ──
    outcomes = [e for e in all_exp if e.get("outcome")]
    if outcomes:
        lines.append("## Outcome Analysis")
        lines.append("")
        positive = [e for e in outcomes if e["outcome"] in ("win", "profit", "positive")]
        negative = [e for e in outcomes if e["outcome"] in ("loss", "negative")]
        win_rate = len(positive) / len(outcomes) * 100 if outcomes else 0

        lines.append(f"- Resolved entries: {len(outcomes)}")
        lines.append(f"- Wins: {len(positive)} | Losses: {len(negative)}")
        lines.append(f"- Win rate: {win_rate:.1f}%")
        lines.append("")

        # ── 4. Bias detection ──
        lines.append("## Bias Analysis")
        lines.append("")

        recommendations: list[str] = []

        # Check chase-related losses
        chase_losses = [
            e for e in outcomes
            if e["outcome"] in ("loss", "negative")
            and (e.get("category", "") in ("chase", "stop_loss", "timing"))
        ]
        if chase_losses:
            avg_return = (
                sum(e.get("raw_return", 0) or 0 for e in chase_losses)
                / len(chase_losses)
            )
            recommendations.append(
                f"**Chase/fade pattern detected**: {len(chase_losses)} entries with "
                f"losses in chase/timing categories (avg return {avg_return*100:+.1f}%). "
                f"Consider enabling stricter stop-loss (-5%) for momentum entries."
            )

        # Check if stop_loss category exists but has poor outcomes
        stop_loss_exps = [
            e for e in outcomes if e.get("category", "") == "stop_loss"
        ]
        if stop_loss_exps:
            sl_wins = [e for e in stop_loss_exps if e["outcome"] in ("win", "profit", "positive")]
            if len(sl_wins) < len(stop_loss_exps) * 0.5:
                recommendations.append(
                    "**Stop-loss discipline**: Less than 50% of stop-loss-tagged "
                    "entries were wins. Consider tightening stop-loss from -5% to -3%."
                )

        # Low sample warning
        if len(outcomes) < 10:
            recommendations.append(
                f"**Sample size warning**: Only {len(outcomes)} resolved entries. "
                f"Calibration needs ≥10 entries for statistical significance. "
                f"These recommendations are directional only."
            )

        # Smart money score threshold suggestion
        sms_exps = [e for e in outcomes if e.get("source_ticker")]
        if sms_exps:
            recommendations.append(
                "**Smart Money Score**: Review whether `smart_money_score >= 50` "
                "is the right filter threshold. If confirmed entries (>70) have "
                "significantly higher win rates, consider raising the default."
            )

        if recommendations:
            lines.append("### Adjustments Recommended")
            lines.append("")
            for i, rec in enumerate(recommendations, 1):
                lines.append(f"{i}. {rec}")
            lines.append("")
        else:
            lines.append("No systematic biases detected with current sample size.")
            lines.append("")

    # ── 5. Data freshness ──
    try:
        from quantconclave.advisory.extraction import extract_patterns_from_log
        lines.append("## Pattern Extraction")
        lines.append("")
        lines.append(
            "Pattern extraction module loaded. Call `extract_patterns_from_log` "
            "against the memory log to auto-detect chase-fade and gap-trap patterns."
        )
    except Exception as e:
        lines.append(f"Pattern extraction unavailable: {e}")

    lines.append("")
    lines.append(f"*Calibration completed. Review recommendations above.*")
    return "\n".join(lines)
