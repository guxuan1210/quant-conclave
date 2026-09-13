"""Sector rotation + MACD golden-cross scanner.

Orchestrates: sector fund-flow detection → leader stock filtering
→ golden-cross identification → candidate ranking.

This is a NEW pre-screening layer that runs BEFORE the existing
QuantConclave LLM pipeline. It does NOT modify any existing code.

Data source: Tushare (TUSHARE_TOKEN required).
"""

from __future__ import annotations

import logging

from quantconclave.dataflows.eastmoney_sector import (
    SectorInfo,
    StockCandidate,
    get_sector_list,
    scan_candidates,
)

logger = logging.getLogger(__name__)


def get_sectors_sorted(fund_flow_days: str = "1d") -> list[dict]:
    """Return all A-share industries (申万分类), sorted by stock count descending.

    Args:
        fund_flow_days: Ignored (kept for API compatibility).
    """
    sectors = get_sector_list()

    return [
        {
            "name": s.name,
            "stock_count": s.stock_count,
        }
        for s in sectors
    ]


def scan_sector(industry_name: str, sector_name: str = "",
                strategy_groups: list | None = None,
                cap_percent: int = 20, require_positive_inflow: bool = False) -> dict:
    """Run a full scan on one industry and return the candidate list.

    When no strategy candidates match, returns ALL stocks in the industry
    with basic data (no strategy filtering) as a fallback.
    """
    name = sector_name or industry_name
    logger.info("Scanning industry: %s with %d strategy groups ...", name,
                len(strategy_groups) if strategy_groups else 0)
    candidates = scan_candidates(industry_name, strategy_groups=strategy_groups,
                                 cap_percent=cap_percent,
                                 require_positive_inflow=require_positive_inflow)

    # Fallback: if no strategy matches, return all stocks with basic data
    fallback_all = None
    if not candidates:
        from quantconclave.dataflows.eastmoney_sector import get_industry_stocks, get_daily_basic_batch
        stocks = get_industry_stocks(industry_name)
        if stocks:
            ts_codes = [s["ts_code"] for s in stocks]
            basics = get_daily_basic_batch(ts_codes)
            all_list = []
            for s in stocks:
                basic = basics.get(s["ts_code"], {})
                ticker = s["ts_code"]
                if ticker.endswith(".SH"):
                    ticker = ticker[:-3] + ".SS"
                all_list.append({
                    "code": s["symbol"],
                    "name": s["name"],
                    "ts_code": s["ts_code"],
                    "ticker": ticker,
                    "close": basic.get("close", 0),
                    "change_pct": basic.get("pct_chg", 0),
                    "market_cap": basic.get("total_mv", 0),
                    "score": 0,
                    "golden_cross": False,
                    "main_net_inflow": 0,
                    "cross_strength": 0,
                })
            all_list.sort(key=lambda x: x["market_cap"], reverse=True)
            fallback_all = all_list[:50]  # cap at 50

    return {
        "sector_code": industry_name,
        "sector_name": name,
        "candidates": [_candidate_to_dict(c) for c in candidates],
        "total_candidates": len(candidates),
        "fallback_all": fallback_all,
    }


def _candidate_to_dict(c: StockCandidate) -> dict:
    """Convert a StockCandidate to a JSON-safe dict."""
    # Derive standard yfinance ticker from ts_code
    # Tushare uses .SH for Shanghai, yfinance needs .SS
    ticker = c.ts_code
    if ticker.endswith(".SH"):
        ticker = ticker[:-3] + ".SS"
    return {
        "code": c.code,
        "name": c.name,
        "ts_code": c.ts_code,
        "close": c.close,
        "change_pct": c.change_pct,
        "market_cap": c.market_cap,
        "main_net_inflow": c.main_net_inflow,
        "main_inflow_ratio": c.main_inflow_ratio,
        "golden_cross": c.golden_cross,
        "cross_strength": c.cross_strength,
        "score": c.score,
        "ticker": ticker,
    }
