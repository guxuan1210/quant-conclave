"""Weekly fixed-rule sampling for the evaluation main leaderboard.

Every week we sample ``csi300_count`` + ``csi500_count`` stocks from the CSI 300
and CSI 500 constituents, spread across industries, excluding ST / suspended /
recently-listed (< 120 trading days) / recently-sampled (< dedup_weeks) names.
The sample is deterministic for a given ``(seed, week_key)`` and frozen once
selected — never replaced because a later analysis looks unpromising.
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime

from .config import get_eval_config

_INDEX_CODES = {
    "csi300": "000300.SH",
    "csi500": "000905.SH",
}


def week_key(date_str: str) -> str:
    """ISO week key ``YYYY-Www`` for a ``YYYY-MM-DD`` date."""
    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def seed_for_week(seed: int, wk: str) -> int:
    """Deterministic, process-stable integer seed for a sampling week."""
    digest = hashlib.md5(f"{seed}:{wk}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def trading_days_since(list_date: str, selection_date: str, open_days: set[str]) -> int:
    """Count open trading days in ``[list_date, selection_date]``."""
    if not list_date:
        return 10 ** 9
    start = list_date.replace("-", "")
    end = selection_date.replace("-", "")
    if start > end:
        return 10 ** 9
    return sum(1 for d in open_days if start <= d <= end)


def filter_candidates(
    candidates: list[dict],
    *,
    st_codes: set[str] | None = None,
    traded_codes: set[str] | None = None,
    recent_tickers: set[str] | None = None,
    open_days: set[str] | None = None,
    selection_date: str = "",
    min_trading_days: int = 120,
) -> list[dict]:
    """Drop ST, suspended/missing-data, too-new, and recently-sampled names.

    ``candidates`` is a list of ``{code, name, industry, list_date}`` dicts
    (``code`` in tushare ``ts_code`` form, e.g. ``600519.SH``).
    """
    st_codes = st_codes or set()
    traded_codes = traded_codes or set()
    recent_tickers = recent_tickers or set()
    open_days = open_days or set()

    out = []
    for c in candidates:
        code = c.get("code", "")
        if not code or code in recent_tickers:
            continue
        if code in st_codes:
            continue
        if traded_codes and code not in traded_codes:
            # Not traded on selection_date → suspended or no data.
            continue
        if open_days and trading_days_since(c.get("list_date", ""), selection_date, open_days) < min_trading_days:
            continue
        out.append(c)
    return out


def stratified_sample(candidates: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Pick up to ``n`` candidates, spreading selection across industries.

    Industry stratification keeps a single sector from dominating the sample:
    candidates are grouped by industry and selected round-robin by industry,
    so the sample stays diversified even when one industry is over-represented.
    """
    if n <= 0 or not candidates:
        return []
    by_industry: dict[str, list[dict]] = {}
    for c in sorted(candidates, key=lambda item: (
        item.get("industry") or "(unknown)", item.get("code", "")
    )):
        ind = c.get("industry") or "(unknown)"
        by_industry.setdefault(ind, []).append(c)

    # Shuffle within each industry (stable under the given rng), then round-robin.
    buckets = []
    for ind in sorted(by_industry):
        bucket = list(by_industry[ind])
        rng.shuffle(bucket)
        buckets.append(bucket)

    picked: list[dict] = []
    idx = 0
    while len(picked) < n and buckets:
        progressed = False
        for bucket in buckets:
            if idx < len(bucket) and len(picked) < n:
                picked.append(bucket[idx])
                progressed = True
        if not progressed:
            break
        idx += 1
    return picked


def sample_weekly_stocks(
    config: dict | None,
    selection_date: str,
    constituents: dict[str, list[dict]],
    *,
    recent_tickers: set[str] | None = None,
    st_codes: set[str] | None = None,
    traded_codes: set[str] | None = None,
    open_days: set[str] | None = None,
) -> list[dict]:
    """Sample this week's fixed set of stocks.

    ``constituents`` maps ``"csi300"``/``"csi500"`` to candidate dicts
    ``{code, name, industry, list_date}``. Returns a list of
    ``{code, name, industry, index_source, week_key}`` dicts.
    """
    ecfg = get_eval_config(config)
    rng = random.Random(seed_for_week(ecfg["seed"], week_key(selection_date)))
    sampled: list[dict] = []

    for index_key in ("csi300", "csi500"):
        count = ecfg[f"{index_key}_count"]
        candidates = filter_candidates(
            constituents.get(index_key, []),
            st_codes=st_codes,
            traded_codes=traded_codes,
            recent_tickers=recent_tickers,
            open_days=open_days,
            selection_date=selection_date,
            min_trading_days=120,
        )
        for c in stratified_sample(candidates, count, rng):
            sampled.append({
                "code": c.get("code", ""),
                "name": c.get("name", ""),
                "industry": c.get("industry", ""),
                "index_source": index_key,
                "week_key": week_key(selection_date),
            })
    return sampled


