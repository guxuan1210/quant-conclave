"""Small US-market data wrappers used by the evidence snapshot builder."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .stockstats_utils import yf_retry
from .y_finance import _yf_ticker


def _info(symbol):
    return yf_retry(lambda: _yf_ticker(symbol).info) or {}


def fetch_price_history(symbol, analysis_date=None, config=None):
    end = analysis_date or date.today().isoformat()
    start = (datetime.fromisoformat(end).date() - timedelta(days=380)).isoformat()
    frame = yf_retry(lambda: _yf_ticker(symbol).history(start=start, end=end))
    if frame is None or frame.empty:
        return []
    frame = frame.reset_index()
    return frame.to_dict(orient="records")


def fetch_identity(symbol, **kwargs):
    info = _info(symbol)
    return {key: info.get(key) for key in ("symbol", "shortName", "longName", "exchange", "sector", "industry") if info.get(key) is not None}


def fetch_quote(symbol, **kwargs):
    info = _info(symbol)
    return {key: info.get(key) for key in ("currentPrice", "regularMarketPrice", "previousClose", "bid", "ask", "dayHigh", "dayLow", "volume", "marketState") if info.get(key) is not None}


def _frame_records(frame):
    if frame is None or getattr(frame, "empty", True):
        return []
    return frame.reset_index().to_dict(orient="records")


def fetch_yfinance_financials(symbol, **kwargs):
    ticker = _yf_ticker(symbol)
    result = {}
    for name in ("income_stmt", "balance_sheet", "cashflow"):
        try:
            result[name] = _frame_records(yf_retry(lambda n=name: getattr(ticker, n)))
        except Exception:
            result[name] = []
    return {key: value for key, value in result.items() if value}


def fetch_holders(symbol, **kwargs):
    ticker = _yf_ticker(symbol)
    result = {}
    for name in ("institutional_holders", "major_holders"):
        try:
            rows = _frame_records(yf_retry(lambda n=name: getattr(ticker, n)))
            if rows:
                result[name] = rows
        except Exception:
            pass
    return result


def fetch_analyst_ratings(symbol, **kwargs):
    ticker = _yf_ticker(symbol)
    result = {}
    for name in ("recommendations", "upgrades_downgrades"):
        try:
            rows = _frame_records(yf_retry(lambda n=name: getattr(ticker, n)))
            if rows:
                result[name] = rows
        except Exception:
            pass
    return result


def fetch_benchmark_context(symbol, benchmark="SPY", **kwargs):
    return {"benchmark": benchmark, "price_history": fetch_price_history(benchmark, kwargs.get("analysis_date"), kwargs.get("config"))}
