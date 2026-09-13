
"""Pattern detection -> experience proposal generation."""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CHASE_KEYWORDS = ["追", "chase", "追高", "追涨", "fomo", "追入", "高位"]
_GAP_KEYWORDS = ["跳空", "gap", "高开", "利好", "政策", "跳涨"]


def extract_patterns_from_log(
    memory_log,
    min_entries: int = 3,
) -> list[dict]:
    """Scan resolved memory log entries for detectable patterns.

    Returns a list of experience proposals with:
    - pattern_type: 'chase_fade' | 'gap_trap'
    - tickers: list[str]
    - proposed_content: str
    - proposed_category: str
    """
    resolved = memory_log.get_resolved_entries()
    if len(resolved) < min_entries:
        return []

    proposals = []

    # Pattern: chase high without stop loss, large drawdown
    chase_losses = [
        e for e in resolved
        if e.get("raw_return", 0) < -0.05
        and any(k in (e.get("decision", "") or "").lower() for k in _CHASE_KEYWORDS)
    ]
    if len(chase_losses) >= 2:
        avg_loss = sum(abs(e.get("raw_return", 0) or 0) for e in chase_losses) / len(chase_losses)
        tickers_str = ", ".join(e.get("ticker", "?") for e in chase_losses)
        proposals.append({
            "pattern_type": "chase_fade",
            "tickers": [e.get("ticker", "") for e in chase_losses],
            "proposed_content": (
                f"追高热门股必须设-5%止损线。"
                f"{tickers_str}等标的追入后未设止损，平均回撤{avg_loss*100:.0f}%"
            ),
            "proposed_category": "stop_loss",
        })

    # Pattern: gap up on policy news then fade
    gap_fades = [
        e for e in resolved
        if e.get("raw_return", 0) < -0.03
        and any(k in (e.get("decision", "") or "").lower() for k in _GAP_KEYWORDS)
    ]
    if len(gap_fades) >= 2:
        proposals.append({
            "pattern_type": "gap_trap",
            "tickers": [e.get("ticker", "") for e in gap_fades],
            "proposed_content": (
                "政策利好驱动的跳空高开，至少等3个交易日确认承接后再入场。"
                "跳空后追入被套的概率较高，不如等回踩确认支撑。"
            ),
            "proposed_category": "timing",
        })

    return proposals
