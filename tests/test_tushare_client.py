"""Regression tests for non-persistent Tushare client initialization."""

from __future__ import annotations

import pytest

from quantconclave.dataflows import tushare_data


@pytest.mark.unit
def test_get_pro_passes_env_token_without_writing_global_token(monkeypatch):
    sentinel = object()
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")

    def forbidden_set_token(_token):
        pytest.fail("ts.set_token persists tk.csv in the user home directory")

    def fake_pro_api(token):
        assert token == "test-token"
        return sentinel

    monkeypatch.setattr(tushare_data.ts, "set_token", forbidden_set_token)
    monkeypatch.setattr(tushare_data.ts, "pro_api", fake_pro_api)

    assert tushare_data._get_pro() is sentinel
