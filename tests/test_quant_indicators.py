"""Tests for the expanded indicator tools (Phase-4 wrappers over quant.indicators).

Uses a mocked ``_fetch_ohlcv`` so no network is touched.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantconclave.agents.utils import quant_tools as qt


@pytest.fixture()
def ohlcv_df():
    np.random.seed(5)
    n = 180
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.01))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.random.randint(1e5, 5e5, n).astype(float),
        }
    )


@pytest.fixture()
def patched_fetch(ohlcv_df, monkeypatch):
    def _fetch(symbol, lookback=60):
        return ohlcv_df
    monkeypatch.setattr(qt, "_fetch_ohlcv", _fetch)
    return ohlcv_df


@pytest.mark.parametrize("tool", qt.ADVANCED_INDICATOR_TOOLS)
def test_each_tool_returns_csv_string(patched_fetch, tool):
    result = tool.invoke({"symbol": "TEST.SH"})
    assert isinstance(result, str)
    lines = result.split("\n")
    # First line is the CSV header (contains 'date')
    assert lines[0].startswith("date,")
    # Exactly 20 data rows between header and comment
    data_rows = [l for l in lines if l and not l.startswith("#")]
    assert len(data_rows) == 21  # header + 20 rows
    # Trailing comment line
    assert any(l.startswith("# Latest") for l in lines)


def test_insufficient_data_returns_error(monkeypatch):
    monkeypatch.setattr(qt, "_fetch_ohlcv", lambda symbol, lookback=60: None)
    result = qt.get_cci.invoke({"symbol": "TEST.SH"})
    assert "Insufficient OHLCV data" in result


def test_cci_numerical_correctness(patched_fetch):
    # CCI should be a float series, and on a strong uptrend the latest
    # typically registers as overbought/neutral — just assert it's parseable.
    result = qt.get_cci.invoke({"symbol": "TEST.SH", "period": 20})
    cci_lines = [l for l in result.split("\n") if l and not l.startswith("#")][1:]
    last_row = cci_lines[-1]
    cci_val = float(last_row.split(",")[1])
    assert -400 <= cci_val <= 400  # sane range for a 20-period CCI


def test_quant_indicators_importable():
    from quantconclave.quant import indicators, utils
    assert hasattr(indicators, "cci")
    assert hasattr(indicators, "trend_score")
    assert hasattr(indicators, "rsrs")
    assert hasattr(indicators, "er")
    assert hasattr(utils, "calculate_atr")
