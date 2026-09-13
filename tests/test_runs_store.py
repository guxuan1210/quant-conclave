"""Tests for backtest_runs persistence."""
from __future__ import annotations

import json
import sqlite3

import pytest

from quantconclave.backtest import runs


def _conn_for(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def test_save_and_read_back_runs(monkeypatch, tmp_path):
    db_path = tmp_path / "runs.db"
    monkeypatch.setattr(runs, "_get_conn", lambda: _conn_for(db_path))

    run_id = runs.save_backtest_run(
        ticker="600519.SH",
        strategy_name="ma_cross",
        strategy_id="ma_cross",
        start_date="2024-01-01",
        end_date="2024-12-31",
        parameters={"fast_period": 5, "slow_period": 20},
        initial_capital=100000.0,
        results={"total_return_pct": 10.5},
        equity_curve=[{"date": "2024-01-02", "value": 100100.0}],
        trade_log=[{"entry_date": "2024-01-02", "exit_date": "2024-02-01"}],
    )
    assert run_id > 0

    rows = runs.list_backtest_runs()
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "600519.SH"
    # JSON fields are parseable
    params = json.loads(row["parameters_used"])
    assert params["fast_period"] == 5
    results = json.loads(row["results"])
    assert results["total_return_pct"] == 10.5
    equity = json.loads(row["equity_curve"])
    assert equity[0]["value"] == 100100.0
    trades = json.loads(row["trade_log"])
    assert trades[0]["entry_date"] == "2024-01-02"
