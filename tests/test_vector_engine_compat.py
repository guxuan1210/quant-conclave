"""Compatibility regression: the vectorized engine must produce the exact
dict shape the old backtrader engine did, consumable by report.py and the
frontend unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalradar.backtest.engine import run_backtest
from capitalradar.backtest.report import compute_performance, render_equity_chart_data

# Legacy engine's expected result dict keys (from the old bt-based engine.py)
LEGACY_RESULTS_KEYS = {
    "total_return_pct", "annual_return", "sharpe",
    "max_drawdown_pct", "total_trades", "initial_capital", "final_value",
}
LEGACY_TRADE_KEYS = {"entry_date", "exit_date", "entry_price", "exit_price", "pnl", "pnl_pct"}


@pytest.fixture()
def synthetic_panel():
    np.random.seed(11)
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


def test_result_key_sets_match_legacy(synthetic_panel):
    for tid in ("ma_cross", "macd", "rsi", "bollinger", "turtle", "ma_arrange", "volume_breakout"):
        result = run_backtest("TEST.SH", tid, {}, "2024-01-01", "2024-12-31",
                              data=synthetic_panel, persist=False)
        assert set(result.keys()) == {"results", "equity_curve", "trade_log"}
        assert set(result["results"].keys()) == LEGACY_RESULTS_KEYS
        for t in result["trade_log"]:
            assert set(t.keys()) == LEGACY_TRADE_KEYS
        for p in result["equity_curve"]:
            assert set(p.keys()) == {"date", "value"}


def test_compute_performance_consumes_new_results(synthetic_panel):
    result = run_backtest("TEST.SH", "ma_cross", {}, "2024-01-01", "2024-12-31",
                          data=synthetic_panel, persist=False)
    report = compute_performance(result)
    assert "Backtest Report" in report
    assert "Total Return" in report
    assert "Sharpe" in report


def test_render_equity_chart_data_consumes_new_results(synthetic_panel):
    result = run_backtest("TEST.SH", "ma_cross", {}, "2024-01-01", "2024-12-31",
                          data=synthetic_panel, persist=False)
    chart = render_equity_chart_data(result["equity_curve"])
    assert len(chart) == len(result["equity_curve"])
    assert chart[0]["date"] and chart[0]["value"]
