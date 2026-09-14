"""Tests for evaluation weekly sampling (pure, no network)."""

from __future__ import annotations

import pytest

from quantconclave.evaluation.sampling import (
    filter_candidates,
    sample_weekly_stocks,
    seed_for_week,
    stratified_sample,
    trading_days_since,
    week_key,
)


def _candidates(n, industries=("A", "B", "C", "D"), prefix="S"):
    out = []
    for i in range(n):
        out.append({
            "code": f"{prefix}{i:03d}.SH",
            "name": f"Stock {i}",
            "industry": industries[i % len(industries)],
            "list_date": "20200101",
        })
    return out


@pytest.mark.unit
def test_week_key():
    assert week_key("2026-01-05") == "2026-W02"


@pytest.mark.unit
def test_seed_stable_and_distinct():
    a1 = seed_for_week(20260913, "2026-W02")
    a2 = seed_for_week(20260913, "2026-W02")
    b = seed_for_week(20260913, "2026-W03")
    assert a1 == a2
    assert a1 != b


@pytest.mark.unit
def test_stratified_sample_spreads_industries():
    cands = _candidates(12, industries=("A", "B", "C", "D"))
    import random
    rng = random.Random(1)
    picked = stratified_sample(cands, 4, rng)
    assert len(picked) == 4
    industries = {c["industry"] for c in picked}
    assert industries == {"A", "B", "C", "D"}


@pytest.mark.unit
def test_stratified_sample_respects_count():
    import random
    picked = stratified_sample(_candidates(20), 3, random.Random(2))
    assert len(picked) == 3
    assert stratified_sample([], 3, random.Random(2)) == []


@pytest.mark.unit
def test_sample_reproducible():
    config = {}
    constituents = {"csi300": _candidates(10, prefix="L"), "csi500": _candidates(10, prefix="M")}
    a = sample_weekly_stocks(config, "2026-01-05", constituents)
    b = sample_weekly_stocks(config, "2026-01-05", constituents)
    assert a == b
    assert len(a) == 8
    assert sum(1 for s in a if s["index_source"] == "csi300") == 4
    assert sum(1 for s in a if s["index_source"] == "csi500") == 4


@pytest.mark.unit
def test_sample_dedup_excludes_recent():
    config = {}
    constituents = {"csi300": _candidates(10, prefix="L"), "csi500": _candidates(10, prefix="M")}
    # Exclude the first 4 csi300 codes (deterministic pick would otherwise include them).
    recent = {"L000.SH", "L001.SH", "L002.SH", "L003.SH"}
    sampled = sample_weekly_stocks(config, "2026-01-05", constituents, recent_tickers=recent)
    codes = {s["code"] for s in sampled}
    assert codes.isdisjoint(recent)


@pytest.mark.unit
def test_filter_candidates_exclusions():
    cands = [
        {"code": "A.SH", "name": "a", "industry": "X", "list_date": "20200101"},
        {"code": "B.SH", "name": "b", "industry": "X", "list_date": "20200101"},  # ST
        {"code": "C.SH", "name": "c", "industry": "X", "list_date": "20200101"},  # suspended
        {"code": "D.SH", "name": "d", "industry": "X", "list_date": "20260101"},  # too new
        {"code": "E.SH", "name": "e", "industry": "X", "list_date": "20200101"},  # recent dedup
    ]
    open_days = {"20200102", "20200103", "20260102", "20260103"}
    out = filter_candidates(
        cands,
        st_codes={"B.SH"},
        traded_codes={"A.SH", "D.SH", "E.SH"},  # C excluded via missing? keep C absent below
        recent_tickers={"E.SH"},
        open_days=open_days,
        selection_date="2026-01-05",
        min_trading_days=3,
    )
    # C not in traded_codes → excluded as suspended. D too new. B ST. E recent.
    codes = {c["code"] for c in out}
    assert codes == {"A.SH"}


@pytest.mark.unit
def test_trading_days_since():
    open_days = {"20260101", "20260102", "20260103"}
    assert trading_days_since("20260101", "20260103", open_days) == 3
    assert trading_days_since("", "20260103", open_days) == 10 ** 9
