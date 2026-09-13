"""Smart Scanner — dual-strategy screening for Leading vs Improving industries.

Strategy per quadrant:
  - Leading (持续领涨): momentum + volume confirmation + sustained inflow
  - Improving (刚刚转强): reversal signal + volume spike + inflow turnaround

Scoring: momentum(40%) + volume(30%) + capital(30%)
Top-N per industry, then LLM batch analysis for final selection.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from quantconclave.dataflows.eastmoney_sector import (
    get_industry_stocks,
    get_stock_daily,
    get_daily_basic_batch,
    get_stock_moneyflow,
    calc_macd,
    detect_golden_cross,
)

logger = logging.getLogger(__name__)

_TS_PRO = None


def _get_pro():
    global _TS_PRO
    if _TS_PRO is None:
        import tushare as ts
        import os
        _TS_PRO = ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))
    return _TS_PRO


# ---------------------------------------------------------------------------
# Per-stock scoring
# ---------------------------------------------------------------------------


def _score_one_stock(s: dict, quadrant: str, idx: int = 0) -> dict | None:
    """Score a single stock. Returns dict with scores or None if no data."""
    ts_code = s["ts_code"]
    # Rate-limit: stagger requests to avoid Tushare 500/min limit
    if idx > 0:
        time.sleep(0.3)
    try:
        klines = get_stock_daily(ts_code, days=90)
    except Exception:
        return None
    if len(klines) < 30:
        return None

    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    latest_close = closes[-1]

    # --- Momentum Score (40%) ---
    ma20 = sum(closes[-21:-1]) / 20
    low_20 = min(closes[-20:])
    rsi = _calc_rsi(closes)

    klines_macd = calc_macd(klines)
    has_cross, cross_str = detect_golden_cross(klines_macd)

    momentum = 0.0
    if quadrant == "leading":
        # Strong trend: price > MA20, MACD bullish, steady uptrend
        if latest_close > ma20:
            momentum += 40
        dif_val = klines_macd[-1].get("dif", 0) or 0
        if dif_val > 0:
            momentum += 30
        if cross_str > 0:
            momentum += 20
        # Bonus: near 20-day high
        if latest_close >= max(closes[-5:]):
            momentum += 10
    else:
        # Improving (reversal): price bouncing from low
        rebound_pct = (latest_close - low_20) / low_20 * 100 if low_20 > 0 else 0
        if rebound_pct > 5:
            momentum += 40
        elif rebound_pct > 2:
            momentum += 20
        if rsi is not None and rsi < 50:
            momentum += 30  # still room to run
        if rsi is not None and rsi >= 30:
            momentum += 10
        if has_cross:
            momentum += 20

    # --- Volume Score (30%) ---
    avg_vol_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes[:-1]) / max(len(volumes) - 1, 1)
    recent_vol = sum(volumes[-3:]) / 3
    vol_ratio = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1

    volume = 0.0
    if quadrant == "leading":
        if vol_ratio > 1.5:
            volume += 50
        elif vol_ratio > 1.2:
            volume += 35
        elif vol_ratio > 1.0:
            volume += 20
        # Volume trend: increasing vs decreasing
        if sum(volumes[-5:]) > sum(volumes[-10:-5]):
            volume += 30
        else:
            volume += 10
        # Price direction on recent volume
        if latest_close > closes[-2] if len(closes) >= 2 else True:
            volume += 20
    else:
        # Improving: need significant volume spike as reversal confirmation
        if vol_ratio > 2.0:
            volume += 60
        elif vol_ratio > 1.5:
            volume += 45
        elif vol_ratio > 1.2:
            volume += 25
        else:
            volume += 10
        # Big up-day on volume
        if len(closes) >= 2 and latest_close > closes[-2] and vol_ratio > 1.3:
            volume += 40

    # --- Capital Score (30%) ---
    try:
        mf = get_stock_moneyflow(ts_code, days=5)
        main_force = mf.get("main_force_net", 0)
        trend = mf.get("trend", "stable")
        mf_ratio = mf.get("mf_ratio", 0)
        buy_elg = mf.get("buy_elg_ratio", 0)
        net_ratio = mf.get("net_inflow_ratio", 0)
    except Exception:
        main_force = 0
        trend = "stable"
        mf_ratio = 0
        buy_elg = 0
        net_ratio = 0

    capital = 0.0
    # Main force strength (0-30)
    if main_force > 10000:
        capital += 30
    elif main_force > 5000:
        capital += 25
    elif main_force > 2000:
        capital += 18
    elif main_force > 0:
        capital += 10
    else:
        capital += 3
    # Trend quality (0-20)
    if trend == "accelerating":
        capital += 20
    elif trend == "stable":
        capital += 12
    else:
        capital += 4
    # Main force vs retail ratio (0-20)
    if mf_ratio > 3:
        capital += 20
    elif mf_ratio > 2:
        capital += 15
    elif mf_ratio > 1:
        capital += 10
    else:
        capital += 5
    # Super-large order activity (0-15)
    if buy_elg > 30:
        capital += 15
    elif buy_elg > 20:
        capital += 10
    elif buy_elg > 10:
        capital += 7
    else:
        capital += 3
    # Net inflow intensity (0-15)
    if net_ratio > 8:
        capital += 15
    elif net_ratio > 5:
        capital += 10
    elif net_ratio > 2:
        capital += 6
    else:
        capital += 2

    # Normalize to 0-100
    momentum = min(100, momentum)
    volume = min(100, volume)
    capital = min(100, capital)

    total = momentum * 0.4 + volume * 0.3 + capital * 0.3

    return {
        "ts_code": ts_code,
        "code": s["symbol"],
        "name": s["name"],
        "close": latest_close,
        "momentum": round(momentum, 1),
        "volume_score": round(volume, 1),
        "capital_score": round(capital, 1),
        "total_score": round(total, 1),
        "net_inflow": round(net_inflow / 1e4, 2),
        "quadrant": quadrant,
        "has_golden_cross": has_cross,
        "rsi_14": rsi,
        "ticker": ts_code if not ts_code.endswith(".SH") else ts_code[:-3] + ".SS",
        "market_cap": s.get("total_mv", 0),
    }


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------



_ST_CACHE = None  # {ts_code: True/False}, True means it IS an ST stock
_ST_CACHE_DATE = None


def _filter_st_stocks(stocks: list[dict]) -> list[dict]:
    """Filter out ST (Special Treatment) stocks from the candidate list.

    Uses tushare namechange API to detect current ST status.
    Results are cached for 1 day since ST list changes slowly.
    """
    global _ST_CACHE, _ST_CACHE_DATE

    from datetime import date
    today = date.today().isoformat()

    if _ST_CACHE is not None and _ST_CACHE_DATE == today:
        # Use cached result
        filtered = [s for s in stocks if not _ST_CACHE.get(s.get("ts_code", ""), False)]
        removed = len(stocks) - len(filtered)
        if removed > 0:
            logger.info("ST filter: removed %d ST stocks (cached)", removed)
        return filtered

    # Build fresh ST cache
    try:
        pro = _get_pro()
        # Get all ST status changes (namechange API)
        # We check stocks whose name currently contains ST/*ST
        st_set = set()

        # Group tickers for efficient batch lookup
        # namechange returns historical name changes; we check latest entries
        all_codes = [s.get("ts_code", "") for s in stocks if s.get("ts_code")]
        batch_size = 50
        for i in range(0, len(all_codes), batch_size):
            batch = all_codes[i:i + batch_size]
            try:
                data = pro.namechange(ts_code=",".join(batch), fields="ts_code,name")
                if data is not None and not data.empty:
                    for _, row in data.iterrows():
                        name = str(row.get("name", ""))
                        if "ST" in name:
                            st_set.add(row["ts_code"])
            except Exception:
                continue

        _ST_CACHE = {code: (code in st_set) for code in all_codes}
        _ST_CACHE_DATE = today

        filtered = [s for s in stocks if not _ST_CACHE.get(s.get("ts_code", ""), False)]
        removed = len(stocks) - len(filtered)
        if removed > 0:
            logger.info("ST filter: removed %d ST stocks from %d total", removed, len(stocks))
        return filtered
    except Exception as e:
        logger.warning("ST filter failed (proceeding without): %s", e)
        return stocks


_HSGT_CACHE = None  # set of ts_codes that are HSGT eligible
_HSGT_CACHE_DATE = None


def _filter_hsgt_stocks(stocks: list[dict]) -> list[dict]:
    """Filter to only HSGT (????) eligible stocks.

    North-bound (??) capital is the most important institutional force
    in A-shares. By limiting analysis to HSGT-eligible stocks, we focus on
    tickers where foreign capital can actually participate - increasing the
    relevance of capital flow analysis.

    Results are cached for 1 day since HSGT constituent list is stable.
    Configurable via QUANTCONCLAVE_HSGT_ONLY env var or config key.
    """
    global _HSGT_CACHE, _HSGT_CACHE_DATE

    # Check config: hsgt_only can be disabled
    try:
        from quantconclave.dataflows.config import get_config
        cfg = get_config()
        if not cfg.get("hsgt_only", True):
            return stocks
    except Exception:
        pass

    from datetime import date
    today = date.today().isoformat()

    if _HSGT_CACHE is not None and _HSGT_CACHE_DATE == today:
        filtered = [s for s in stocks if s.get("ts_code", "") in _HSGT_CACHE]
        removed = len(stocks) - len(filtered)
        if removed > 0:
            logger.info("HSGT filter: kept %d/%d (removed %d non-HSGT stocks, cached)",
                       len(filtered), len(stocks), removed)
        return filtered

    # Build fresh HSGT cache
    try:
        pro = _get_pro()
        data = pro.hs_const(hs_type="SH")
        sh_set = set(data["ts_code"].values) if data is not None and not data.empty else set()
        data_sz = pro.hs_const(hs_type="SZ")
        sz_set = set(data_sz["ts_code"].values) if data_sz is not None and not data_sz.empty else set()
        _HSGT_CACHE = sh_set | sz_set
        _HSGT_CACHE_DATE = today

        filtered = [s for s in stocks if s.get("ts_code", "") in _HSGT_CACHE]
        removed = len(stocks) - len(filtered)
        if removed > 0:
            logger.info("HSGT filter: kept %d/%d (removed %d non-HSGT stocks)",
                       len(filtered), len(stocks), removed)
        return filtered
    except Exception as e:
        logger.warning("HSGT filter failed (proceeding without): %s", e)
        return stocks

def smart_scan_industry(industry_name: str, quadrant: str = "leading", top_n: int = 5,
                        rps_map: dict | None = None) -> list[dict]:
    """Smart-scan one industry with quadrant-aware scoring + RPS.

    Returns top-N stocks sorted by total_score descending.
    """
    stocks = get_industry_stocks(industry_name)
    if not stocks:
        return []

    # Filter out ST stocks before scoring (avoid wasting analysis on risky tickers)
    stocks = _filter_st_stocks(stocks)
    if not stocks:
        return []

    # Focus on HSGT-eligible stocks where foreign capital can participate
    stocks = _filter_hsgt_stocks(stocks)
    if not stocks:
        return []

    ts_codes = [s["ts_code"] for s in stocks]
    basics = get_daily_basic_batch(ts_codes)

    stock_info = []
    for s in stocks:
        basic = basics.get(s["ts_code"], {})
        if basic.get("total_mv", 0) <= 0:
            continue
        stock_info.append({**s, "total_mv": basic.get("total_mv", 0)})

    if not stock_info:
        return []

    # Cap at top 50 by market cap for performance
    stock_info.sort(key=lambda x: x["total_mv"], reverse=True)
    stock_info = stock_info[:50]

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_score_one_stock, s, quadrant, i): s for i, s in enumerate(stock_info)}
        for future in as_completed(futures):
            try:
                r = future.result()
                if r:
                    # Add RPS if available
                    if rps_map:
                        rps_val = rps_map.get(r["ts_code"])
                        r["rps"] = rps_val if rps_val is not None else None
                    results.append(r)
            except Exception:
                pass

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results[:top_n]


def run_smart_scan(industries: list[str], quadrants: dict[str, str],
                   top_n: int = 5, use_rps: bool = True) -> dict:
    """Run smart scan across multiple industries.

    Args:
        industries: List of industry names
        quadrants: {industry_name: "leading"|"improving"}
        top_n: Number of top stocks per industry
        use_rps: Whether to compute and attach RPS values

    Returns {"results": [...], "summary": {...}}
    """
    # Pre-compute RPS for all stocks (cached)
    rps_map = None
    if use_rps:
        try:
            from quantconclave.sector_scan.rps import compute_rps
            rps_map = compute_rps(120)
            logger.info("RPS loaded: %d stocks with RPS>90",
                        sum(1 for v in rps_map.values() if v is not None and v > 90))
        except Exception as e:
            logger.warning("RPS computation failed: %s", e)

    all_results = []
    for name in industries:
        q = quadrants.get(name, "leading")
        logger.info("Smart-scanning %s (%s)...", name, q)
        results = smart_scan_industry(name, q, top_n=top_n, rps_map=rps_map)
        for r in results:
            r["industry"] = name
        all_results.extend(results)

    # Deduplicate by ts_code
    seen = {}
    unique = []
    for r in all_results:
        if r["ts_code"] not in seen:
            seen[r["ts_code"]] = True
            unique.append(r)

    # Add ranks based on total_score within pool
    unique.sort(key=lambda x: x["total_score"], reverse=True)
    pool_size = len(unique)
    for rank_idx, r in enumerate(unique):
        r["pool_rank"] = rank_idx + 1
        # Bonus for top 10%
        if rank_idx < max(1, pool_size // 10):
            r["total_score"] = min(100, r["total_score"] + 8)
        elif rank_idx < max(3, pool_size // 5):
            r["total_score"] = min(100, r["total_score"] + 5)

    unique.sort(key=lambda x: x["total_score"], reverse=True)

    return {
        "results": unique,
        "total": len(unique),
        "by_quadrant": {
            "leading": len([r for r in unique if r["quadrant"] == "leading"]),
            "improving": len([r for r in unique if r["quadrant"] == "improving"]),
        },
    }
