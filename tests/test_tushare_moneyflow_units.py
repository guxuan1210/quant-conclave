"""Tests for tushare moneyflow 万元→元 unit normalization.

tushare's `moneyflow` API (doc_id=25) returns ALL amount fields in 万元 —
e.g. net_mf_amount=96825.16 for 600030.SH on a day = 9.68亿 (plausible in
万元, absurd as 元). ``get_money_flow`` must normalize ×1e4 to 元 so every
consumer (SMS engine, verify_moneyflow, analyst/advisor prompts, Raw Flow
display) reads ONE convention matching the "All values in yuan" header.

No network — ``_get_pro`` is monkeypatched with a fake returning canned 万元
rows, exactly what the real API returns.
"""

from __future__ import annotations

import csv
import io
import re

import pandas as pd
import pytest

from capitalradar.dataflows import tushare_data


def _fake_pro(df):
    class _Pro:
        def moneyflow(self, **kwargs):
            return df
    return _Pro()


def _wan_frame():
    """Realistic 万元-scale rows for 600030 (中信证券): 9.68亿 net inflow day."""
    return pd.DataFrame([
        {
            "ts_code": "600030.SH", "trade_date": "20260826",
            "net_mf_amount": 96825.16,
            "buy_elg_amount": 152417.08, "buy_elg_vol": 551743,
            "sell_elg_amount": 86115.90, "sell_elg_vol": 300000,
            "buy_lg_amount": 70000.00,   "buy_lg_vol": 250000,
            "sell_lg_amount": 50000.00,  "sell_lg_vol": 180000,
            "buy_md_amount": 30000.00,   "buy_md_vol": 120000,
            "sell_md_amount": 20000.00,  "sell_md_vol": 90000,
            "buy_sm_amount": 10000.00,   "buy_sm_vol": 50000,
            "sell_sm_amount": 5000.00,   "sell_sm_vol": 25000,
        },
        {
            "ts_code": "600030.SH", "trade_date": "20260825",
            "net_mf_amount": -16774.68,
            "buy_elg_amount": 90000.00,  "buy_elg_vol": 320000,
            "sell_elg_amount": 106774.68, "sell_elg_vol": 380000,
            "buy_lg_amount": 50000.00,   "buy_lg_vol": 180000,
            "sell_lg_amount": 50000.00,  "sell_lg_vol": 180000,
            "buy_md_amount": 20000.00,   "buy_md_vol": 80000,
            "sell_md_amount": 15000.00,  "sell_md_vol": 60000,
            "buy_sm_amount": 8000.00,    "buy_sm_vol": 40000,
            "sell_sm_amount": 10000.00,  "sell_sm_vol": 50000,
        },
    ])


def _parse_csv(out: str) -> list[dict]:
    lines = out.split("\n")
    start = next(i for i, l in enumerate(lines)
                 if "ts_code" in l and "trade_date" in l)
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def _run(monkeypatch):
    monkeypatch.setattr(tushare_data, "_get_pro", lambda: _fake_pro(_wan_frame()))
    return tushare_data.get_money_flow("600030.SH", "20260801", "20260826")


def test_get_money_flow_normalizes_wan_to_yuan(monkeypatch):
    out = _run(monkeypatch)
    assert "# SKIP_VENDOR" not in out
    assert "All values in yuan" in out
    rows = _parse_csv(out)
    assert len(rows) == 2
    # sorted newest-first → 20260826 first
    assert rows[0]["trade_date"] == "20260826"
    # 万元 → 元: net_mf_amount 96825.16万元 → 968251600 元 (9.68亿)
    assert float(rows[0]["net_amount"]) == pytest.approx(96825.16 * 1e4)
    assert float(rows[0]["buy_elg_amount"]) == pytest.approx(152417.08 * 1e4)
    assert float(rows[0]["sell_elg_amount"]) == pytest.approx(86115.90 * 1e4)
    assert float(rows[1]["net_amount"]) == pytest.approx(-16774.68 * 1e4)
    # volume fields (手) stay raw — amounts ×1e4, vols NOT
    assert float(rows[0]["buy_elg_vol"]) == pytest.approx(551743.0)


def test_get_money_flow_summary_in_yuan_scale(monkeypatch):
    out = _run(monkeypatch)
    m = re.search(r"MainForce: ([+-][\d.]+)", out)
    assert m, "summary MainForce line missing"
    val = float(m.group(1))
    # elg+lg net over 2 days in 万元:
    #   (152417.08-86115.90 + 90000-106774.68) + (70000-50000 + 50000-50000)
    #   = 66301.18 - 16774.68 + 20000 = 69526.50万元 → 6.95265e8 元
    assert abs(val) > 1e8, f"MainForce should be 元-scale (~7e8), got {val}"
    assert abs(val) < 1e9, f"MainForce should be 元-scale (~7e8), got {val}"
