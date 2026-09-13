"""EastMoney micro-cap constituent replication for Wind index 868008.WI.

868008.WI (万得微盘股指数, monthly-rebalanced since 2025-01-02) is a Wind
proprietary index: tushare's ``index_weight`` has no row for it and no public
constituent list exists. We therefore **replicate Wind's documented selection
rule** using EastMoney's public ``clist/get`` ranking API:

    Among 沪深 A 股, take the smallest ``limit`` stocks by total market cap,
    excluding ST/*ST/PT/delisting-arrangement names and stocks listed for
    fewer than 60 trading days.  (等权 — weight is irrelevant for a watchlist.)

Network notes (verified on this machine):
- ``push2.eastmoney.com`` is blocked by the system proxy (502/ProxyError);
  ``push2delay.eastmoney.com`` is stable. All hosts work when the proxy is
  bypassed per-request (same pattern as ``top_gainers.py:get_em_rankings``).
- ``clist/get`` caps at 100 rows/page; ascending ``fid=f20`` puts the
  suspended/delisted shells (``f20='-'``) first, so several pages must be
  fetched before enough valid stocks appear.
- The response body is GBK-encoded; set ``resp.encoding`` before ``.json()``.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta
from typing import Optional

import requests as _req

logger = logging.getLogger(__name__)

# Host fallback chain — push2delay first (stable), push2 last (flaky direct).
_EM_HOSTS = (
    "https://push2delay.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://push2.eastmoney.com",
)
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

# 沪深 A 股 (SH main + STAR + SZ main + ChiNext); 不含北交所 — matches Wind's
# 样本空间 (上交所 + 深交所).
_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"

_NEW_LISTING_TRADE_DAYS = 60  # Wind 868008.WI 剔除上市不满 60 个交易日的股票
_CAL_WINDOW_DAYS = 200        # how far back to fetch the trade calendar


def _get(params: dict, timeout: int = 10) -> list[dict]:
    """Fetch one clist/get page, falling back across hosts (proxy bypassed).

    Returns the ``diff`` row list. Empty rows (legit end of data) return ``[]``;
    raises ``RuntimeError`` only when every host/retry fails.
    """
    last_exc: Optional[Exception] = None
    for host in _EM_HOSTS:
        for _attempt in range(2):
            try:
                resp = _req.get(
                    host + "/api/qt/clist/get",
                    params=params,
                    headers=_HEADERS,
                    timeout=timeout,
                    proxies={"http": None, "https": None},
                )
                # clist/get now declares charset=UTF-8 and requests honors it
                # via resp.encoding. Only fall back to detection when the
                # server omits a charset (older code forced "gbk" here, which
                # mojibake'd every name once EastMoney switched to UTF-8).
                if not resp.encoding:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return (resp.json().get("data") or {}).get("diff") or []
            except Exception as e:  # noqa: BLE001 — network hiccup, try next
                last_exc = e
                logger.debug("EastMoney %s failed: %s", host, e)
    raise RuntimeError(f"EastMoney clist/get unreachable: {last_exc}")


def _to_ts_code(code, market: int) -> Optional[str]:
    """Convert EastMoney (f12, f13) into tushare-style ``600519.SH``."""
    code = str(code or "").strip()
    if not code.isdigit():
        return None
    suffix = {1: "SH", 0: "SZ"}.get(market)
    return f"{code}.{suffix}" if suffix else None


def _is_excluded(name, f20) -> bool:
    """ST/*ST/PT/退市整理 names and stocks without a valid market cap."""
    if f20 in ("-", None, ""):
        return True
    n = str(name or "").upper()
    return any(k in n for k in ("ST", "PT", "退"))


# ---------------------------------------------------------------------------
# Trade calendar (for the "listed < 60 trading days" exclusion)
# ---------------------------------------------------------------------------

_OPEN_DAYS: Optional[tuple] = None
_OPEN_DAYS_LOCK = threading.Lock()


def _open_days(today: date) -> tuple:
    """Cached tuple of open trading days in ``[today - window, today]``.

    Prefers tushare ``trade_cal`` (exact, includes CN holidays) when
    ``TUSHARE_TOKEN`` is set; otherwise falls back to pandas business days
    (weekends only — a close approximation for the 60-trading-day filter).
    """
    global _OPEN_DAYS
    if _OPEN_DAYS is not None:
        return _OPEN_DAYS
    with _OPEN_DAYS_LOCK:
        if _OPEN_DAYS is not None:
            return _OPEN_DAYS
        start = today - timedelta(days=_CAL_WINDOW_DAYS)
        days: Optional[set] = None
        token = os.environ.get("TUSHARE_TOKEN", "")
        if token:
            try:
                import tushare as ts

                pro = ts.pro_api(token)
                cal = pro.trade_cal(
                    exchange="SSE",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=today.strftime("%Y%m%d"),
                )
                days = {
                    datetime.strptime(str(d), "%Y%m%d").date()
                    for d in cal.loc[cal["is_open"] == 1, "cal_date"]
                }
            except Exception as e:  # noqa: BLE001 — degrade to business days
                logger.debug("tushare trade_cal failed (%s), using business days", e)
        if days is None:
            import pandas as pd

            days = set(pd.bdate_range(start, today).date.tolist())
        _OPEN_DAYS = tuple(sorted(d for d in days if start <= d <= today))
        logger.debug("trade calendar: %d open days in [%s, %s]", len(_OPEN_DAYS), start, today)
        return _OPEN_DAYS


def _is_newly_listed(listing, today: date) -> bool:
    """True if the stock has been listed for fewer than 60 trading days."""
    if listing in (None, "", "-", 0):
        return False  # unknown listing date — keep the stock
    try:
        listing_date = datetime.strptime(str(listing)[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return False
    if listing_date > today:
        return True  # future/placeholder date — treat as brand new
    open_days = _open_days(today)
    traded = sum(1 for d in open_days if listing_date <= d <= today)
    return traded < _NEW_LISTING_TRADE_DAYS


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_microcap_constituents(
    limit: int = 400,
    max_pages: int = 14,
    timeout: int = 10,
) -> list[dict]:
    """Smallest-``limit`` A-share stocks by total market cap (EastMoney).

    Replicates Wind 868008.WI's selection rule. Returns a list of
    ``{"code": "600519.SH", "name": "..."}`` sorted ascending by market cap.
    Raises ``RuntimeError`` if ``limit`` valid stocks cannot be collected.
    """
    today = date.today()
    result: list[dict] = []
    for pn in range(1, max_pages + 1):
        params = {
            "pn": str(pn),
            "pz": "100",
            "po": "0",
            "fid": "f20",
            "fs": _A_SHARE_FS,
            "fields": "f12,f13,f14,f20,f26",
            "np": "1",
            "fltt": "2",
            "invt": "2",
        }
        rows = _get(params, timeout=timeout)
        if not rows:
            break  # end of data
        for it in rows:
            if _is_excluded(it.get("f14"), it.get("f20")):
                continue
            code = _to_ts_code(it.get("f12"), it.get("f13"))
            if not code:
                continue
            if _is_newly_listed(it.get("f26"), today):
                continue
            result.append({"code": code, "name": str(it.get("f14", ""))})
            if len(result) >= limit:
                logger.info("Micro-cap constituents: collected %d from %d pages", limit, pn)
                return result
    raise RuntimeError(
        f"EastMoney returned only {len(result)}/{limit} valid micro-cap "
        f"constituents after {max_pages} pages"
    )
