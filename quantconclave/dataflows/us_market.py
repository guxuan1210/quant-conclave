"""Small US-market data wrappers used by the evidence snapshot builder."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
import math
import pandas as pd

from .stockstats_utils import yf_retry
from .y_finance import _yf_ticker


def _info(symbol):
    try: return yf_retry(lambda: _yf_ticker(symbol).info) or {}
    except Exception: return {}

def _ticker(symbol):
    try: return _yf_ticker(symbol)
    except Exception: return None


def _safe(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return _safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict): return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_safe(v) for v in value]
    return value


def fetch_price_history(symbol, analysis_date, lookback_days=120):
    cutoff = datetime.fromisoformat(str(analysis_date)).date()
    start = (cutoff - timedelta(days=int(lookback_days))).isoformat()
    end = (cutoff + timedelta(days=1)).isoformat()
    ticker = _ticker(symbol)
    if ticker is None: return None
    try: frame = yf_retry(lambda: ticker.history(start=start, end=end))
    except Exception: return None
    if frame is None or frame.empty:
        return None
    rows = _records(frame, cutoff)
    return rows or None


def fetch_identity(symbol, analysis_date):
    info = _info(symbol)
    return _safe({key: info.get(key) for key in ("symbol", "shortName", "longName", "exchange", "sector", "industry") if info.get(key) is not None}) or None


def fetch_quote(symbol, analysis_date):
    info = _info(symbol)
    return _safe({key: info.get(key) for key in ("currentPrice", "regularMarketPrice", "previousClose", "bid", "ask", "dayHigh", "dayLow", "volume", "marketState") if info.get(key) is not None}) or None


def _frame_records(frame, cutoff=None):
    if frame is None or getattr(frame, "empty", True):
        return None
    return _records(frame, cutoff)


def _records(frame, cutoff=None):
    data = frame.copy()
    index_dates = pd.to_datetime(data.index, errors="coerce")
    has_index_dates = len(index_dates) and index_dates.notna().all()
    if has_index_dates and not any(str(c).lower() == "date" for c in data.columns):
        data = data.reset_index(drop=True)
        data.insert(0, "date", index_dates)
    else:
        data = data.reset_index(drop=True)
        for col in list(data.columns):
            if str(col).lower() == "date": data.rename(columns={col: "date"}, inplace=True)
    rows = _safe(data.to_dict(orient="records"))
    if cutoff is not None:
        rows = [r for r in rows if str(r.get("date", ""))[:10] <= cutoff.isoformat()]
    return rows


def fetch_yfinance_financials(symbol, analysis_date):
    ticker = _ticker(symbol)
    if ticker is None: return None
    result = {}
    for name in ("income_stmt", "balance_sheet", "cashflow"):
        try:
            result[name] = _frame_records(yf_retry(lambda n=name: getattr(ticker, n)), datetime.fromisoformat(str(analysis_date)).date())
        except Exception:
            result[name] = []
    result = _safe({key: value for key, value in result.items() if value})
    return result or None


def fetch_holders(symbol, analysis_date):
    ticker = _ticker(symbol)
    if ticker is None: return None
    result = {}
    for name in ("institutional_holders", "major_holders"):
        try:
            rows = _frame_records(yf_retry(lambda n=name: getattr(ticker, n)), datetime.fromisoformat(str(analysis_date)).date())
            if rows:
                result[name] = rows
        except Exception:
            pass
    result = _safe(result)
    return result or None


def fetch_analyst_ratings(symbol, analysis_date):
    ticker = _ticker(symbol)
    if ticker is None: return None
    result = {}
    for name in ("recommendations", "upgrades_downgrades"):
        try:
            rows = _frame_records(yf_retry(lambda n=name: getattr(ticker, n)), datetime.fromisoformat(str(analysis_date)).date())
            if rows:
                result[name] = rows
        except Exception:
            pass
    result = _safe(result)
    return result or None


def fetch_benchmark_context(symbol, benchmark, analysis_date):
    payload = {"benchmark": benchmark, "price_history": fetch_price_history(benchmark, analysis_date)}
    return payload if payload["price_history"] else None
