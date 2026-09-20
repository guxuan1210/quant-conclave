from dataclasses import dataclass

import pytest
import inspect
import json

from quantconclave.evidence import EvidenceStatus
from quantconclave.evidence.builder import build_evidence_pack


@dataclass
class Profile:
    symbol: str
    market: str = "US"


class FakeSec:
    def resolve_cik(self, ticker):
        return "0000320193"

    def get_submissions(self, cik):
        return {"name": "Apple Inc."}

    def get_filings(self, cik, as_of, forms=("10-K", "10-Q", "8-K", "4")):
        return [{"form": "10-K", "filingDate": as_of, "accessionNumber": "x"}]

    def get_company_facts(self, cik):
        return {"facts": {}}

    def get_form4_transactions(self, cik, as_of, limit=20):
        return [{"owner": "Officer", "shares": 10}]


def _fetchers():
    return {
        "price_history": lambda symbol, analysis_date=None, config=None: [{"date": "2026-09-17", "close": 225}],
        "quote": lambda symbol, **kwargs: {"price": 225},
        "identity": lambda symbol, **kwargs: {"name": "Apple Inc."},
        "financials": lambda symbol, **kwargs: {"revenue": 100},
        "holders": lambda symbol, **kwargs: [{"holder": "Fund"}],
        "analyst_ratings": lambda symbol, **kwargs: {"buy": 10},
        "benchmark_context": lambda symbol, **kwargs: {"benchmark": "SPY"},
    }


def test_us_pack_has_stable_order_and_sec_primary_identity():
    pack = build_evidence_pack(Profile("AAPL"), "2026-09-17", {}, sec_client=FakeSec(), market_fetchers=_fetchers())
    assert [item.kind for item in pack.items] == [
        "company_identity", "price_history", "quote", "filings", "financials", "insider_transactions", "institutional_holders", "analyst_ratings", "benchmark_context"
    ]
    assert pack.get("company_identity").source == "sec"
    assert pack.get("financials").status is EvidenceStatus.DEGRADED


def test_optional_empty_payload_is_no_data_and_required_price_fails():
    fetchers = _fetchers()
    fetchers["holders"] = lambda *args, **kwargs: []
    fetchers["price_history"] = lambda *args, **kwargs: [{"date": "2026-09-17", "close": 225}]
    pack = build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=FakeSec(), market_fetchers=fetchers)
    assert pack.get("institutional_holders").status is EvidenceStatus.NO_DATA


def test_price_required_exact_message():
    fetchers = _fetchers()
    fetchers["price_history"] = lambda *args, **kwargs: []
    with pytest.raises(RuntimeError, match="required price history"):
        build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=FakeSec(), market_fetchers=fetchers)

def test_price_fetcher_exception_has_exact_required_error():
    fetchers = _fetchers()
    fetchers["price_history"] = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("provider down"))
    with pytest.raises(RuntimeError, match="^required price history unavailable for AAPL$"):
        build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=FakeSec(), market_fetchers=fetchers)


def test_non_us_pack_is_minimal_identity_and_price():
    pack = build_evidence_pack({"symbol": "0700.HK", "market": "HK"}, "2026-09-17", {}, market_fetchers=_fetchers())
    assert [item.kind for item in pack.items] == ["company_identity", "price_history"]


def test_identity_yfinance_fallback_is_degraded():
    fetchers = _fetchers()
    class BrokenSec(FakeSec):
        def resolve_cik(self, ticker): raise RuntimeError("offline")
    pack = build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=BrokenSec(), market_fetchers=fetchers)
    assert pack.get("company_identity").status is EvidenceStatus.DEGRADED

def test_identity_fallback_exception_has_exact_required_error():
    class BrokenSec(FakeSec):
        def resolve_cik(self, ticker): raise RuntimeError("offline")
    fetchers = _fetchers()
    fetchers["identity"] = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad"))
    with pytest.raises(RuntimeError, match="^required company identity unavailable for AAPL$"):
        build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=BrokenSec(), market_fetchers=fetchers)

