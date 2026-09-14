"""Weekly paper portfolio construction from buyable predictions."""

from __future__ import annotations

_RATING_PRIORITY = {"Buy": 0, "Overweight": 1}
_CONF_PRIORITY = {"high": 0, "medium": 1, "low": 2}


def is_buyable(rating: str) -> bool:
    return rating in ("Buy", "Overweight")


def build_weekly_portfolio(predictions: list[dict], max_positions: int = 3):
    """Return ``(allocations, cash_weight)`` for a week's buyable predictions.

    Only ``Buy``/``Overweight`` are buyable. They are sorted by rating priority
    (Buy > Overweight), then confidence (high > medium > low), then smart-money
    score descending, and the top ``max_positions`` each get 1/``max_positions``
    of the portfolio (1/3 by default); the remainder is held as cash. No shorting.
    """
    buyable = [p for p in predictions if is_buyable(p.get("rating"))]
    buyable.sort(key=lambda p: (
        _RATING_PRIORITY.get(p.get("rating"), 9),
        _CONF_PRIORITY.get(str(p.get("confidence") or "").lower(), 9),
        -(p.get("smart_money_score") or 0),
    ))
    selected = buyable[:max_positions]
    if not selected:
        return [], 1.0

    weight = 1.0 / max_positions
    allocations = [{"code": p.get("code"), "weight": weight} for p in selected]
    cash_weight = 1.0 - weight * len(selected)
    return allocations, cash_weight
