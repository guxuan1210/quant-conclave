"""Tests for the watchlist close-price cache + moneyflow miss-detection fixes.

Both fixes target the same two-pass watchlist slowness:

1. ``attach_analysis_to_stocks`` used to do a per-stock OHLCV vendor fetch
   (N × ~0.10s serial). It now serves close-on-analysis-day from a persistent
   ``watchlist_close_cache`` table, batch-populated from tushare full-market
   snapshots (one ``pro.daily(trade_date=)`` call per trading day).
2. ``fetch_or_cache_moneyflow`` refetched a 90-day window whenever the cache's
   latest date < today — always true on weekends/holidays and before the day's
   data is published. It now compares against the last *open trading day* and
   memoizes empty same-day fetches.

No network in these tests — vendor / calendar / tushare calls are stubbed.
"""

from __future__ import annotations

import pytest

from web import trade_cal
from web import watchlist_store as ws
from web.moneyflow_cache import (
    fetch_or_cache_moneyflow,
    init_moneyflow_cache,
)
from web.results_store import _get_conn, init_db
from capitalradar.backtest import data as bdata
from capitalradar.dataflows import interface
from capitalradar.agents.utils import capital_flow_tools as cft


@pytest.fixture()
def config(tmp_path):
    """Temp results.db with all watchlist + moneyflow tables."""
    cfg = {"results_dir": str(tmp_path)}
    init_db(cfg)
    ws.init_watchlist_store(cfg)
    init_moneyflow_cache(cfg)
    return cfg


@pytest.fixture(autouse=True)
def _no_calendar_network(monkeypatch):
    """Never hit tushare for the trading calendar during these tests."""
    # Map analyzed_at timestamps to fixed trading days.
    def _fixed(date_str: str) -> str | None:
        if date_str in ("2026-08-20T10:00:00", "2026-08-20", "2026-08-20 10:00:00"):
            return "20260820"
        if date_str in ("2026-08-21T10:00:00", "2026-08-21"):
            return "20260821"
        return "20260820"

    monkeypatch.setattr(trade_cal, "last_open_day", _fixed)


@pytest.fixture(autouse=True)
def _clear_moneyflow_memo():
    """The same-day memo is module-global — keep it clean across tests."""
    from web import moneyflow_cache as mfc
    mfc._last_fetch_attempt.clear()
    yield
    mfc._last_fetch_attempt.clear()


# ── watchlist_close_cache table + bulk CRUD ──

def test_init_creates_close_cache_table(config):
    conn = _get_conn(config)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name='watchlist_close_cache'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_close_cache_roundtrip(config):
    n = ws._write_close_cache(config, {
        ("000001.SZ", "20260820"): 10.5,
        ("600519.SH", "20260820"): 1500.0,
    })
    assert n == 2
    out = ws._read_close_cache(config, [
        ("000001.SZ", "20260820"),
        ("600519.SH", "20260820"),
        ("000002.SZ", "20260820"),   # not written → absent
    ])
    assert out == {
        ("000001.SZ", "20260820"): 10.5,
        ("600519.SH", "20260820"): 1500.0,
    }


def test_close_cache_upsert(config):
    ws._write_close_cache(config, {("000001.SZ", "20260820"): 10.5})
    ws._write_close_cache(config, {("000001.SZ", "20260820"): 11.2})
    out = ws._read_close_cache(config, [("000001.SZ", "20260820")])
    assert out[("000001.SZ", "20260820")] == 11.2


# ── prefetch_close_cache (batch full-market snapshots) ──

def test_prefetch_groups_by_trading_day_with_suspension_carry(config, monkeypatch):
    """Two trading days; 000001 suspended on the 2nd → gets the 1st's close."""
    monkeypatch.setattr(ws, "_snapshot_closes_by_day", lambda days: {
        "20260820": {"000001.SZ": 10.5, "600519.SH": 1500.0},
        "20260821": {"600519.SH": 1510.0},
    })
    pairs = [
        ("000001.SZ", "2026-08-20T10:00:00"),
        ("600519.SH", "2026-08-20T10:00:00"),
        ("000001.SZ", "2026-08-21T10:00:00"),   # suspended on the 21st
        ("600519.SH", "2026-08-21T10:00:00"),
    ]
    n = ws.prefetch_close_cache(config, pairs)
    assert n == 4
    cache = ws._read_close_cache(config, [
        ("000001.SZ", "20260820"), ("600519.SH", "20260820"),
        ("000001.SZ", "20260821"), ("600519.SH", "20260821"),
    ])
    assert cache[("000001.SZ", "20260820")] == 10.5
    assert cache[("600519.SH", "20260820")] == 1500.0
    assert cache[("000001.SZ", "20260821")] == 10.5   # carried forward
    assert cache[("600519.SH", "20260821")] == 1510.0


