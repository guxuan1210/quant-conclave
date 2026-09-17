# Native US Market Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class, point-in-time-safe single-stock US equity deep analysis while preserving the existing A-share workflow.

**Architecture:** Resolve every ticker into a serializable `InstrumentProfile`, build one serializable `EvidencePack` before graph execution, and inject both into LangGraph state. Market-specific agents select tools and prompts from the profile; SEC EDGAR is the authoritative US filing source, with existing yfinance functions used as explicit fallbacks.

**Tech Stack:** Python 3.10+, dataclasses, requests, xml.etree.ElementTree, LangGraph state dictionaries, yfinance, pytest.

---

## File Map

New files:

- `quantconclave/instruments/__init__.py` — public exports for instrument types and resolver.
- `quantconclave/instruments/models.py` — market enums and immutable `InstrumentProfile`.
- `quantconclave/instruments/resolver.py` — deterministic ticker classification and optional metadata enrichment.
- `quantconclave/evidence/__init__.py` — public evidence exports.
- `quantconclave/evidence/models.py` — immutable `EvidenceItem` and `EvidencePack`, serialization, quality summary.
- `quantconclave/evidence/builder.py` — per-run evidence orchestration and fallback policy.
- `quantconclave/dataflows/sec_edgar.py` — SEC identity, submissions, Company Facts, and Form 4 adapter.
- `quantconclave/dataflows/us_market.py` — US quote, holdings, analyst, and benchmark evidence wrappers.
- `tests/fixtures/sec/company_tickers.json` — minimal deterministic SEC ticker map.
- `tests/fixtures/sec/aapl_submissions.json` — filing metadata with before/after cutoff cases.
- `tests/fixtures/sec/aapl_companyfacts.json` — XBRL facts with multiple filing dates.
- `tests/fixtures/sec/aapl_form4.xml` — representative ownership transaction document.
- `tests/test_instrument_profiles.py` — resolver and serialization tests.
- `tests/test_evidence_models.py` — evidence status, summary, and serialization tests.
- `tests/test_sec_edgar.py` — offline SEC parsing, cache, headers, and cutoff tests.
- `tests/test_evidence_builder.py` — required evidence, fallback, and no-data semantics.
- `tests/test_us_agent_routing.py` — market-specific tools and prompt tests.
- `tests/test_us_market_workflow.py` — fixed-pack graph state and persistence tests.

Modified files:

- `quantconclave/default_config.py` — SEC identity, cache, and request pacing settings.
- `.env.example` — documented SEC `User-Agent` configuration.
- `quantconclave/agents/utils/agent_states.py` — serialized profile and evidence fields.
- `quantconclave/graph/propagation.py` — initial-state arguments and defaults.
- `quantconclave/graph/trading_graph.py` — resolve/build before full and partial runs; persist provenance.
- `quantconclave/agents/utils/agent_utils.py` — state-aware instrument/evidence context builder.
- `quantconclave/agents/analysts/capital_flow_analyst.py` — US positioning prompt and tool routing.
- `quantconclave/agents/analysts/fundamentals_analyst.py` — SEC-aware, market-specific tools.
- `quantconclave/agents/analysts/market_analyst.py` — shared evidence context.
- `quantconclave/agents/analysts/news_analyst.py` — shared evidence context and no-data wording.
- `quantconclave/agents/analysts/sentiment_analyst.py` — US/CN source routing and shared evidence context.
- `quantconclave/agents/analysts/competitor_analyst.py` — market-aware context.
- `quantconclave/agents/analysts/partner_analyst.py` — market-aware context.
- `quantconclave/agents/managers/research_manager.py` — freshness-aware evidence context.
- `quantconclave/agents/managers/portfolio_manager.py` — freshness-aware evidence context.
- `quantconclave/agents/trader/trader.py` — market-aware currency and benchmark context.
- `README.md` and `README.zh-CN.md` — first-release US support and configuration.

No database schema migration is required. The existing JSON state files carry the optional provenance fields, and older files remain readable.

### Task 1: Add the instrument domain model and resolver

**Files:**
- Create: `quantconclave/instruments/__init__.py`
- Create: `quantconclave/instruments/models.py`
- Create: `quantconclave/instruments/resolver.py`
- Create: `tests/test_instrument_profiles.py`

- [ ] **Step 1: Write resolver tests that describe the supported market semantics**

```python
from quantconclave.instruments import Market, resolve_instrument


def test_resolves_plain_us_equity():
    profile = resolve_instrument("aapl")
    assert profile.symbol == "AAPL"
    assert profile.market is Market.US
    assert profile.currency == "USD"
    assert profile.timezone == "America/New_York"
    assert profile.calendar == "XNYS"
    assert profile.benchmark == "SPY"


def test_preserves_us_class_share_symbol():
    profile = resolve_instrument("BRK.B")
    assert profile.symbol == "BRK.B"
    assert profile.market is Market.US


def test_preserves_existing_a_share_resolution():
    profile = resolve_instrument("600519.SS")
    assert profile.symbol == "600519.SH"
    assert profile.market is Market.CN
    assert profile.currency == "CNY"


def test_does_not_misclassify_known_international_suffix():
    profile = resolve_instrument("7203.T")
    assert profile.market is Market.JP
    assert profile.benchmark == "^N225"


def test_profile_round_trip_is_json_safe():
    profile = resolve_instrument("NVDA")
    assert type(profile).from_dict(profile.to_dict()) == profile
```

