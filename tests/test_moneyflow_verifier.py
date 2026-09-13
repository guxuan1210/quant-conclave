"""Tests for verify_moneyflow — cross-validation between EastMoney-derived
逐日 (akshare primary, tushare moneyflow fallback) and 同花顺 (tushare
moneyflow_dc verifier). Both report yuan; window sums render in 万元. A window
below the noise floor (~100万) is skipped, not judged; a direction mismatch or
ratio > max_ratio is a 偏差; either side unavailable → 数据缺失 (never a raise)."""

from __future__ import annotations

import pytest

from capitalradar.sector_scan.moneyflow_verifier import (
    _parse_moneyflow_csv,
    _render_verify_moneyflow,
    verify_moneyflow,
)

DATES = [f"2026-06-{d:02d}" for d in range(1, 21)]  # 20 chronological days


def _akshare_csv(nets, dates=DATES):
    header = "# Money Flow (主力资金流向)\n# Source: 东方财富 via AKShare\n\n"
    cols = "trade_date,net_amount,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount\n"
    body = "".join(f"{d},{n},0,0,0,0\n" for d, n in zip(dates, nets))
    return header + cols + body


def _tushare_csv(nets, dates=DATES, raw=None):
    # Realistic tushare moneyflow (doc_id=25): it carries BOTH the raw
    # ``net_amount`` (= net_mf_amount, 铁律#6: unusable — can flip opposite to
    # 主力, e.g. 001366 2026-09-02 涨停日 raw -7982万 vs 主力 +3628万) AND
    # per-tier nets. 主力 = net_elg_amount + net_lg_amount; the verifier must
    # read 主力, never raw. ``raw`` defaults to ``nets`` so older tests that
    # don't model the divergence keep their meaning.
    header = "# Money Flow (主力资金流向)\n# Source: 东方财富 via tushare\n\n"
    cols = ("ts_code,trade_date,net_amount,net_elg_amount,net_lg_amount,"
            "buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount\n")
    if raw is None:
        raw = nets
    body = "".join(
        f"600030.SH,{d},{g},{n},0,0,0,0,0\n"
        for d, n, g in zip(reversed(dates), reversed(nets), reversed(raw)))
    return header + cols + body


def _ths_csv(nets, dates=DATES):
    # tushare moneyflow_dc (同花顺) returns NEWEST-first too.
    header = "# Money Flow DC (同花顺)\n# Source: 同花顺 via tushare\n\n"
    cols = "trade_date,ts_code,name,net_amount\n"
    body = "".join(f"{d},600030.SH,中信证券,{n}\n" for d, n in zip(reversed(dates), reversed(nets)))
    return header + cols + body


def _monkeypatch_vendors(monkeypatch, akshare_out=None, tushare_out=None, ths_out=None):
    """Patch the three vendor entry points verify_moneyflow may call.

    akshare_out — akshare 东财逐日 (primary, tried first).
    tushare_out — tushare moneyflow, the EastMoney fallback primary.
    ths_out     — tushare moneyflow_dc, the 同花顺 verifier.
    """
    import capitalradar.dataflows.akshare_data as aks
    import capitalradar.dataflows.tushare_data as ts

    def _stub(out):
        if callable(out):
            return out
        return lambda ticker=None, start_date=None, end_date=None: out

    monkeypatch.setattr(aks, "get_money_flow_akshare", _stub(akshare_out))
    monkeypatch.setattr(ts, "get_money_flow", _stub(tushare_out))
    monkeypatch.setattr(ts, "get_money_flow_dc", _stub(ths_out))


# ---- _parse_moneyflow_csv ----

def test_parse_moneyflow_csv_skips_comments_and_sorts():
    csv_text = (
        "# Money Flow (主力资金流向)\n# Source: tushare\n\n"
        "ts_code,trade_date,net_amount\n"
        "600030.SH,2026-06-20,100\n"
        "600030.SH,2026-06-19,90\n"
        "600030.SH,2026-06-18,80\n"
    )
    rows = _parse_moneyflow_csv(csv_text)
    assert [r["date"] for r in rows] == ["2026-06-18", "2026-06-19", "2026-06-20"]
    assert rows[-1]["net_amount"] == 100


