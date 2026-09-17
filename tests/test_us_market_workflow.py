"""Focused persistence coverage for the native US market context."""

import json

from quantconclave.graph.trading_graph import QuantConclaveGraph


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
