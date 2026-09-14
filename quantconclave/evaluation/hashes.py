"""Deterministic hashes for model configuration and frozen input snapshots.

These exist so a settled evaluation result can be traced back to exactly what
produced it — the model/provider/skill version, and the frozen input the
prediction was made against. Hashes are stable across processes and ordering.
"""

from __future__ import annotations

import hashlib
import json


def _stable(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)


def model_config_hash(config: dict) -> str:
    """SHA256 over the resolved deep/quick LLM config + thinking knobs + skill version."""
    from quantconclave.llm_clients.factory import resolve_role_llm

    try:
        from quantconclave.graph.skill_generator import get_active_skill_version
        skill_version = get_active_skill_version()
    except Exception:
        skill_version = 0

    deep = resolve_role_llm(config, "deep")
    quick = resolve_role_llm(config, "quick")
    payload = {
        "deep": deep,
        "quick": quick,
        "google_thinking_level": config.get("google_thinking_level"),
        "openai_reasoning_effort": config.get("openai_reasoning_effort"),
        "anthropic_effort": config.get("anthropic_effort"),
        "skill_version": skill_version,
    }
    return hashlib.sha256(_stable(payload).encode("utf-8")).hexdigest()


def input_snapshot_hash(ticker: str, selection_date: str, fingerprint: str) -> str:
    """SHA256 over the frozen input identity (ticker + cutoff date + data fingerprint)."""
    payload = {
        "ticker": ticker,
        "selection_date": selection_date,
        "fingerprint": fingerprint,
    }
    return hashlib.sha256(_stable(payload).encode("utf-8")).hexdigest()
