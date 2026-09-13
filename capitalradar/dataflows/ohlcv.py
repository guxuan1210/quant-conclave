"""Shared OHLCV fetching for the web dashboard (web/app.py) and the standalone
K-line chart server (chart_app.py).

Both servers previously duplicated a "yfinance first, vendor chain fallback"
pattern. The ordering is now network-aware:

- A-shares (detected via ``_is_cn_ticker``) go through the vendor chain first
  (tushare → akshare → yfinance). On this deployment tushare returns same-day
  bars reliably, while Yahoo serves A-shares only under a converted suffix
  (``.SH`` → ``.SS``) and is IP-flaky.
- Non-A-shares try yfinance first (better coverage), falling back to the chain.
- Week/month intervals: yfinance has native bars but no A-share reliability
  guarantee, so CN tickers resample daily bars from the chain first; everyone
  else uses yfinance and resamples only as fallback.

Every path returns the same shape — a DataFrame indexed by ``Date`` with
uppercase ``Open/High/Low/Close/Volume`` columns — or ``None`` when every
source fails. Callers (``_compute_indicators`` in web/app.py, ``_indicators``
in chart_app.py) already expect that layout, so they need no changes.
"""

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from capitalradar.backtest.data import parse_ohlcv_csv
from capitalradar.dataflows.interface import route_to_vendor, _is_cn_ticker
from capitalradar.dataflows.ticker_utils import to_yfinance

_PERIOD_DAYS = {
    "1d": 5, "5d": 10, "1mo": 45, "3mo": 90,
    "6mo": 180, "1y": 380, "2y": 760, "5y": 1900, "max": 7300,
}

# Resample rule per interval, with per-field aggregation for weekly/monthly bars.
_RESAMPLE_RULES = {
    "1wk": ("W-FRI", {"Open": "first", "High": "max", "Low": "min",
                      "Close": "last", "Volume": "sum"}),
    "1mo": ("ME", {"Open": "first", "High": "max", "Low": "min",
                   "Close": "last", "Volume": "sum"}),
}


def _via_yfinance(ticker: str, period: str, interval: str):
    """yfinance bars. Uses the converted ticker (``600519.SH`` → ``600519.SS``)
    so A-shares resolve when Yahoo is reachable."""
    try:
        data = yf.Ticker(to_yfinance(ticker)).history(period=period, interval=interval)
        return data if not data.empty else None
    except Exception:
        return None


def _via_chain(ticker: str, period: str) -> pd.DataFrame | None:
    """Daily bars through the vendor chain (tushare → akshare → yfinance)."""
    try:
        days = _PERIOD_DAYS.get(period, 180)
        end = datetime.today().date()
        start = end - timedelta(days=days)
        raw = route_to_vendor(
            "get_stock_data", ticker,
            start_date=start.isoformat(), end_date=end.isoformat(),
        )
        df = parse_ohlcv_csv(str(raw))
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "date": "Date", "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception:
        return None


def _resample_daily(daily: pd.DataFrame, interval: str) -> pd.DataFrame | None:
    """Aggregate daily bars into weekly/monthly bars, or None when impossible."""
    rule, agg = _RESAMPLE_RULES.get(interval, (None, None))
    if rule is None or daily is None:
        return None
    return daily.resample(rule).agg(agg).dropna(how="all")


def fetch_ohlcv(ticker: str, period: str = "6mo", interval: str = "1d"):
    """Fetch OHLCV bars as a DataFrame (Date index + uppercase OHLCV columns),
    or None when every source fails. See module docstring for source order."""
    is_cn = _is_cn_ticker(ticker)

    if interval == "1d":
        if is_cn:
            df = _via_chain(ticker, period)
            return df if df is not None else _via_yfinance(ticker, period, "1d")
        df = _via_yfinance(ticker, period, "1d")
        return df if df is not None else _via_chain(ticker, period)

    # Weekly/monthly: CN prefers tushare daily resampled; non-CN prefers yfinance.
    if is_cn:
        df = _resample_daily(_via_chain(ticker, period), interval)
        return df if df is not None else _via_yfinance(ticker, period, interval)
    df = _via_yfinance(ticker, period, interval)
    return df if df is not None else _resample_daily(_via_chain(ticker, period), interval)