def fetch_constituents(config: dict | None, selection_date: str) -> dict[str, list[dict]]:
    """Fetch CSI 300 / CSI 500 constituents with name + industry.

    Tries tushare first (name/industry/list_date rich), then falls back to
    AKShare (code + name only) when the tushare token lacks the index/stock
    interfaces. Returns ``{index_key: [{code, name, industry, list_date}]}``.
    """
    result = _fetch_constituents_tushare(config, selection_date)
    if result.get("csi300") and result.get("csi500"):
        return result
    logging.getLogger(__name__).warning("tushare constituents empty, falling back to akshare")
    return _fetch_constituents_akshare()


def _fetch_constituents_tushare(config: dict | None, selection_date: str) -> dict[str, list[dict]]:
    from quantconclave.dataflows.tushare_data import _get_pro

    pro = _get_pro()
    meta = {}
    try:
        basic = pro.stock_basic(exchange="", list_status="L",
                                fields="ts_code,name,industry,list_date")
        if basic is not None and not basic.empty:
            for _, r in basic.iterrows():
                meta[r["ts_code"]] = {
                    "name": r.get("name", ""),
                    "industry": r.get("industry", ""),
                    "list_date": r.get("list_date", ""),
                }
    except Exception as e:
        logging.getLogger(__name__).warning("tushare stock_basic failed: %s", e)

    result: dict[str, list[dict]] = {}
    for index_key, index_code in _INDEX_CODES.items():
        codes: list[str] = []
        try:
            from datetime import datetime as _dt
            for m in range(3):
                month = _dt.now().month - m if (_dt.now().month - m) > 0 else 12
                year = _dt.now().year if m == 0 or _dt.now().month - m > 0 else _dt.now().year - 1
                df = pro.index_weight(index_code=index_code, start_date="20260101",
                                      end_date=f"{year}{month:02d}28")
                if df is not None and not df.empty:
                    latest = df["trade_date"].max()
                    codes = df[df["trade_date"] == latest]["con_code"].tolist()
                    break
        except Exception as e:
            logging.getLogger(__name__).warning("index_weight failed for %s: %s", index_code, e)

        result[index_key] = [
            {"code": c, "name": meta.get(c, {}).get("name", ""),
             "industry": meta.get(c, {}).get("industry", ""),
             "list_date": meta.get(c, {}).get("list_date", "")}
            for c in codes if c
        ]
    return result


def _fetch_constituents_akshare() -> dict[str, list[dict]]:
    from quantconclave.dataflows.akshare_data import get_index_constituents_akshare

    result: dict[str, list[dict]] = {}
    for index_key, index_code in _INDEX_CODES.items():
        try:
            result[index_key] = get_index_constituents_akshare(index_code[:6])
        except Exception as e:
            logging.getLogger(__name__).warning("akshare constituents failed for %s: %s", index_code, e)
            result[index_key] = []
    return result


def fetch_exclusion_sets(config: dict | None, selection_date: str) -> tuple[set[str], set[str], set[str]]:
    """Return ``(st_codes, traded_codes, open_days)`` for sampling-time filters.

    ``st_codes`` via tushare ``namechange`` (falling back to AKShare's stock
    name map), ``traded_codes`` via ``pro.daily`` on the selection date,
    ``open_days`` from the cached trade calendar. Any individual source may
    fail independently and degrade to an empty set.
    """
    st_codes, traded_codes, open_days = _fetch_exclusion_sets_tushare(config, selection_date)
    if not st_codes:
        st_codes = _fetch_st_codes_akshare()
    return st_codes, traded_codes, open_days


def _fetch_exclusion_sets_tushare(config: dict | None, selection_date: str) -> tuple[set[str], set[str], set[str]]:
    from quantconclave.dataflows.tushare_data import _get_pro
    pro = _get_pro()

    st_codes: set[str] = set()
    try:
        from quantconclave.sector_scan.top_gainers import _filter_st_codes
        names = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        if names is not None and not names.empty:
            st_codes = _filter_st_codes(pro, names["ts_code"].tolist())
    except Exception:
        pass

    traded_codes: set[str] = set()
    try:
        daily = pro.daily(trade_date=selection_date.replace("-", ""))
        if daily is not None and not daily.empty:
            traded_codes = set(daily["ts_code"].tolist())
    except Exception:
        pass

    open_days: set[str] = set()
    try:
        from web.trade_cal import get_open_days
        open_days = get_open_days()
    except Exception:
        pass

    return st_codes, traded_codes, open_days


def _fetch_st_codes_akshare() -> set[str]:
    """ST/*ST codes derived from the AKShare full-market name map (ts_code form)."""
    from quantconclave.dataflows.akshare_data import get_stock_name_map_akshare
    try:
        name_map = get_stock_name_map_akshare()
    except Exception as e:
        logging.getLogger(__name__).warning("akshare stock name map failed: %s", e)
        return set()
    return {code for code, name in name_map.items() if "ST" in name.upper()}