def test_parse_moneyflow_csv_empty_or_garbage():
    assert _parse_moneyflow_csv("") == []
    assert _parse_moneyflow_csv("# only comments\n# more\n") == []


def test_parse_prefers_main_force_over_raw_net_amount():
    # tushare moneyflow row: raw net_amount (net_mf_amount) is OPPOSITE to 主力
    # (001366 shape: -7982万 vs +3628万). The parser must read 主力 = elg+lg.
    csv_text = (
        "# header\n\n"
        "ts_code,trade_date,net_amount,net_elg_amount,net_lg_amount\n"
        "600030.SH,2026-09-02,-79820000,36280000,0\n"
    )
    rows = _parse_moneyflow_csv(csv_text)
    assert rows[0]["net_amount"] == pytest.approx(36280000)


def test_parse_uses_net_amount_when_no_per_tier_nets():
    # 同花顺 moneyflow_dc / akshare expose no net_*_amount columns; their
    # net_amount IS already 主力 — read it directly.
    csv_text = (
        "# header\n\n"
        "trade_date,ts_code,name,net_amount\n"
        "2026-09-02,600030.SH,中信证券,36010000\n"
    )
    rows = _parse_moneyflow_csv(csv_text)
    assert rows[0]["net_amount"] == pytest.approx(36010000)


# ---- verify_moneyflow: verdicts ----

def test_consistent_sources_verdict_yizhi(monkeypatch):
    _monkeypatch_vendors(monkeypatch, _akshare_csv([1e8] * 20),
                         ths_out=_ths_csv([1e8] * 20))
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "一致"
    assert out["max_ratio"] == 10.0
    assert out["primary_source"] == "东财逐日(akshare)"
    assert out["verifier_source"].startswith("同花顺")
    expected_wan = {"5日": 50000, "10日": 100000, "20日": 200000}  # 1e8/day × N /1e4
    for w in out["windows"]:
        assert w["ratio"] == pytest.approx(1.0, abs=1e-6)
        assert w["primary_wan"] == expected_wan[w["window"]]
        assert w["verifier_wan"] == expected_wan[w["window"]]


def test_primary_falls_back_to_tushare_when_akshare_down(monkeypatch):
    # akshare unreachable (proxy-blocked on this machine) → tushare moneyflow
    # (still EastMoney) becomes the primary; 同花顺 still the verifier.
    _monkeypatch_vendors(monkeypatch, "# SKIP_VENDOR: AKShare money flow failed: x",
                         tushare_out=_tushare_csv([1e8] * 20),
                         ths_out=_ths_csv([1e8] * 20))
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "一致"
    assert out["primary_source"] == "东财逐日(tushare)"
    assert all(w["ratio"] == pytest.approx(1.0, abs=1e-6) for w in out["windows"])


def test_tushare_primary_uses_main_force_not_raw_net_amount(monkeypatch):
    """001366 shape: the tushare primary's raw net_amount (net_mf_amount) is
    OPPOSITE to both 主力 and 同花顺 (raw -1e8/day vs 主力/同花顺 +1e8/day).
    The verifier must read 东财主力 (net_elg+net_lg), so every window agrees —
    NOT the old behavior that compared the raw 净流入额 and flagged 反向."""
    _monkeypatch_vendors(
        monkeypatch,
        "# SKIP_VENDOR: AKShare money flow failed: x",   # force tushare primary
        tushare_out=_tushare_csv([1e8] * 20, raw=[-1e8] * 20),
        ths_out=_ths_csv([1e8] * 20),
    )
    out = verify_moneyflow("600030.SH", config={})
    assert out["primary_source"] == "东财逐日(tushare)"
    assert out["verdict"] == "一致"
    assert all(w["direction"] == "同向" for w in out["windows"])
    # 1e8 主力(元)/日 ×N /1e4 → 万元
    assert {w["window"]: w["primary_wan"] for w in out["windows"]} == {
        "5日": 50000, "10日": 100000, "20日": 200000}


