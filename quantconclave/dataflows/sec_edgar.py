"""Small, cache-aware SEC EDGAR client and filing normalizers."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable
import xml.etree.ElementTree as ET

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"


class SecConfigurationError(ValueError):
    """Raised when SEC access is not configured with an identifiable agent."""


def _local(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element.iter() if child.tag.rsplit("}", 1)[-1] == name]


def _value(element: ET.Element, name: str) -> str:
    found = _local(element, name)
    if not found:
        return ""
    values = _local(found[0], "value")
    if values:
        return (values[0].text or "").strip()
    return (found[0].text or "").strip()


class SecEdgarClient:
    def __init__(self, user_agent: str, cache_dir: str | Path,
                 request_interval_seconds: float = 0.12,
                 timeout_seconds: float = 10.0, session=None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise SecConfigurationError("SEC user_agent must identify an application and contact")
        self.user_agent = user_agent.strip()
        self.cache_dir = Path(cache_dir) / "sec"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.timeout_seconds = timeout_seconds
        self.session = session
        self.sleep = sleep
        self.clock = clock
        self._last_request: float | None = None

    def _get(self, url: str, is_json: bool = True) -> Any:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        suffix = ".json" if is_json else ".xml"
        path = self.cache_dir / (key + suffix)
        meta = self.cache_dir / (key + ".meta.json")
        if path.exists():
            if is_json:
                return json.loads(path.read_text(encoding="utf-8"))
            return path.read_text(encoding="utf-8")
        if self._last_request is not None:
            remaining = self.request_interval_seconds - (self.clock() - self._last_request)
            if remaining > 0:
                self.sleep(remaining)
        session = self.session
        if session is None:
            import requests
            session = requests.Session()
        self._last_request = self.clock()
        response = session.get(url, headers={"User-Agent": self.user_agent}, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json() if is_json else response.text
        path.write_text(json.dumps(payload, ensure_ascii=False) if is_json else payload, encoding="utf-8")
        meta.write_text(json.dumps({"fetched_at": time.time(), "url": url}), encoding="utf-8")
        return payload

    @staticmethod
    def _cik(cik: str | int) -> str:
        return str(cik).strip().zfill(10)

    def resolve_cik(self, ticker: str) -> str:
        wanted = ticker.strip().upper()
        data = self._get(TICKERS_URL)
        rows = data.values() if isinstance(data, dict) else data
        for row in rows:
            if str(row.get("ticker", "")).upper() == wanted:
                return self._cik(row.get("cik_str", row.get("cik")))
        raise KeyError(f"SEC ticker not found: {ticker}")

    def get_submissions(self, cik: str) -> dict:
        return self._get(SUBMISSIONS_URL.format(cik=self._cik(cik)))

    def get_filings(self, cik: str, as_of: str,
                    forms=("10-K", "10-Q", "8-K", "4")) -> list[dict]:
        data = self.get_submissions(cik)
        recent = data.get("filings", {}).get("recent", {})
        keys = list(recent)
        rows = []
        for i in range(len(recent.get("form", []))):
            row = {key: recent[key][i] for key in keys if i < len(recent.get(key, []))}
            if row.get("form") in forms and row.get("filingDate", "") <= as_of:
                rows.append(row)
        return rows

    def get_company_facts(self, cik: str) -> dict:
        return self._get(COMPANYFACTS_URL.format(cik=self._cik(cik)))

    def get_form4_transactions(self, cik: str, as_of: str, limit: int = 20) -> list[dict]:
        filings = [row for row in self.get_filings(cik, as_of, forms=("4",))]
        result = []
        cik10 = self._cik(cik)
        for filing in filings[:limit]:
            accession = str(filing.get("accessionNumber", "")).replace("-", "")
            document = filing.get("primaryDocument", "")
            if not accession or not document:
                continue
            url = ARCHIVE_DOCUMENT_URL.format(cik_int=str(int(cik10)), accession=accession, document=document)
            result.extend(self.parse_form4(self._get(url, is_json=False)))
        return result

    def parse_form4(self, xml_text: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        owners = _local(root, "reportingOwner")
        owner = _value(owners[0], "reportingOwnerName") if owners else ""
        rows = []
        for transaction in _local(root, "nonDerivativeTransaction"):
            code = _value(transaction, "transactionCode")
            if code != "P":
                continue
            shares = _value(transaction, "transactionShares")
            price = _value(transaction, "transactionPricePerShare")
            rows.append({"owner": owner, "transaction_date": _value(transaction, "transactionDate"),
                         "code": code, "shares": float(shares) if shares else 0.0,
                         "price": float(price) if price else 0.0,
                         "acquired_disposed": _value(transaction, "transactionAcquiredDisposedCode")})
        return rows


_CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    "net_income": ("NetIncomeLoss",), "assets": ("Assets",),
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
            selected[normalized] = {"value": row.get("val"), "filed": row.get("filed", ""),
                                    "period_end": row.get("end", ""), "form": row.get("form", ""),
                                    "concept": concept, "accession": row.get("accn", "")}
    return selected
