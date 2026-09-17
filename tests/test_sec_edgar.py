import json
from pathlib import Path
import pytest
import requests
from quantconclave.dataflows.sec_edgar import SecEdgarClient, SecConfigurationError, select_company_facts

FIXTURES = Path(__file__).parent / "fixtures" / "sec"

class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text
    def raise_for_status(self): return None
    def json(self): return self._payload

class FailingResponse:
    def raise_for_status(self): raise RuntimeError("HTTP 503")
    def json(self): return {}

class StatusResponse:
    def __init__(self, status_code, payload=None): self.status_code, self._payload = status_code, payload
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(f"HTTP {self.status_code}")
    def json(self): return self._payload

class FakeSession:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException): raise response
        return response

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

def test_cache_hit_reads_payload_and_metadata_without_network(tmp_path):
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json"))])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, session=session, sleep=lambda _: None)
    first = client.get_submissions("320193")
    second = client.get_submissions("0000320193")
    assert first == second and len(session.calls) == 1
    metadata = list((tmp_path / "sec").glob("*.meta.json"))
    assert len(metadata) == 1 and "fetched_at" in json.loads(metadata[0].read_text())

def test_http_failure_is_not_cached(tmp_path):
    session = FakeSession([FailingResponse(), FakeResponse(load_json("aapl_submissions.json"))])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, session=session, sleep=lambda _: None)
    with pytest.raises(RuntimeError): client.get_submissions("320193")
    assert client.get_submissions("320193")["name"] == "Apple Inc."
    assert len(session.calls) == 2

def test_pacing_uses_injected_clock_and_sleep(tmp_path):
    now = [0.0]
    sleeps = []
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json")), FakeResponse(load_json("aapl_companyfacts.json"))])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, request_interval_seconds=.12, session=session, sleep=sleeps.append, clock=lambda: now[0])
    client.get_submissions("320193")
    client.get_company_facts("320193")
    assert sleeps == [.12]

def test_timeout_is_forwarded_to_session(tmp_path):
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json"))])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, timeout_seconds=3.5, session=session, sleep=lambda _: None)
    client.get_submissions("320193")
    assert session.calls[0][2] == 3.5

def test_endpoint_urls_normalize_cik(tmp_path):
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json")), FakeResponse(load_json("aapl_companyfacts.json"))])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, session=session, sleep=lambda _: None)
    client.get_submissions("320193")
    client.get_company_facts("0000320193")
    assert session.calls[0][0].endswith("/CIK0000320193.json")
    assert "/companyfacts/CIK0000320193.json" in session.calls[1][0]

def test_form4_fetch_uses_archive_url_and_final_transaction_limit(tmp_path):
    xml = (FIXTURES / "aapl_form4.xml").read_text(encoding="utf-8")
    second = xml.replace("</nonDerivativeTable>", xml[xml.index("<nonDerivativeTransaction>"):xml.index("</nonDerivativeTable>")].replace("2026-09-01", "2026-09-02") + "</nonDerivativeTable>")
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json")), FakeResponse(text=second)])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, session=session, sleep=lambda _: None)
    rows = client.get_form4_transactions("320193", "2026-09-17", limit=1)
    assert len(rows) == 1
    assert "/Archives/edgar/data/320193/000032019326000003/form4.xml" in session.calls[1][0]

def test_transient_http_status_retries_with_exponential_backoff(tmp_path):
    session = FakeSession([StatusResponse(429), StatusResponse(500), FakeResponse(load_json("aapl_submissions.json"))])
    sleeps = []
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, request_interval_seconds=0, session=session, sleep=sleeps.append, retry_backoff_seconds=.1)
    assert client.get_submissions("320193")["name"] == "Apple Inc."
    assert sleeps == [.1, .2]

def test_transient_request_exception_retries_then_succeeds(tmp_path):
    session = FakeSession([requests.ConnectionError("temporary"), FakeResponse(load_json("aapl_submissions.json"))])
    sleeps = []
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, request_interval_seconds=0, session=session, sleep=sleeps.append, retry_backoff_seconds=.25)
    assert client.get_submissions("320193")["name"] == "Apple Inc."
    assert sleeps == [.25]

def test_non_retryable_4xx_fails_without_retry(tmp_path):
    session = FakeSession([StatusResponse(403)])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, request_interval_seconds=0, session=session, sleep=lambda _: None)
    with pytest.raises(requests.HTTPError): client.get_submissions("320193")
    assert len(session.calls) == 1

def test_form4_limit_walks_filings_until_enough_transactions(tmp_path):
    xml = (FIXTURES / "aapl_form4.xml").read_text(encoding="utf-8")
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json")), FakeResponse(text="<ownershipDocument/>"), FakeResponse(text=xml)])
    client = SecEdgarClient("QuantConclave admin@example.com", tmp_path, request_interval_seconds=0, session=session, sleep=lambda _: None)
    rows = client.get_form4_transactions("320193", "2026-09-17", limit=1)
    assert len(rows) == 1 and len(session.calls) == 3
