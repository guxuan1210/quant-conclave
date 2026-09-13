"""Tests for the Eastmoney watchlist persistence layer.

Uses a temp results.db via ``config={"results_dir": str(tmp_path)}`` — no
monkeypatching, no network.
"""

from __future__ import annotations

import json

import pytest

from web.watchlist_store import (
    init_watchlist_store,
    merge_watchlist_stocks,
    get_watchlist_stocks,
    get_watchlist_stock,
    log_sync,
    get_sync_history,
    extract_verdict,
    save_watchlist_analysis,
    get_watchlist_analysis_history,
    get_latest_watchlist_analysis,
)


@pytest.fixture()
def config(tmp_path):
    cfg = {"results_dir": str(tmp_path)}
    init_watchlist_store(cfg)
    return cfg


def _stock(code, name="Test", price=10.0, chg=1.0, turnover=2.0, vol_ratio=1.5):
    return {"code": code, "name": name, "price": price,
            "change_pct": chg, "turnover": turnover, "vol_ratio": vol_ratio}


# ── merge_watchlist_stocks ──

def test_merge_inserts_and_updates(config):
    # First sync: 3 new stocks
    stats = merge_watchlist_stocks(config, [
        _stock("600519.SH", "贵州茅台", 1500.0),
        _stock("000001.SZ", "平安银行", 11.0),
        _stock("601318.SH", "中国平安", 50.0),
    ])
    assert stats["added"] == 3 and stats["updated"] == 0
    assert len(get_watchlist_stocks(config)) == 3

    # Second sync: update 2 (price change) + add 1
    stats = merge_watchlist_stocks(config, [
        _stock("600519.SH", "贵州茅台", 1520.0),
        _stock("000001.SZ", "平安银行", 11.5),
        _stock("000002.SZ", "万科A", 8.0),
    ])
    assert stats["added"] == 1 and stats["updated"] == 2
    rows = get_watchlist_stocks(config)
    assert len(rows) == 4  # 万科A added, all previous retained


def test_merge_never_deletes_local(config):
    merge_watchlist_stocks(config, [
        _stock("600519.SH"), _stock("000001.SZ"), _stock("601318.SH"), _stock("000002.SZ"),
    ])
    # Next sync only returns 1 stock — the other 3 must remain locally
    merge_watchlist_stocks(config, [_stock("600519.SH", price=1501.0)])
    rows = get_watchlist_stocks(config)
    assert len(rows) == 4
    codes = {r["code"] for r in rows}
    assert codes == {"600519.SH", "000001.SZ", "601318.SH", "000002.SZ"}


def test_merge_price_updated_and_nullable_fields(config):
    merge_watchlist_stocks(config, [_stock("600519.SH", price=1500.0, chg=1.2, turnover=2.0, vol_ratio=1.5)])
    # Update with different price + None for turnover/vol_ratio
    merge_watchlist_stocks(config, [{"code": "600519.SH", "name": "贵州茅台",
                                     "price": "1,520", "change_pct": "2.5",
                                     "turnover": None, "vol_ratio": ""}])
    row = get_watchlist_stock(config, "600519.SH")
    assert row["price"] == 1520.0       # comma stripped, updated
    assert row["change_pct"] == 2.5
    assert row["turnover"] is None      # NULL stored for empty turnover
    assert row["vol_ratio"] is None


# ── sync log ──

def test_sync_log_and_history(config):
    log_sync(config, total=3, added=2, updated=1, removed=0, source="mxapi")
    log_sync(config, total=4, added=1, updated=3, removed=0, source="mxapi")
    log_sync(config, total=4, added=0, updated=4, removed=0, source="mxapi")
    history = get_sync_history(config)
    assert len(history) == 3
    # Newest first
    assert history[0]["total"] == 4 and history[0]["added"] == 0
    assert history[2]["total"] == 3


# ── analysis history ──

def _analysis_result(code="600519.SH"):
    return {
        "code": code,
        "rt_price": "1520.00", "rt_change": "10.00", "rt_change_pct": "0.66",
        "rt_high": "1530.00", "rt_low": "1510.00", "rt_volume": "12345", "rt_amount": "1888",
        "flow_detail": [{"date": "2026-07-31", "net": 1000.0, "buy_elg": 2000.0,
                         "sell_elg": 1000.0, "buy_lg": 500.0, "sell_lg": 500.0,
                         "buy_md": 100.0, "sell_md": 100.0, "buy_sm": 50.0, "sell_sm": 50.0}],
        "analysis": "结论：看多\n主力超大单持续净流入，吸筹特征明显。",
        "intraday": [{"t": "09:35", "o": 1520.0, "c": 1521.0, "h": 1522.0, "l": 1519.0, "v": 100}],
        "flow_data": "date,net\n2026-07-31,1000",
    }


