"""Concept Sector Scanner (概念板块扫描).

A-share concept-themed stock screening via tushare ths_daily/ths_member APIs.
Finds hot concepts (AI, NEV, chip, etc.) and ranks constituent stocks,
feeding the best candidates into capital flow analysis.

Flow: concept ranking -> member extraction -> smart scoring -> output
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_TS_PRO = None
_CONCEPT_CACHE = {}


def _get_pro():
    global _TS_PRO
    if _TS_PRO is None:
        import tushare as ts
        import os
        token = os.environ.get("TUSHARE_TOKEN", "")
        _TS_PRO = ts.pro_api(token)
    return _TS_PRO


def _resolve_concept_name(code_or_name: str) -> tuple[str, str] | None:
    """Resolve a concept code or name to (code, name)."""
    try:
        pro = _get_pro()
        data = pro.ths_index()
        if data is None or data.empty:
            return None
        match = data[data["ths_code"] == code_or_name]
        if not match.empty:
            row = match.iloc[0]
            return (row["ths_code"], row["ths_name"])
        match = data[data["ths_name"].str.contains(code_or_name, na=False)]
        if not match.empty:
            row = match.iloc[0]
            return (row["ths_code"], row["ths_name"])
    except Exception:
        pass
    return None


def list_concepts(keyword: str | None = None) -> list[dict]:
    """List all THS concept sectors, optionally filtered by keyword."""
    try:
        pro = _get_pro()
        data = pro.ths_index()
        if data is None or data.empty:
            return []
        results = []
        for _, row in data.iterrows():
            name = str(row.get("ths_name", ""))
            code = str(row.get("ths_code", ""))
            if keyword and keyword.lower() not in name.lower():
                continue
            results.append({"code": code, "name": name})
        results.sort(key=lambda x: x["name"])
        return results
    except Exception as e:
        logger.error("Failed to list concepts: %s", e)
        return []


def get_concept_daily(concept_code: str, days: int = 90) -> list[dict]:
    """Get daily performance data for a concept sector."""
    try:
        pro = _get_pro()
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        data = pro.ths_daily(ts_code=concept_code, start_date=start_date, end_date=end_date)
        if data is None or data.empty:
            return []
        data = data.sort_values("trade_date", ascending=True)
        return data.to_dict(orient="records")
    except Exception as e:
        logger.error("Failed to get concept daily for %s: %s", concept_code, e)
        return []


def get_concept_members(concept_code: str) -> list[dict]:
    """Get constituent stocks of a concept sector."""
    global _CONCEPT_CACHE
    now = time.time()
    cached = _CONCEPT_CACHE.get(concept_code)
    if cached and (now - cached["fetched_at"]) < 3600:
        return cached["members"]
    try:
        pro = _get_pro()
        data = pro.ths_member(ts_code=concept_code)
        if data is None or data.empty:
            return []
        members = []
        for _, row in data.iterrows():
            members.append({
                "ts_code": str(row.get("code") or row.get("ts_code", "")),
                "name": str(row.get("name", "")),
                "concept": concept_code,
            })
        _CONCEPT_CACHE[concept_code] = {"members": members, "fetched_at": now}
        return members
    except Exception as e:
        logger.error("Failed to get concept members for %s: %s", concept_code, e)
        return []


def rank_concepts(lookback_days: int = 60, top_n: int = 20) -> list[dict]:
    """Rank concept sectors by recent performance (RPS-like)."""
    try:
        pro = _get_pro()
        index_data = pro.ths_index()
        if index_data is None or index_data.empty:
            return []
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=lookback_days+10)).strftime("%Y%m%d")
        concepts = []
        for _, row in index_data.iterrows():
            code = str(row["ths_code"])
            name = str(row["ths_name"])
            if not code:
                continue
            try:
                daily = pro.ths_daily(ts_code=code, start_date=start_date, end_date=end_date, limit=lookback_days+5)
                if daily is None or daily.empty or len(daily) < 10:
                    continue
                daily = daily.sort_values("trade_date", ascending=True)
                closes = daily["close"].values
                recent_ret = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
                if "pct_change" in daily.columns:
                    pct_changes = daily["pct_change"].dropna().tail(lookback_days).values
                    pos_ratio = sum(1 for x in pct_changes if x > 0) / max(len(pct_changes), 1) * 100
                else:
                    pos_ratio = 50
                if "vol" in daily.columns:
                    vol = daily["vol"].tail(30).values
                    vol_10 = vol[-10:].mean() if len(vol) >= 10 else 0
                    vol_30 = vol.mean() if len(vol) > 0 else 1
                    vol_ratio = (vol_10 / vol_30 - 1) * 100 if vol_30 > 0 else 0
                else:
                    vol_ratio = 0
                score = recent_ret * 0.4 + pos_ratio * 0.3 + max(min(vol_ratio, 50), -50) * 0.3
                concepts.append({
                    "code": code, "name": name,
                    "recent_return_pct": round(recent_ret, 2),
                    "pos_day_ratio": round(pos_ratio, 1),
                    "volume_trend_pct": round(vol_ratio, 1),
                    "score": round(score, 2),
                })
            except Exception:
                continue
        concepts.sort(key=lambda x: x["score"], reverse=True)
        return concepts[:top_n]
    except Exception as e:
        logger.error("Failed to rank concepts: %s", e)
        return []


def scan_concept_stocks(concept_code: str, top_n: int = 10) -> dict:
    """Scan stocks within a concept, return sorted by composite score."""
    from capitalradar.sector_scan.smart_scanner import _score_one_stock, _filter_st_stocks
    from capitalradar.dataflows.eastmoney_sector import get_daily_basic_batch

    concept_info = _resolve_concept_name(concept_code)
    if not concept_info:
        return {"error": f"Concept not found: {concept_code}", "stocks": []}
    code, name = concept_info

    members = get_concept_members(code)
    if not members:
        return {"concept": {"code": code, "name": name}, "stocks": [], "total": 0}

    members = _filter_st_stocks(members)
    if not members:
        return {"concept": {"code": code, "name": name}, "stocks": [], "total": 0}

    ts_codes = [m["ts_code"] for m in members]
    basics = get_daily_basic_batch(ts_codes)

    stock_info = []
    for m in members:
        basic = basics.get(m["ts_code"], {})
        if basic.get("total_mv", 0) <= 0:
            continue
        stock_info.append({**m, "total_mv": basic.get("total_mv", 0)})

    if not stock_info:
        return {"concept": {"code": code, "name": name}, "stocks": [], "total": 0}

    stock_info.sort(key=lambda x: x["total_mv"], reverse=True)
    stock_info = stock_info[:50]

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_score_one_stock, s, "leading", i): s for i, s in enumerate(stock_info)}
        for future in as_completed(futures):
            try:
                r = future.result()
                if r:
                    r["concept"] = name
                    results.append(r)
            except Exception:
                pass

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return {
        "concept": {"code": code, "name": name},
        "stocks": results[:top_n],
        "total": len(results),
    }
