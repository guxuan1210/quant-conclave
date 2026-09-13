"""Tests for OHLCV 换手率 support (2026-09-01).

The watchlist / index-picking prompt (``watchlist_stock_detail``) demands
per-day 换手 checks (路径B 换手<8%, 路径E 高换手>10%), but the daily OHLCV
feed used to drop turnover entirely:

- tushare ``pro.daily`` has NO turnover column — it lives in ``daily_basic``.
- akshare maps 换手率 → ``TurnoverRate``, which the parser dropped.

These tests pin the fix: ``parse_ohlcv_csv`` keeps a ``turnover`` float column
(0 = vendor didn't supply it), and the tushare ``get_stock_data`` vendor joins
``daily_basic.turnover_rate`` so the prompt path gets real per-day values.
The backtest panel is untouched — it selects columns explicitly.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalradar.backtest.data import fetch_ohlcv_panel, parse_ohlcv_csv
from capitalradar.dataflows import tushare_data


# ---- parse_ohlcv_csv keeps turnover from every vendor spelling ----------------


def test_parse_keeps_turnover_tushare_style():
    raw = (
        "# Stock Data for 600030.SH\n\n"
        "ts_code,trade_date,open,high,low,close,volume,turnover_rate\n"
        "600030.SH,2026-08-31,28.0,28.2,27.7,27.8,300000,0.93\n"
        "600030.SH,2026-08-28,27.8,28.0,27.7,27.93,250000,0.80\n"
    )
    df = parse_ohlcv_csv(raw)
    assert list(df["turnover"]) == pytest.approx([0.80, 0.93])  # ascending date
    assert df.iloc[-1]["turnover"] == pytest.approx(0.93)


def test_parse_keeps_turnover_akshare_style():
    """akshare emits TurnoverRate (mapped from 换手率)."""
    raw = (
        "Date,Open,Close,High,Low,Volume,TurnoverRate\n"
        "2026-08-31,28.0,27.8,28.2,27.7,300000,1.42\n"
    )
    df = parse_ohlcv_csv(raw)
    assert df.iloc[0]["turnover"] == pytest.approx(1.42)


def test_parse_turnover_defaults_zero_when_absent():
    """yfinance / plain daily CSV has no turnover → 0, never None or NaN."""
    raw = (
        "trade_date,open,high,low,close,volume\n"
        "2026-08-31,28.0,28.2,27.7,27.8,300000\n"
    )
    df = parse_ohlcv_csv(raw)
    assert float(df.iloc[0]["turnover"]) == 0.0
    assert pd.notna(df.iloc[0]["turnover"])


def test_fetch_ohlcv_panel_columns_unchanged(monkeypatch):
    """The backtester selects open/high/low/close/volume explicitly — the extra
    turnover column must not leak into the (symbol, eob) panel."""
    raw = (
        "ts_code,trade_date,open,high,low,close,volume,turnover_rate\n"
        "600030.SH,2026-08-31,28.0,28.2,27.7,27.8,300000,0.93\n"
        "600030.SH,2026-08-28,27.8,28.0,27.7,27.93,250000,0.80\n"
    )
    monkeypatch.setattr(
        "capitalradar.dataflows.interface.route_to_vendor",
        lambda *a, **k: raw,
    )
    panel = fetch_ohlcv_panel("600030.SH", "2026-08-28", "2026-08-31")
    assert list(panel.columns) == ["open", "high", "low", "close", "volume"]


# ---- tushare get_stock_data joins daily_basic turnover ------------------------


def _daily_frame():
    return pd.DataFrame([
        {"ts_code": "600030.SH", "trade_date": "20260831", "open": 28.0,
         "high": 28.2, "low": 27.7, "close": 27.8, "pre_close": 27.9,
         "change": -0.1, "pct_chg": -0.36, "vol": 300000, "amount": 8.4e8},
        {"ts_code": "600030.SH", "trade_date": "20260828", "open": 27.8,
         "high": 28.0, "low": 27.7, "close": 27.93, "pre_close": 27.8,
         "change": 0.13, "pct_chg": 0.47, "vol": 250000, "amount": 7.0e8},
    ])


def _basic_frame():
    return pd.DataFrame([
        {"trade_date": "20260831", "turnover_rate": 0.93, "turnover_rate_f": 0.90},
        {"trade_date": "20260828", "turnover_rate": 0.80, "turnover_rate_f": 0.78},
    ])


def _fake_pro(basic_df=_basic_frame(), basic_raises=False):
    class _Pro:
        def daily(self, **kwargs):
            return _daily_frame()

        def daily_basic(self, **kwargs):
            if basic_raises:
                raise RuntimeError("no permission")
            return basic_df
    return _Pro()


def test_tushare_vendor_joins_daily_basic_turnover(monkeypatch):
    monkeypatch.setattr(tushare_data, "_get_pro", lambda: _fake_pro())
    out = tushare_data.get_stock_data("600030.SH", "20260820", "20260831")
    assert "# SKIP_VENDOR" not in out
    df = parse_ohlcv_csv(out)
    assert len(df) == 2
    # newest-first CSV → parse normalizes ascending; 08-31 turnover 0.93 present
    assert df.iloc[-1]["turnover"] == pytest.approx(0.93)
    assert df.iloc[-2]["turnover"] == pytest.approx(0.80)
    assert all(df["turnover"] > 0)          # join actually landed, not all-zero


def test_tushare_vendor_degrades_when_daily_basic_fails(monkeypatch):
    """daily_basic permission error must not kill the OHLCV fetch — the CSV is
    emitted without a turnover column and parse falls back to 0."""
    monkeypatch.setattr(tushare_data, "_get_pro", lambda: _fake_pro(basic_raises=True))
    out = tushare_data.get_stock_data("600030.SH", "20260820", "20260831")
    assert "# SKIP_VENDOR" not in out
    df = parse_ohlcv_csv(out)
    assert len(df) == 2
    assert float(df.iloc[-1]["turnover"]) == 0.0
    assert float(df.iloc[-1]["close"]) == pytest.approx(27.8)  # price data intact
