"""Regression tests: SMS 主力口径 (Field-semantics) fixes, 2026-09-01.

Pins the 002696 bug + the 5 fixes:
  Fix 1  _fetch_flow_data parses 主力 = 超大单净额 + 大单净额, NOT the vendor's
         raw net_amount column (tushare net_amount is an ill-defined 净流入额:
         002696 on 2026-09-01 reports -5052万 while 超大单+大单 = +3225万).
  Fix 2  _dual_source_gate cross-checks the primary vendor against 同花顺 before
         scoring; on 偏差 the verdict is demoted to no_signal and the summary
         becomes a conflict warning.
  Fix 2b _dual_source_gate compares 主力 vs 同花顺主力 (both sides' net_amount
         now = 主力). It must NOT swap net_amount for _gross_net (东财原始
         net_mf_amount) — 001366 (2026-09-02 涨停) 曾因此 5/10/20日全报「方向相反」
         而 东财主力 +3628万 vs 同花顺 +3601万 实际同向一致.
  Fix 3  _score_direction downweights a limit-up day (×0.2) so a single
         distorted day cannot dominate the 5-day direction read.
  Fix 4  _score_alignment only calls it 拉高出货 when outflow is sustained
         (2+ consecutive outflow days), not after one noisy day.
"""

from __future__ import annotations

import capitalradar.agents.utils.capital_flow_tools as cft
import capitalradar.sector_scan.moneyflow_verifier as mv
import capitalradar.sector_scan.smart_money_score as sms
import pytest

from capitalradar.sector_scan.smart_money_score import (
    _dual_source_gate,
    _fetch_flow_data,
    _is_limit_up_day,
    _score_alignment,
    _score_direction,
    detect_smart_money,
)


# ---- Fix 1: parser 主力口径 -------------------------------------------------


def test_fetch_flow_data_parses_main_force_not_raw_net_amount(monkeypatch):
    """002696 (2026-09-01) shape: raw net_amount=-5052万 but 主力 = +3225万.

    The parser must compute net_amount from the four bucket columns, and keep
    the vendor's raw field as _gross_net for cross-vendor comparison.
    """
    newest_first = (
        "# Money Flow (主力资金流向) for 002696.SZ\n"
        "# Source: 东方财富 via tushare\n\n"
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount\n"
        #     date         net_amount   buy_elg  sell_elg  buy_lg  sell_lg
        "002696.SZ,2026-09-01,-5052,4000,2000,2500,1275\n"   # 主力 = +2000 + 1225 = +3225
        "002696.SZ,2026-08-29,1000,1500,500,800,200\n"       # 主力 = +1000 + 600 = +1600
        "002696.SZ,2026-08-28,800,1200,400,600,300\n"        # 主力 = +800 + 300 = +1100
        "002696.SZ,2026-08-27,700,1000,300,400,100\n"        # 主力 = +700 + 300 = +1000
        "002696.SZ,2026-08-26,-600,300,400,200,300\n"        # 主力 = -100 - 100 = -200
    )

    class _Invoke:
        def invoke(self, *args, **kwargs):
            return newest_first

    monkeypatch.setattr(cft, "get_money_flow", _Invoke())
    rows = _fetch_flow_data("002696.SZ", {})
    assert len(rows) == 5
    assert rows[-1]["date"] == "2026-09-01"      # normalized chronological
    newest = rows[-1]
    # 主力净额 = 超大单净额 + 大单净额 = (4000-2000) + (2500-1275)
    assert newest["net_amount"] == pytest.approx((4000 - 2000) + (2500 - 1275))
    assert newest["net_amount"] == pytest.approx(3225)      # ← the corrected 主力
    assert newest["_gross_net"] == pytest.approx(-5052)     # ← raw vendor field kept
    # 修正后 9/1 为净流入；用原始 net_amount 口径则 5日净额 = -3158 净流出
    assert newest["net_amount"] > 0


def test_fetch_flow_data_falls_back_to_net_amount_when_no_bucket_cols(monkeypatch):
    """Defensive: a CSV with no 四档 columns falls back to raw net_amount
    instead of silently zeroing every row."""
    csv_text = (
        "# Money Flow for 600030.SH\n\n"
        "ts_code,trade_date,net_amount\n"
        + "".join(
            f"600030.SH,2026-08-{d:02d},{100 + d * 10}\n" for d in range(20, 15, -1)
        )
    )

    class _Invoke:
        def invoke(self, *args, **kwargs):
            return csv_text

    monkeypatch.setattr(cft, "get_money_flow", _Invoke())
    rows = _fetch_flow_data("600030.SH", {})
    assert len(rows) == 5
    assert rows[-1]["date"] == "2026-08-20"
    assert rows[-1]["net_amount"] == pytest.approx(100 + 20 * 10)   # 300
    assert rows[-1]["_gross_net"] == pytest.approx(100 + 20 * 10)


