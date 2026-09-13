"""Unified ticker format conversion.

Each data source expects a different ticker format:

| Source       | Format      | Example      |
|--------------|-------------|--------------|
| yfinance     | `.SS` for SH | `600519.SS`  |
| tushare      | `code.SH/SZ`| `000001.SZ`   |
| akshare      | bare + exch | `000001` + `sz` |
| Tencent      | `sz000001`  | `sz000001`   |
| Xueqiu       | `SZ000001`  | `SZ000001`   |
| Eastmoney/MX | `code.SH/SZ`| `000001.SZ`   |

The canonical in-DB format is ``000001.SZ`` (``web.ticker_utils.normalize_ticker``).
``normalize_symbol`` maps any input to that canonical form first; each ``to_*``
derives the target source's format from it. This gives a single logic chain for
"pick the input format based on the data source".
"""

from __future__ import annotations

import re

# 6-digit A-share with an exchange suffix (case-insensitive).
_A_SHARE_RE = re.compile(r"^(\d{6})\.(SH|SZ|SS|SHE|SZE|BJ)$", re.IGNORECASE)

# HK stocks are 5-digit, e.g. 00700.HK or hk00700.
_HK_RE = re.compile(r"^(\d{5})\.HK$", re.IGNORECASE)


def is_cn_ticker(symbol: str) -> bool:
    """Whether a symbol is a Chinese A-share (accepts .SS, unlike some copies)."""
    s = symbol.strip().upper()
    if _A_SHARE_RE.match(s):
        return True
    if s.isdigit() and len(s) == 6:
        return True
    return False


def _guess_cn_exchange(code: str) -> str:
    """Guess the exchange suffix for a bare 6-digit A-share code."""
    if code[0] in ("6", "9"):
        return "SH"
    if code[0] in ("4", "8"):
        return "BJ"
    return "SZ"  # 0, 3 → SZ


def normalize_symbol(symbol: str) -> str:
    """Normalize any ticker to the canonical DB format (000001.SZ / 600519.SH).

    - A-shares: bare 6-digit gets its exchange suffix; .SS→.SH; .SHE/.SZE→.SZ
    - HK: 00700 → 00700.HK (kept with .HK)
    - US/international: preserved as-is (AAPL, CNC.TO, 0700.HK)
    """
    s = symbol.strip().upper()
    if not s:
        return s

    m = _A_SHARE_RE.match(s)
    if m:
        code, suffix = m.group(1), m.group(2).upper()
        if suffix in ("SS", "SHE", "SZE"):
            suffix = "SH" if suffix == "SS" else "SZ"
        return f"{code}.{suffix}"

    # Bare 6-digit A-share → add exchange suffix
    if s.isdigit() and len(s) == 6:
        return f"{s}.{_guess_cn_exchange(s)}"

    # 5-digit bare code starting with 0 → likely HK (e.g. 00700 → 00700.HK)
    if s.isdigit() and len(s) == 5 and s.startswith("0"):
        return f"{s}.HK"

    # Already-suffixed international (0700.HK) or US ticker → as-is
    return s


def to_yfinance(symbol: str) -> str:
    """yfinance format: SH exchange uses .SS, SZ stays .SZ, US/international as-is."""
    s = normalize_symbol(symbol)
    if s.endswith(".SH"):
        return s[:-3] + ".SS"
    return s


def to_tushare(symbol: str) -> str:
    """tushare format: code.SH / code.SZ / code.BJ (canonical DB form)."""
    return normalize_symbol(symbol)


def to_akshare(symbol: str) -> tuple[str, str]:
    """akshare format: (bare 6-digit code, 'sz'/'sh'/'bj')."""
    s = normalize_symbol(symbol)
    m = _A_SHARE_RE.match(s)
    if m:
        code, suffix = m.group(1), m.group(2).upper()
        if suffix == "SS":
            suffix = "SH"
        return code, suffix.lower()
    return s, ""


def to_tencent(symbol: str) -> str:
    """Tencent qt.gtimg format: sz000001 / sh600519 / hk00700."""
    s = symbol.strip().upper()

    # Already in Tencent format (lowercased prefix)
    if s[:2] in ("SH", "SZ", "HK") and s[2:].isdigit():
        return s.lower()

    # HK: 0700.HK or 00700.HK or bare 00700
    hm = _HK_RE.match(s)
    if hm:
        return f"hk{hm.group(1)}"
    if s.endswith(".HK"):
        return f"hk{s[:-3]}"
    if s.isdigit() and len(s) == 5 and s.startswith("0"):
        return f"hk{s}"

    # A-share: normalize to canonical then map exchange
    norm = normalize_symbol(s)
    m = _A_SHARE_RE.match(norm)
    if m:
        code, suffix = m.group(1), m.group(2).upper()
        if suffix == "SS":
            suffix = "SH"
        return f"{suffix.lower()}{code}"
    return s.lower()


def to_xueqiu(symbol: str) -> str:
    """Xueqiu format: SZ000001 / SH600519 (uppercase prefix)."""
    norm = normalize_symbol(symbol)
    m = _A_SHARE_RE.match(norm)
    if m:
        code, suffix = m.group(1), m.group(2).upper()
        if suffix == "SS":
            suffix = "SH"
        return f"{suffix}{code}"
    return norm


def to_eastmoney(symbol: str) -> str:
    """Eastmoney / MX API format: code.SH / code.SZ (same as tushare canonical)."""
    return normalize_symbol(symbol)


def to_code(symbol: str) -> str:
    """Bare 6-digit code (used by push2 / akshare intraday)."""
    norm = normalize_symbol(symbol)
    m = _A_SHARE_RE.match(norm)
    if m:
        return m.group(1)
    return symbol.strip().upper()


__all__ = [
    "is_cn_ticker",
    "normalize_symbol",
    "to_yfinance",
    "to_tushare",
    "to_akshare",
    "to_tencent",
    "to_xueqiu",
    "to_eastmoney",
    "to_code",
]