def test_deviation_beyond_threshold_verdict_piancha(monkeypatch):
    # akshare 1e9/day vs 同花顺 1e6/day → ratio 1000 >> 10 → 偏差
    _monkeypatch_vendors(monkeypatch, _akshare_csv([1e9] * 20),
                         ths_out=_ths_csv([1e6] * 20))
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "偏差"
    assert len(out["windows"]) == 3
    assert all(w["ratio"] == pytest.approx(1000.0) for w in out["windows"])


def test_opposite_direction_verdict_piancha(monkeypatch):
    _monkeypatch_vendors(monkeypatch, _akshare_csv([1e8] * 20),
                         ths_out=_ths_csv([-1e8] * 20))
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "偏差"
    assert all(w["direction"] == "反向" for w in out["windows"])


def test_primary_unavailable_verdict_missing(monkeypatch):
    # akshare AND tushare moneyflow both down → no primary side at all.
    _monkeypatch_vendors(monkeypatch, "# SKIP_VENDOR: AKShare money flow failed: x",
                         tushare_out="# SKIP_VENDOR: tushare money flow failed: x",
                         ths_out=_ths_csv([1e8] * 20))
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "数据缺失"
    assert "主源" in out["note"] and "不可用" in out["note"]


def test_both_unavailable_verdict_missing(monkeypatch):
    def boom(ticker=None, start_date=None, end_date=None):
        raise RuntimeError("network down")
    _monkeypatch_vendors(monkeypatch, boom, boom, boom)
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "数据缺失"
    assert not out["windows"] == []  # windows still populated with None sides


def test_tiny_window_skipped_not_judged(monkeypatch):
    # verifier all-zero → window abs 0 < noise floor → skipped, so no 偏差
    _monkeypatch_vendors(monkeypatch, _akshare_csv([1e8] * 20),
                         ths_out=_ths_csv([0] * 20))
    out = verify_moneyflow("600030.SH", config={})
    assert out["verdict"] == "一致"
    assert all("量级过小" in w["note"] for w in out["windows"])
    assert all(w["ratio"] is None for w in out["windows"])


def test_custom_flow_rows_used_as_primary(monkeypatch):
    # flow_rows provided → no vendor fetch for the primary; 同花顺 is the verifier.
    import capitalradar.dataflows.akshare_data as aks
    import capitalradar.dataflows.tushare_data as ts
    called = {"akshare": False, "tushare": False}
    monkeypatch.setattr(aks, "get_money_flow_akshare",
                        lambda ticker=None, start_date=None, end_date=None: (
                            called.__setitem__("akshare", True) or "# SKIP_VENDOR: x"))
    monkeypatch.setattr(ts, "get_money_flow",
                        lambda ticker=None, start_date=None, end_date=None: (
                            called.__setitem__("tushare", True) or "# SKIP_VENDOR: x"))
    monkeypatch.setattr(ts, "get_money_flow_dc",
                        lambda ticker=None, start_date=None, end_date=None: _ths_csv([1e8] * 20))
    flow_rows = [{"net_amount": 1e8, "date": d} for d in DATES]
    out = verify_moneyflow("600030.SH", flow_rows=flow_rows, config={})
    assert called["akshare"] is False
    assert called["tushare"] is False
    assert out["primary_source"] == "SMS引擎数据"
    assert out["verdict"] == "一致"


# ---- renderer ----

def test_render_verify_moneyflow_markdown(monkeypatch):
    _monkeypatch_vendors(monkeypatch, _akshare_csv([1e8] * 20),
                         ths_out=_ths_csv([1e8] * 20))
    out = verify_moneyflow("600030.SH", config={})
    md = _render_verify_moneyflow(out)
    assert "## 资金流交叉验证 · 600030.SH" in md
    assert "**结论**: 一致" in md
    assert "100,000" in md          # 万元 formatting with thousands separators
    assert "5日" in md and "10日" in md and "20日" in md
