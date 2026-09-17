from quantconclave.instruments import Market, resolve_instrument


def test_resolves_plain_us_equity():
    profile = resolve_instrument("aapl")
    assert profile.symbol == "AAPL"
    assert profile.market is Market.US
    assert profile.currency == "USD"
    assert profile.timezone == "America/New_York"
    assert profile.calendar == "XNYS"
    assert profile.benchmark == "SPY"


def test_preserves_us_class_share_symbol():
    profile = resolve_instrument("BRK.B")
    assert profile.symbol == "BRK.B"
    assert profile.market is Market.US


def test_preserves_existing_a_share_resolution():
    profile = resolve_instrument("600519.SS")
    assert profile.symbol == "600519.SH"
    assert profile.market is Market.CN
    assert profile.currency == "CNY"


def test_does_not_misclassify_known_international_suffix():
    profile = resolve_instrument("7203.T")
    assert profile.market is Market.JP
    assert profile.benchmark == "^N225"


def test_profile_round_trip_is_json_safe():
    profile = resolve_instrument("NVDA")
    assert type(profile).from_dict(profile.to_dict()) == profile
