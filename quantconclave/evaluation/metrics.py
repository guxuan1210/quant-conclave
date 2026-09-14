"""Evaluation performance metrics (pure functions, synthetic-price testable).

The existing backtest metrics (``VectorBacktester.calculate_metrics``) compute
Sharpe / max-drawdown / annual return but no benchmark-relative statistics, so
the excess-return / information-ratio primitives live here.
"""

from __future__ import annotations

import math

from .config import get_eval_config


def annualized_information_ratio(excess: list[float], periods_per_year: int = 52):
    """Annualized information ratio of a series of period excess returns.

    Uses sample std (ddof=1). Returns None when there are < 2 observations or
    zero variance (IR is undefined).
    """
    xs = [x for x in excess if x is not None]
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(periods_per_year)


def max_drawdown(nav: list[float]) -> float:
    """Maximum drawdown of an NAV series (returns a non-positive fraction)."""
    peak = float("-inf")
    mdd = 0.0
    for v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def build_nav(returns: list[float], start: float = 1.0) -> list[float]:
    """Cumulative NAV series from a list of period returns."""
    nav = [start]
    for r in returns:
        nav.append(nav[-1] * (1.0 + r))
    return nav


def portfolio_return(allocations: list[dict], net_returns: dict, cash_weight: float = 0.0) -> float:
    """Portfolio return = Σ weight·net_return; cash earns 0."""
    total = 0.0
    for a in allocations:
        total += a["weight"] * (net_returns.get(a["code"]) or 0.0)
    return total


def direction_accuracy(records: list[dict]) -> dict:
    """Direction accuracy + neutral (观望) rate over settled prediction records.

    ``records`` items carry ``rating`` and an ``excess_return`` (None = unsettled).
    Buy/Overweight count as 看多 (correct when return > 0), Sell/Underweight as
    看空 (correct when return < 0), Hold as 观望.
    """
    correct = 0
    directional = 0
    neutral = 0
    for r in records:
        rating = r.get("rating")
        ret = r.get("excess_return")
        if rating in ("Buy", "Overweight"):
            directional += 1
            if ret is not None and ret > 0:
                correct += 1
        elif rating in ("Sell", "Underweight"):
            directional += 1
            if ret is not None and ret < 0:
                correct += 1
        else:
            neutral += 1
    return {
        "correct": correct,
        "directional_total": directional,
        "neutral": neutral,
        "accuracy": (correct / directional) if directional else None,
    }


def evaluate_validity(
    config: dict | None,
    weeks: int,
    coverage_pct: float | None,
    cum_excess_return: float | None,
    annualized_ir: float | None,
    max_drawdown_delta: float | None,
) -> dict:
    """Rolling validity gate: is the system "有效", "未达标", or "数据不足"?

    Rules (in order): fewer than ``min_weeks_for_validity`` mature weeks or
    coverage below threshold → 数据不足; otherwise any of the three main
    conditions failing → 未达标; else 有效.
    """
    ecfg = get_eval_config(config)
    min_weeks = ecfg["min_weeks_for_validity"]
    cov_threshold = ecfg["coverage_threshold"]
    mdd_limit = -ecfg["max_drawdown_delta_pp"] / 100.0

    if weeks < min_weeks:
        return {"status": "insufficient_data", "reason": f"need {min_weeks} mature weeks, have {weeks}"}
    if coverage_pct is None or coverage_pct < cov_threshold:
        return {"status": "insufficient_data",
                "reason": f"coverage {coverage_pct} below {cov_threshold}"}
    if cum_excess_return is None or cum_excess_return <= 0:
        return {"status": "not_effective", "reason": "cumulative net excess <= 0"}
    if annualized_ir is None or annualized_ir <= 0:
        return {"status": "not_effective", "reason": "annualized information ratio <= 0"}
    if max_drawdown_delta is not None and max_drawdown_delta < mdd_limit:
        return {"status": "not_effective",
                "reason": f"max drawdown delta {max_drawdown_delta:.4f} worse than {mdd_limit}pp"}
    return {"status": "effective", "reason": "all validity conditions met"}
