"""Evaluation subsystem configuration.

The canonical defaults live in ``quantconclave.default_config`` under the
``evaluation`` key; this module mirrors those defaults so the evaluation
package can run standalone (e.g. in unit tests) without importing the full
config stack, while still honouring any ambient overrides.
"""

from __future__ import annotations

from copy import deepcopy

EVAL_DEFAULTS: dict = {
    "enabled": True,
    "weekly_sample_size": 8,
    "csi300_count": 4,
    "csi500_count": 4,
    "dedup_weeks": 8,
    "seed": 20260913,
    "friction_bp": 20,
    "holdings_horizons": [5, 20, 60],
    "entry_grace_days": 10,
    "max_monthly_cases": 50,
    "monthly_ablation_count": 6,
    "min_weeks_for_validity": 26,
    "coverage_threshold": 0.90,
    "max_drawdown_delta_pp": 3.0,
    "benchmark_weights": {"csi300": 0.5, "csi500": 0.5},
    "benchmark_codes": {"csi300": "000300.SH", "csi500": "000905.SH"},
}


def get_eval_config(config: dict | None = None) -> dict:
    """Return the merged evaluation config (defaults overridden by ambient config).

    When ``config`` is None, reads the ambient config via
    :func:`quantconclave.dataflows.config.get_config`. Nested keys are merged
    one level deep so a partial override keeps the remaining defaults.
    """
    if config is None:
        from quantconclave.dataflows.config import get_config
        config = get_config()
    merged = deepcopy(EVAL_DEFAULTS)
    incoming = (config or {}).get("evaluation") or {}
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged
