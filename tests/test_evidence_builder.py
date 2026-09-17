from dataclasses import dataclass

import pytest

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
        "identity", "price", "quote", "filings", "financials", "insider", "holders", "analyst", "benchmark"
    ]
    assert pack.get("identity").source == "sec"
    assert pack.get("financials").status is EvidenceStatus.DEGRADED


def test_optional_empty_payload_is_no_data_and_required_price_fails():
    fetchers = _fetchers()
    fetchers["holders"] = lambda *args, **kwargs: []
    fetchers["price_history"] = lambda *args, **kwargs: []
    with pytest.raises(RuntimeError, match="price"):
        build_evidence_pack({"symbol": "AAPL", "market": "US"}, "2026-09-17", {}, sec_client=FakeSec(), market_fetchers=fetchers)


def test_non_us_pack_is_minimal_identity_and_price():
    pack = build_evidence_pack({"symbol": "0700.HK", "market": "HK"}, "2026-09-17", {}, market_fetchers=_fetchers())
    assert [item.kind for item in pack.items] == ["identity", "price"]
