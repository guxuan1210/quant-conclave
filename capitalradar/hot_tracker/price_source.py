"""Multi-source latest-price fetch for CapitalRadar.

PR1 of the Hot Tracker module: a robust fallback chain that fixes the
"latest price = 0.00 / stuck in PENDING" bug. The previous resolver used
yfinance only, which has poor coverage for A-shares, so most CN picks never
resolved. This module tries each vendor in order and NEVER returns 0 — it
raises :class:`PriceFetchError` when every source fails, so callers keep the
pick PENDING instead of persisting a bogus zero.

Vendor order on this machine (Eastmoney push2 / akshare are proxy-blocked,
so they sit late in the chain; tushare EOD + Tencent realtime are reliable):
    CN:    tushare EOD → tencent realtime → akshare → yfinance
    HK:    tencent realtime → yfinance
    other: yfinance
"""

import logging
import re

logger = logging.getLogger(__name__)


class PriceFetchError(Exception):
    """Raised when every vendor fails. Callers must NOT write a 0.00 price."""

    def __init__(self, ts_code: str, detail: str = ""):
        self.ts_code = ts_code
        self.detail = detail
        super().__init__(f"Price fetch failed for {ts_code}: {detail or 'no vendor available'}")


def _is_cn_ticker(ts_code: str) -> bool:
    t = (ts_code or "").strip().upper()
    if not t:
        return False
    if any(t.endswith(s) for s in (".SH", ".SZ", ".SS", ".BJ")):
        return True
    return t.isdigit() and len(t) == 6


def _is_hk_ticker(ts_code: str) -> bool:
    return (ts_code or "").strip().upper().endswith(".HK")


def _norm_date(date_val) -> str:
    """Normalize a tushare '20260820' / '2026-08-20' / date to 'YYYY-MM-DD'."""
    s = str(date_val)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _tushare_latest(ts_code: str):
    """Latest EOD close via tushare. Returns (price, date) or None."""
    from capitalradar.dataflows.eastmoney_sector import get_stock_daily
    rows = get_stock_daily(ts_code, days=40)
    if not rows:
        return None
    last = rows[-1]
    price = float(last.get("close") or 0)
    if price <= 0:
        return None
    return price, _norm_date(last.get("date"))


def _tencent_latest(ts_code: str):
    """Live price via Tencent qt.gtimg.cn (free, ~100ms). Returns (price, today)."""
    import requests
    from datetime import datetime

    from capitalradar.dataflows.tencent_realtime import _normalize_symbol
    resp = requests.get(f"http://qt.gtimg.cn/q={_normalize_symbol(ts_code)}", timeout=8)
    resp.encoding = "gbk"
    text = resp.text
    if '="' not in text:
        return None
    fields = text.split('="')[1].rstrip('";\n').split("~")
    if len(fields) < 4 or not fields[3]:
        return None
    try:
        price = float(fields[3])
    except (ValueError, TypeError):
        return None
    if price <= 0:
        return None
    return price, datetime.now().strftime("%Y-%m-%d")


def _akshare_latest(ts_code: str):
    """Live price via akshare (Xueqiu single-stock). Returns (price, today) or None."""
    from datetime import datetime

    from capitalradar.dataflows.akshare_data import get_realtime_quote_akshare
    text = get_realtime_quote_akshare(ts_code)
    if not text or text.startswith("# SKIP_VENDOR"):
        return None
    m = re.search(r"Current Price:\s*([0-9.]+)", text)
    if not m:
        return None
    try:
        price = float(m.group(1))
    except (ValueError, TypeError):
        return None
    if price <= 0:
        return None
    return price, datetime.now().strftime("%Y-%m-%d")


def _yfinance_latest(ts_code: str):
    """Latest close via yfinance (works for US/HK; weak for A-shares). Returns (price, date)."""
    import yfinance as yf
    hist = yf.Ticker(ts_code).history(period="3mo")
    if hist is None or len(hist) < 1:
        return None
    price = float(hist["Close"].iloc[-1])
    if price <= 0:
        return None
    try:
        date = hist.index[-1].date().isoformat()
    except Exception:
        date = ""
    return price, date


def fetch_latest_price(ts_code: str, config: dict | None = None) -> dict:
    """Fetch the latest price through a multi-source fallback chain.

    Returns ``{"price": float, "date": "YYYY-MM-DD", "source": str}``.
    Raises :class:`PriceFetchError` if every source fails — callers must keep
    the pick PENDING rather than persist a 0.00.
    """
    ts_code = (ts_code or "").strip().upper()
    errors = []

    if _is_cn_ticker(ts_code):
        chain = [("tushare", _tushare_latest), ("tencent", _tencent_latest),
                 ("akshare", _akshare_latest), ("yfinance", _yfinance_latest)]
    elif _is_hk_ticker(ts_code):
        chain = [("tencent", _tencent_latest), ("yfinance", _yfinance_latest)]
    else:
        chain = [("yfinance", _yfinance_latest)]

    for source, fn in chain:
        try:
            result = fn(ts_code)
        except Exception as e:
            errors.append(f"{source}: {e}")
            continue
        if result:
            price, date = result
            return {"price": round(float(price), 3), "date": date, "source": source}

    raise PriceFetchError(ts_code, detail=" | ".join(errors))


def fetch_price_history(ts_code: str, days: int = 40) -> list[dict]:
    """Daily OHLCV history — tushare for CN, yfinance otherwise.

    Returns a list of ``{"date": "YYYY-MM-DD", "open", "close", "high", "low",
    "volume"}`` ordered oldest → newest. Used for 20-day low/high and for
    backfilling a missing pick_price.
    """
    if _is_cn_ticker(ts_code):
        from capitalradar.dataflows.eastmoney_sector import get_stock_daily
        rows = get_stock_daily(ts_code, days=days)
        if rows:
            return [{
                "date": _norm_date(r.get("date")),
                "open": float(r.get("open") or 0),
                "close": float(r.get("close") or 0),
                "high": float(r.get("high") or 0),
                "low": float(r.get("low") or 0),
                "volume": float(r.get("volume") or 0),
            } for r in rows]

    import yfinance as yf
    hist = yf.Ticker(ts_code).history(period=f"{max(days, 20)}d")
    return [{
        "date": idx.date().isoformat(),
        "open": float(r["Open"]),
        "close": float(r["Close"]),
        "high": float(r["High"]),
        "low": float(r["Low"]),
        "volume": float(r["Volume"]),
    } for idx, r in hist.iterrows()]