def test_prefetch_normalizes_watchlist_code(config, monkeypatch):
    """A bare 6-digit code must match tushare's '000001.SZ' ts_code."""
    monkeypatch.setattr(ws, "_snapshot_closes_by_day", lambda days: {
        "20260820": {"000001.SZ": 10.5},
    })
    n = ws.prefetch_close_cache(config, [("000001", "2026-08-20T10:00:00")])
    assert n == 1
    out = ws._read_close_cache(config, [("000001", "20260820")])
    assert out[("000001", "20260820")] == 10.5


def test_prefetch_no_op_without_snapshot(config, monkeypatch):
    monkeypatch.setattr(ws, "_snapshot_closes_by_day", lambda days: {})
    n = ws.prefetch_close_cache(config, [("000001.SZ", "2026-08-20T10:00:00")])
    assert n == 0


# ── attach_analysis_to_stocks serves from the DB cache ──

def _add_stock(config, code, price):
    ws.merge_watchlist_stocks(config, [{
        "code": code, "name": code, "price": price,
        "change_pct": 1.0, "turnover": None, "vol_ratio": None,
    }])


def _add_analysis(config, code, analyzed_at, verdict="看多"):
    conn = _get_conn(config)
    conn.execute(
        "INSERT INTO watchlist_analysis"
        " (code, analyzed_at, analysis_text, verdict) VALUES (?,?,?,?)",
        (code, analyzed_at, "分析" + verdict, verdict),
    )
    conn.commit()
    conn.close()


def test_attach_uses_cache_without_vendor_calls(config, monkeypatch):
    _add_stock(config, "000001.SZ", price=11.0)
    _add_analysis(config, "000001.SZ", "2026-08-20T10:00:00", verdict="看多")
    ws._write_close_cache(config, {("000001.SZ", "20260820"): 10.0})

    def _boom(*args, **kwargs):
        raise AssertionError("vendor must not be hit when cache is warm")

    monkeypatch.setattr(interface, "route_to_vendor", _boom)

    stocks = [{"code": "000001.SZ", "price": 11.0}]
    ws.attach_analysis_to_stocks(config, stocks)

    s = stocks[0]
    assert s["verdict"] == "看多"
    assert s["buy_price"] == 10.0
    assert s["return_pct"] == 10.0   # (11 - 10) / 10 * 100


def test_attach_batch_prefetch_populates_cache(config, monkeypatch):
    """Cold cache → one full-market snapshot covers the stock, no vendor chain."""
    _add_stock(config, "000001.SZ", price=11.0)
    _add_analysis(config, "000001.SZ", "2026-08-20T10:00:00", verdict="看多")
    monkeypatch.setattr(ws, "_snapshot_closes_by_day", lambda days: {
        "20260820": {"000001.SZ": 10.0},
    })

    def _boom(*args, **kwargs):
        raise AssertionError("vendor must not be hit when snapshot fills cache")

    monkeypatch.setattr(interface, "route_to_vendor", _boom)

    stocks = [{"code": "000001.SZ", "price": 11.0}]
    ws.attach_analysis_to_stocks(config, stocks)

    assert stocks[0]["buy_price"] == 10.0
    assert stocks[0]["return_pct"] == 10.0
    # Snapshot close is persisted so the next run is a pure DB hit.
    out = ws._read_close_cache(config, [("000001.SZ", "20260820")])
    assert out[("000001.SZ", "20260820")] == 10.0


def test_attach_fallback_writes_back_to_cache(config, monkeypatch):
    """Cold cache → per-code fetch → close persisted for next run."""
    _add_stock(config, "000001.SZ", price=11.0)
    _add_analysis(config, "000001.SZ", "2026-08-20T10:00:00", verdict="看多")

    import pandas as pd

    def _fake_parse(raw: str) -> pd.DataFrame:
        return pd.DataFrame({
            "date": pd.to_datetime(["2026-08-19", "2026-08-20", "2026-08-21"]),
            "close": [9.8, 10.0, 10.2],
        })

    monkeypatch.setattr(interface, "route_to_vendor", lambda *a, **k: "ohlcv csv")
    monkeypatch.setattr(bdata, "parse_ohlcv_csv", _fake_parse)
    # Cold cache would trigger the batch snapshot path — stub it so the test
    # exercises the per-code fallback instead of hitting tushare.
    monkeypatch.setattr(ws, "_snapshot_closes_by_day", lambda days: {})

    stocks = [{"code": "000001.SZ", "price": 11.0}]
    ws.attach_analysis_to_stocks(config, stocks)
    assert stocks[0]["return_pct"] == 10.0

    # Fallback close must now be in the persistent cache.
    out = ws._read_close_cache(config, [("000001.SZ", "20260820")])
    assert out[("000001.SZ", "20260820")] == 10.0


