import json
from pathlib import Path
import pytest
from quantconclave.dataflows.sec_edgar import SecEdgarClient, SecConfigurationError, select_company_facts

FIXTURES = Path(__file__).parent / "fixtures" / "sec"

class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text
    def raise_for_status(self): return None
    def json(self): return self._payload

class FakeSession:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout)); return self.responses.pop(0)

def load_json(name): return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

def test_requires_identifiable_sec_user_agent(tmp_path):
    with pytest.raises(SecConfigurationError): SecEdgarClient(user_agent="", cache_dir=tmp_path)

def test_sends_configured_user_agent(tmp_path):
    session = FakeSession([FakeResponse(load_json("company_tickers.json"))])
    client = SecEdgarClient(user_agent="QuantConclave admin@example.com", cache_dir=tmp_path, session=session, sleep=lambda _: None)
    assert client.resolve_cik("AAPL") == "0000320193"
    assert session.calls[0][1]["User-Agent"] == "QuantConclave admin@example.com"

def test_filters_filings_by_publication_date(tmp_path):
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json"))])
    client = SecEdgarClient(user_agent="QuantConclave admin@example.com", cache_dir=tmp_path, session=session, sleep=lambda _: None)
    filings = client.get_filings("0000320193", "2026-09-17")
    assert all(row["filingDate"] <= "2026-09-17" for row in filings)
    assert not any(row["form"] == "8-K" and row["filingDate"] > "2026-09-17" for row in filings)

def test_company_facts_choose_latest_publicly_available_value():
    selected = select_company_facts(load_json("aapl_companyfacts.json"), as_of="2026-09-17")
    assert selected["revenue"]["filed"] <= "2026-09-17"
    assert selected["revenue"]["value"] == 100

def test_form4_parser_returns_purchase_transaction(tmp_path):
    xml = (FIXTURES / "aapl_form4.xml").read_text(encoding="utf-8")
    client = SecEdgarClient(user_agent="QuantConclave admin@example.com", cache_dir=tmp_path, session=FakeSession([]), sleep=lambda _: None)
    assert client.parse_form4(xml) == [{"owner":"Example Officer","transaction_date":"2026-09-01","code":"P","shares":1000.0,"price":225.5,"acquired_disposed":"A"}]