# ---- Fix 3: limit-up downweight ---------------------------------------------


def test_direction_downweights_limit_up_day():
    """Most-recent day is a big outflow; other 4 days are inflow. Without the
    downweight the single day dominates (net_5d < 0); with it the weighted read
    flips to inflow. Reported 5日净额 stays the true raw sum either way."""
    rows = [
        {"net_amount": 1e7, "date": f"2026-08-{d:02d}"} for d in range(1, 5)
    ] + [{"net_amount": -5e7, "date": "2026-09-01"}]   # limit-up day: big out
    raw = _score_direction(rows, is_limit_up=False)
    down = _score_direction(rows, is_limit_up=True)

    assert raw["net_5d_wan"] == down["net_5d_wan"]       # reported sum identical
    assert raw["net_5d_wan"] < 0                         # raw 5日 = -1000万
    assert down["score"] > raw["score"]                  # downweight lifts the read
    assert "涨停日资金已降权" in down["detail"]


def test_direction_limit_up_downweight_does_not_distort_all_same_sign():
    """When all 5 days are same-direction, downweighting must not change the
    score — the ratio net/|net| is 1.0 either way."""
    rows = [{"net_amount": 1e7, "date": f"2026-08-{d:02d}"} for d in range(1, 6)]
    assert _score_direction(rows, is_limit_up=True)["score"] == \
           _score_direction(rows, is_limit_up=False)["score"]


@pytest.mark.parametrize("ticker,pct,expected", [
    ("600519.SH", 9.98, True),     # 主板 10%
    ("600519.SH", 9.5, False),     # 未触板
    ("300750.SZ", 19.9, True),     # 创业板 20%
    ("300750.SZ", 15.0, False),
    ("688981.SH", 19.6, True),     # 科创板 20%
    ("830799.BJ", 29.5, True),     # 北交所 30%
])
def test_is_limit_up_day_by_board(ticker, pct, expected):
    assert _is_limit_up_day(pct, ticker) is expected


def test_is_limit_up_day_none_is_false():
    assert _is_limit_up_day(None, "600519.SH") is False


# ---- Fix 4: 拉高出货 tightening ----------------------------------------------


def _price_info_up():
    return {
        "closes": [10.0, 10.1, 10.2, 10.3, 10.4],
        "current": 10.5,          # 5日 +5.0%
        "today_change": 0.01,
        "data_date": "2026-08-05",
    }


def test_alignment_not_lagao_on_single_outflow_day():
    """Price up 5% but only the most recent day outflows → 量价背离 (40),
    NOT 拉高出货 (25). The old code called every price-up + net-out day
    拉高出货 even when the outflow was one noisy day."""
    rows = [
        {"net_amount": 1e7, "date": f"2026-08-{d:02d}"} for d in range(1, 5)
    ] + [{"net_amount": -5e7, "date": "2026-09-01"}]     # single big outflow day
    # net_5d = 4×1e7 − 5e7 = −1e7 < 0 → flow_out, but only 1 consecutive out day
    out = _score_alignment(rows, _price_info_up())
    assert out["score"] == 40
    assert out["detail"].startswith("量价背离")
    assert "未确认拉高出货" in out["detail"]     # 判定标签不是「拉高出货」


def test_alignment_confirms_lagao_on_two_consecutive_outflow_days():
    """Two consecutive outflow days while price rises → sustained distribution,
    so 拉高出货 is justified."""
    rows = [
        {"net_amount": 1e7, "date": "2026-08-27"},
        {"net_amount": 1e7, "date": "2026-08-28"},
        {"net_amount": 1e7, "date": "2026-08-29"},
        {"net_amount": -2e7, "date": "2026-09-01"},
        {"net_amount": -2e7, "date": "2026-09-02"},
    ]
    # net_5d = 3×1e7 − 2×2e7 = −1e7 < 0, two consecutive out days
    out = _score_alignment(rows, _price_info_up())
    assert out["score"] == 25
    assert "拉高出货" in out["detail"]


# ---- Fix 2: dual-source trust gate ------------------------------------------


def test_dual_source_gate_off_when_not_enabled():
    assert _dual_source_gate("000001.SZ", [], {})["verdict"] == "未启用"


def test_dual_source_gate_skips_realtime_rows():
    rows = [{"net_amount": 1e6, "_source": "realtime", "date": "2026-09-01"}]
    out = _dual_source_gate("002696.SZ", rows, {"sms_dual_source_gate": True})
    assert out["verdict"] == "未启用"


