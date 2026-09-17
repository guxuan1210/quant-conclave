from quantconclave.evidence import EvidenceItem, EvidencePack, EvidenceStatus
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal


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


def test_models_copy_mutable_inputs_and_outputs():
    payload = {"nested": {"values": [1]}}
    item = EvidenceItem(
        kind="facts", source="test", as_of="", fetched_at="",
        status=EvidenceStatus.AVAILABLE, payload=payload,
    )
    payload["nested"]["values"].append(2)
    assert item.payload == {"nested": {"values": (1,)}}

    serialized = item.to_dict()
    serialized["payload"]["nested"]["values"].append(3)
    assert item.payload == {"nested": {"values": (1,)}}


def test_pack_normalizes_items_to_tuple_and_copies_output():
    item = EvidenceItem("facts", "test", "", "", EvidenceStatus.AVAILABLE, {})
    pack = EvidencePack("AAPL", "2026-09-17", [item])
    assert isinstance(pack.items, tuple)
    output = pack.to_dict()
    output["items"].clear()
    assert pack.items == (item,)


def test_complex_payload_is_json_safe():
    numpy = __import__("pytest").importorskip("numpy")
    payload = {
        "date": date(2026, 9, 17),
        "datetime": datetime(2026, 9, 17, 1, 2, 3, tzinfo=timezone.utc),
        "decimal": Decimal("12.3400"),
        "set": {"b", "a"},
        "bytes": b"hello",
        "numpy_int": numpy.int64(7),
        "numpy_float": numpy.float64(1.25),
    }
    item = EvidenceItem("complex", "test", "", "", EvidenceStatus.AVAILABLE, payload)
    encoded = json.dumps(item.to_dict())
    assert encoded


def test_nested_payload_is_immutable_but_serialized_output_is_mutable():
    item = EvidenceItem("facts", "test", "", "", EvidenceStatus.AVAILABLE, {"nested": {"x": [1]}})
    with __import__("pytest").raises(TypeError):
        item.payload["nested"]["x"].append(2)
    with __import__("pytest").raises(TypeError):
        item.payload["nested"]["new"] = 3

    output = item.to_dict()
    assert isinstance(output["payload"], dict)
    assert isinstance(output["payload"]["nested"]["x"], list)
    output["payload"]["nested"]["x"].append(2)
    assert item.payload["nested"]["x"] == (1,)


def test_non_finite_floats_are_rejected_for_json_safety():
    item = EvidenceItem(
        "facts", "test", "", "", EvidenceStatus.AVAILABLE,
        {"nan": math.nan, "infinity": math.inf},
    )
    with __import__("pytest").raises(ValueError, match="finite"):
        item.to_dict()
