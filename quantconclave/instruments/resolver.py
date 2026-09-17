import re

from quantconclave.dataflows.ticker_utils import normalize_symbol
from .models import InstrumentProfile, Market

_US = re.compile(r"^[A-Z][A-Z0-9-]{0,5}(?:\.[A-Z])?$")

_SUFFIXES = {
    ".HK": (Market.HK, "HKEX", "HKD", "Asia/Hong_Kong", "XHKG", "^HSI"),
    ".T": (Market.JP, "TSE", "JPY", "Asia/Tokyo", "XTKS", "^N225"),
    ".NS": (Market.IN, "NSE", "INR", "Asia/Kolkata", "XNSE", "^NSEI"),
    ".BO": (Market.IN, "BSE", "INR", "Asia/Kolkata", "XBOM", "^BSESN"),
    ".L": (Market.GB, "LSE", "GBP", "Europe/London", "XLON", "^FTSE"),
    ".TO": (Market.CA, "TSX", "CAD", "America/Toronto", "XTSE", "^GSPTSE"),
    ".AX": (Market.AU, "ASX", "AUD", "Australia/Sydney", "XASX", "^AXJO"),
}


def resolve_instrument(symbol: str, asset_type: str = "stock") -> InstrumentProfile:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A non-empty ticker symbol is required")
    normalized = normalize_symbol(symbol)
    if normalized.endswith((".SH", ".SZ", ".BJ")):
        exchange = normalized.rsplit(".", 1)[1]
        return InstrumentProfile(
            normalized, asset_type, Market.CN, exchange, "CNY",
            "Asia/Shanghai", "XSHG" if exchange == "SH" else "XSHE",
            "000300.SH", "CITICS", ("cn_money_flow", "cn_filings"),
        )
    for suffix, values in _SUFFIXES.items():
        if normalized.endswith(suffix):
            market, exchange, currency, timezone, calendar, benchmark = values
            return InstrumentProfile(
                normalized, asset_type, market, exchange, currency, timezone,
                calendar, benchmark, "GICS", ("price",),
            )
    if _US.fullmatch(normalized):
        return InstrumentProfile(
            normalized, asset_type, Market.US, "US", "USD",
            "America/New_York", "XNYS", "SPY", "GICS",
            ("sec_filings", "form4", "institutional_holders", "analyst_ratings"),
        )
    raise ValueError(f"Unable to resolve market for ticker '{symbol}'")
