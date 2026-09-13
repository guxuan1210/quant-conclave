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