- [ ] **Step 2: Run the tests and confirm the package is missing**

Run: `pytest tests/test_instrument_profiles.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'quantconclave.instruments'`.

- [ ] **Step 3: Add immutable market and profile types**

```python
# quantconclave/instruments/models.py
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Market(str, Enum):
    CN = "CN"
    US = "US"
    HK = "HK"
    JP = "JP"
    IN = "IN"
    GB = "GB"
    CA = "CA"
    AU = "AU"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str
    asset_type: str
    market: Market
    exchange: str
    currency: str
    timezone: str
    calendar: str
    benchmark: str
    sector_taxonomy: str
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["market"] = self.market.value
        data["capabilities"] = list(self.capabilities)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstrumentProfile":
        values = dict(data)
        values["market"] = Market(values["market"])
        values["capabilities"] = tuple(values.get("capabilities", ()))
        return cls(**values)
```

- [ ] **Step 4: Implement deterministic resolution without network access**

```python
# quantconclave/instruments/resolver.py
import re

from quantconclave.dataflows.ticker_utils import normalize_symbol
from .models import InstrumentProfile, Market

_US = re.compile(r"^[A-Z][A-Z0-9-]{0,5}(?:\.[A-Z])?$")

_SUFFIXES = {
    ".HK": (Market.HK, "HKEX", "HKD", "Asia/Hong_Kong", "XHKG", "^HSI"),
    ".T": (Market.JP, "TSE", "JPY", "Asia/Tokyo", "XTKS", "^N225"),
    ".NS": (Market.IN, "NSE", "INR", "Asia/Kolkata", "XNSE", "^NSEI"),
    ".BO": (Market.IN, "BSE", "INR", "Asia/Kolkata", "XBOM", "^BSESN"),
    ".L": (Market.GB, "LSE", "GBP", "Europe/London", "XLON", "^FTSE"),
    ".TO": (Market.CA, "TSX", "CAD", "America/Toronto", "XTSE", "^GSPTSE"),
    ".AX": (Market.AU, "ASX", "AUD", "Australia/Sydney", "XASX", "^AXJO"),
}


def resolve_instrument(symbol: str, asset_type: str = "stock") -> InstrumentProfile:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A non-empty ticker symbol is required")
    normalized = normalize_symbol(symbol)
    if normalized.endswith((".SH", ".SZ", ".BJ")):
        exchange = normalized.rsplit(".", 1)[1]
        return InstrumentProfile(
            normalized, asset_type, Market.CN, exchange, "CNY",
            "Asia/Shanghai", "XSHG" if exchange == "SH" else "XSHE",
            "000300.SH", "CITICS", ("cn_money_flow", "cn_filings"),
        )
    for suffix, values in _SUFFIXES.items():
        if normalized.endswith(suffix):
            market, exchange, currency, timezone, calendar, benchmark = values
            return InstrumentProfile(
                normalized, asset_type, market, exchange, currency, timezone,
                calendar, benchmark, "GICS", ("price",),
            )
    if _US.fullmatch(normalized):
        return InstrumentProfile(
            normalized, asset_type, Market.US, "US", "USD",
            "America/New_York", "XNYS", "SPY", "GICS",
            ("sec_filings", "form4", "institutional_holders", "analyst_ratings"),
        )
    raise ValueError(f"Unable to resolve market for ticker '{symbol}'")
```

Ticker syntax alone does not reliably distinguish NYSE from Nasdaq. The deterministic resolver therefore uses exchange `US`; the Evidence Builder may replace it with provider metadata in the serialized run profile when authoritative exchange metadata is available. Tests must not guess an exchange from the ticker spelling.

- [ ] **Step 5: Export the public API and run the focused tests**

```python
# quantconclave/instruments/__init__.py
from .models import InstrumentProfile, Market
from .resolver import resolve_instrument

__all__ = ["InstrumentProfile", "Market", "resolve_instrument"]
```

Run: `pytest tests/test_instrument_profiles.py tests/test_ticker_utils.py -v`

Expected: all tests pass, including existing A-share normalization tests.

- [ ] **Step 6: Commit the instrument layer**

```bash
git add quantconclave/instruments tests/test_instrument_profiles.py
git commit -m "feat: add market-aware instrument profiles"
```

### Task 2: Add evidence models and compact prompt summaries

**Files:**
- Create: `quantconclave/evidence/__init__.py`
- Create: `quantconclave/evidence/models.py`
- Create: `tests/test_evidence_models.py`

- [ ] **Step 1: Write evidence semantics tests**