# ── moneyflow need_fetch: last trading day instead of "today" ──

def _moneyflow_csv() -> str:
    return (
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount,buy_md_amount,sell_md_amount,"
        "buy_sm_amount,sell_sm_amount\n"
        "000001.SZ,20260820,1000,2000,1000,3000,2000,4000,3000,5000,4000\n"
    )


def _seed_moneyflow(config, code="000001.SZ", trade_date="20260820"):
    conn = _get_conn(config)
    conn.execute(
        "INSERT INTO moneyflow_cache (ts_code, trade_date, net_amount)"
        " VALUES (?,?,?)",
        (code, trade_date, 1000),
    )
    conn.commit()
    conn.close()


def _fake_moneyflow_tool(calls: list):
    class _Tool:
        def invoke(self, kwargs):
            calls.append(kwargs)
            return _moneyflow_csv()

    return _Tool()


def test_moneyflow_no_refetch_when_last_trading_day_cached(config, monkeypatch):
    _seed_moneyflow(config)   # cache already has 20260820
    calls: list = []
    monkeypatch.setattr(cft, "get_money_flow", _fake_moneyflow_tool(calls))

    fetch_or_cache_moneyflow(config, "000001.SZ", "2026-08-01", "2026-08-20")
    assert calls == []   # no tushare fetch


def test_moneyflow_fetches_when_last_trading_day_missing(config, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cft, "get_money_flow", _fake_moneyflow_tool(calls))

    out = fetch_or_cache_moneyflow(config, "000001.SZ", "2026-08-01", "2026-08-20")
    assert len(calls) == 1
    assert calls[0]["ticker"] == "000001.SZ"
    assert out and "000001.SZ" in out

    # Second call: cache now covers the trading day → no refetch.
    fetch_or_cache_moneyflow(config, "000001.SZ", "2026-08-01", "2026-08-20")
    assert len(calls) == 1


def test_moneyflow_weekend_holiday_no_refetch(config, monkeypatch):
    """Sat/Sun with Friday's data cached must not trigger a fetch storm."""
    _seed_moneyflow(config, trade_date="20260814")   # Friday's data

    # end_date is the weekend (2026-08-16) → last_open_day maps to Friday.
    monkeypatch.setattr(trade_cal, "last_open_day", lambda ds: "20260814")
    calls: list = []
    monkeypatch.setattr(cft, "get_money_flow", _fake_moneyflow_tool(calls))

    fetch_or_cache_moneyflow(config, "000001.SZ", "2026-08-01", "2026-08-16")
    assert calls == []


def test_moneyflow_same_day_memo_skips_repeated_empty_fetches(config, monkeypatch):
    """Empty fetch (data not published yet) is remembered for the process/day."""
    calls: list = []

    class _EmptyTool:
        def invoke(self, kwargs):
            calls.append(kwargs)
            return "ts_code,trade_date\n"   # no data rows → 0 new

    monkeypatch.setattr(cft, "get_money_flow", _EmptyTool())

    fetch_or_cache_moneyflow(config, "000001.SZ", "2026-08-01", "2026-08-20")
    fetch_or_cache_moneyflow(config, "000001.SZ", "2026-08-01", "2026-08-20")
    assert len(calls) == 1


# ── endpoint ordering contract (watchlist_stock_detail) ──

def test_detail_normalizes_asc_cache_to_newest_first(monkeypatch):
    """The moneyflow cache reads back ASC; the detail endpoint must emit
    newest-first (flow_detail[0] = most recent trading day) so dates_range,
    the OHLCV window and flow_text all line up. Regression test for the
    two-pass prompt ordering bug."""
    import requests
    from web.app import watchlist_stock_detail

    # Stub Tencent realtime (guarded by try/except; minimal payload is fine).
    class _FakeResp:
        encoding = "gbk"
        text = 'v_sh="1~平安银行~000001~11.20~11.25~11.10~123456~...";\n'

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp())

    # get_cached_moneyflow returns rows ORDER BY trade_date ASC — oldest first.
    # Values are the cache's native 元 scale (get_money_flow ×1e4 + user_version=2
    # rebuild); the detail endpoint converts 元→万元 at parse time. net = 主力
    # (超大+大单), so a day with 主力 (buy_elg−sell_elg)+(buy_lg−sell_lg) = 4,000,000元
    # must surface as net == 400. net_amount is deliberately ignored (语义未定义).
    asc_csv = (
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount\n"
        "000001.SZ,20260818,1000000,2000000,1000000,3000000,2000000\n"
        "000001.SZ,20260819,2000000,3000000,1500000,4000000,2500000\n"
        "000001.SZ,20260820,3000000,4000000,2000000,5000000,3000000\n"
    )
    from web import moneyflow_cache as mfc
    monkeypatch.setattr(mfc, "fetch_or_cache_moneyflow", lambda *a, **k: asc_csv)

    result = watchlist_stock_detail("000001.SZ", no_llm=True)

    dates = [r["date"] for r in result["flow_detail"]]
    assert dates == ["20260820", "20260819", "20260818"]   # newest-first
    # And the values follow their dates (not re-ordered independently) — now
    # expressed in 万元 after the 元→万元 parse conversion (1万倍 inflation fix).
    # net = 主力: 20260820 (4M−2M)+(5M−3M)=4M元→400万; 20260818 (2M−1M)+(3M−2M)=2M元→200万.
    assert result["flow_detail"][0]["net"] == 400          # 20260820 主力 400万
    assert result["flow_detail"][-1]["net"] == 200         # 20260818 主力 200万


