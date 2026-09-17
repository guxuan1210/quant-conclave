from quantconclave.evidence import EvidenceItem, EvidencePack, EvidenceStatus


def test_zero_value_is_available_not_no_data():
    item = EvidenceItem(
        kind="net_income", source="sec_companyfacts", as_of="2026-06-30",
        fetched_at="2026-09-17T00:00:00Z", status=EvidenceStatus.AVAILABLE,
        payload={"value": 0},
    )
    assert item.status is EvidenceStatus.AVAILABLE
    assert item.payload["value"] == 0


def test_pack_quality_is_degraded_when_required_fallback_is_used():
    pack = EvidencePack(
        symbol="AAPL", analysis_date="2026-09-17",
        items=(EvidenceItem(
            kind="financials", source="yfinance", as_of="2026-09-17",
            fetched_at="2026-09-17T00:00:00Z",
            status=EvidenceStatus.DEGRADED, payload={"reason": "SEC unavailable"},
        ),),
    )
    assert pack.quality == "degraded"


def test_summary_calls_missing_data_unavailable_not_neutral():
    pack = EvidencePack(
        symbol="AAPL", analysis_date="2026-09-17",
        items=(EvidenceItem(
            kind="insider_transactions", source="sec_form4", as_of="2026-09-17",
            fetched_at="2026-09-17T00:00:00Z",
            status=EvidenceStatus.NO_DATA, payload={},
        ),),
    )
    summary = pack.to_prompt_summary()
    assert "NO_DATA" in summary
    assert "neutral" not in summary.lower()


def test_pack_round_trip_is_json_safe():
    pack = EvidencePack(symbol="NVDA", analysis_date="2026-09-17", items=())
    assert EvidencePack.from_dict(pack.to_dict()) == pack
