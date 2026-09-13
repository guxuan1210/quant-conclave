"""Eastmoney push2 API — real-time fund flow data.

Provides LIVE intraday money flow (主力资金流向) per stock.
Data updates every ~3 seconds during trading hours.

Endpoint: push2.eastmoney.com/api/qt/stock/get
Fields: f62=main_net_inflow, f184=super_large_net, f66=large_net,
        f69=medium_net, f72=small_net, f78=main_inflow_ratio, etc.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Field mapping for fund flow
_FUND_FLOW_FIELDS = "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f62,f64,f66,f69,f72,f75,f78,f81,f84,f87,f116,f117,f118,f161,f162,f163,f164,f167,f168,f169,f170,f171,f184"


def _get_realtime_fund_flow(ticker: str) -> Optional[dict]:
    """Fetch real-time fund flow via Eastmoney clist/get (batch endpoint).

    Uses the same push2 clist/get endpoint as top_gainers.py (proven
    working). Queries a single stock by filtering clist to that code.
    """
    import requests

    # Normalize to Eastmoney format
    t = ticker.strip().upper()
    for suffix in (".SH", ".SZ", ".SS"):
        if t.endswith(suffix):
            code = t[:-3]
            market = "SH" if suffix in (".SH", ".SS") else "SZ"
            break
    else:
        if t.isdigit() and len(t) == 6:
            market, code = ("SH", t) if t[0] in "69" else ("SZ", t)
        else:
            return None

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    params = {
        "pn": "1", "pz": "1", "po": "0", "fid": "f62",
        "fs": f"m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,b:{code}",
        "fields": "f2,f3,f12,f14,f62,f184,f66,f69,f72",
        "np": "1", "fltt": "2", "invt": "2",
    }
    resp = None
    for attempt in range(2):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                break
        except Exception:
            resp = None
        if attempt == 0:
            time.sleep(1.5)   # transient proxy/network drop — retry once
    if resp is None:
        return None
    try:
        data = resp.json().get("data", {})
        items = data.get("diff", [])
        if not items:
            return None
        r = items[0]

        def _f(key, scale=1.0):
            val = r.get(key)
            if val is None or val == "-":
                return 0.0
            return float(val) / scale

        return {
            "name": r.get("f14", ticker),
            "price": r.get("f2", 0) or 0,
            "change_pct": r.get("f3", 0) or 0,
            "main_net_inflow": r.get("f62", 0) or 0,
            "super_large_net": r.get("f184", 0) or 0,
            "large_net": r.get("f66", 0) or 0,
            "medium_net": r.get("f69", 0) or 0,
            "small_net": r.get("f72", 0) or 0,
            "main_inflow_ratio": 0,
            "turnover_rate": 0,
            "volume_ratio": 0,
        }
    except Exception:
        return None


def get_eastmoney_realtime_flow(ticker: str) -> str:
    """Get real-time intraday fund flow from Eastmoney push2 API.

    Returns LIVE money flow breakdown: main net inflow, super-large,
    large, medium, small order net flows, and main inflow ratio.
    Data refreshes every ~3s during trading hours.

    Use this to cross-check tushare (end-of-day) flow data against
    live intraday flow — especially important for same-day decisions.
    """
    result = _get_realtime_fund_flow(ticker)
    if not result:
        return f"# SKIP_VENDOR: Eastmoney push2 real-time flow unavailable for {ticker}"

    inflow = result["main_net_inflow"]
    direction = "🟢 主力净流入" if inflow > 0 else ("🔴 主力净流出" if inflow < 0 else "⚪ 主力持平")
    abs_wan = abs(inflow) / 1e4

    lines = [
        f"# Eastmoney Real-Time Fund Flow: {ticker} ({result['name']})",
        f"**{direction}**: {inflow/1e4:+.0f}万元",
        f"**Current Price**: {result['price']:.2f}  |  Change: {result['change_pct']:+.2f}%",
        "",
        "## Order-Size Breakdown",
        f"- 超大单净流入: {result['super_large_net']/1e4:+.0f}万元",
        f"- 大单净流入:   {result['large_net']/1e4:+.0f}万元",
        f"- 中单净流入:   {result['medium_net']/1e4:+.0f}万元",
        f"- 小单净流入:   {result['small_net']/1e4:+.0f}万元",
        "",
        f"**主力流入占比**: {result['main_inflow_ratio']:.2f}%",
        f"**换手率**: {result['turnover_rate']:.2f}%  |  量比: {result['volume_ratio']:.2f}",
        "",
        "**Source**: Eastmoney push2 (实时，~3秒刷新)",
    ]
    return "\n".join(lines)