def test_detail_flow_amounts_are_wan_yuan_after_parse(monkeypatch):
    """flow_detail amounts are 万元 after the parse-time 元→万元 conversion.

    Regression for the 1万倍 inflation surfaced on 002566 (12日主力 rendered
    +19009100万 = 1900亿 for a 27亿-cap stock; real +1900.91万元) and again on
    600493 (12日主力 −8012600万 = 801亿 for a ~20亿 small cap; real −801.26万元).
    The moneyflow cache is 元 (get_money_flow ×1e4 + user_version=2 rebuild);
    the LLM prompt and the raw-flow panel are 万元. net is 主力 (超大+大单):
    (4,000,000−2,000,000)+(5,000,000−3,000,000) = 4,000,000元 surfaces as 400万.
    The net_amount column (2,950,500元) is deliberately not used — 语义未定义
    (002696 铁律#6), its sign diverges from 主力 on 6/11 days for 002495.
    """
    import requests
    from web.app import watchlist_stock_detail

    class _FakeResp:
        encoding = "gbk"
        text = 'v_sh="1~平安银行~000001~11.20~11.25~11.10~123456~...";\n'

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp())
    yuan_csv = (
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount\n"
        "000001.SZ,20260820,2950500,4000000,2000000,5000000,3000000\n"
    )
    from web import moneyflow_cache as mfc
    monkeypatch.setattr(mfc, "fetch_or_cache_moneyflow", lambda *a, **k: yuan_csv)

    result = watchlist_stock_detail("000001.SZ", no_llm=True)
    row = result["flow_detail"][0]
    assert row["net"] == 400            # 主力=(4M-2M)+(5M-3M)=4,000,000元 → 400万
    assert row["buy_elg"] == 400        # 4,000,000元 → 400万
    assert row["sell_lg"] == 300        # 3,000,000元 → 300万


# ── tushare 万元→元 unit fix: purge stale 万元-scale cache rows ──

def test_init_moneyflow_cache_purges_stale_wan_rows(config):
    """Rows cached before the 2026-08-27 unit fix (万元) must be purged once so
    they never mix with new 元 rows — a mixed-scale cache is garbage."""
    from web import moneyflow_cache as mfc
    # The fixture's init already set user_version=2 on this fresh DB. Rewind to
    # the pre-fix version, insert a stale 万元-scale row, then re-init to run
    # the one-time migration.
    conn = _get_conn(config)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    stale = (
        "# Money Flow (主力资金流向)\n# Source: tushare (万元 scale, pre-fix)\n\n"
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount\n"
        "000001.SZ,20260820,96825.16,152417.08,86115.9\n"
    )
    assert mfc.save_moneyflow_csv(config, "000001.SZ", stale) == 1

    mfc.init_moneyflow_cache(config)

    conn = _get_conn(config)
    n = conn.execute("SELECT COUNT(*) FROM moneyflow_cache").fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert n == 0
    assert ver == 2


def test_init_moneyflow_cache_keeps_rows_after_migration(config):
    """After the version bump, re-inits are a no-op — freshly cached 元 rows
    survive (the migration runs exactly once)."""
    from web import moneyflow_cache as mfc
    conn = _get_conn(config)
    conn.execute("PRAGMA user_version = 1")   # simulate pre-fix DB (empty)
    conn.commit()
    conn.close()

    mfc.init_moneyflow_cache(config)   # migration runs on empty DB → no-op + version=2
    fresh = (
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount\n"
        "000001.SZ,20260820,968251600.0,1524170800.0,861159000.0\n"   # 元 scale
    )
    assert mfc.save_moneyflow_csv(config, "000001.SZ", fresh) == 1
    mfc.init_moneyflow_cache(config)   # second init → version already 2 → no-op

    conn = _get_conn(config)
    n = conn.execute("SELECT COUNT(*) FROM moneyflow_cache").fetchone()[0]
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert n == 1      # second init kept the freshly-cached 元 row
    assert ver == 2