def test_save_analysis_full_history(config):
    a1 = save_watchlist_analysis(config, _analysis_result())
    a2 = save_watchlist_analysis(config, _analysis_result())
    a3 = save_watchlist_analysis(config, _analysis_result())
    assert a1 < a2 < a3  # increasing ids

    history = get_watchlist_analysis_history(config, "600519.SH")
    assert len(history) == 3
    assert history[0]["analysis_id"] == a3  # newest first

    latest = get_latest_watchlist_analysis(config, "600519.SH")
    assert latest["analysis_id"] == a3
    # JSON fields rehydrated
    assert isinstance(latest["flow_detail"], list) and len(latest["flow_detail"]) == 1
    assert isinstance(latest["intraday"], list) and len(latest["intraday"]) == 1
    assert latest["analysis"] == "结论：看多\n主力超大单持续净流入，吸筹特征明显。"


def test_analysis_recorded_time_and_verdict(config):
    save_watchlist_analysis(config, _analysis_result())
    history = get_watchlist_analysis_history(config, "600519.SH")
    assert history[0]["analyzed_at"]  # non-empty timestamp
    assert history[0]["verdict"] == "看多"


def test_extract_verdict_variants():
    assert extract_verdict("结论：看多，主力吸筹") == "看多"
    assert extract_verdict("看好买入") == "看多"
    assert extract_verdict("结论：看空，资金流出") == "看空"
    assert extract_verdict("建议卖出") == "看空"
    assert extract_verdict("结论：观望") == "观望"
    assert extract_verdict("持有等待") == "观望"
    assert extract_verdict("中性偏谨慎") == "观望"
    assert extract_verdict("") == "观望"
    assert extract_verdict("结论：观望（LLM失败）") == "观望"


def test_empty_history_returns_none(config):
    assert get_watchlist_analysis_history(config, "NOPE.SH") == []
    assert get_latest_watchlist_analysis(config, "NOPE.SH") is None


# ── return validation ──

def test_compute_analysis_return_positive(monkeypatch):
    """Buy at analysis-day close, sell at current price → positive return."""
    import web.watchlist_store as ws
    monkeypatch.setattr(ws, "_fetch_close_on_date", lambda code, d: 10.0)
    monkeypatch.setattr(ws, "_fetch_current_price", lambda code: 11.5)
    ret = ws.compute_analysis_return({}, "TEST.SH", "2026-07-01T10:00:00")
    assert ret is not None
    assert ret["buy_price"] == 10.0
    assert ret["sell_price"] == 11.5
    assert ret["return_pct"] == 15.0  # (11.5-10)/10*100


def test_compute_analysis_return_negative(monkeypatch):
    import web.watchlist_store as ws
    monkeypatch.setattr(ws, "_fetch_close_on_date", lambda code, d: 10.0)
    monkeypatch.setattr(ws, "_fetch_current_price", lambda code: 8.0)
    ret = ws.compute_analysis_return({}, "TEST.SH", "2026-07-01T10:00:00")
    assert ret["return_pct"] == -20.0


def test_compute_analysis_return_none_on_missing_price(monkeypatch):
    import web.watchlist_store as ws
    monkeypatch.setattr(ws, "_fetch_close_on_date", lambda code, d: None)
    monkeypatch.setattr(ws, "_fetch_current_price", lambda code: 10.0)
    assert ws.compute_analysis_return({}, "TEST.SH", "2026-07-01") is None


