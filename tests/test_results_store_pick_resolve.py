"""Tests for PR1 pick resolution and the Hot Tracker persistence layer.

Uses a temp results.db via ``config={"results_dir": str(tmp_path)}`` — no
monkeypatching of sqlite, no network. The price source chain is stubbed at
the module boundary so resolve_picks exercises its real control flow.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from web.results_store import (
    get_picks,
    init_db,
    resolve_picks,
    save_pick,
)
from capitalradar.hot_tracker import price_source as ps
from capitalradar.hot_tracker import store as ht
from capitalradar.hot_tracker.settlement import (
    PROFIT_TAKEN, STOPPED, classify_settlement, compute_settle_date,
)


@pytest.fixture()
def config(tmp_path):
    cfg = {"results_dir": str(tmp_path)}
    init_db(cfg)
    return cfg


def _pick_date(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ── resolve_picks ──

def test_resolve_updates_latest_price_and_returns(config, monkeypatch):
    save_pick(config, {
        "pick_id": "p1", "ticker": "000001.SZ", "pick_date": _pick_date(25),
        "source": "hot_reversal", "strategy": "热点反转", "smart_money_score": 60,
        "pick_price": 10.0,
    })
    monkeypatch.setattr(ps, "fetch_latest_price",
                        lambda ts, cfg=None: {"price": 11.0, "date": "2026-08-20", "source": "tushare"})

    assert resolve_picks(config) == 1

    row = get_picks(config)[0]
    assert row["latest_price"] == 11.0
    assert row["latest_date"] == "2026-08-20"
    assert row["return_20d"] == 10.0        # (11 - 10) / 10 * 100
    assert row["return_60d"] is None        # only 25 days elapsed
    assert row["return_5d"] is not None


def test_resolve_price_fetch_failure_keeps_pending(config, monkeypatch):
    save_pick(config, {
        "pick_id": "p2", "ticker": "600519.SH", "pick_date": _pick_date(30),
        "source": "hot_reversal", "strategy": "热点反转", "smart_money_score": 70,
        "pick_price": 1500.0,
    })

    def _fail(ts, cfg=None):
        raise ps.PriceFetchError(ts, detail="all vendors down")

    monkeypatch.setattr(ps, "fetch_latest_price", _fail)

    assert resolve_picks(config) == 0        # must NOT write a bogus 0.00
    row = get_picks(config)[0]
    assert row["latest_price"] == 0.0
    assert row["return_20d"] is None


def test_resolve_backfills_missing_pick_price(config, monkeypatch):
    save_pick(config, {
        "pick_id": "p3", "ticker": "000002.SZ", "pick_date": _pick_date(25),
        "source": "hot_reversal", "strategy": "热点反转", "smart_money_score": 55,
        "pick_price": 0.0,                  # historical row with no pick price
    })
    monkeypatch.setattr(ps, "fetch_latest_price",
                        lambda ts, cfg=None: {"price": 11.0, "date": "2026-08-20", "source": "tencent"})
    monkeypatch.setattr(ps, "fetch_price_history",
                        lambda ts, days=40: [{"date": _pick_date(25), "close": 10.0}])

    assert resolve_picks(config) == 1
    row = get_picks(config)[0]
    assert row["pick_price"] == 10.0         # backfilled from history
    assert row["return_20d"] == 10.0


# ── hot_tracker tables + store CRUD ──

def test_pool_snapshot_roundtrip(config):
    rows = [{
        "ts_code": "300770.SZ", "name": "新媒股份", "total_score": 82.0,
        "score_fund": 75.0, "theme_name": "影视音像", "theme_rank": 1,
        "sms_score": 65.0, "fund_net_3d": 1200.0, "dist_low_20d": 5.0,
    }]
    assert ht.insert_pool_snapshot(config, "2026-08-20", rows) == 1
    snap = ht.get_pool_snapshot(config)
    assert len(snap) == 1
    assert snap[0]["ts_code"] == "300770.SZ"
    assert snap[0]["theme_name"] == "影视音像"


def test_pick_track_update_and_settlement(config):
    ht.insert_pick(config, {
        "pick_date": "2026-08-01", "ts_code": "300770.SZ", "name": "新媒股份",
        "pick_price": 20.0, "total_score": 80.0, "sms_score": 60.0,
        "settle_date": "2026-08-30",
    })
    assert len(ht.get_pending_picks(config)) == 1

    # Daily update at +10%: peak/return tracked, daily_track_json appended
    assert ht.update_pick_track(config, "300770.SZ", "2026-08-01",
                                22.0, "2026-08-02", {"date": "08-02", "price": 22.0})
    row = ht.get_pending_picks(config)[0]
    assert row["latest_price"] == 22.0
    assert row["return_pct"] == 10.0
    assert row["peak_price"] == 22.0
    assert row["max_return_pct"] == 10.0

    # Two updates → peak stays at the max
    ht.update_pick_track(config, "300770.SZ", "2026-08-01",
                         21.0, "2026-08-03", {"date": "08-03", "price": 21.0})
    row = ht.get_pending_picks(config)[0]
    assert row["peak_price"] == 22.0
    assert row["max_return_pct"] == 10.0

    # Take-profit at +21% (>20) → terminal status, no longer PENDING
    ht.update_pick_track(config, "300770.SZ", "2026-08-01",
                         24.2, "2026-08-04", {"date": "08-04", "price": 24.2})
    status, terminal = classify_settlement(21.0, "2026-08-30", today="2026-08-04")
    assert (status, terminal) == (PROFIT_TAKEN, True)
    ht.settle_pick(config, "300770.SZ", "2026-08-01", status, 21.0, note="已达止盈线")
    assert len(ht.get_pending_picks(config)) == 0

    dash = ht.get_hot_tracker_dashboard(config)
    assert any(p["status"] == PROFIT_TAKEN for p in dash["picks"])
    assert len(dash["alerts"]) == 1


def test_classify_settlement_priority():
    # Stop-loss beats maturity: return below -8% even after settle_date → STOPPED
    assert classify_settlement(-9.0, "2026-01-01", today="2026-02-01")[0] == STOPPED
    # Take-profit beats stop-loss if both plausible on the same read
    assert classify_settlement(25.0, "2026-08-30", today="2026-08-01")[0] == PROFIT_TAKEN
    # Pending until settle date
    assert classify_settlement(5.0, "2026-08-30", today="2026-08-01")[0] == "PENDING"
    # Maturity settles even on a small gain
    assert classify_settlement(5.0, "2026-08-30", today="2026-08-31")[0] == "SETTLED"


def test_compute_settle_date_falls_back_without_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "")
    settle = compute_settle_date("2026-08-20")
    # No token → calendar estimate; must still be a valid, later date
    assert len(settle) == 10
    assert settle > "2026-08-20"
