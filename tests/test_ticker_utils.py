"""Tests for the unified ticker format conversion layer.

Verifies that each data source receives the ticker format it expects.
"""

from __future__ import annotations

import pytest

from capitalradar.dataflows.ticker_utils import (
    is_cn_ticker,
    normalize_symbol,
    to_yfinance,
    to_tushare,
    to_akshare,
    to_tencent,
    to_xueqiu,
    to_eastmoney,
    to_code,
)


# ── is_cn_ticker ──

@pytest.mark.parametrize("symbol,expected", [
    ("000001.SZ", True),
    ("600519.SH", True),
    ("600519.SS", True),   # .SS must be accepted
    ("688981.SH", True),
    ("000001", True),      # bare 6-digit
    ("AAPL", False),
    ("0700.HK", False),
    ("CNC.TO", False),
])
def test_is_cn_ticker(symbol, expected):
    assert is_cn_ticker(symbol) is expected


# ── normalize_symbol (canonical DB format) ──

@pytest.mark.parametrize("raw,expected", [
    ("000001.SZ", "000001.SZ"),
    ("600519.SH", "600519.SH"),
    ("600519.SS", "600519.SH"),   # .SS → .SH
    ("600519.she", "600519.SZ"),   # .SHE → .SZ
    ("000001", "000001.SZ"),       # bare → add suffix (0→SZ)
    ("600519", "600519.SH"),       # bare → add suffix (6→SH)
    ("688981", "688981.SH"),
    ("830001", "830001.BJ"),       # 8→BJ
    ("AAPL", "AAPL"),
    ("0700.HK", "0700.HK"),
    ("00700", "00700.HK"),         # bare 5-digit → HK
    (" 600519.SH ", "600519.SH"),  # whitespace trimmed
])
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


# ── per-source conversions ──

def test_to_yfinance_shanghai_uses_ss():
    assert to_yfinance("600519.SH") == "600519.SS"
    assert to_yfinance("600519.SS") == "600519.SS"
    # SZ stays .SZ
    assert to_yfinance("000001.SZ") == "000001.SZ"
    # US unchanged
    assert to_yfinance("AAPL") == "AAPL"


def test_to_tushare_canonical():
    assert to_tushare("600519.SS") == "600519.SH"
    assert to_tushare("000001") == "000001.SZ"
    assert to_tushare("000001.SZ") == "000001.SZ"


def test_to_akshare_pairs():
    assert to_akshare("000001.SZ") == ("000001", "sz")
    assert to_akshare("600519.SH") == ("600519", "sh")
    assert to_akshare("600519.SS") == ("600519", "sh")  # .SS → sh
    assert to_akshare("688981") == ("688981", "sh")


def test_to_tencent_prefix():
    assert to_tencent("000001.SZ") == "sz000001"
    assert to_tencent("600519.SH") == "sh600519"
    assert to_tencent("600519.SS") == "sh600519"
    assert to_tencent("0700.HK") == "hk0700"
    assert to_tencent("00700") == "hk00700"  # bare HK
    assert to_tencent("sh600519") == "sh600519"  # already tencent format


def test_to_xueqiu_uppercase_prefix():
    assert to_xueqiu("000001.SZ") == "SZ000001"
    assert to_xueqiu("600519.SH") == "SH600519"
    assert to_xueqiu("600519.SS") == "SH600519"


def test_to_eastmoney_canonical():
    assert to_eastmoney("600519.SS") == "600519.SH"
    assert to_eastmoney("000001.SZ") == "000001.SZ"


def test_to_code_bare():
    assert to_code("000001.SZ") == "000001"
    assert to_code("600519.SH") == "600519"
    assert to_code("600519") == "600519"


# ── integration: vendor bug fixes ──

def test_akshare_accepts_ss_suffix():
    from capitalradar.dataflows.akshare_data import (
        _is_cn_ticker as ak_is_cn, _ticker_to_ak_symbol,
    )
    assert ak_is_cn("600519.SS")
    assert _ticker_to_ak_symbol("600519.SS") == ("600519", "sh")


def test_tushare_accepts_ss_suffix():
    from capitalradar.dataflows.tushare_data import _is_cn_ticker as ts_is_cn
    assert ts_is_cn("600519.SS")


def test_chart_app_yfinance_converts_ss():
    """ohlcv._via_yfinance must feed yfinance the .SS form for A-shares."""
    import inspect
    from capitalradar.dataflows import ohlcv
    src = inspect.getsource(ohlcv._via_yfinance)
    assert "to_yfinance" in src
