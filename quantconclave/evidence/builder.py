"""Build deterministic, source-labelled evidence snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import EvidenceItem, EvidencePack, EvidenceStatus
from quantconclave.dataflows import us_market
from quantconclave.dataflows.sec_edgar import SecEdgarClient, select_company_facts


_US_ORDER = ("company_identity", "price_history", "quote", "filings", "financials", "insider_transactions", "institutional_holders", "analyst_ratings", "benchmark_context")


def _value(profile, key, default=None):
    return profile.get(key, default) if isinstance(profile, dict) else getattr(profile, key, default)


def _call(fn, symbol, analysis_date, config):
    try:
        return fn(symbol, analysis_date=analysis_date, config=config)
    except TypeError:
        return fn(symbol, analysis_date)


def _optional(fn, symbol, analysis_date, config, *, benchmark=None):
    try:
        if benchmark is not None:
            return fn(symbol, benchmark, analysis_date), None
        return _call(fn, symbol, analysis_date, config), None
    except Exception:
        return None, EvidenceStatus.ERROR


def _item(kind, source, as_of, payload, status=None, **kwargs):
    if status is None:
        status = EvidenceStatus.AVAILABLE if payload else EvidenceStatus.NO_DATA
    return EvidenceItem(kind=kind, source=source, as_of=as_of, fetched_at=datetime.now(timezone.utc).isoformat(), status=status, payload=payload or {}, **kwargs)


def build_evidence_pack(profile, analysis_date, config, *, sec_client=None, market_fetchers=None):
    symbol = str(_value(profile, "symbol", _value(profile, "ticker", ""))).upper()
    market = str(_value(profile, "market", _value(profile, "market_type", "US"))).upper()
    fetchers = {name: getattr(us_market, name) for name in ("fetch_price_history", "fetch_identity", "fetch_quote", "fetch_yfinance_financials", "fetch_holders", "fetch_analyst_ratings", "fetch_benchmark_context")}
    if market_fetchers:
        fetchers.update(market_fetchers)
    price = _call(fetchers["price_history"], symbol, analysis_date, config)
    if not price:
        raise RuntimeError(f"required price history unavailable for {symbol}")
    items = []
    sec = sec_client
    cik = None
    sec_identity = None
    if sec is not None and market in {"US", "USA", "US_EQUITY"}:
        try:
            cik = sec.resolve_cik(symbol)
            sec_identity = sec.get_submissions(cik)
        except Exception:
            sec_identity = None
    if sec_identity:
        identity = _item("company_identity", "sec", analysis_date, sec_identity)
    else:
        try:
            identity_payload = _call(fetchers["identity"], symbol, analysis_date, config)
        except Exception:
            identity_payload = None
        if not identity_payload:
            raise RuntimeError(f"required company identity unavailable for {symbol}")
        identity = _item("company_identity", "yfinance", analysis_date, identity_payload, EvidenceStatus.DEGRADED)
    items.append(identity)
    items.append(_item("price_history", "yfinance", analysis_date, price))
    if market not in {"US", "USA", "US_EQUITY"}:
        return EvidencePack(symbol, analysis_date, tuple(items))
    quote, quote_status = _optional(fetchers["quote"], symbol, analysis_date, config)
    items.append(_item("quote", "yfinance", analysis_date, quote, quote_status))
    try: filings = sec.get_filings(cik, analysis_date) if sec and cik else []
    except Exception: filings = None
    items.append(_item("filings", "sec", analysis_date, filings or {}, EvidenceStatus.ERROR if filings is None else None))
    financials = {}
    financial_source = "sec"
    if sec and cik:
        try:
            financials = select_company_facts(sec.get_company_facts(cik), analysis_date)
        except Exception:
            financials = {}
    if not financials:
        financials, fallback_status = _optional(fetchers["financials"], symbol, analysis_date, config)
        financial_source = "yfinance"
    items.append(_item("financials", financial_source, analysis_date, financials, EvidenceStatus.AVAILABLE if financial_source == "sec" and financials else (fallback_status or (EvidenceStatus.DEGRADED if financials else EvidenceStatus.NO_DATA))))
    try: insider = sec.get_form4_transactions(cik, analysis_date) if sec and cik else []
    except Exception: insider = None
    items.append(_item("insider_transactions", "sec", analysis_date, insider or {}, EvidenceStatus.ERROR if insider is None else None))
    holders, hs = _optional(fetchers["holders"], symbol, analysis_date, config)
    items.append(_item("institutional_holders", "yfinance", analysis_date, holders, hs))
    ratings, rs = _optional(fetchers["analyst_ratings"], symbol, analysis_date, config)
    items.append(_item("analyst_ratings", "yfinance", analysis_date, ratings, rs))
    benchmark, bs = _optional(fetchers["benchmark_context"], symbol, analysis_date, config, benchmark=config.get("benchmark_ticker", "SPY"))
    items.append(_item("benchmark_context", "yfinance", analysis_date, benchmark, bs))
    return EvidencePack(symbol, analysis_date, tuple(items))
