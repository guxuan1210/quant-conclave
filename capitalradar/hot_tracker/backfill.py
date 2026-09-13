"""One-time backfill: resolve all pending stock_picks via the multi-source chain.

Usage:
    python -m capitalradar.hot_tracker.backfill

Resolves every pick where return_60d IS NULL — the picks stuck at "待结算"
with a 0.00 latest price. Idempotent: picks already resolved are skipped.
Requires the same env (.env with TUSHARE_TOKEN / network) as the web app.
"""

import logging

from capitalradar.default_config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def backfill(config: dict | None = None) -> int:
    """Run resolve_picks over all pending picks. Returns count resolved."""
    from web.results_store import resolve_picks

    cfg = config or DEFAULT_CONFIG.copy()
    count = resolve_picks(cfg)
    logger.info("Backfill resolved %d pending picks", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    resolved = backfill()
    print(f"Backfill complete: resolved {resolved} pending picks.")
