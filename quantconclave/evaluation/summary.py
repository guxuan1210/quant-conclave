"""Aggregate settled evaluation results into the summary metrics for the API.

Pure aggregation over rows already stored by ``web.eval_store``; reuses the
portfolio builder and metric primitives. No LLM calls happen here — the report
numbers must be recomputable from the DB.
"""

from __future__ import annotations

from .config import get_eval_config
from .metrics import (
    annualized_information_ratio,
    build_nav,
    direction_accuracy,
    evaluate_validity,
    max_drawdown,
)
from .portfolio import _RATING_PRIORITY, _CONF_PRIORITY, is_buyable

_HORIZON_FOR_PERIOD = {"week": 5, "month": 20, "quarter": 60}


def _sort_key(r: dict):
    return (
        _RATING_PRIORITY.get(r.get("rating"), 9),
        _CONF_PRIORITY.get(str(r.get("confidence") or "").lower(), 9),
        -(r.get("smart_money_score") or 0),
    )


def compute_summary(config: dict | None, period: str = "week") -> dict:
    """Compute the evaluation summary for a period (week/month/quarter)."""
    from web import eval_store

    ecfg = get_eval_config(config)
    horizon = _HORIZON_FOR_PERIOD.get(period, 5)
    rows = eval_store.get_eval_metrics_rows(config)
    full = [r for r in rows if r.get("variant") == "full" and r.get("horizon") == horizon]

    by_week: dict[str, list[dict]] = {}
    for r in full:
        by_week.setdefault(r["week_key"], []).append(r)

    weekly = []
    for wk in sorted(by_week):
        preds = by_week[wk]
        bench = next((r["benchmark_return"] for r in preds if r["benchmark_return"] is not None), None)
        buyable = sorted(
            [r for r in preds if is_buyable(r.get("rating")) and r.get("net_return") is not None],
            key=_sort_key,
        )
        selected = buyable[:3]
        port_net = sum((1.0 / 3.0) * r["net_return"] for r in selected) if selected else 0.0
        excess = (port_net - bench) if bench is not None else None
        weekly.append({
            "week_key": wk,
            "portfolio_net_return": port_net,
            "benchmark_return": bench,
            "excess_return": excess,
            "positions": len(selected),
        })

    excess_series = [w["excess_return"] for w in weekly if w["excess_return"] is not None]
    cum_excess = sum(excess_series) if excess_series else None
    ir = annualized_information_ratio(excess_series)

    port_returns = [w["portfolio_net_return"] for w in weekly]
    bench_returns = [w["benchmark_return"] for w in weekly if w["benchmark_return"] is not None]
    port_mdd = max_drawdown(build_nav(port_returns)) if port_returns else 0.0
    bench_mdd = max_drawdown(build_nav(bench_returns)) if bench_returns else 0.0
    mdd_delta = (port_mdd - bench_mdd) if bench_returns else None

    # A cohort becomes horizon-eligible when at least one stock in its week has
    # reached that horizon. Coverage then exposes missing prices within the same
    # mature cohort without penalising newer weeks that cannot have matured yet.
    eligible_weeks = {r["week_key"] for r in full if r.get("net_return") is not None}
    all_cases = eval_store.list_eval_cases(config, source="weekly", limit=100000)
    eligible_cases = {
        case["case_id"] for case in all_cases
        if case.get("week_key") in eligible_weeks
    }
    settled_cases = {
        r.get("case_id") or (r.get("week_key"), r.get("ticker"))
        for r in full if r.get("net_return") is not None
    }
    coverage_pct = (len(settled_cases) / len(eligible_cases)) if eligible_cases else None

    validity = evaluate_validity(
        config, len(weekly), coverage_pct, cum_excess, ir, mdd_delta,
    )

    direction = direction_accuracy(full)

    rating_avg = {}
    rating_counts = {}
    for rating in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        vals = [r["excess_return"] for r in full
                if r.get("rating") == rating and r.get("excess_return") is not None]
        rating_avg[rating] = (sum(vals) / len(vals)) if vals else None
        rating_counts[rating] = sum(1 for r in full if r.get("rating") == rating)

    result = {
        "period": period,
        "horizon": horizon,
        "mature_weeks": len(weekly),
        "coverage_pct": coverage_pct,
        "cum_excess_return": cum_excess,
        "annualized_ir": ir,
        "max_drawdown_delta": mdd_delta,
        "portfolio_max_drawdown": port_mdd,
        "benchmark_max_drawdown": bench_mdd,
        "validity": validity,
        "direction": direction,
        "rating_avg_excess": rating_avg,
        "rating_counts": rating_counts,
        "weekly": weekly,
    }

    if period == "month":
        result["ablation"] = _ablation_delta(
            rows, horizon, friction_bp=ecfg.get("friction_bp", 20)
        )

    return result


def _strategy_return(row: dict, friction_bp: float) -> float:
    """Translate a rating into a paper position return for fair ablation."""
    exposure = {
        "Buy": 1.0, "Overweight": 1.0, "Hold": 0.0,
        "Underweight": -1.0, "Sell": -1.0,
    }.get(row.get("rating"), 0.0)
    if exposure == 0:
        return 0.0
    gross = row.get("gross_return")
    if gross is None:
        gross = row.get("net_return") or 0.0
        return exposure * gross
    return exposure * gross - (float(friction_bp) / 10000.0)


def _ablation_delta(rows: list[dict], horizon: int, friction_bp: float = 20) -> dict:
    """Mean decision-aware strategy-return delta (full minus single)."""
    single = {r["case_id"]: r for r in rows
              if r.get("variant") == "single" and r.get("horizon") == horizon
              and r.get("net_return") is not None}
    full = {r["case_id"]: r for r in rows
            if r.get("variant") == "full" and r.get("horizon") == horizon
            and r.get("net_return") is not None}
    pairs = []
    for case_id in sorted(set(single) & set(full)):
        full_return = _strategy_return(full[case_id], friction_bp)
        single_return = _strategy_return(single[case_id], friction_bp)
        delta = full_return - single_return
        pairs.append({"case_id": case_id, "ticker": full[case_id].get("ticker"),
                      "full_net_return": full[case_id]["net_return"],
                      "single_net_return": single[case_id]["net_return"],
                      "full_strategy_return": full_return,
                      "single_strategy_return": single_return,
                      "delta": delta})
    mean_delta = (sum(p["delta"] for p in pairs) / len(pairs)) if pairs else None
    return {"pairs": pairs, "mean_delta": mean_delta}
