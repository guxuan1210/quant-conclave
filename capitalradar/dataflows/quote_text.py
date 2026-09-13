"""Parse text-format realtime quotes from the ``get_realtime_quote`` vendor chain.

Two formats occur in practice (Tencent is the primary CN source on this machine):

    Tencent : **Current Price**: 24.35\\n**Change**: -0.06 / 2.28%
    akshare : Current Price: 168.50\\nChange: +0.5%

The authoritative today move is the ``%``-terminated number after ``Change``
(fld[31]=涨跌额 / fld[32]=涨跌幅 on Tencent) — prefer it over recomputing
``(current - prev_close) / prev_close``, which degrades to 0 when the vendor
returns the last close for a halted/pre-open stock.
"""

from __future__ import annotations

import re

_QUOTE_PRICE_RE = re.compile(r"Current Price[^\d]*([\d.]+)")
_QUOTE_CHG_RE = re.compile(r"Change[^\n]*?([+-]?[\d.]+)%")


def parse_quote_price(text) -> float | None:
    """Extract the current price from a quote text blob, or None."""
    if not text:
        return None
    m = _QUOTE_PRICE_RE.search(str(text))
    return float(m.group(1)) if m else None


def parse_quote_change_pct(text) -> float | None:
    """Extract today's change % (e.g. 2.28 for +2.28%) from a quote blob."""
    if not text:
        return None
    m = _QUOTE_CHG_RE.search(str(text))
    return float(m.group(1)) if m else None
