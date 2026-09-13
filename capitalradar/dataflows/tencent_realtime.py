"""Tencent qt.gtimg.cn real-time quote vendor.

Free, zero-auth, ~88 fields per stock. ~100ms response time.
Covers A-shares, HK stocks, indices. Ideal as the PRIMARY realtime
vendor — much faster than tushare/akshare for live quotes.

Format: v_sh601939="1~name~code~price~..."
Fields (key indices): 1=name, 2=code, 3=price, 4=prev_close, 5=open,
  6=volume_hands, 8=high, 9=low, 31=change_pct, 32=change,
  38=pe, 44=total_mv, 45=circulation_mv, 46=pb, 47=volume_ratio,
  50=high_52w, 51=low_52w, 56=turnover_rate
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Field indices verified against live Tencent API for STOCKS (2026-07-30)
# NOTE: indices (sh000001) have DIFFERENT field positions!
# Fields 9-18 = 5 ask levels, 19-28 = 5 bid levels
_FIELD_MAP = {
    1:  ("名称", ""),
    3:  ("最新价", "元"),
    4:  ("昨收", "元"),
    5:  ("开盘", "元"),
    6:  ("成交量", "手"),
    31: ("涨跌额", "元"),
    32: ("涨跌幅", "%"),
    33: ("最高", "元"),
    34: ("最低", "元"),
    38: ("换手率", "%"),     # NOT PE — turnover rate
    39: ("市盈率", ""),      # actual PE
    43: ("振幅", "%"),
    44: ("总市值", "亿"),
    45: ("流通市值", "亿"),
    46: ("市净率", "倍"),
    49: ("量比", ""),        # actual volume ratio (not 47)
    52: ("52周高", "元"),
    53: ("52周低", "元"),
    56: ("成交额", "万元"),  # amount in 万元
}


def _normalize_symbol(symbol: str) -> str:
    """Convert ticker to Tencent format: sh600519, sz000001, hk00700."""
    s = symbol.strip().upper()
    # Already in Tencent format
    if s.startswith("SH") or s.startswith("SZ") or s.startswith("HK"):
        return s.lower()
    # Strip suffix
    for suffix in (".SH", ".SZ", ".SS", ".HK"):
        if s.endswith(suffix):
            code = s[:-3]
            prefix = suffix[1:].lower()
            if prefix == "ss":
                prefix = "sh"
            return f"{prefix}{code}"
    # Bare code: guess exchange by first digit
    if s.isdigit() and len(s) == 6:
        if s[0] in "69":
            return f"sh{s}"
        else:
            return f"sz{s}"
    return s.lower()


def _parse_line(line: str) -> Optional[dict]:
    """Parse a single v_XXX="..." line into a dict."""
    if '="' not in line:
        return None
    try:
        fields = line.split('="')[1].rstrip('";\n').split("~")
        if len(fields) < 40:
            return None
        result = {}
        for idx, (label, unit) in _FIELD_MAP.items():
            if idx < len(fields):
                val = fields[idx]
                result[label] = f"{val}{unit}" if unit else val
            else:
                result[label] = "N/A"
        return result
    except Exception:
        return None


def get_tencent_realtime_quote(symbol: str) -> str:
    """Fetch real-time quote from Tencent qt.gtimg.cn.

    Returns formatted text with: name, price, change%, PE, PB,
    market cap, turnover, volume ratio, 52w high/low, etc.
    """
    import requests

    norm = _normalize_symbol(symbol)
    url = f"http://qt.gtimg.cn/q={norm}"

    try:
        resp = requests.get(url, timeout=10)
        resp.encoding = "gbk"
        text = resp.text
    except Exception as e:
        logger.warning("Tencent realtime fetch failed for %s: %s", symbol, e)
        return f"# SKIP_VENDOR: Tencent API unreachable ({e})"

    if not text or "none_match" in text or '=""' in text:
        return f"# SKIP_VENDOR: Tencent returned no data for {symbol}"

    parsed = _parse_line(text)
    if not parsed:
        return f"# SKIP_VENDOR: Tencent parse failed for {symbol}"

    lines = [f"# Tencent Real-Time Quote: {symbol}"]
    # Market state detection
    state_raw = text.split("~")
    market_state = "CLOSED"
    if len(state_raw) > 40:
        # Field ~40 typically contains market state info
        pass

    lines.append(f"**Current Price**: {parsed.get('最新价', 'N/A')}")
    lines.append(f"**Change**: {parsed.get('涨跌额', 'N/A')} / {parsed.get('涨跌幅', 'N/A')}")
    lines.append(f"**Open**: {parsed.get('开盘', 'N/A')}  |  High: {parsed.get('最高', 'N/A')}  |  Low: {parsed.get('最低', 'N/A')}")
    lines.append(f"**Prev Close**: {parsed.get('昨收', 'N/A')}  |  Amplitude: {parsed.get('振幅', 'N/A')}")
    lines.append(f"**PE**: {parsed.get('市盈率', 'N/A')}  |  PB: {parsed.get('市净率', 'N/A')}")
    lines.append(f"**Volume Ratio**: {parsed.get('量比', 'N/A')}  |  Turnover: {parsed.get('换手率', 'N/A')}")
    lines.append(f"**Market Cap**: {parsed.get('总市值', 'N/A')}亿  |  Circ: {parsed.get('流通市值', 'N/A')}亿")
    lines.append(f"**52W High**: {parsed.get('52周高', 'N/A')}  |  52W Low: {parsed.get('52周低', 'N/A')}")
    lines.append(f"**Volume**: {parsed.get('成交量', 'N/A')} hands")
    lines.append(f"**Source**: Tencent qt.gtimg.cn (free, real-time)")

    return "\n".join(lines)
