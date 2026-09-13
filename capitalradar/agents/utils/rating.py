"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re
from typing import Tuple


# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Words that should never be matched as ratings even if they appear
# near "Rating:" labels or in free text.
_SKIP_WORDS: Tuple[str, ...] = ("bearish", "bullish", "rating", "ratings")

# Chinese rating mapping
_CN_RATING_MAP = {
    "买入": "Buy", "增持": "Overweight", "持有": "Hold",
    "减持": "Underweight", "卖出": "Sell",
}

# Matches "Rating: X" / "rating - X" / "评级: X" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(r"(?:rating|评级).*?[:\-：][\s*]*(\S+)", re.IGNORECASE)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text (English + Chinese).

    Three-pass strategy:
    1. Look for an explicit "Rating: X" or "评级: X" label.
    2. Look for Chinese rating words (买入/增持/持有/减持/卖出).
    3. Fall back to English rating words anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    # Pass 1: "Rating: X" or "评级: X" label
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m:
            word = m.group(1).strip("*:.,").lower()
            if word in _RATING_SET and word not in _SKIP_WORDS:
                return word.capitalize()
            # Check if it's a Chinese rating
            for cn, en in _CN_RATING_MAP.items():
                if cn in m.group(1):
                    return en

    # Pass 2: Chinese rating words anywhere
    for cn, en in _CN_RATING_MAP.items():
        if cn in text:
            return en

    # Pass 3: English rating words anywhere
    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET and clean not in _SKIP_WORDS:
                return clean.capitalize()

    return default