```python
from quantconclave.evidence import EvidenceItem, EvidencePack, EvidenceStatus


def test_zero_value_is_available_not_no_data():
    item = EvidenceItem(
        kind="net_income", source="sec_companyfacts", as_of="2026-06-30",
        fetched_at="2026-09-17T00:00:00Z", status=EvidenceStatus.AVAILABLE,
        payload={"value": 0},
    )
    assert item.status is EvidenceStatus.AVAILABLE
    assert item.payload["value"] == 0


def test_pack_quality_is_degraded_when_required_fallback_is_used():
    pack = EvidencePack(
        symbol="AAPL", analysis_date="2026-09-17",
        items=(EvidenceItem(
            kind="financials", source="yfinance", as_of="2026-09-17",
            fetched_at="2026-09-17T00:00:00Z",
            status=EvidenceStatus.DEGRADED, payload={"reason": "SEC unavailable"},
        ),),
    )
    assert pack.quality == "degraded"


def test_summary_calls_missing_data_unavailable_not_neutral():
    pack = EvidencePack(
        symbol="AAPL", analysis_date="2026-09-17",
        items=(EvidenceItem(
            kind="insider_transactions", source="sec_form4", as_of="2026-09-17",
            fetched_at="2026-09-17T00:00:00Z",
            status=EvidenceStatus.NO_DATA, payload={},
        ),),
    )
    summary = pack.to_prompt_summary()
    assert "NO_DATA" in summary
    assert "neutral" not in summary.lower()


def test_pack_round_trip_is_json_safe():
    pack = EvidencePack(symbol="NVDA", analysis_date="2026-09-17", items=())
    assert EvidencePack.from_dict(pack.to_dict()) == pack
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `pytest tests/test_evidence_models.py -v`

Expected: collection fails because `quantconclave.evidence` does not exist.

- [ ] **Step 3: Implement immutable evidence types and serialization**

```python
# quantconclave/evidence/models.py
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvidenceStatus(str, Enum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    DEGRADED = "degraded"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    source: str
    as_of: str
    fetched_at: str
    status: EvidenceStatus
    payload: dict[str, Any]
    period_end: str = ""
    filed_at: str = ""
    freshness: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "status": self.status.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        values = dict(data)
        values["status"] = EvidenceStatus(values["status"])
        return cls(**values)


@dataclass(frozen=True)
class EvidencePack:
    symbol: str
    analysis_date: str
    items: tuple[EvidenceItem, ...]

    @property
    def quality(self) -> str:
        statuses = {item.status for item in self.items}
        if EvidenceStatus.ERROR in statuses or EvidenceStatus.DEGRADED in statuses:
            return "degraded"
        if EvidenceStatus.STALE in statuses:
            return "stale"
        return "complete"

    def get(self, kind: str) -> EvidenceItem | None:
        return next((item for item in self.items if item.kind == kind), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "analysis_date": self.analysis_date,
            "quality": self.quality,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidencePack":
        return cls(
            symbol=data["symbol"], analysis_date=data["analysis_date"],
            items=tuple(EvidenceItem.from_dict(item) for item in data.get("items", [])),
        )

    def to_prompt_summary(self, max_payload_chars: int = 1200) -> str:
        lines = [f"Evidence snapshot for {self.symbol} as of {self.analysis_date}:"]
        for item in self.items:
            payload = str(item.payload)[:max_payload_chars]
            lines.append(
                f"- {item.kind}: {item.status.value.upper()} | source={item.source} "
                f"| as_of={item.as_of or 'unknown'} | filed_at={item.filed_at or 'n/a'} "
                f"| data={payload}"
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Add exports and run the model tests**

```python
# quantconclave/evidence/__init__.py
from .models import EvidenceItem, EvidencePack, EvidenceStatus

__all__ = ["EvidenceItem", "EvidencePack", "EvidenceStatus"]
```

Run: `pytest tests/test_evidence_models.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit evidence models**

```bash
git add quantconclave/evidence tests/test_evidence_models.py
git commit -m "feat: add shared evidence snapshot models"
```

### Task 3: Implement the SEC EDGAR adapter with point-in-time filtering

**Files:**
- Create: `quantconclave/dataflows/sec_edgar.py`
- Create: `tests/fixtures/sec/company_tickers.json`
- Create: `tests/fixtures/sec/aapl_submissions.json`
- Create: `tests/fixtures/sec/aapl_companyfacts.json`
- Create: `tests/fixtures/sec/aapl_form4.xml`
- Create: `tests/test_sec_edgar.py`

- [ ] **Step 1: Add minimal SEC fixtures containing cutoff-sensitive records**

The ticker fixture must map AAPL to CIK `320193`. The submissions fixture must contain one 10-Q filed before `2026-09-17`, one 8-K filed after that date, and one Form 4 with an accession number and primary document. The Company Facts fixture must include two values for the same concept where only the earlier `filed` value is eligible. The Form 4 XML must contain one non-derivative purchase transaction with code `P`, shares, price, transaction date, reporting owner, and accession-linked issuer CIK.

- [ ] **Step 2: Write offline client and parsing tests**

```python
import json
from pathlib import Path

import pytest

from quantconclave.dataflows.sec_edgar import (
    SecEdgarClient, SecConfigurationError, select_company_facts,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sec"


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text
    def raise_for_status(self):
        return None
    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        return self.responses.pop(0)


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_requires_identifiable_sec_user_agent(tmp_path):
    with pytest.raises(SecConfigurationError):
        SecEdgarClient(user_agent="", cache_dir=tmp_path)


def test_sends_configured_user_agent(tmp_path):
    session = FakeSession([FakeResponse(load_json("company_tickers.json"))])
    client = SecEdgarClient(
        user_agent="QuantConclave admin@example.com", cache_dir=tmp_path,
        session=session, sleep=lambda _: None,
    )
    assert client.resolve_cik("AAPL") == "0000320193"
    assert session.calls[0][1]["User-Agent"] == "QuantConclave admin@example.com"


def test_filters_filings_by_publication_date(tmp_path):
    session = FakeSession([FakeResponse(load_json("aapl_submissions.json"))])
    client = SecEdgarClient(
        user_agent="QuantConclave admin@example.com", cache_dir=tmp_path,
        session=session, sleep=lambda _: None,
    )
    filings = client.get_filings("0000320193", "2026-09-17")
    assert all(row["filingDate"] <= "2026-09-17" for row in filings)
    assert not any(row["form"] == "8-K" and row["filingDate"] > "2026-09-17" for row in filings)


def test_company_facts_choose_latest_publicly_available_value():
    facts = load_json("aapl_companyfacts.json")
    selected = select_company_facts(facts, as_of="2026-09-17")
    assert selected["revenue"]["filed"] <= "2026-09-17"
    assert selected["revenue"]["value"] == 100


def test_form4_parser_returns_purchase_transaction(tmp_path):
    xml = (FIXTURES / "aapl_form4.xml").read_text(encoding="utf-8")
    client = SecEdgarClient(
        user_agent="QuantConclave admin@example.com", cache_dir=tmp_path,
        session=FakeSession([]), sleep=lambda _: None,
    )
    rows = client.parse_form4(xml)
    assert rows == [{
        "owner": "Example Officer", "transaction_date": "2026-09-01",
        "code": "P", "shares": 1000.0, "price": 225.5,
        "acquired_disposed": "A",
    }]
```

- [ ] **Step 3: Run tests and verify the adapter is absent**

Run: `pytest tests/test_sec_edgar.py -v`

Expected: collection fails because `quantconclave.dataflows.sec_edgar` does not exist.

- [ ] **Step 4: Implement the SEC client and cache contract**

Implement `SecEdgarClient` with this public interface:

```python
class SecConfigurationError(ValueError): ...

class SecEdgarClient:
    def __init__(
        self, user_agent: str, cache_dir: str | Path,
        request_interval_seconds: float = 0.12,
        timeout_seconds: float = 10.0,
        session=None, sleep=time.sleep, clock=time.monotonic,
    ): ...

    def resolve_cik(self, ticker: str) -> str: ...
    def get_submissions(self, cik: str) -> dict: ...
    def get_filings(self, cik: str, as_of: str, forms=("10-K", "10-Q", "8-K", "4")) -> list[dict]: ...
    def get_company_facts(self, cik: str) -> dict: ...
    def get_form4_transactions(self, cik: str, as_of: str, limit: int = 20) -> list[dict]: ...
    def parse_form4(self, xml_text: str) -> list[dict]: ...
```

Use these endpoints:

```python
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"
```

Cache each successful response under `cache_dir/sec/` using a SHA-256 URL key and an adjacent metadata JSON containing `fetched_at`. Read cache before the network. Do not cache HTTP failures. Pace network calls by comparing `clock()` with the prior request time and calling the injected `sleep` for the remaining interval.

- [ ] **Step 5: Implement point-in-time fact normalization**

```python
_CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    "net_income": ("NetIncomeLoss",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities", "LiabilitiesAndStockholdersEquity"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
}


def select_company_facts(companyfacts: dict, as_of: str) -> dict[str, dict]:
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    selected = {}
    for normalized, candidates in _CONCEPTS.items():
        observations = []
        for concept in candidates:
            for unit_rows in us_gaap.get(concept, {}).get("units", {}).values():
                for row in unit_rows:
                    if row.get("filed", "") <= as_of:
                        observations.append((row.get("filed", ""), row.get("end", ""), concept, row))
        if observations:
            _, _, concept, row = max(observations, key=lambda value: (value[0], value[1]))
            selected[normalized] = {
                "value": row.get("val"), "filed": row.get("filed", ""),
                "period_end": row.get("end", ""), "form": row.get("form", ""),
                "concept": concept, "accession": row.get("accn", ""),
            }
    return selected
```

- [ ] **Step 6: Run adapter tests and the existing dataflow tests**

Run: `pytest tests/test_sec_edgar.py tests/test_dataflows_config.py tests/test_ticker_utils.py -v`

Expected: all tests pass without network access.

- [ ] **Step 7: Commit the SEC adapter**

```bash
git add quantconclave/dataflows/sec_edgar.py tests/fixtures/sec tests/test_sec_edgar.py
git commit -m "feat: add point-in-time SEC EDGAR adapter"
```

### Task 4: Build US market evidence with explicit fallback semantics

**Files:**
- Create: `quantconclave/dataflows/us_market.py`
- Create: `quantconclave/evidence/builder.py`
- Modify: `quantconclave/evidence/__init__.py`
- Create: `tests/test_evidence_builder.py`

- [ ] **Step 1: Write builder tests with injected providers**

```python
from quantconclave.evidence import EvidenceStatus
from quantconclave.evidence.builder import build_evidence_pack
from quantconclave.instruments import resolve_instrument


class FakeSec:
    def resolve_cik(self, ticker): return "0000320193"
    def get_filings(self, cik, as_of):
        return [{"form": "10-Q", "filingDate": "2026-08-01", "accessionNumber": "x"}]
    def get_company_facts(self, cik):
        return {"facts": {"us-gaap": {}}}
    def get_form4_transactions(self, cik, as_of): return []


def test_builds_required_identity_and_price_items():
    pack = build_evidence_pack(
        resolve_instrument("AAPL"), "2026-09-17", {}, sec_client=FakeSec(),
        market_fetchers={
            "price_history": lambda *_: {"rows": 60},
            "quote": lambda *_: {"price": 225.0},
            "holders": lambda *_: {},
            "analyst_ratings": lambda *_: {},
        },
    )
    assert pack.get("company_identity").status is EvidenceStatus.AVAILABLE
    assert pack.get("price_history").status is EvidenceStatus.AVAILABLE
    assert pack.get("insider_transactions").status is EvidenceStatus.NO_DATA


def test_sec_failure_uses_yfinance_financial_fallback():
    class BrokenSec:
        def resolve_cik(self, ticker): raise ConnectionError("offline")
    pack = build_evidence_pack(
        resolve_instrument("AAPL"), "2026-09-17", {}, sec_client=BrokenSec(),
        market_fetchers={
            "identity": lambda *_: {"name": "Apple Inc.", "source": "yfinance"},
            "price_history": lambda *_: {"rows": 60},
            "quote": lambda *_: {"price": 225.0},
            "financials": lambda *_: {"source": "yfinance"},
            "holders": lambda *_: {},
            "analyst_ratings": lambda *_: {},
        },
    )
    financials = pack.get("financials")
    assert financials.status is EvidenceStatus.DEGRADED
    assert financials.source == "yfinance"


def test_missing_required_price_raises_clear_error():
    import pytest
    with pytest.raises(RuntimeError, match="required price history"):
        build_evidence_pack(
            resolve_instrument("AAPL"), "2026-09-17", {}, sec_client=FakeSec(),
            market_fetchers={"price_history": lambda *_: None},
        )
```

- [ ] **Step 2: Run tests and confirm the builder is missing**

Run: `pytest tests/test_evidence_builder.py -v`

Expected: collection fails because `quantconclave.evidence.builder` does not exist.

- [ ] **Step 3: Add provider wrappers that return structured payloads**

`quantconclave/dataflows/us_market.py` must expose:

```python
def fetch_price_history(symbol: str, analysis_date: str, lookback_days: int = 120) -> dict | None: ...
def fetch_identity(symbol: str, analysis_date: str) -> dict | None: ...
def fetch_quote(symbol: str, analysis_date: str) -> dict | None: ...
def fetch_yfinance_financials(symbol: str, analysis_date: str) -> dict | None: ...
def fetch_holders(symbol: str, analysis_date: str) -> dict | None: ...
def fetch_analyst_ratings(symbol: str, analysis_date: str) -> dict | None: ...
def fetch_benchmark_context(symbol: str, benchmark: str, analysis_date: str) -> dict | None: ...
```

Each wrapper must return JSON-safe dictionaries, filter dated rows to `analysis_date`, and return `None` for unavailable data. Reuse `_yf_ticker`, `yf_retry`, and existing OHLCV parsing instead of duplicating suffix or retry logic.

- [ ] **Step 4: Implement the builder with required and optional evidence rules**

```python
def build_evidence_pack(
    profile, analysis_date: str, config: dict, *, sec_client=None,
    market_fetchers: dict | None = None,
) -> EvidencePack:
    """Build one JSON-safe, point-in-time evidence snapshot for a graph run."""
```

For `Market.US`, build items in this stable order:

1. `company_identity` — SEC ticker/CIK primary; degraded yfinance company identity fallback; required after fallback.
2. `price_history` — adjusted OHLCV; required.
3. `quote` — optional.
4. `filings` — SEC 10-K/10-Q/8-K metadata.
5. `financials` — normalized Company Facts or degraded yfinance fallback.
6. `insider_transactions` — SEC Form 4; empty list becomes `NO_DATA`.
7. `institutional_holders` — yfinance; empty becomes `NO_DATA`.
8. `analyst_ratings` — yfinance; empty becomes `NO_DATA`.
9. `benchmark_context` — SPY/QQQ-relative performance.

If both SEC and yfinance identity resolution fail, raise `RuntimeError("required company identity unavailable for <symbol>")`. For non-US profiles in this release, return a minimal pack containing identity and price items without changing the existing A-share tool behavior.

- [ ] **Step 5: Export the builder and run tests**

```python
# quantconclave/evidence/__init__.py
from .builder import build_evidence_pack
from .models import EvidenceItem, EvidencePack, EvidenceStatus

__all__ = ["EvidenceItem", "EvidencePack", "EvidenceStatus", "build_evidence_pack"]
```

Run: `pytest tests/test_evidence_builder.py tests/test_evidence_models.py tests/test_ohlcv_turnover.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit evidence collection**

```bash
git add quantconclave/dataflows/us_market.py quantconclave/evidence tests/test_evidence_builder.py
git commit -m "feat: build shared US market evidence packs"
```

### Task 5: Add SEC configuration and inject profiles/evidence into graph state

**Files:**
- Modify: `quantconclave/default_config.py`
- Modify: `.env.example`
- Modify: `quantconclave/agents/utils/agent_states.py`
- Modify: `quantconclave/graph/propagation.py`
- Modify: `quantconclave/graph/trading_graph.py`
- Modify: `tests/test_env_overrides.py`
- Modify: `tests/test_memory_log.py`

- [ ] **Step 1: Add failing config and state tests**

```python
def test_sec_user_agent_env_override(monkeypatch):
    monkeypatch.setenv("QUANTCONCLAVE_SEC_USER_AGENT", "QuantConclave owner@example.com")
    from quantconclave.default_config import _apply_env_overrides, DEFAULT_CONFIG
    config = _apply_env_overrides(dict(DEFAULT_CONFIG))
    assert config["sec_user_agent"] == "QuantConclave owner@example.com"
```

Add to the propagation tests:

```python
def test_initial_state_carries_serialized_profile_and_evidence():
    state = Propagator().create_initial_state(
        "AAPL", "2026-09-17",
        instrument_profile={"symbol": "AAPL", "market": "US"},
        evidence_pack={"symbol": "AAPL", "quality": "complete", "items": []},
    )
    assert state["instrument_profile"]["market"] == "US"
    assert state["evidence_pack"]["quality"] == "complete"
```

- [ ] **Step 2: Run the focused tests and verify signature/config failures**

Run: `pytest tests/test_env_overrides.py tests/test_memory_log.py -v`

Expected: the new assertions fail because the config keys and propagation parameters do not exist.

- [ ] **Step 3: Add configuration keys and environment documentation**

Add these mappings and defaults:

```python
_ENV_OVERRIDES.update({
    "QUANTCONCLAVE_SEC_USER_AGENT": "sec_user_agent",
    "QUANTCONCLAVE_SEC_REQUEST_INTERVAL_SECONDS": "sec_request_interval_seconds",
    "QUANTCONCLAVE_SEC_TIMEOUT_SECONDS": "sec_timeout_seconds",
})

# Inside DEFAULT_CONFIG
"sec_user_agent": _getenv("QUANTCONCLAVE_SEC_USER_AGENT", ""),
"sec_request_interval_seconds": 0.12,
"sec_timeout_seconds": 10.0,
```

Add this example without a real address:

```dotenv
# Required for live SEC EDGAR access. Use your application name and monitored email.
QUANTCONCLAVE_SEC_USER_AGENT=QuantConclave your-email@example.com
```

- [ ] **Step 4: Extend serializable graph state**

Add to `AgentState`:

```python
instrument_profile: Annotated[dict, "Resolved JSON-safe instrument profile"]
evidence_pack: Annotated[dict, "Shared JSON-safe evidence snapshot"]
```

Extend `Propagator.create_initial_state` with optional `instrument_profile` and `evidence_pack` dictionaries and store `{}` when omitted so old callers and tests remain compatible.

- [ ] **Step 5: Resolve and build once for both full and partial graph entry points**

Add a focused helper to `QuantConclaveGraph`:

```python
def _prepare_market_context(self, ticker: str, trade_date: str, asset_type: str) -> tuple[dict, dict]:
    from quantconclave.instruments import resolve_instrument
    from quantconclave.evidence import build_evidence_pack

    profile = resolve_instrument(ticker, asset_type=asset_type)
    pack = build_evidence_pack(profile, str(trade_date), self.config)
    return profile.to_dict(), pack.to_dict()
```

Call it once in `_run_graph` and once in `analyze_partial`, then pass both dictionaries into `create_initial_state`. Do not resolve/build inside individual agents.

- [ ] **Step 6: Run config, state, checkpoint, and memory tests**

Run: `pytest tests/test_env_overrides.py tests/test_memory_log.py tests/test_checkpoint_resume.py tests/test_crypto_asset_mode.py -v`

Expected: all tests pass. Tests that instantiate the graph must monkeypatch `_prepare_market_context` with fixed dictionaries so the suite remains offline.

- [ ] **Step 7: Commit graph integration**

```bash
git add .env.example quantconclave/default_config.py quantconclave/agents/utils/agent_states.py quantconclave/graph/propagation.py quantconclave/graph/trading_graph.py tests/test_env_overrides.py tests/test_memory_log.py
git commit -m "feat: inject market profiles and evidence into analysis state"
```

### Task 6: Make agent context and tool routing market-aware

**Files:**
- Modify: `quantconclave/agents/utils/agent_utils.py`
- Modify: `quantconclave/agents/analysts/capital_flow_analyst.py`
- Modify: `quantconclave/agents/analysts/fundamentals_analyst.py`
- Modify: `quantconclave/agents/analysts/market_analyst.py`
- Modify: `quantconclave/agents/analysts/news_analyst.py`
- Modify: `quantconclave/agents/analysts/sentiment_analyst.py`
- Modify: `quantconclave/agents/analysts/competitor_analyst.py`
- Modify: `quantconclave/agents/analysts/partner_analyst.py`
- Modify: `quantconclave/agents/managers/research_manager.py`
- Modify: `quantconclave/agents/managers/portfolio_manager.py`
- Modify: `quantconclave/agents/trader/trader.py`
- Create: `tests/test_us_agent_routing.py`

- [ ] **Step 1: Write tests for state-aware context and market-specific tool sets**

```python
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


def test_us_fundamental_tools_exclude_eastmoney():
    from quantconclave.agents.analysts.fundamentals_analyst import _tools_for_market
    names = {tool.name for tool in _tools_for_market("US")}
    assert "get_eastmoney_fundamentals" not in names
    assert "get_eastmoney_data" not in names


def test_cn_fundamental_tools_keep_eastmoney():
    from quantconclave.agents.analysts.fundamentals_analyst import _tools_for_market
    names = {tool.name for tool in _tools_for_market("CN")}
    assert "get_eastmoney_fundamentals" in names
```

- [ ] **Step 2: Run the routing tests and confirm helpers are absent**

Run: `pytest tests/test_us_agent_routing.py -v`

Expected: import errors for the new helpers.

- [ ] **Step 3: Add a single state-aware context helper**

```python
def build_state_instrument_context(state: dict) -> str:
    base = build_instrument_context(
        state["company_of_interest"], state.get("asset_type", "stock")
    )
    profile = state.get("instrument_profile") or {}
    pack_data = state.get("evidence_pack") or {}
    profile_line = (
        f"Market profile: market={profile.get('market', 'UNKNOWN')}, "
        f"exchange={profile.get('exchange', 'unknown')}, "
        f"currency={profile.get('currency', 'unknown')}, "
        f"timezone={profile.get('timezone', 'unknown')}, "
        f"benchmark={profile.get('benchmark', 'unknown')}."
    )
    evidence = EvidencePack.from_dict(pack_data).to_prompt_summary() if pack_data else "No shared evidence snapshot."
    return f"{base}\n{profile_line}\n{evidence}"
```

Update every listed analyst, manager, and trader to call this helper with `state`. Keep `build_instrument_context` for compatibility with external callers.

- [ ] **Step 4: Extract market-specific capital-flow prompt and tool selection**

Add pure helpers:

```python
def _market_code(state: dict) -> str:
    return str((state.get("instrument_profile") or {}).get("market", "UNKNOWN"))


def _tools_for_market(market: str) -> list:
    if market == "CN":
        return [get_money_flow, get_hsgt_flow, get_market_flow, get_margin_trading,
                get_dragon_tiger_list, get_share_pledge, get_share_unlock,
                get_stock_buyback, get_holder_changes, get_realtime_quote,
                get_intraday_data, get_indicators, get_eastmoney_money_flow,
                get_eastmoney_quote, get_eastmoney_block_trades, web_search]
    if market == "US":
        return [get_institutional_holders, get_major_holders,
                get_analyst_recommendations, get_insider_transactions,
                get_realtime_quote, get_intraday_data, get_indicators, web_search]
    return [get_realtime_quote, get_intraday_data, get_indicators, web_search]
```

Move the existing CN prompt into the `CN` branch unchanged. Write a separate US prompt that:

- treats price/volume as current positioning evidence;
- treats Form 4 as event evidence;
- labels 13F as delayed quarterly holdings;
- prohibits equating daily short-sale volume with short interest;
- uses `NO_DATA` as unavailable, not neutral;
- makes no claim about real-time institutional buying from holder snapshots.

Call `_fetch_market_context()` only for `CN`.

- [ ] **Step 5: Route fundamentals and sentiment sources by market**

For fundamentals, create `_tools_for_market(market)` and include Eastmoney tools only for `CN`. SEC evidence is already present in the shared context, so no new LLM tool is required in this task.

For sentiment, build the executor map conditionally:

```python
fetchers = {
    _exec.submit(get_news.func, ticker, start_date, end_date): "news",
    _exec.submit(fetch_stocktwits_messages, ticker, 30): "stocktwits",
    _exec.submit(fetch_reddit_posts, ticker): "reddit",
    _exec.submit(_safe_fetch_realtime, ticker): "realtime",
}
if market == "CN":
    fetchers[_exec.submit(_safe_fetch_xueqiu, ticker)] = "xueqiu"
    fetchers[_exec.submit(_safe_fetch_guba, ticker)] = "guba"
```

The prompt must say `NO_DATA` means insufficient evidence and must not be converted to neutral sentiment.

- [ ] **Step 6: Run agent and structured-output regressions**

Run: `pytest tests/test_us_agent_routing.py tests/test_structured_agents.py tests/test_analyst_execution.py tests/test_parallel_analyst_runner.py -v`

Expected: all tests pass; existing CN tool assertions remain unchanged.

- [ ] **Step 7: Commit market-aware agents**

```bash
git add quantconclave/agents tests/test_us_agent_routing.py
git commit -m "feat: route analyst evidence by market"
```

### Task 7: Persist provenance without a schema migration

**Files:**
- Modify: `quantconclave/graph/trading_graph.py`
- Modify: `tests/test_memory_log.py`
- Create: `tests/test_us_market_workflow.py`

- [ ] **Step 1: Write JSON-state compatibility tests**

```python
def test_log_state_persists_profile_and_evidence_provenance(tmp_path):
    graph = make_test_graph(tmp_path)
    state = make_complete_state()
    state["instrument_profile"] = {"symbol": "AAPL", "market": "US", "benchmark": "SPY"}
    state["evidence_pack"] = {
        "symbol": "AAPL", "quality": "degraded", "analysis_date": "2026-09-17",
        "items": [{"kind": "financials", "source": "yfinance", "status": "degraded"}],
    }
    graph.ticker = "AAPL"
    graph._log_state("2026-09-17", state)
    saved = graph._load_last_state("AAPL")
    assert saved["instrument_profile"]["market"] == "US"
    assert saved["evidence_quality"] == "degraded"
    assert saved["evidence_sources"] == ["yfinance"]


def test_old_state_without_provenance_remains_loadable(tmp_path):
    path = write_legacy_state(tmp_path, ticker="AAPL")
    loaded = load_json(path)
    assert loaded.get("instrument_profile", {}) == {}
```

- [ ] **Step 2: Run tests and verify provenance is absent**

Run: `pytest tests/test_us_market_workflow.py tests/test_memory_log.py -v`

Expected: the new provenance assertions fail.

- [ ] **Step 3: Extend `_log_state` with bounded provenance fields**

Add:

```python
profile = final_state.get("instrument_profile") or {}
pack = final_state.get("evidence_pack") or {}
items = pack.get("items") or []

logged_state.update({
    "instrument_profile": profile,
    "evidence_as_of": pack.get("analysis_date", ""),
    "evidence_quality": pack.get("quality", ""),
    "evidence_sources": sorted({item.get("source", "") for item in items if item.get("source")}),
    "evidence_pack": pack,
})
```

Refactor the current literal assigned to `self.log_states_dict[str(trade_date)]` into a local `logged_state` dictionary before adding these fields. Do not add columns to `result_runs`; the existing `json_path` remains the lookup route.

- [ ] **Step 4: Run persistence and history tests**

Run: `pytest tests/test_us_market_workflow.py tests/test_memory_log.py tests/test_results_store_pick_resolve.py tests/test_workspace_store.py -v`

Expected: all tests pass, including legacy-state reads.

- [ ] **Step 5: Commit provenance persistence**

```bash
git add quantconclave/graph/trading_graph.py tests/test_memory_log.py tests/test_us_market_workflow.py
git commit -m "feat: persist market evidence provenance"
```

### Task 8: Add fixed-pack workflow acceptance tests and documentation

**Files:**
- Modify: `tests/test_us_market_workflow.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Add the three-ticker acceptance matrix**

```python
import pytest


@pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "BRK.B"])
def test_us_ticker_preparation_is_serializable_and_spy_benchmarked(ticker):
    profile = resolve_instrument(ticker)
    pack = fixed_evidence_pack(ticker)
    state = Propagator().create_initial_state(
        ticker, "2026-09-17",
        instrument_profile=profile.to_dict(), evidence_pack=pack.to_dict(),
    )
    json.dumps(state, default=str)
    assert state["instrument_profile"]["benchmark"] == "SPY"
    assert state["evidence_pack"]["symbol"] == ticker


def test_all_agent_contexts_share_the_same_core_financial_value():
    state = fixed_us_state(revenue=100)
    contexts = [build_state_instrument_context(dict(state)) for _ in range(7)]
    assert all("'revenue': 100" in context for context in contexts)


def test_us_context_contains_no_cn_only_terms():
    context = build_state_instrument_context(fixed_us_state(revenue=100))
    assert "北向资金" not in context
    assert "龙虎榜" not in context
```

- [ ] **Step 2: Run the acceptance tests**

Run: `pytest tests/test_us_market_workflow.py -v`

Expected: all tests pass without network or LLM calls.

- [ ] **Step 3: Document the first-release behavior and limitations**

Add to both READMEs:

- supported examples: AAPL, NVDA, BRK.B;
- SEC `User-Agent` configuration requirement;
- SEC/yfinance source roles;
- US positioning evidence is not equivalent to A-share main-force flow;
- 13F is delayed quarterly evidence;
- US AI Pick, market-wide screening, and sector rotation are not included in this release;
- research-only and data-latency disclaimer remains in force.

- [ ] **Step 4: Run the full relevant regression suite**

Run:

```bash
pytest tests/test_instrument_profiles.py tests/test_evidence_models.py tests/test_sec_edgar.py tests/test_evidence_builder.py tests/test_us_agent_routing.py tests/test_us_market_workflow.py tests/test_ticker_utils.py tests/test_dataflows_config.py tests/test_structured_agents.py tests/test_memory_log.py tests/test_crypto_asset_mode.py -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Run the full test suite**

Run: `pytest`

Expected: all tests pass. Integration-marked tests may be skipped when external credentials are absent; there must be no new failures.

- [ ] **Step 6: Perform a manual live smoke test when SEC identity is configured**

Run:

```bash
python -c "from quantconclave.instruments import resolve_instrument; from quantconclave.evidence import build_evidence_pack; from quantconclave.default_config import DEFAULT_CONFIG; p=resolve_instrument('AAPL'); e=build_evidence_pack(p, '2026-09-17', DEFAULT_CONFIG); print(p.to_dict()); print(e.quality); print([i.kind + ':' + i.status.value for i in e.items])"
```

Expected: profile reports `market=US`, `currency=USD`, and `benchmark=SPY`; evidence includes identity, price, filings, financials, insider, holders, analyst, and benchmark entries with explicit statuses. If SEC is unavailable, financials are marked `degraded` rather than silently treated as complete.

- [ ] **Step 7: Commit documentation and acceptance coverage**

```bash
git add README.md README.zh-CN.md tests/test_us_market_workflow.py
git commit -m "docs: document native US equity analysis"
```

### Task 9: Final verification and scope audit

**Files:**
- Verify only; no planned production edits.

- [ ] **Step 1: Confirm no deferred US screening work entered the change set**

Run:

```bash
git diff --name-only HEAD~8..HEAD
```

Expected: no functional changes under `quantconclave/sector_scan/`, no US universe management, and no changes that make `web/ai_pick_agent.py` claim US-market support.

- [ ] **Step 2: Search for accidental CN-only instructions in the US branch**

Run:

```bash
rg -n "get_money_flow|北向资金|龙虎榜|Dragon-Tiger" quantconclave/agents/analysts/capital_flow_analyst.py
```

Expected: occurrences are confined to the explicit CN prompt/tool branch and its comments; the US helper test proves those terms are absent from `_market_instructions("US")`.

- [ ] **Step 3: Verify formatting and repository status**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` reports no whitespace errors. Only unrelated user-owned changes that predated this plan may remain uncommitted.

- [ ] **Step 4: Record verification evidence in the final handoff**

Report the exact focused-test and full-suite results, the live smoke-test status, any skipped integration tests, and the commit range. Do not claim live SEC success if the configured identity or network was unavailable.
