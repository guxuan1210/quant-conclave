"""Tests for the Hot Tracker multi-source price fallback chain.

Pure unit tests — vendor chain functions are monkeypatched, no network.
Covers chain ordering, per-vendor failure fall-through, and the
never-return-0 contract (PriceFetchError on total failure).
"""

from __future__ import annotations

import pytest

from quantconclave.hot_tracker import price_source as ps


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Ensure no real vendor is ever hit during these tests."""
    for name in ("_tushare_latest", "_tencent_latest", "_akshare_latest", "_yfinance_latest"):
        monkeypatch.setattr(ps, name, lambda ts_code: None)


def test_cn_uses_tushare_first(monkeypatch):
    monkeypatch.setattr(ps, "_tushare_latest", lambda ts_code: (10.5, "2026-08-19"))
    result = ps.fetch_latest_price("000001.SZ")
    assert result == {"price": 10.5, "date": "2026-08-19", "source": "tushare"}


def test_falls_through_to_tencent_when_tushare_empty(monkeypatch):
    monkeypatch.setattr(ps, "_tushare_latest", lambda ts_code: None)
    monkeypatch.setattr(ps, "_tencent_latest", lambda ts_code: (10.8, "2026-08-20"))
    result = ps.fetch_latest_price("600519.SH")
    assert result["source"] == "tencent"
    assert result["price"] == 10.8


def test_tushare_exception_falls_through(monkeypatch):
    def boom(ts_code):
        raise RuntimeError("tushare rate limit")

    monkeypatch.setattr(ps, "_tushare_latest", boom)
    monkeypatch.setattr(ps, "_tencent_latest", lambda ts_code: (9.9, "2026-08-20"))
    result = ps.fetch_latest_price("000001.SZ")
    assert result["source"] == "tencent"
    assert result["price"] == 9.9


def test_all_sources_fail_raises_price_fetch_error():
    with pytest.raises(ps.PriceFetchError) as exc:
        ps.fetch_latest_price("000001.SZ")
    assert "000001.SZ" in str(exc.value)


def test_non_cn_only_tries_yfinance(monkeypatch):
    called = []
    monkeypatch.setattr(ps, "_yfinance_latest", lambda ts_code: called.append(ts_code) or (42.0, "2026-08-20"))
    # A US ticker must NOT hit the CN vendors (they are stubbed to None anyway)
    result = ps.fetch_latest_price("AAPL")
    assert called == ["AAPL"]
    assert result["source"] == "yfinance"


def test_hk_tries_tencent_then_yfinance(monkeypatch):
    monkeypatch.setattr(ps, "_tencent_latest", lambda ts_code: None)
    monkeypatch.setattr(ps, "_yfinance_latest", lambda ts_code: (300.0, "2026-08-20"))
    result = ps.fetch_latest_price("00700.HK")
    assert result["source"] == "yfinance"
