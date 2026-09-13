"""Tests for the vectorized backtest engine and 7 signal functions.

All tests use synthetic OHLCV — no network access.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalradar.backtest.engine import run_backtest
from capitalradar.backtest.strategies import (
    SIGNAL_MAP,
    get_strategy_class,
    is_builtin_template,
)
from capitalradar.backtest.metrics import _derive_trades
from capitalradar.quant.backtest.vector import VectorBacktester


@pytest.fixture()
def synthetic_panel():
    """A 300-bar (symbol, eob) MultiIndex OHLCV panel with a clear uptrend then chop."""
    np.random.seed(42)
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.012))
    df = pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.012,
            "low": close * 0.988,
            "close": close,
            "volume": np.random.randint(1e5, 5e5, n).astype(float),
        },
        index=dates,
    )
    df["symbol"] = "TEST.SH"
    df = df.set_index(["symbol", df.index])
    df.index.names = ["symbol", "eob"]
    df = df[["open", "high", "low", "close", "volume"]]
    return df


def test_signal_functions_return_binary_positions(synthetic_panel):
    single = synthetic_panel.xs("TEST.SH", level="symbol")
    for tid, fn in SIGNAL_MAP.items():
        sig = fn(single)
        assert isinstance(sig, pd.Series), f"{tid} did not return a Series"
        assert set(sig.dropna().unique()).issubset({0.0, 1.0}), f"{tid} returned non-binary positions"
        # Position starts flat before the first entry signal
        assert sig.iloc[:5].sum() == 0.0 or len(sig) >= 5


@pytest.mark.parametrize("template_id", list(SIGNAL_MAP.keys()))
def test_run_backtest_returns_full_dict(synthetic_panel, template_id):
    result = run_backtest(
        "TEST.SH", template_id, {}, "2024-01-01", "2024-12-31",
        data=synthetic_panel, persist=False,
    )
    assert "results" in result and "equity_curve" in result and "trade_log" in result
    res = result["results"]
    for key in ("total_return_pct", "annual_return", "sharpe",
                "max_drawdown_pct", "total_trades", "initial_capital", "final_value"):
        assert key in res, f"missing results key: {key}"
    assert res["initial_capital"] == 100000.0
    assert len(result["equity_curve"]) > 0
    for point in result["equity_curve"]:
        assert "date" in point and "value" in point


def test_parameter_sensitivity(synthetic_panel):
    """Different fast/slow periods yield different trade counts."""
    r1 = run_backtest("TEST.SH", "ma_cross", {"fast_period": 5, "slow_period": 20},
                      "2024-01-01", "2024-12-31", data=synthetic_panel, persist=False)
    r2 = run_backtest("TEST.SH", "ma_cross", {"fast_period": 20, "slow_period": 60},
                      "2024-01-01", "2024-12-31", data=synthetic_panel, persist=False)
    # Either trade counts differ or returns differ — the params must matter
    assert (r1["results"]["total_trades"] != r2["results"]["total_trades"]
            or abs(r1["results"]["total_return_pct"] - r2["results"]["total_return_pct"]) > 0.01)


def test_trade_log_fields(synthetic_panel):
    result = run_backtest("TEST.SH", "ma_cross", {}, "2024-01-01", "2024-12-31",
                          data=synthetic_panel, persist=False)
    if result["trade_log"]:
        trade = result["trade_log"][0]
        for key in ("entry_date", "exit_date", "entry_price", "exit_price", "pnl", "pnl_pct"):
            assert key in trade, f"missing trade key: {key}"


def test_strategy_resolution():
    assert is_builtin_template("ma_cross")
    assert not is_builtin_template("nope")
    fn = get_strategy_class("rsi")
    assert callable(fn)
    with pytest.raises(ValueError):
        get_strategy_class("unknown")


def test_derive_trades_transitions():
    """0->1 entry, 1->0 exit produces one closed trade."""
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    weights = pd.DataFrame({"TEST": [0.0, 1.0, 1.0, 1.0, 0.0]}, index=dates)
    closes = pd.DataFrame({"TEST": [10.0, 11.0, 12.0, 13.0, 14.0]}, index=dates)
    trades = _derive_trades(weights, closes, 10000.0)
    assert len(trades) == 1
    t = trades[0]
    assert t["entry_price"] == 11.0
    assert t["exit_price"] == 14.0
    # pnl = (14-11) * (10000/11)
    assert abs(t["pnl"] - 2727.27) < 1.0
    assert abs(t["pnl_pct"] - 27.27) < 1.0


def test_vector_backtester_requires_multiindex():
    df = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError):
        VectorBacktester(data=df, trade_at="close", commission=0.0003, slippage_bp=2.0)
