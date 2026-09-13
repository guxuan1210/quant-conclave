"""Tests for the EastMoney micro-cap constituent replication (868008.WI).

Pure unit tests — ``requests.get`` and the trade calendar are monkeypatched,
no network access. Covers ST/null-cap filtering, ts_code formatting, the
<60-trading-day new-listing exclusion, pagination-to-limit, and UTF-8 decoding.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from quantconclave.dataflows import eastmoney_microcap as em


def _weekdays(end: date, n: int = 150) -> tuple:
    """Last ``n`` business days (Mon-Fri) ending at ``end``, ascending."""
    days = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return tuple(reversed(days))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Pin a fake trade calendar so no tushare/pandas call is made."""
    monkeypatch.setattr(em, "_OPEN_DAYS", _weekdays(date(2026, 9, 1), 150))


class _FakeResp:
    """requests-like response that mimics the real clist/get endpoint: it
    declares ``charset=UTF-8`` (so ``resp.encoding`` is already set) and
    ``json()`` decodes those UTF-8 bytes."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.encoding = "utf-8"  # from Content-Type: ...; charset=UTF-8

    def json(self) -> dict:
        return json.loads(json.dumps(self._payload, ensure_ascii=False).encode("utf-8").decode("utf-8"))


class _FakeEM:
    """Fake EastMoney clist endpoint keyed by page number."""

    def __init__(self, pages: dict[int, dict]):
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None, proxies=None):
        self.calls.append(dict(params or {}))
        pn = int((params or {}).get("pn", "1"))
        return _FakeResp(self.pages.get(pn, {"data": {"diff": []}}))


def _row(code, market, name, f20, f26) -> dict:
    return {"f12": code, "f13": market, "f14": name, "f20": f20, "f26": f26}


def test_filters_st_and_null_cap_and_formats_ts_code(monkeypatch):
    pages = {
        1: {"data": {"diff": [
            _row("000003", 0, "PT金田A", "-", 19910703),      # PT → dropped
            _row("000005", 0, "ST星源", "-", 19901210),       # ST + null cap
            _row("600000", 1, "*ST浦发", 1.0e12, 19991230),   # *ST → dropped
            _row("688701", 1, "卓锦股份", 1279663355, 20210916),
            _row("301192", 0, "泰祥股份", 2067361640, 20220811),
        ]}},
    }
    fake = _FakeEM(pages)
    monkeypatch.setattr(em, "_req", fake)

    got = em.get_microcap_constituents(limit=2, max_pages=2)

    assert got == [
        {"code": "688701.SH", "name": "卓锦股份"},
        {"code": "301192.SZ", "name": "泰祥股份"},
    ]
    # Stops as soon as the limit is reached (one page fetched).
    assert [p["pn"] for p in fake.calls] == ["1"]


def test_new_listing_under_60_trade_days_excluded(monkeypatch):
    pages = {
        1: {"data": {"diff": [
            _row("000001", 0, "平安银行", 2.0e11, 19910403),      # old → kept
            _row("688999", 1, "微盘新贵", 1.5e9, 20260815),       # listed ~2 wks ago → dropped
        ]}},
    }
    fake = _FakeEM(pages)
    monkeypatch.setattr(em, "_req", fake)

    got = em.get_microcap_constituents(limit=1, max_pages=2)

    assert got == [{"code": "000001.SZ", "name": "平安银行"}]


def test_paginates_until_limit_reached(monkeypatch):
    pages = {}
    # Two valid rows per page; need 5 → fetch pages 1..3.
    for pn in (1, 2, 3):
        pages[pn] = {"data": {"diff": [
            _row(f"600{pn}01", 1, f"沪股{pn}A", 1.0e9, 20200101),
            _row(f"300{pn}02", 0, f"深股{pn}B", 1.1e9, 20200102),
        ]}}
    fake = _FakeEM(pages)
    monkeypatch.setattr(em, "_req", fake)

    got = em.get_microcap_constituents(limit=5, max_pages=5)

    assert len(got) == 5
    assert [p["pn"] for p in fake.calls] == ["1", "2", "3"]


def test_raises_when_not_enough_valid_rows(monkeypatch):
    pages = {1: {"data": {"diff": [
        _row("600001", 1, "沪股1", 1.0e9, 20200101),
        _row("300002", 0, "ST深股2", 1.1e9, 20200102),  # dropped
    ]}}}
    fake = _FakeEM(pages)
    monkeypatch.setattr(em, "_req", fake)

    with pytest.raises(RuntimeError, match="only 1/5"):
        em.get_microcap_constituents(limit=5, max_pages=3)


def test_utf8_response_decoded_via_declared_charset(monkeypatch):
    """Names survive when _get honors the endpoint's declared charset=UTF-8.

    Regression guard: the clist/get endpoint switched to UTF-8; forcing
    ``resp.encoding = "gbk"`` (the old behavior) mojibake'd every name.
    """
    pages = {1: {"data": {"diff": [_row("688701", 1, "卓锦股份", 1279663355, 20210916)]}}}
    fake = _FakeEM(pages)
    monkeypatch.setattr(em, "_req", fake)

    got = em.get_microcap_constituents(limit=1, max_pages=2)

    assert got[0]["name"] == "卓锦股份"


def test_utf8_fallback_when_charset_missing(monkeypatch):
    """If the server omits charset, _get falls back to apparent_encoding."""
    pages = {1: {"data": {"diff": [_row("688701", 1, "卓锦股份", 1279663355, 20210916)]}}}
    fake = _FakeEM(pages)

    class _NoCharsetResp(_FakeResp):
        def __init__(self, payload):
            super().__init__(payload)
            self.encoding = None  # server omitted charset
            self.apparent_encoding = "utf-8"  # chardet guess

    def fake_get(url, params=None, headers=None, timeout=None, proxies=None):
        pn = int((params or {}).get("pn", "1"))
        return _NoCharsetResp(pages.get(pn, {"data": {"diff": []}}))

    monkeypatch.setattr(em, "_req", type("F", (), {"get": staticmethod(fake_get)})())

    got = em.get_microcap_constituents(limit=1, max_pages=2)
    assert got[0]["name"] == "卓锦股份"


def test_is_newly_listed_trading_day_threshold():
    today = date(2026, 9, 1)
    # ~21 weekdays ago → <60 trading days → new
    assert em._is_newly_listed((today - timedelta(days=30)).strftime("%Y%m%d"), today)
    # ~120 weekdays ago → >60 trading days → mature
    assert not em._is_newly_listed((today - timedelta(days=170)).strftime("%Y%m%d"), today)
    # Unknown listing date → kept
    assert not em._is_newly_listed(None, today)
