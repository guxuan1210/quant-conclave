"""Tests for the dynamic strategy engine — 180-day bottom fields and
candidate-pool field validation (the data-validation contract).
"""

from __future__ import annotations

import pytest

from quantconclave.sector_scan.dynamic_strategy import (
    STRATEGY_FIELDS,
    validate_strategy_fields,
    execute_strategy,
)


def _pool():
    return [
        {"ticker": "000001.SZ", "smart_money_score": 60, "rsi_14": 24, "price_vs_180d_low": 1.05},
        {"ticker": "000002.SZ", "smart_money_score": 75, "rsi_14": 28, "price_vs_180d_low": 1.35},
        {"ticker": "000003.SZ", "smart_money_score": 55, "rsi_14": 26, "price_vs_180d_low": 1.10},
    ]


def test_strategy_fields_include_180d_bottom_fields():
    assert "price_vs_180d_low" in STRATEGY_FIELDS
    assert "price_vs_180d_high" in STRATEGY_FIELDS
    # 60-day kept for backward compatibility
    assert "price_vs_60d_low" in STRATEGY_FIELDS


def test_validate_strategy_fields_no_missing():
    strategy = {
        "name": "超跌反弹",
        "conditions": [
            {"field": "smart_money_score", "op": ">=", "value": 50},
            {"field": "price_vs_180d_low", "op": "<=", "value": 1.2},
        ],
        "order_by": "smart_money_score",
        "limit": 10,
    }
    assert validate_strategy_fields(strategy, _pool()) == []


def test_validate_strategy_fields_detects_missing():
    strategy = {
        "name": "抄底",
        "conditions": [
            {"field": "smart_money_score", "op": ">=", "value": 50},
            {"field": "price_vs_180d_low", "op": "<=", "value": 1.2},
            {"field": "net_inflow_5d", "op": ">=", "value": 5000},
        ],
        "order_by": "smart_money_score",
        "limit": 10,
    }
    missing = validate_strategy_fields(strategy, _pool())
    assert missing == ["net_inflow_5d"]


def test_validate_strategy_fields_empty_pool_reports_all():
    strategy = {
        "name": "x",
        "conditions": [{"field": "smart_money_score", "op": ">=", "value": 50}],
    }
    assert validate_strategy_fields(strategy, []) == ["smart_money_score"]


def test_execute_strategy_filters_by_180d_bottom():
    strategy = {
        "name": "超跌反弹",
        "conditions": [
            {"field": "smart_money_score", "op": ">=", "value": 50},
            {"field": "price_vs_180d_low", "op": "<=", "value": 1.2},
            {"field": "rsi_14", "op": "<=", "value": 30},
        ],
        "order_by": "smart_money_score",
        "limit": 10,
    }
    results = execute_strategy(strategy, _pool())
    codes = [r["ticker"] for r in results]
    # 000002 excluded: too far from its 180-day low (1.35 > 1.2)
    assert codes == ["000001.SZ", "000003.SZ"]
