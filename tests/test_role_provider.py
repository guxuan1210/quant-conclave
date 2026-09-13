"""Tests for per-role LLM provider resolution (deep/quick independent providers).

Covers ``resolve_role_llm`` (the deep/quick provider resolver) and the
``deep_think_provider`` / ``quick_think_provider`` config keys.
"""

from __future__ import annotations

import importlib

import pytest

import capitalradar.default_config as default_config_module
from capitalradar.llm_clients import resolve_role_llm


def test_own_provider_wins():
    """A role's own provider beats the global llm_provider."""
    cfg = {
        "deep_think_provider": "openai",
        "llm_provider": "deepseek",
        "deep_think_llm": "m1",
        "backend_url": None,
    }
    assert resolve_role_llm(cfg, "deep") == ("openai", "m1", None)


def test_global_fallback():
    """With no per-role override, each role falls back to llm_provider."""
    cfg = {
        "llm_provider": "deepseek",
        "deep_think_llm": "m1",
        "quick_think_llm": "m2",
        "backend_url": None,
    }
    assert resolve_role_llm(cfg, "deep") == ("deepseek", "m1", None)
    assert resolve_role_llm(cfg, "quick") == ("deepseek", "m2", None)


def test_empty_dict_safe_defaults():
    """Empty config returns deepseek + empty model + None backend_url."""
    assert resolve_role_llm({}, "deep") == ("deepseek", "", None)
    assert resolve_role_llm({}, "quick") == ("deepseek", "", None)


def test_model_default_fallback():
    """When a role's model key is missing, model_default is used."""
    cfg = {
        "llm_provider": "deepseek",
        "backend_url": None,
    }
    assert resolve_role_llm(cfg, "quick", model_default="fallback-model") == (
        "deepseek", "fallback-model", None,
    )


def test_quick_provider_independent_of_deep():
    """deep and quick can point at different providers simultaneously."""
    cfg = {
        "deep_think_provider": "deepseek",
        "quick_think_provider": "ollama",
        "llm_provider": "openai",
        "deep_think_llm": "deep-model",
        "quick_think_llm": "qwen3.5:latest",
        "backend_url": None,
    }
    assert resolve_role_llm(cfg, "deep") == ("deepseek", "deep-model", None)
    assert resolve_role_llm(cfg, "quick") == ("ollama", "qwen3.5:latest", None)


def test_unknown_role_raises():
    with pytest.raises(ValueError):
        resolve_role_llm({}, "banana")


def _reload_with_env(monkeypatch, **overrides):
    """Set/clear env vars then reload default_config to re-evaluate DEFAULT_CONFIG."""
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


def test_env_override_keys_registered():
    """The per-role provider env vars are mapped to config keys."""
    dc = default_config_module
    assert "CAPITALRADAR_DEEP_THINK_PROVIDER" in dc._ENV_OVERRIDES
    assert "CAPITALRADAR_QUICK_THINK_PROVIDER" in dc._ENV_OVERRIDES
    assert dc._ENV_OVERRIDES["CAPITALRADAR_DEEP_THINK_PROVIDER"] == "deep_think_provider"
    assert dc._ENV_OVERRIDES["CAPITALRADAR_QUICK_THINK_PROVIDER"] == "quick_think_provider"


def test_env_override_applies(monkeypatch):
    """CAPITALRADAR_DEEP_THINK_PROVIDER lands in DEFAULT_CONFIG."""
    dc = _reload_with_env(
        monkeypatch,
        CAPITALRADAR_DEEP_THINK_PROVIDER="google",
        CAPITALRADAR_QUICK_THINK_PROVIDER="ollama",
    )
    assert dc.DEFAULT_CONFIG["deep_think_provider"] == "google"
    assert dc.DEFAULT_CONFIG["quick_think_provider"] == "ollama"