def test_dual_source_gate_compares_main_force_and_memoizes(monkeypatch):
    """The gate forwards the SMS 主力 rows (net_amount = elg+lg) UNCHANGED to
    verify_moneyflow — the 同花顺 side measures 主力 too, so both compare like
    for like (001366: raw 东财 net_amount / _gross_net falsely reversed every
    window while 东财主力 +3628 vs 同花顺 +3601 agreed) — and memoizes per
    ticker per day (1 tushare call)."""
    calls = []

    def fake_verify(ticker, flow_rows=None, config=None):
        calls.append(ticker)
        assert flow_rows, "must receive primary rows"
        # net_amount must stay the 主力 value — NEVER swapped for _gross_net
        assert flow_rows[0]["net_amount"] == 100.0
        assert flow_rows[0]["_gross_net"] == -5052.0
        return {"verdict": "偏差", "note": "1个窗口方向相反", "windows": [],
                "primary_source": "SMS引擎数据", "verifier_source": "ths",
                "max_ratio": 10.0}

    monkeypatch.setattr(mv, "verify_moneyflow", fake_verify)
    rows = [
        {"net_amount": 100.0, "_gross_net": -5052.0, "date": "2026-09-01"},
        {"net_amount": 200.0, "_gross_net": 1234.0, "date": "2026-08-29"},
    ]
    sms._DUAL_SOURCE_CACHE.clear()
    try:
        g1 = _dual_source_gate("002696.SZ", rows, {"sms_dual_source_gate": True})
        g2 = _dual_source_gate("002696.SZ", rows, {"sms_dual_source_gate": True})
    finally:
        sms._DUAL_SOURCE_CACHE.clear()
    assert g1["verdict"] == "偏差"
    assert g2["verdict"] == "偏差"
    assert len(calls) == 1        # second call served from cache


def test_detect_withdraws_score_on_dual_source_conflict(monkeypatch):
    """Gate verdict 偏差 → verdict demoted to no_signal, summary becomes a
    conflict warning, original composite preserved for renderers."""
    rows = [
        {"net_amount": 1e7, "buy_elg_amount": 1e7, "sell_elg_amount": 0,
         "buy_lg_amount": 0, "sell_lg_amount": 0, "date": f"2026-08-{i:02d}"}
        for i in range(1, 6)
    ]
    monkeypatch.setattr(sms, "_fetch_flow_data", lambda t, c: rows)
    monkeypatch.setattr(sms, "_fetch_price_data", lambda t, c: {
        "closes": [10.0] * 5, "current": 10.0, "today_change": 0.0,
        "data_date": "2026-08-05",
    })
    monkeypatch.setattr(sms, "_score_confirmation",
                        lambda *a, **k: {"score": 60, "detail": "ok", "sources": 1})
    monkeypatch.setattr(sms, "_dual_source_gate",
                        lambda *a, **k: {"verdict": "偏差", "note": "测试冲突", "detail": {}})
    out = detect_smart_money("002696.SZ", {"sms_dual_source_gate": True})
    assert out["verdict"] == "no_signal"
    assert out["confidence"] == "low"
    assert "双源资金面冲突" in out["summary"]
    assert "dual_source_conflict" in out["_validity"]["flags"]
    assert out["score"] >= 50          # 原始分保留（净流入→高分），但已停发


def test_detect_no_withdrawal_when_gate_agrees(monkeypatch):
    """Gate verdict 一致 → score flows through unchanged."""
    rows = [
        {"net_amount": 1e7, "buy_elg_amount": 1e7, "sell_elg_amount": 0,
         "buy_lg_amount": 0, "sell_lg_amount": 0, "date": f"2026-08-{i:02d}"}
        for i in range(1, 6)
    ]
    monkeypatch.setattr(sms, "_fetch_flow_data", lambda t, c: rows)
    monkeypatch.setattr(sms, "_fetch_price_data", lambda t, c: {
        "closes": [10.0] * 5, "current": 10.0, "today_change": 0.0,
        "data_date": "2026-08-05",
    })
    monkeypatch.setattr(sms, "_score_confirmation",
                        lambda *a, **k: {"score": 60, "detail": "ok", "sources": 1})
    monkeypatch.setattr(sms, "_dual_source_gate",
                        lambda *a, **k: {"verdict": "一致", "note": "一致", "detail": {}})
    out = detect_smart_money("002696.SZ", {"sms_dual_source_gate": True})
    assert out["verdict"] == "confirmed"       # high-inflow data → top tier
    assert "双源资金面冲突" not in out["summary"]
    assert "dual_source_conflict" not in out["_validity"]["flags"]