def test_fetch_close_on_date_uses_vendor_chain(monkeypatch):
    """_fetch_close_on_date should pick the close on/before the analysis date."""
    import pandas as pd
    fake_df = pd.DataFrame({
        "date": pd.to_datetime(["2026-07-29", "2026-07-30", "2026-07-31"]),
        "close": [11.28, 11.61, 11.63],
    })
    # The function imports parse_ohlcv_csv / route_to_vendor lazily inside itself,
    # so patch at their defining modules.
    monkeypatch.setattr("capitalradar.backtest.data.parse_ohlcv_csv", lambda raw: fake_df)
    monkeypatch.setattr("capitalradar.dataflows.interface.route_to_vendor", lambda *a, **k: "fake-csv")

    from web.watchlist_store import _fetch_close_on_date
    # On a trading day → that day's close
    assert _fetch_close_on_date("000001.SZ", "2026-07-31") == 11.63
    # On a weekend → nearest prior trading day's close
    assert _fetch_close_on_date("000001.SZ", "2026-08-02") == 11.63
    # Before all data → earliest available close
    assert _fetch_close_on_date("000001.SZ", "2026-07-01") == 11.28


# ── extract_verdict: conclusion-line only, no negated-phrase false positives ──

def test_extract_verdict_conclusion_line_negated_body():
    """Body text mentioning 看多 inside a negation must not flip the verdict."""
    import web.watchlist_store as ws
    txt = (
        "结论：观望（数据日期范围：2026-08-06 ~ 2026-08-21）\n"
        "……不可将全口径数字等同于主力看多，且从看空语境中修正……"
    )
    assert ws.extract_verdict(txt) == "观望"


def test_extract_verdict_markdown_bold_conclusion():
    import web.watchlist_store as ws
    txt = "**结论：看多** 数据日期范围：2026-08-06 ~ 2026-08-21（12个交易日）\n正文含观望……"
    assert ws.extract_verdict(txt) == "看多"


def test_extract_verdict_legacy_body_fallback():
    """Analyses that don't open with 结论： still use the body scan (看多 first)."""
    import web.watchlist_store as ws
    assert ws.extract_verdict("主力看多，建议持有") == "看多"
    assert ws.extract_verdict("主力看空，注意风险") == "看空"
    assert ws.extract_verdict("观望为主") == "观望"
    assert ws.extract_verdict("") == "观望"


# ── setup_type (path-classification framework) ──

def test_save_watchlist_analysis_setup_type(config):
    """setup_type from the path framework persists to DB and rehydrates."""
    r = _analysis_result()
    r["verdict"] = "看多"
    r["setup_type"] = "放量突破新高"
    save_watchlist_analysis(config, r)
    latest = get_latest_watchlist_analysis(config, "600519.SH")
    assert latest["verdict"] == "看多"
    assert latest["setup_type"] == "放量突破新高"


def test_save_watchlist_analysis_setup_type_defaults_to_neutral(config):
    """Missing setup_type falls back to the 观望-无明确路径 residual."""
    save_watchlist_analysis(config, _analysis_result())
    latest = get_latest_watchlist_analysis(config, "600519.SH")
    assert latest["setup_type"] == "观望-无明确路径"


def test_render_watchlist_analysis_setup_type():
    """render_watchlist_analysis emits the 命中路径 line under the verdict line."""
    from capitalradar.agents.schemas import (
        WatchlistAnalysis, WatchlistSetupType, render_watchlist_analysis,
    )
    wa = WatchlistAnalysis(
        verdict="看多",
        setup_type=WatchlistSetupType.B_BREAKOUT,
        main_force_behavior="超大单连续净流入，机构吸筹明显。",
        volume_price_analysis="放量上涨，量比1.8，换手2.5%，量价健康。",
        divergence_check="未见背离，价涨钱进一致。",
        conclusion_detail="核心依据：12日主力+9309万，近3日递增，建议看多。",
    )
    rendered = render_watchlist_analysis(wa)
    lines = rendered.split("\n")
    assert lines[0].startswith("结论：看多")
    assert lines[1] == "命中路径：放量突破新高"
    assert "主力同向" not in rendered  # B_BREAKOUT not C_SAME_DIR_INFLOW


def test_render_watchlist_analysis_neutral_residual():
    """观望 verdict pairs with the 观望-无明确路径 residual setup_type."""
    from capitalradar.agents.schemas import (
        WatchlistAnalysis, WatchlistSetupType, render_watchlist_analysis,
    )
    wa = WatchlistAnalysis(
        verdict="观望",
        setup_type=WatchlistSetupType.NO_SETUP,
        main_force_behavior="主力净流出为主。",
        volume_price_analysis="缩量整理。",
        divergence_check="无显著背离。",
        conclusion_detail="六条路径均未命中，观望。",
    )
    rendered = render_watchlist_analysis(wa)
    assert rendered.split("\n")[1] == "命中路径：观望-无明确路径"
