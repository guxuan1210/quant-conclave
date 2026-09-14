"""Tests for evaluation metrics (pure functions, synthetic values)."""

from __future__ import annotations

import math

import pytest

from quantconclave.evaluation.metrics import (
    annualized_information_ratio,
    build_nav,
    direction_accuracy,
    evaluate_validity,
    max_drawdown,
    portfolio_return,
)


@pytest.mark.unit
def test_annualized_information_ratio():
    ir = annualized_information_ratio([0.01, 0.02, 0.03])
    mean = 0.02
    var = ((0.01 - mean) ** 2 + (0.02 - mean) ** 2 + (0.03 - mean) ** 2) / 2
    std = math.sqrt(var)
    assert ir == pytest.approx((mean / std) * math.sqrt(52))


@pytest.mark.unit
def test_annualized_information_ratio_filters_none():
    a = annualized_information_ratio([0.01, None, 0.03])
    b = annualized_information_ratio([0.01, 0.03])
    assert a == pytest.approx(b)


@pytest.mark.unit
def test_annualized_information_ratio_undefined():
    assert annualized_information_ratio([]) is None
    assert annualized_information_ratio([0.01]) is None
    assert annualized_information_ratio([0.02, 0.02, 0.02]) is None  # zero variance


@pytest.mark.unit
def test_max_drawdown():
    assert max_drawdown([1.0, 1.2, 0.9, 1.1]) == pytest.approx(0.9 / 1.2 - 1.0)
    assert max_drawdown([1.0, 1.1, 1.2]) == 0.0
    assert max_drawdown([1.0, 0.8]) == pytest.approx(-0.20)


@pytest.mark.unit
def test_build_nav():
    assert build_nav([0.10, -0.10]) == pytest.approx([1.0, 1.1, 0.99])
    assert build_nav([]) == [1.0]


@pytest.mark.unit
def test_portfolio_return():
    allocations = [
        {"code": "A.SH", "weight": 1 / 3},
        {"code": "B.SH", "weight": 1 / 3},
        {"code": "C.SH", "weight": 1 / 3},
    ]
    net_returns = {"A.SH": 0.06, "B.SH": 0.03, "C.SH": 0.0}
    assert portfolio_return(allocations, net_returns) == pytest.approx(0.03)
    # Missing return treated as 0.
    assert portfolio_return(allocations, {"A.SH": 0.06}) == pytest.approx(0.02)


@pytest.mark.unit
def test_direction_accuracy():
    records = [
        {"rating": "Buy", "excess_return": 0.05},        # correct bullish
        {"rating": "Buy", "excess_return": -0.01},       # wrong
        {"rating": "Overweight", "excess_return": 0.02},  # correct
        {"rating": "Sell", "excess_return": -0.03},      # correct bearish
        {"rating": "Sell", "excess_return": 0.01},       # wrong
        {"rating": "Underweight", "excess_return": -0.02},  # correct
        {"rating": "Hold", "excess_return": None},       # neutral
        {"rating": "Hold", "excess_return": 0.01},       # neutral
    ]
    result = direction_accuracy(records)
    assert result["directional_total"] == 6
    assert result["correct"] == 4
    assert result["neutral"] == 2
    assert result["accuracy"] == pytest.approx(4 / 6)


@pytest.mark.unit
def test_direction_accuracy_no_directional():
    assert direction_accuracy([{"rating": "Hold", "excess_return": 0.01}])["accuracy"] is None


@pytest.mark.unit
def test_evaluate_validity_insufficient_weeks():
    out = evaluate_validity({}, 25, 0.95, 0.01, 1.0, 0.0)
    assert out["status"] == "insufficient_data"


@pytest.mark.unit
def test_evaluate_validity_insufficient_coverage():
    out = evaluate_validity({}, 26, 0.80, 0.01, 1.0, 0.0)
    assert out["status"] == "insufficient_data"


@pytest.mark.unit
def test_evaluate_validity_effective():
    out = evaluate_validity({}, 26, 0.95, 0.01, 1.0, -0.01)
    assert out["status"] == "effective"


@pytest.mark.unit
def test_evaluate_validity_not_effective_excess():
    assert evaluate_validity({}, 26, 0.95, -0.01, 1.0, 0.0)["status"] == "not_effective"
    assert evaluate_validity({}, 26, 0.95, 0.0, 1.0, 0.0)["status"] == "not_effective"


@pytest.mark.unit
def test_evaluate_validity_not_effective_ir():
    assert evaluate_validity({}, 26, 0.95, 0.01, -1.0, 0.0)["status"] == "not_effective"


@pytest.mark.unit
def test_evaluate_validity_not_effective_drawdown():
    # -4pp is worse than the 3pp limit.
    assert evaluate_validity({}, 26, 0.95, 0.01, 1.0, -0.04)["status"] == "not_effective"
    # -2pp is within tolerance.
    assert evaluate_validity({}, 26, 0.95, 0.01, 1.0, -0.02)["status"] == "effective"
