"""Estimated token-cost calculation for evaluation runs.

There is no cost table elsewhere in the codebase, so this module carries a small
provider-hint → (input, output) USD-per-1M-token map plus a conservative fallback.
Estimates are order-of-magnitude only — the recorded token counts are exact; the
cost is an approximation for the "估算成本" report column.
"""

from __future__ import annotations

_DEFAULT = (0.50, 2.00)

_PROVIDER_HINTS = {
    "deepseek": (0.55, 2.19),
    "openai": (2.50, 10.00),
    "gpt": (2.50, 10.00),
    "anthropic": (3.00, 15.00),
    "claude": (3.00, 15.00),
    "google": (1.25, 5.00),
    "gemini": (1.25, 5.00),
    "qwen": (0.40, 1.60),
    "glm": (0.40, 1.60),
    "minimax": (0.40, 1.60),
}


def estimate_cost(tokens_in: int, tokens_out: int, model: str | None = None, provider: str | None = None) -> float:
    """Return an estimated USD cost for the given token usage."""
    key = f"{model or ''} {provider or ''}".lower()
    pin, pout = _DEFAULT
    for hint, (i, o) in _PROVIDER_HINTS.items():
        if hint in key:
            pin, pout = i, o
            break
    return (tokens_in / 1_000_000.0) * pin + (tokens_out / 1_000_000.0) * pout
