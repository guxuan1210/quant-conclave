"""Tests for the shared 180-day bottom-window helper (reversal_metrics).

Pure unit tests — route_to_vendor is monkeypatched, no network. Verifies the
data-validation contract: metrics are produced only when ≥180 trading days of
history exist; otherwise an explicit ok=False reason is returned (never a
truncated window masquerading as a bottom).
"""

from __future__ import annotations

import pytest

from quantconclave.sector_scan import reversal_metrics as rm


def _ohlcv_csv(n_rows: int, base: float = 10.0, step: float = 0.1) -> str:
    """Monotonic-increase OHLCV CSV in tushare column spelling.

    Dates are distinct, valid, and ascending so parse_ohlcv_csv's sort
    (unstable quicksort) cannot scramble the row order.
    """
    from datetime import date, timedelta

    lines = ["trade_date,open,high,low,close,volume"]
    d = date(2026, 1, 1)
    for i in range(n_rows):
        c = base + i * step
        lines.append(
            f"{d.strftime('%Y%m%d')},{c:.2f},{c + 0.4:.2f},{c - 0.4:.2f},{c:.2f},100000"
        )
        d += timedelta(days=1)
    return "\n".join(lines)


def test_ok_metrics_with_enough_history(monkeypatch):
    n = 210
    monkeypatch.setattr(rm, "route_to_vendor", lambda *a, **k: _ohlcv_csv(n))
    m = rm.fetch_reversal_metrics("000001.SZ", "2026-08-26")

    closes = [10.0 + i * 0.1 for i in range(n)]
    low180 = min(closes[-180:])
    high180 = max(closes[-180:])
    current = closes[-1]
    low60 = min(closes[-60:])
    ma20 = sum(closes[-20:]) / 20

    assert m["ok"] is True
    assert m["n_bars"] == n
    assert m["ts_code"] == "000001.SZ"
    assert m["current"] == round(current, 2)
    assert m["low180"] == round(low180, 2)
    assert m["high180"] == round(high180, 2)
    assert m["low60"] == round(low60, 2)
    assert m["ma20"] == round(ma20, 2)
    assert m["price_vs_180d_low"] == round(current / low180, 3)
    assert m["price_vs_180d_high"] == round(current / high180, 3)
    assert m["price_vs_60d_low"] == round(current / low60, 3)
    assert m["price_vs_ma20"] == round(current / ma20, 3)
    assert m["drop_from_high"] == 0.0
    assert isinstance(m["rsi_14"], float)
    assert m["volume_ratio_5d"] == 1.0  # flat volumes → ratio 1.0


def test_insufficient_history_rejected(monkeypatch):
    monkeypatch.setattr(rm, "route_to_vendor", lambda *a, **k: _ohlcv_csv(120))
    m = rm.fetch_reversal_metrics("000001.SZ", "2026-08-26")
    assert m["ok"] is False
    assert m["reason"] == "insufficient_history"
    assert m["n_bars"] == 120
    # Must NOT fabricate metrics from a truncated window.
    assert "low180" not in m


def test_fetch_exception_maps_to_fetch_failed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vendor down")

    monkeypatch.setattr(rm, "route_to_vendor", boom)
    m = rm.fetch_reversal_metrics("000001.SZ", "2026-08-26")
    assert m["ok"] is False
    assert m["reason"] == "fetch_failed"


def test_unparseable_csv_maps_to_fetch_failed(monkeypatch):
    monkeypatch.setattr(rm, "route_to_vendor", lambda *a, **k: "garbage no date header")
    m = rm.fetch_reversal_metrics("000001.SZ", "2026-08-26")
    assert m["ok"] is False
    assert m["reason"] == "fetch_failed"


def test_custom_min_bars_floor(monkeypatch):
    # Same 120-bar CSV: rejected at default 180, accepted when floor lowered.
    monkeypatch.setattr(rm, "route_to_vendor", lambda *a, **k: _ohlcv_csv(120))
    m = rm.fetch_reversal_metrics("000001.SZ", "2026-08-26")
    assert m["ok"] is False

    m2 = rm.fetch_reversal_metrics("000001.SZ", "2026-08-26", min_bars=100)
    assert m2["ok"] is True
    assert m2["n_bars"] == 120
