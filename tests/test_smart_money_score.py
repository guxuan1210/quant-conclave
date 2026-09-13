"""Unit tests for the Smart Money Score price-口径 fixes.

The advisor's "价格-0.6%" bug: ``_score_alignment`` computed a 5-day price
change but labelled it just "价格" with no period or date, so the LLM quoted
it as today's move. These tests pin the corrected behaviour — every price
label carries an explicit window (5日 / 今日), ``_fetch_price_data`` returns
the single-day change + data date, and the breakdown carries ``_meta`` for
tool renderers.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from quantconclave.sector_scan.smart_money_score import (
    _fetch_flow_data,
    _fetch_price_data,
    _fetch_realtime_flow_signal,
    _price_meta_from,
    _realtime_limited_result,
    _score_alignment,
    compute_smart_money_score,
    detect_smart_money,
    format_sms_unavailable,
)


# ---- _score_alignment: explicit period labels ---------------------------------


def test_score_alignment_detail_labels_5d_and_today():
    rows = [{"net_amount": 1e6} for _ in range(5)]  # 5-day net inflow
    price_info = {
        "closes": [10.0, 10.2, 10.4, 10.6, 10.8],
        "current": 10.8,               # == latest close → 5d change = +8.0%
        "today_change": 0.0228,        # real single-day move: +2.28%
        "data_date": "2026-08-26",
    }
    out = _score_alignment(rows, price_info)
    assert out["score"] >= 90                       # price up + flow in
    assert "5日价格+8.0%" in out["detail"]          # the multi-day window is explicit
    assert "今日+2.28%" in out["detail"]            # today's move is separate


def test_score_alignment_today_na_when_missing():
    rows = [{"net_amount": -1e6} for _ in range(5)]
    price_info = {"closes": [10.0] * 5, "current": 10.0, "today_change": None}
    out = _score_alignment(rows, price_info)
    assert "今日N/A" in out["detail"]
    assert "5日价格+0.0%" in out["detail"]


def test_score_alignment_insufficient_price_data_defaults():
    out = _score_alignment([{"net_amount": 1e6}], {"closes": [10.0]})
    assert out["score"] == 50
    assert "价格数据不足" in out["detail"]


# ---- _fetch_price_data: sort order, today_change, data_date -------------------


class _FakeInvoke:
    def __init__(self, text: str):
        self._text = text

    def invoke(self, *args, **kwargs):
        return self._text


# tushare get_stock_data returns rows NEWEST-first (trade_date DESC).
_CSV_STALE = (
    "trade_date,close\n"
    "2026-08-25,23.80\n"   # last daily bar — NOT today → realtime fallback
    "2026-08-24,23.50\n"
    "2026-08-21,23.20\n"
    "2026-08-20,23.00\n"
    "2026-08-19,22.80\n"
)


def test_fetch_price_data_stale_daily_uses_realtime_change(monkeypatch):
    """Stale daily bar + Tencent-style realtime quote → today_change is the
    realtime-vs-last-close move and data_date is today."""
    monkeypatch.setattr(
        "quantconclave.agents.utils.core_stock_tools.get_stock_data",
        _FakeInvoke(_CSV_STALE))
    monkeypatch.setattr(
        "quantconclave.dataflows.interface.route_to_vendor",
        lambda method, symbol="": "**Current Price**: 24.35\n")

    out = _fetch_price_data("600030.SH", {})
    assert out["current"] == 24.35
    assert out["data_date"] == datetime.now().strftime("%Y-%m-%d")
    # reversed to chronological → closes[-1] is 2026-08-25 close 23.80
    assert out["today_change"] == pytest.approx((24.35 - 23.80) / 23.80)
    # latest bar is the most recent one after the reversal
    assert out["closes"][-1] == 23.80


def test_fetch_price_data_daily_already_today_falls_back_when_realtime_gone(monkeypatch):
    """When the daily bar already covers today AND realtime is unreachable
    (SKIP_VENDOR), today_change comes from the last two daily closes.
    Behavior change vs the original: the realtime quote is now ALWAYS
    attempted — the stale-day skip no longer exists."""
    today = datetime.now().strftime("%Y-%m-%d")
    csv_text = (
        "trade_date,close\n"
        f"{today},24.35\n"
        "2026-08-24,23.80\n"
        "2026-08-21,23.20\n"
        "2026-08-20,23.00\n"
        "2026-08-19,22.80\n"
    )
    monkeypatch.setattr(
        "quantconclave.agents.utils.core_stock_tools.get_stock_data",
        _FakeInvoke(csv_text))
    calls = []
    monkeypatch.setattr(
        "quantconclave.dataflows.interface.route_to_vendor",
        lambda method, symbol="": calls.append(method) or "# SKIP_VENDOR: unreachable")

    out = _fetch_price_data("600030.SH", {})
    assert len(calls) == 1                 # route_to_vendor now always consulted
    assert out["current"] is None
    assert out["data_date"] == today
    assert out["today_change"] == pytest.approx((24.35 - 23.80) / 23.80)


def test_fetch_price_data_short_history_returns_none_change(monkeypatch):
    monkeypatch.setattr(
        "quantconclave.agents.utils.core_stock_tools.get_stock_data",
        _FakeInvoke("trade_date,close\n2026-08-25,23.80\n2026-08-24,23.50\n"))
    out = _fetch_price_data("600030.SH", {})
    assert out["today_change"] is None
    assert out["current"] is None


# ---- _price_meta_from -----------------------------------------------------------


def test_price_meta_from_prefers_realtime():
    meta = _price_meta_from({"closes": [10.0, 10.5], "current": 11.0,
                             "today_change": 0.02, "data_date": "2026-08-26"})
    assert meta["price"] == 11.0
    assert meta["today_change_pct"] == 2.0
    assert meta["data_date"] == "2026-08-26"


def test_price_meta_from_falls_back_to_last_close():
    meta = _price_meta_from({"closes": [10.0, 10.5], "current": None,
                             "today_change": None, "data_date": "2026-08-25"})
    assert meta["price"] == 10.5
    assert meta["today_change_pct"] is None
    assert meta["data_date"] == "2026-08-25"


def test_price_meta_from_none_is_empty():
    assert _price_meta_from(None) == {}


# ---- compute_smart_money_score: breakdown carries _meta ------------------------


def test_compute_breakdown_carries_price_meta(monkeypatch):
    import quantconclave.sector_scan.smart_money_score as sms
    monkeypatch.setattr(sms, "detect_smart_money", lambda t, c: {
        "verdict": "confirmed",
        "score": 80,
        "summary": "主力确认",
        "dimensions": {"scale": {"score": 80, "detail": "规模大"}},
        "_price_meta": {"price": 24.35, "today_change_pct": 2.28,
                        "data_date": "2026-08-26"},
    })
    total, breakdown = compute_smart_money_score("600030.SH", {})
    assert total == 80
    assert breakdown["_meta"]["price"] == 24.35
    assert breakdown["_meta"]["today_change_pct"] == 2.28


def test_compute_breakdown_meta_missing_when_not_set(monkeypatch):
    import quantconclave.sector_scan.smart_money_score as sms
    monkeypatch.setattr(sms, "detect_smart_money", lambda t, c: {
        "verdict": "no_signal", "score": 20, "summary": "无信号",
        "dimensions": {},
    })
    _, breakdown = compute_smart_money_score("600030.SH", {})
    assert breakdown["_meta"] == {}


# ---- ① 单位换算: MX 万元 → 元 归一 + 展示万位 ----

def test_realtime_limited_result_displays_wan_not_shrunk():
    """6.5亿元 in yuan (after the ×1e4 MX normalization) must render as
    +65000万元 — never +6万元 (the old 1万倍 shrink)."""
    row = {
        "net_amount": 6.5e8,        # 6.5亿元 in yuan
        "buy_elg_amount": 4.2e8,
        "sell_elg_amount": 0,
        "buy_lg_amount": 2.3e8,
        "sell_lg_amount": 0,
    }
    out = _realtime_limited_result("600030.SH", [row])
    assert out["score"] > 0
    assert "+65000万元" in out["summary"]
    assert "+6万元" not in out["summary"]


def test_fetch_realtime_flow_signal_mx_wan_to_yuan(monkeypatch):
    """MX 主力资金 metrics arrive in 万元 ("主力净流入": 65000.0); the MX branch
    must ×1e4 → 6.5e8 yuan so the yuan convention holds everywhere."""
    import quantconclave.dataflows.mx_client as mx_client
    monkeypatch.setattr(mx_client, "query", lambda *a, **k: "text")
    monkeypatch.setattr(mx_client, "extract_snapshot_metrics", lambda d: {
        "超大单净流入": 42000.0,
        "大单净流入": 23000.0,
        "主力净流入": 65000.0,
    })
    rows = _fetch_realtime_flow_signal("600030.SH")
    assert rows and len(rows) == 1
    assert rows[0]["net_amount"] == pytest.approx(6.5e8)
    assert rows[0]["buy_elg_amount"] == pytest.approx(4.2e8)
    assert rows[0]["buy_lg_amount"] == pytest.approx(2.3e8)


# ---- ② 空数据禁止出分 ----

def test_realtime_limited_result_all_zero_is_unavailable():
    """An all-zero realtime snapshot is DATA UNAVAILABLE — must not emit a
    25/45 directional score."""
    row = {"net_amount": 0, "buy_elg_amount": 0, "sell_elg_amount": 0,
           "buy_lg_amount": 0, "sell_lg_amount": 0}
    out = _realtime_limited_result("600030.SH", [row])
    assert out["_data_unavailable"] is True
    assert out["score"] == 0
    assert out["verdict"] == "no_signal"


def test_detect_all_zero_windows_unavailable(monkeypatch):
    import quantconclave.sector_scan.smart_money_score as sms
    zero_rows = [
        {"net_amount": 0, "buy_elg_amount": 0, "sell_elg_amount": 0,
         "buy_lg_amount": 0, "sell_lg_amount": 0, "date": f"2026-08-{i:02d}"}
        for i in range(1, 21)
    ]
    monkeypatch.setattr(sms, "_fetch_flow_data", lambda t, c: zero_rows)
    out = detect_smart_money("600030.SH", {})
    assert out["_data_unavailable"] is True
    assert out["score"] == 0
    assert out["verdict"] == "no_signal"


def test_compute_breakdown_passthrough_data_unavailable(monkeypatch):
    import quantconclave.sector_scan.smart_money_score as sms
    monkeypatch.setattr(sms, "detect_smart_money", lambda t, c: {
        "verdict": "no_signal", "score": 0, "summary": "数据缺失",
        "dimensions": {}, "_data_unavailable": True,
        "_validity": {"ok": False, "flags": ["flow_too_small"], "note": "x"},
    })
    _, breakdown = compute_smart_money_score("600030.SH", {})
    assert breakdown["_data_unavailable"] is True
    assert breakdown["_validity"]["flags"] == ["flow_too_small"]


def test_format_sms_unavailable_mentions_verify():
    out = format_sms_unavailable("600030.SH", "测试")
    assert "数据不可用" in out
    assert "不是看空信号" in out
    assert "verify_moneyflow" in out


# ---- ③ 实时涨跌幅优先 + 日线回落 ----

def test_fetch_price_data_prefers_realtime_change_pct(monkeypatch):
    """Tencent quote carries its own authoritative 涨跌幅; today_change must
    come from that (2.28% → 0.0228), not recomputed from the price diff."""
    monkeypatch.setattr(
        "quantconclave.agents.utils.core_stock_tools.get_stock_data",
        _FakeInvoke(_CSV_STALE))
    monkeypatch.setattr(
        "quantconclave.dataflows.interface.route_to_vendor",
        lambda method, symbol="": "**Current Price**: 24.35\n**Change**: +0.24 / 2.28%\n")
    out = _fetch_price_data("600030.SH", {})
    assert out["current"] == 24.35
    assert out["today_change"] == pytest.approx(0.0228)
    assert out["data_date"] == datetime.now().strftime("%Y-%m-%d")


def test_fetch_price_data_daily_today_falls_back_on_skip_vendor(monkeypatch):
    """The daily bar already covers today AND realtime is unreachable
    (SKIP_VENDOR) → today_change falls back to the last two daily closes.
    Behavior change vs before: route_to_vendor IS now always consulted."""
    today = datetime.now().strftime("%Y-%m-%d")
    csv_text = (
        "trade_date,close\n"
        f"{today},24.35\n"
        "2026-08-24,23.80\n"
        "2026-08-21,23.20\n"
        "2026-08-20,23.00\n"
        "2026-08-19,22.80\n"
    )
    monkeypatch.setattr(
        "quantconclave.agents.utils.core_stock_tools.get_stock_data",
        _FakeInvoke(csv_text))
    calls = []
    monkeypatch.setattr(
        "quantconclave.dataflows.interface.route_to_vendor",
        lambda method, symbol="": calls.append(method) or "# SKIP_VENDOR: unreachable")
    out = _fetch_price_data("600030.SH", {})
    assert len(calls) == 1              # realtime now always attempted
    assert out["current"] is None
    assert out["data_date"] == today
    assert out["today_change"] == pytest.approx((24.35 - 23.80) / 23.80)


# ---- 窗口排序: newest-first → chronological ----

def test_fetch_flow_data_sorts_newest_first_to_chronological(monkeypatch):
    """tushare emits newest-first (trade_date DESC); _fetch_flow_data must
    normalize to chronological so rows[-5:] = the MOST RECENT 5 days."""
    import quantconclave.agents.utils.capital_flow_tools as cft
    newest_first = (
        "# Money Flow (主力资金流向) for 600030.SH\n"
        "# Source: 东方财富 via tushare\n\n"
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount\n"
        + "".join(
            # 主力净额 = buy_elg - sell_elg + buy_lg - sell_lg = net_amount（列内不一致时以四档列为准）
            f"600030.SH,2026-08-{d:02d},{100 + d*10},{100 + d*10},0,0,0\n"
            for d in range(20, 0, -1)  # 08-20 .. 08-01 (DESC)
        )
    )

    class _Invoke:
        def invoke(self, *args, **kwargs):
            return newest_first

    monkeypatch.setattr(cft, "get_money_flow", _Invoke())
    rows = _fetch_flow_data("600030.SH", {})
    assert len(rows) == 20
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert rows[-1]["date"] == "2026-08-20"      # most recent at the end
    assert rows[-5:][0]["date"] == "2026-08-16"  # most-recent-5 window
    # Fix 1 regression: parsed net_amount is 主力净额 from the four bucket columns,
    # and the raw vendor field is preserved as _gross_net for cross-vendor checks.
    for r in rows:
        assert r["net_amount"] == r["buy_elg_amount"] - r["sell_elg_amount"] + r["buy_lg_amount"] - r["sell_lg_amount"]
        assert r["_gross_net"] == float(r["date"][8:10]) * 10 + 100


# ---- F _validity 标注 ----

def test_detect_validity_flow_too_small_flagged(monkeypatch):
    import quantconclave.sector_scan.smart_money_score as sms
    rows = [
        {"net_amount": 1e6, "buy_elg_amount": 6e5, "sell_elg_amount": 0,
         "buy_lg_amount": 4e5, "sell_lg_amount": 0, "date": f"2026-08-{i:02d}"}
        for i in range(1, 6)
    ]  # net_5d = 500万元 < 1000万 floor → flow_too_small (annotate, not suppress)
    monkeypatch.setattr(sms, "_fetch_flow_data", lambda t, c: rows)
    monkeypatch.setattr(sms, "_fetch_price_data", lambda t, c: {
        "closes": [10.0] * 5, "current": 10.0, "today_change": 0.0,
        "data_date": "2026-08-05",
    })
    monkeypatch.setattr(sms, "_score_confirmation",
                        lambda *a, **k: {"score": 60, "detail": "ok", "sources": 1})
    out = detect_smart_money("600030.SH", {})
    assert out["_validity"]["ok"] is False
    assert "flow_too_small" in out["_validity"]["flags"]
    assert out["score"] > 0   # annotated, not suppressed


def test_detect_validity_ok_when_flow_large(monkeypatch):
    import quantconclave.sector_scan.smart_money_score as sms
    rows = [
        {"net_amount": 5e7, "buy_elg_amount": 3e7, "sell_elg_amount": 0,
         "buy_lg_amount": 2e7, "sell_lg_amount": 0, "date": f"2026-08-{i:02d}"}
        for i in range(1, 6)
    ]  # net_5d = 2.5亿元 >> 1000万 floor
    monkeypatch.setattr(sms, "_fetch_flow_data", lambda t, c: rows)
    monkeypatch.setattr(sms, "_fetch_price_data", lambda t, c: {
        "closes": [10.0] * 5, "current": 10.0, "today_change": 0.0,
        "data_date": "2026-08-05",
    })
    monkeypatch.setattr(sms, "_score_confirmation",
                        lambda *a, **k: {"score": 60, "detail": "ok", "sources": 1})
    out = detect_smart_money("600030.SH", {})
    assert out["_validity"]["ok"] is True
    assert out["_validity"]["flags"] == []
