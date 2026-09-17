import pytest

from quantconclave.instruments import Market, resolve_instrument


def test_resolves_plain_us_equity():
    profile = resolve_instrument("aapl")
    assert profile.symbol == "AAPL"
    assert profile.market is Market.US
    assert profile.currency == "USD"
    assert profile.timezone == "America/New_York"
    assert profile.calendar == "XNYS"  # generic US calendar, not an exchange claim
    assert profile.benchmark == "SPY"
    assert profile.exchange == "US"


def test_preserves_us_class_share_symbol():
    profile = resolve_instrument("BRK.B")
    assert profile.symbol == "BRK.B"
    assert profile.market is Market.US


def test_preserves_existing_a_share_resolution():
    profile = resolve_instrument("600519.SS")
    assert profile.symbol == "600519.SH"
    assert profile.market is Market.CN
    assert profile.currency == "CNY"


def test_resolves_bj_without_mislabeling_shenzhen():
    profile = resolve_instrument("830001.BJ")
    assert profile.market is Market.CN
    assert profile.exchange == "BJ"
    assert profile.calendar == "XBEJ"


@pytest.mark.parametrize(
    "symbol,market,exchange,calendar",
    [
        ("000001.SZ", Market.CN, "SZ", "XSHE"),
        ("0700.HK", Market.HK, "HKEX", "XHKG"),
        ("7203.T", Market.JP, "TSE", "XTKS"),
        ("RELIANCE.NS", Market.IN, "NSE", "XNSE"),
        ("500325.BO", Market.IN, "BSE", "XBOM"),
        ("VOD.L", Market.GB, "LSE", "XLON"),
        ("RY.TO", Market.CA, "TSX", "XTSE"),
        ("BHP.AX", Market.AU, "ASX", "XASX"),
    ],
)
def test_resolves_supported_exchange_suffixes(symbol, market, exchange, calendar):
    profile = resolve_instrument(symbol)
    assert (profile.market, profile.exchange, profile.calendar) == (market, exchange, calendar)


def test_trims_symbol_whitespace():
    assert resolve_instrument("  NVDA ").symbol == "NVDA"


@pytest.mark.parametrize("symbol", ["", "   ", None, 123])
def test_rejects_empty_or_non_string_symbol(symbol):
    with pytest.raises(ValueError, match="non-empty ticker"):
        resolve_instrument(symbol)


def test_rejects_unsupported_ticker():
    with pytest.raises(ValueError, match="Unable to resolve market"):
        resolve_instrument("ABC/DEF")


def test_profile_round_trip_is_json_safe():
    profile = resolve_instrument("NVDA")
    assert type(profile).from_dict(profile.to_dict()) == profile
