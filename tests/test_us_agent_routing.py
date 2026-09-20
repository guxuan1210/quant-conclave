from quantconclave.agents.utils.agent_utils import build_state_instrument_context


US_STATE = {
    "company_of_interest": "AAPL",
    "asset_type": "stock",
    "trade_date": "2026-09-17",
    "instrument_profile": {
        "symbol": "AAPL", "market": "US", "currency": "USD",
        "timezone": "America/New_York", "benchmark": "SPY",
    },
    "evidence_pack": {
        "symbol": "AAPL", "analysis_date": "2026-09-17", "quality": "complete",
        "items": [{
            "kind": "financials", "source": "sec_companyfacts",
            "as_of": "2026-08-01", "filed_at": "2026-08-01",
            "fetched_at": "2026-09-17T00:00:00Z", "freshness": "filing",
            "status": "available", "period_end": "2026-06-30",
            "payload": {"revenue": 100},
        }],
    },
}


def test_state_context_includes_market_currency_benchmark_and_evidence():
    text = build_state_instrument_context(US_STATE)
    assert "market=US" in text
    assert "currency=USD" in text
    assert "benchmark=SPY" in text
    assert "sec_companyfacts" in text


def test_us_capital_flow_prompt_has_no_cn_only_requirements():
    from quantconclave.agents.analysts.capital_flow_analyst import _market_instructions
    text = _market_instructions("US")
    forbidden = ("get_money_flow", "northbound", "Dragon-Tiger", "龙虎榜", "北向资金")
    assert not any(term in text for term in forbidden)
    assert "13F" in text and "quarterly" in text


def test_us_capital_flow_no_tool_fallback_skips_cn_prefetch(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from quantconclave.agents.analysts import capital_flow_analyst as module

    def fail_prefetch(_ticker):
        raise AssertionError("CN prefetch must not run for US")

    class NoToolLLM:
        seen = []

        def bind_tools(self, _tools):
            return self

        def __call__(self, _messages):
            self.seen.append(_messages)
            return AIMessage(content="")

        def invoke(self, _messages):
            self.seen.append(_messages)
            return AIMessage(content="")

    monkeypatch.setattr(module, "_prefetch_capital_flow_data", fail_prefetch)
    state = dict(US_STATE, messages=[HumanMessage(content="analyze")])
    llm = NoToolLLM()
    result = module.create_capital_flow_analyst(llm)(state)
    assert "NO_DATA" in result["capital_flow_report"]
    prompt_value = llm.seen[0]
    prompt_messages = (prompt_value.to_messages()
                       if hasattr(prompt_value, "to_messages") else prompt_value)
    prompt_text = "\n".join(str(message.content) for message in prompt_messages)
    assert "get_money_flow" not in prompt_text


def test_us_fundamental_tools_exclude_eastmoney():
    from quantconclave.agents.analysts.fundamentals_analyst import _tools_for_market
    names = {tool.name for tool in _tools_for_market("US")}
    assert "get_eastmoney_fundamentals" not in names
    assert "get_eastmoney_data" not in names


def test_cn_fundamental_tools_keep_eastmoney():
    from quantconclave.agents.analysts.fundamentals_analyst import _tools_for_market
    names = {tool.name for tool in _tools_for_market("CN")}
    assert "get_eastmoney_fundamentals" in names
