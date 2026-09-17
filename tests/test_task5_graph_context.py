from types import SimpleNamespace, ModuleType
import sys

from quantconclave.evidence.builder import build_evidence_pack
from quantconclave.graph.propagation import Propagator
from quantconclave.graph.trading_graph import QuantConclaveGraph


def test_real_aapl_profile_builds_us_evidence_pack_without_network(monkeypatch):
    instruments = __import__("pytest").importorskip("quantconclave.instruments")
    profile = instruments.resolve_instrument("AAPL")
    fetchers = {
        "price_history": lambda symbol, analysis_date: [{"date": analysis_date}],
        "identity": lambda symbol, analysis_date: {"symbol": symbol},
        "quote": lambda symbol, analysis_date: {"currentPrice": 1},
        "financials": lambda symbol, analysis_date: {"income": []},
        "holders": lambda symbol, analysis_date: {},
        "analyst_ratings": lambda symbol, analysis_date: {},
        "benchmark_context": lambda symbol, benchmark, analysis_date: {"benchmark": benchmark},
    }
    pack = build_evidence_pack(profile, "2026-09-17", {}, market_fetchers=fetchers)
    assert {item.kind for item in pack.items} == {
        "company_identity", "price_history", "quote", "filings", "financials",
        "insider_transactions", "institutional_holders", "analyst_ratings", "benchmark_context",
    }


def test_market_enum_builds_full_us_pack_without_network():
    class Market:
        US = SimpleNamespace(value="US")

    profile = SimpleNamespace(symbol="AAPL", market=Market.US)
    fetchers = {
        "price_history": lambda symbol, analysis_date: [{"date": analysis_date}],
        "identity": lambda symbol, analysis_date: {"symbol": symbol},
        "quote": lambda symbol, analysis_date: {"currentPrice": 1},
        "financials": lambda symbol, analysis_date: {"income": []},
        "holders": lambda symbol, analysis_date: {},
        "analyst_ratings": lambda symbol, analysis_date: {},
        "benchmark_context": lambda symbol, benchmark, analysis_date: {"benchmark": benchmark},
    }
    pack = build_evidence_pack(profile, "2026-09-17", {}, market_fetchers=fetchers)
    assert {item.kind for item in pack.items} == {
        "company_identity", "price_history", "quote", "filings", "financials",
        "insider_transactions", "institutional_holders", "analyst_ratings", "benchmark_context",
    }


def test_prepare_market_context_constructs_configured_sec_client(monkeypatch):
    captured = {}
    class Profile:
        def to_dict(self): return {"symbol": "AAPL", "market": "US"}
    class Pack:
        def to_dict(self): return {"symbol": "AAPL", "items": []}
    class FakeSec:
        def __init__(self, **kwargs): captured.update(kwargs)
    instruments = ModuleType("quantconclave.instruments")
    instruments.resolve_instrument = lambda *a, **k: Profile()
    monkeypatch.setitem(sys.modules, "quantconclave.instruments", instruments)
    monkeypatch.setattr("quantconclave.dataflows.sec_edgar.SecEdgarClient", FakeSec)
    monkeypatch.setattr("quantconclave.evidence.build_evidence_pack", lambda *a, **kw: (captured.update(sec_client=kw["sec_client"]) or Pack()))
    graph = QuantConclaveGraph.__new__(QuantConclaveGraph)
    graph.config = {"sec_user_agent": "App owner@example.com", "data_cache_dir": "cache", "sec_request_interval_seconds": .25, "sec_timeout_seconds": 4.5}
    graph._prepare_market_context("AAPL", "2026-09-17", "stock")
    assert captured["user_agent"] == "App owner@example.com"
    assert captured["cache_dir"] == "cache"
    assert captured["request_interval_seconds"] == .25
    assert captured["timeout_seconds"] == 4.5
    assert isinstance(captured["sec_client"], FakeSec)