def test_optional_benchmark_none_is_no_data_and_financial_error_is_retained():
    class BadSec(FakeSec):
        def get_company_facts(self, cik): raise RuntimeError("facts down")
        def get_filings(self, *args, **kwargs): raise RuntimeError("filings down")
        def get_form4_transactions(self, *args, **kwargs): raise RuntimeError("form4 down")
    fetchers = _fetchers()
    fetchers["financials"] = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("yf down"))
    fetchers["benchmark_context"] = lambda *args, **kwargs: None
    pack = build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=BadSec(), market_fetchers=fetchers)
    assert pack.get("financials").status is EvidenceStatus.ERROR
    assert pack.get("benchmark_context").status is EvidenceStatus.NO_DATA
    assert pack.get("filings").status is EvidenceStatus.ERROR

def test_default_fetcher_wiring_maps_kind_names_to_us_market_functions(monkeypatch):
    from quantconclave.dataflows import us_market

    def price(symbol, analysis_date, lookback_days=120):
        return [{"date": "2026-09-17", "close": 225}]
    def identity(symbol, analysis_date):
        return {"name": "Apple Inc."}
    def quote(symbol, analysis_date):
        return {"price": 225}
    def financials(symbol, analysis_date):
        return {"revenue": 100}
    def holders(symbol, analysis_date):
        return [{"holder": "Fund"}]
    def ratings(symbol, analysis_date):
        return {"buy": 10}
    def benchmark(symbol, benchmark, analysis_date):
        return {"benchmark": benchmark}

    monkeypatch.setattr(us_market, "fetch_price_history", price)
    monkeypatch.setattr(us_market, "fetch_identity", identity)
    monkeypatch.setattr(us_market, "fetch_quote", quote)
    monkeypatch.setattr(us_market, "fetch_yfinance_financials", financials)
    monkeypatch.setattr(us_market, "fetch_holders", holders)
    monkeypatch.setattr(us_market, "fetch_analyst_ratings", ratings)
    monkeypatch.setattr(us_market, "fetch_benchmark_context", benchmark)

    pack = build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=FakeSec())

    assert [item.kind for item in pack.items] == [
        "company_identity", "price_history", "quote", "filings", "financials",
        "insider_transactions", "institutional_holders", "analyst_ratings", "benchmark_context",
    ]
    assert pack.get("price_history").status is EvidenceStatus.AVAILABLE
    assert pack.get("benchmark_context").payload["benchmark"] == "SPY"


def test_benchmark_ticker_none_defaults_to_spy():
    seen = {}
    fetchers = _fetchers()

    def benchmark_spy(symbol, benchmark, analysis_date):
        seen["benchmark"] = benchmark
        return {"benchmark": benchmark}

    fetchers["benchmark_context"] = benchmark_spy
    pack = build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17",
                               {"benchmark_ticker": None}, sec_client=FakeSec(), market_fetchers=fetchers)

    assert seen["benchmark"] == "SPY"
    assert pack.get("benchmark_context").status is EvidenceStatus.AVAILABLE


def test_unamed_index_price_rows_are_normalized_and_json_safe(monkeypatch):
    import pandas as pd
    from quantconclave.dataflows import us_market
    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame({"Close": [1, 2], "Value": [pd.NA, 3]}, index=pd.to_datetime(["2026-09-16", "2026-09-18"]))
    monkeypatch.setattr(us_market, "_yf_ticker", lambda symbol: Ticker())
    rows = us_market.fetch_price_history("AAPL", "2026-09-17", 120)
    assert rows == [{"date": "2026-09-16T00:00:00", "Close": 1, "Value": None}]
    json.dumps(rows)


def test_wrapper_signatures_and_json_safe():
    from quantconclave.dataflows import us_market
    expected = {
        "fetch_price_history": ["symbol", "analysis_date", "lookback_days"],
        "fetch_identity": ["symbol", "analysis_date"], "fetch_quote": ["symbol", "analysis_date"],
        "fetch_yfinance_financials": ["symbol", "analysis_date"], "fetch_holders": ["symbol", "analysis_date"],
        "fetch_analyst_ratings": ["symbol", "analysis_date"], "fetch_benchmark_context": ["symbol", "benchmark", "analysis_date"],
    }
    for name, params in expected.items():
        assert list(inspect.signature(getattr(us_market, name)).parameters) == params
    assert json.dumps({"x": [{"date": "2026-09-17", "value": 1}]})
