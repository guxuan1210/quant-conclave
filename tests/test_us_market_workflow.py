"""Focused persistence coverage for the native US market context."""

import json

import pytest

from quantconclave.agents.utils.agent_utils import build_state_instrument_context
from quantconclave.evidence import EvidenceItem, EvidencePack, EvidenceStatus
from quantconclave.graph.trading_graph import QuantConclaveGraph
from quantconclave.graph.propagation import Propagator
from quantconclave.instruments import resolve_instrument


def _fixed_evidence_pack(ticker, revenue=100):
    return EvidencePack(
        symbol=ticker,
        analysis_date="2026-09-17",
        items=(
            EvidenceItem(
                kind="financials", source="sec_edgar",
                as_of="2026-06-30", filed_at="2026-08-01",
                fetched_at="2026-09-17T00:00:00Z",
                status=EvidenceStatus.AVAILABLE,
                payload={"revenue": revenue},
            ),
            EvidenceItem(
                kind="institutional_holders", source="sec_13f",
                as_of="2026-06-30", filed_at="2026-08-14",
                fetched_at="2026-09-17T00:00:00Z",
                status=EvidenceStatus.NO_DATA, payload={},
            ),
        ),
    )


def _fixed_us_state(ticker="AAPL", revenue=100):
    profile = resolve_instrument(ticker)
    pack = _fixed_evidence_pack(ticker, revenue=revenue)
    return Propagator().create_initial_state(
        ticker,
        "2026-09-17",
        instrument_profile=profile.to_dict(),
        evidence_pack=pack.to_dict(),
    )


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "BRK.B"])
def test_us_ticker_preparation_is_serializable_and_spy_benchmarked(ticker):
    state = _fixed_us_state(ticker)

    json.dumps(state, default=str)
    assert state["instrument_profile"]["market"] == "US"
    assert state["instrument_profile"]["currency"] == "USD"
    assert state["instrument_profile"]["benchmark"] == "SPY"
    assert state["evidence_pack"]["symbol"] == ticker


def test_all_agent_contexts_share_the_same_core_financial_value():
    state = _fixed_us_state(revenue=100)
    contexts = [build_state_instrument_context(dict(state)) for _ in range(7)]

    assert all("'revenue': 100" in context for context in contexts)
    assert all("as_of=2026-06-30" in context for context in contexts)
    assert all("filed_at=2026-08-01" in context for context in contexts)


def test_us_context_uses_explicit_no_data_without_cn_only_terms():
    context = build_state_instrument_context(_fixed_us_state(revenue=100))

    assert "institutional_holders: NO_DATA" in context
    assert "北向资金" not in context
    assert "龙虎榜" not in context


def _complete_state():
    empty_debate = {
        "bull_history": [], "bear_history": [], "history": [],
        "current_response": "", "judge_decision": "",
    }
    risk_state = {
        "aggressive_history": [], "conservative_history": [],
        "neutral_history": [], "history": [], "judge_decision": "",
    }
    return {
        "company_of_interest": "AAPL", "trade_date": "2026-09-17",
        "market_report": "", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "", "capital_flow_report": "",
        "competitor_report": "", "partner_report": "",
        "investment_debate_state": empty_debate,
        "trader_investment_plan": "", "risk_debate_state": risk_state,
        "investment_plan": "", "final_trade_decision": "",
    }


def _test_graph(tmp_path):
    graph = object.__new__(QuantConclaveGraph)
    graph.config = {"results_dir": str(tmp_path)}
    graph.ticker = "AAPL"
    graph.log_states_dict = {}
    return graph


def test_log_state_persists_profile_and_evidence_provenance(tmp_path):
    graph = _test_graph(tmp_path)
    state = _complete_state()
    state["instrument_profile"] = {
        "symbol": "AAPL", "market": "US", "benchmark": "SPY",
    }
    state["evidence_pack"] = {
        "symbol": "AAPL", "quality": "degraded",
        "analysis_date": "2026-09-17",
        "items": [
            {"kind": "financials", "source": "yfinance", "status": "degraded"},
            {"kind": "filings", "source": "sec_edgar", "status": "available"},
            {"kind": "quote", "source": "yfinance", "status": "available"},
        ],
    }

    graph._log_state("2026-09-17", state)
    saved = graph._load_last_state("AAPL")

    assert saved["instrument_profile"]["market"] == "US"
    assert saved["evidence_as_of"] == "2026-09-17"
    assert saved["evidence_quality"] == "degraded"
    assert saved["evidence_sources"] == ["sec_edgar", "yfinance"]
    assert saved["evidence_pack"]["symbol"] == "AAPL"


def test_old_state_without_provenance_remains_loadable(tmp_path):
    graph = _test_graph(tmp_path)
    directory = tmp_path / "AAPL" / "QuantConclaveStrategy_logs"
    directory.mkdir(parents=True)
    legacy = {"company_of_interest": "AAPL", "trade_date": "2026-09-16"}
    path = directory / "full_states_log_2026-09-16.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = graph._load_last_state("AAPL")

    assert loaded["company_of_interest"] == "AAPL"
    assert loaded.get("instrument_profile", {}) == {}
    assert "evidence_pack" not in loaded
