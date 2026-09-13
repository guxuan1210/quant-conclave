"""Data fetching for the vectorized backtest engine.

Fetches OHLCV via the configured vendor chain (route_to_vendor) and normalizes
it into the MultiIndex ``(symbol, eob)`` panel required by ``VectorBacktester``.
"""

from __future__ import annotations

import csv
import io
import logging

import pandas as pd

logger = logging.getLogger(__name__)


def parse_ohlcv_csv(raw: str) -> pd.DataFrame:
    """Parse vendor CSV into a normalized single-symbol OHLCV DataFrame.

    Accepts the CSV strings returned by tushare/akshare/yfinance vendors
    (different column spellings for date/open/high/low/close/volume) and
    returns a DataFrame with lowercase columns and an ascending ``date`` index.
    """
    lines = str(raw).split("\n")
    csv_start = next(
        (i for i, l in enumerate(lines)
         if "trade_date" in l.lower() or "date" in l.lower()),
        None,
    )
    if csv_start is None:
        return pd.DataFrame()
    reader = csv.DictReader(io.StringIO("\n".join(lines[csv_start:])))
    rows = []
    for r in reader:
        try:
            date_col = r.get("trade_date", r.get("Date", r.get("date", "")))
            close = float(r.get("close", r.get("Close", 0)) or 0)
            if close <= 0:
                continue
            rows.append({
                "date": str(date_col).strip()[:10],
                "open": float(r.get("open", r.get("Open", close)) or close),
                "high": float(r.get("high", r.get("High", close)) or close),
                "low": float(r.get("low", r.get("Low", close)) or close),
                "close": close,
                "volume": float(r.get("volume", r.get("Volume", r.get("vol", "0"))) or 0),
                # 换手率% — akshare emits TurnoverRate (原「换手率」列), tushare
                # daily_basic emits turnover_rate, yfinance has none (→ 0). Kept
                # for the watchlist/index-picking prompts' 换手 checks; backtest
                # callers select columns explicitly so this extra column is inert.
                "turnover": float(r.get("turnover", r.get("TurnoverRate", r.get("turnover_rate", 0))) or 0),
            })
        except (ValueError, KeyError):
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def fetch_ohlcv_panel(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch OHLCV for a ticker and return a ``(symbol, eob)`` MultiIndex panel.

    The panel has columns open/high/low/close/volume and index names exactly
    ``["symbol", "eob"]`` (required by ``VectorBacktester``).
    """
    from capitalradar.dataflows.interface import route_to_vendor

    raw = route_to_vendor(
        "get_stock_data", ticker,
        start_date=start_date, end_date=end_date,
    )
    df = parse_ohlcv_csv(raw)
    if df.empty:
        raise ValueError(f"No data for {ticker} from {start_date} to {end_date}")

    df = df.copy()
    df["symbol"] = ticker
    df = df.rename(columns={"date": "eob"})
    df = df.set_index(["symbol", "eob"])
    df = df[["open", "high", "low", "close", "volume"]]
    df.index.names = ["symbol", "eob"]
    return df
