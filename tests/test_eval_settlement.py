"""Tests for evaluation settlement (pure, synthetic prices, no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from quantconclave.evaluation import settlement as S


def _frame(rows):
    """Build a date/open/close frame from ``(date_str, open, close)`` rows."""
    df = pd.DataFrame(rows, columns=["date", "open", "close"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _index_frame(entry_date, exit_dates, close_by_date):
    """A date/close frame for a benchmark leg."""
    dates = [entry_date] + list(exit_dates)
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "close": [close_by_date[d] for d in dates],
    })


@pytest.mark.unit
def test_index_return():
    df = _frame([("2026-01-06", 100.0, 100.0), ("2026-01-13", 101.0, 110.0)])
    assert S.index_return(df, "2026-01-06", "2026-01-13") == pytest.approx(0.10)


@pytest.mark.unit
def test_index_return_missing():
    df = _frame([("2026-01-06", 100.0, 100.0)])
    assert S.index_return(df, "2026-01-06", "2026-01-13") is None


@pytest.mark.unit
def test_benchmark_return_weighted(monkeypatch):
    entry, exit_ = "2026-01-06", "2026-01-13"

    def idx(code, start, end):
        exit_close = 110.0 if code == "000300.SH" else 130.0
        return pd.DataFrame({
            "date": pd.to_datetime([entry, exit_]),
            "close": [100.0, exit_close],
        })

    monkeypatch.setattr(S, "fetch_index_series", idx)
    # 0.5 * 10% + 0.5 * 30% = 20%
    assert S.benchmark_return({}, entry, exit_) == pytest.approx(0.20)


@pytest.mark.unit
def test_settle_basic(monkeypatch):
    selection_date = "2026-01-05"
    entry = "2026-01-06"
    exits = {5: "2026-01-13", 20: "2026-02-03", 60: "2026-04-02"}

    def nth(anchor, n, config=None):
        if n == 1:
            return entry
        return exits[n]

    monkeypatch.setattr(S, "nth_trading_day", nth)
    monkeypatch.setattr(S, "fetch_adjusted_prices", lambda t, s, e: _frame([
        ("2026-01-05", 98.0, 99.0),
        (entry, 100.0, 100.5),
        (exits[5], 100.5, 105.0),
        (exits[20], 105.0, 120.0),
        (exits[60], 120.0, 130.0),
    ]))
    # Flat benchmark (0%) so excess == net.
    monkeypatch.setattr(S, "fetch_index_series", lambda c, s, e: _index_frame(
        entry, exits.values(), {d: 100.0 for d in [entry, *exits.values()]},
    ))

    result = S.settle_prediction({}, "600519.SH", selection_date)
    assert result["failed"] is False
    records = {r["horizon"]: r for r in result["returns"]}
    friction = 20 / 10000.0
    # h=5: gross 0.05, net 0.05-0.002, excess == net (bench 0).
    assert records[5]["gross_return"] == pytest.approx(0.05)
    assert records[5]["net_return"] == pytest.approx(0.05 - friction)
    assert records[5]["excess_return"] == pytest.approx(0.05 - friction)
    assert records[5]["settled"] is True
    assert records[20]["gross_return"] == pytest.approx(0.20)
    assert records[60]["gross_return"] == pytest.approx(0.30)


@pytest.mark.unit
def test_settle_missing_entry_fails(monkeypatch):
    monkeypatch.setattr(S, "nth_trading_day", lambda a, n, config=None: "2026-01-06")
    monkeypatch.setattr(S, "fetch_adjusted_prices", lambda t, s, e: _frame([
        ("2026-01-07", 100.0, 101.0),
    ]))
    result = S.settle_prediction({}, "600519.SH", "2026-01-05")
    assert result["failed"] is True
    assert "no entry open price" in result["reason"]
    assert result["returns"] == []


@pytest.mark.unit
def test_settle_missing_exit_horizon(monkeypatch):
    entry = "2026-01-06"
    exits = {5: "2026-01-13", 20: "2026-02-03", 60: "2026-04-02"}

    def nth(anchor, n, config=None):
        return entry if n == 1 else exits[n]

    monkeypatch.setattr(S, "nth_trading_day", nth)
    # No close for horizon 20's exit date → that horizon stays unsettled.
    monkeypatch.setattr(S, "fetch_adjusted_prices", lambda t, s, e: _frame([
        (entry, 100.0, 100.5),
        (exits[5], 100.5, 105.0),
        (exits[60], 120.0, 130.0),
    ]))
    monkeypatch.setattr(S, "fetch_index_series", lambda c, s, e: _index_frame(
        entry, [exits[5], exits[60]], {entry: 100.0, exits[5]: 100.0, exits[60]: 100.0},
    ))

    result = S.settle_prediction({}, "600519.SH", "2026-01-05")
    assert result["failed"] is False
    records = {r["horizon"]: r for r in result["returns"]}
    assert records[5]["settled"] is True
    assert records[20]["settled"] is False
    assert records[20]["gross_return"] is None
    assert records[20]["exit_price"] is None
    assert records[60]["settled"] is True


@pytest.mark.unit
def test_settle_zero_friction(monkeypatch):
    entry = "2026-01-06"
    exits = {5: "2026-01-13", 20: "2026-02-03", 60: "2026-04-02"}
    monkeypatch.setattr(S, "nth_trading_day", lambda a, n, config=None: entry if n == 1 else exits[n])
    monkeypatch.setattr(S, "fetch_adjusted_prices", lambda t, s, e: _frame([
        (entry, 100.0, 100.5),
        (exits[5], 100.5, 105.0),
        (exits[20], 105.0, 105.0),
        (exits[60], 105.0, 105.0),
    ]))
    monkeypatch.setattr(S, "fetch_index_series", lambda c, s, e: _index_frame(
        entry, exits.values(), {d: 100.0 for d in [entry, *exits.values()]},
    ))

    result = S.settle_prediction(
        {"evaluation": {"friction_bp": 0}}, "600519.SH", "2026-01-05",
    )
    records = {r["horizon"]: r for r in result["returns"]}
    assert records[5]["net_return"] == pytest.approx(records[5]["gross_return"])
