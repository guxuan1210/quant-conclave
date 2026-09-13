"""Top Gainers & Multi-Factor Ranking — Market-wide stock screening.

Provides two core functions:
- get_top_gainers: Full-market daily top gainers ranked by % change
- get_multi_factor_ranking: Composite ranking by momentum + RPS + capital flow + volume

These tools give the AI Pick agent the ability to screen stocks directly from
the entire ~5000-stock A-share universe without requiring industry pre-selection.
"""

from __future__ import annotations

import logging
import os
import time as _time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tushare client (lazy init, rate-limited — same pattern as eastmoney_sector.py)
# ---------------------------------------------------------------------------

_TS_PRO = None
_LAST_CALL = 0.0
_MIN_INTERVAL = 0.35  # ~170 calls/min, well under 200 limit


def _get_pro():
    global _TS_PRO, _LAST_CALL
    elapsed = _time.time() - _LAST_CALL
    if elapsed < _MIN_INTERVAL:
        _time.sleep(_MIN_INTERVAL - elapsed)
    if _TS_PRO is None:
        import tushare as ts
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN not set in environment")
        _TS_PRO = ts.pro_api(token)
    _LAST_CALL = _time.time()
    return _TS_PRO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_latest_trade_dates(pro, n: int = 5) -> list[str]:
    """Get the most recent open trading dates, newest first (YYYYMMDD)."""
    today = datetime.now().strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
        cal = cal[cal["is_open"] == 1]
        return sorted(cal["cal_date"].tolist(), reverse=True)[:max(n, 10)]
    except Exception:
        return [today]


def _build_name_map(pro, ts_codes: list[str]) -> dict[str, str]:
    """Build ts_code -> stock name mapping via stock_basic."""
    name_map = {}
    try:
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,name")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name_map[row["ts_code"]] = row.get("name", "")
    except Exception as e:
        logger.warning("stock_basic name lookup failed: %s", e)
    return name_map


def _filter_st_codes(pro, ts_codes: list[str]) -> set[str]:
    """Return set of ts_codes that ARE ST stocks (should be excluded).

    Uses namechange API to detect ST/*ST in stock names. Batch size 50.
    """
    st_codes = set()
    batch_size = 50
    for i in range(0, len(ts_codes), batch_size):
        batch = ts_codes[i:i + batch_size]
        try:
            data = pro.namechange(ts_code=",".join(batch),
                                  fields="ts_code,name")
            if data is not None and not data.empty:
                for _, row in data.iterrows():
                    name = str(row.get("name", ""))
                    if "ST" in name:
                        st_codes.add(row["ts_code"])
        except Exception:
            continue
    return st_codes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_top_gainers(
    trade_date: str = None,
    top_n: int = 50,
    min_amount: float = 50000000.0,  # 5000万默认
    filter_st: bool = True,
) -> str:
    """Get today's top gainers across the ENTIRE A-share market.

    Covers ALL ~5000 actively listed stocks. Filters out ST and suspended
    stocks automatically. Returns CSV with: ts_code, name, pct_chg, close,
    pre_close, amount, turnover_rate, pe, pb, total_mv.

    Args:
        trade_date: Date in YYYYMMDD format. None = latest trading day.
        top_n: How many top gainers to return.
        min_amount: Minimum turnover (成交额) in yuan to filter illiquid stocks.
        filter_st: Exclude ST (Special Treatment) stocks.
    """
    pro = _get_pro()

    # Resolve trade date
    recent_dates = _find_latest_trade_dates(pro)
    if trade_date is None:
        trade_date = recent_dates[0]
    # Find previous trading day
    try:
        idx = recent_dates.index(trade_date)
        prev_date = recent_dates[idx + 1] if idx + 1 < len(recent_dates) else recent_dates[0]
    except ValueError:
        prev_date = recent_dates[1] if len(recent_dates) > 1 else trade_date

    logger.info("Top gainers: trade_date=%s prev=%s", trade_date, prev_date)

    # ── Step 1: Fetch daily_basic — try recent trade dates ──
    df_today = None
    for d in recent_dates[:8]:
        try:
            df_today = pro.daily_basic(trade_date=d,
                                       fields="ts_code,close,total_mv,pe,pb,turnover_rate,volume_ratio")
        except Exception:
            continue
        if df_today is not None and not df_today.empty:
            trade_date = d
            break
            break

    if df_today is None or df_today.empty:
        return "# No data: daily_basic returned empty for all retry dates.\n"

    df_prev = None
    for d in [prev_date] + recent_dates[1:3]:
        try:
            df_prev = pro.daily_basic(trade_date=d,
                                      fields="ts_code,close")
        except Exception:
            continue
        if df_prev is not None and not df_prev.empty:
            prev_date = d
            break

    if df_prev is None or df_prev.empty:
        return "# No data: previous day daily_basic returned empty.\n"

    # ── Step 2: Compute pct_chg ──
    prev_closes = {}
    for _, row in df_prev.iterrows():
        prev_closes[row["ts_code"]] = float(row.get("close", 0) or 0)

    today_data = {}
    for _, row in df_today.iterrows():
        code = row["ts_code"]
        close = float(row.get("close", 0) or 0)
        pre = prev_closes.get(code, 0)
        if pre > 0 and close > 0:
            pct_chg = (close - pre) / pre * 100
        else:
            pct_chg = 0.0
        today_data[code] = {
            "close": close,
            "pct_chg": round(pct_chg, 2),
            "total_mv": float(row.get("total_mv", 0) or 0),
            "pe": float(row.get("pe", 0) or 0),
            "pb": float(row.get("pb", 0) or 0),
            "turnover_rate": float(row.get("turnover_rate", 0) or 0),
            "volume_ratio": float(row.get("volume_ratio", 0) or 0),
        }

    # ── Step 3: Compute amount from daily API ──
    # daily_basic doesn't include amount, so we fetch from daily()
    all_codes = list(today_data.keys())
    amount_map = {}
    try:
        # daily() with no ts_code filter returns full market for one day
        df_daily = pro.daily(trade_date=trade_date,
                             fields="ts_code,amount")
        if df_daily is not None and not df_daily.empty:
            for _, row in df_daily.iterrows():
                amount_map[row["ts_code"]] = float(row.get("amount", 0) or 0)
    except Exception as e:
        logger.warning("daily amount fetch failed: %s", e)

    # ── Step 4: Build name map ──
    name_map = _build_name_map(pro, all_codes)

    # ── Step 5: Filter ST ──
    st_set = set()
    if filter_st:
        st_set = _filter_st_codes(pro, all_codes)

    # ── Step 6: Filter, sort, and produce output ──
    candidates = []
    for code, data in today_data.items():
        # Skip ST
        if code in st_set:
            continue
        # Skip suspended (pct_chg near 0 with no turnover)
        pct = data["pct_chg"]
        amount = amount_map.get(code, 0)
        if amount < min_amount:
            continue
        # Skip if likely suspended: zero change + zero amount
        if abs(pct) < 0.001 and amount < 100000:
            continue

        candidates.append({
            "ts_code": code,
            "name": name_map.get(code, ""),
            "pct_chg": pct,
            "close": data["close"],
            "amount": amount,
            "turnover_rate": data["turnover_rate"],
            "pe": data["pe"],
            "pb": data["pb"],
            "total_mv": data["total_mv"],
            "volume_ratio": data["volume_ratio"],
        })

    candidates.sort(key=lambda x: x["pct_chg"], reverse=True)
    top = candidates[:top_n]

    # ── Produce CSV output ──
    lines = [
        f"# Top {len(top)} Gainers — {trade_date}",
        f"# Filter: min_amount >= {min_amount/1e8:.1f}亿, ST={'excluded' if filter_st else 'included'}",
        "",
        "ts_code,name,pct_chg,close,amount,turnover_rate,volume_ratio,pe,pb,total_mv",
    ]
    for s in top:
        lines.append(
            f"{s['ts_code']},{s['name']},{s['pct_chg']:.2f},{s['close']:.2f},"
            f"{s['amount']:.0f},{s['turnover_rate']:.2f},{s['volume_ratio']:.2f},"
            f"{s['pe']:.2f},{s['pb']:.2f},{s['total_mv']:.0f}"
        )

    logger.info("Top gainers: %d candidates, returned %d", len(candidates), len(top))
    return "\n".join(lines)


def get_multi_factor_ranking(
    trade_date: str = None,
    top_n: int = 50,
    min_amount: float = 50000000.0,
) -> str:
    """Multi-factor composite ranking across the ENTIRE A-share market.

    Composite score weights:
      - Price momentum (pct_chg): 30%
      - RPS relative strength: 25%
      - Main force net inflow (5-day): 25%
      - Volume ratio: 20%

    All factors are min-max normalized to [0, 1] before scoring.
    Filters ST stocks and illiquid stocks (< min_amount turnover).

    Returns CSV with individual factor scores so the LLM can explain WHY
    each stock ranks high.
    """
    pro = _get_pro()

    # Resolve trade date
    recent_dates = _find_latest_trade_dates(pro)
    if trade_date is None:
        trade_date = recent_dates[0]

    logger.info("Multi-factor ranking: trade_date=%s top_n=%d", trade_date, top_n)

    # ── Step 1: Get top gainers as base candidate pool (wider net) ──
    # We widen to top 200 gainers then score them — this avoids scoring all 5000 stocks
    raw = get_top_gainers(trade_date=trade_date, top_n=200,
                          min_amount=min_amount, filter_st=True)
    if raw.startswith("# No data"):
        return raw

    # Parse candidates from CSV
    candidates = []
    header_found = False
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("ts_code,"):
            header_found = True
            continue
        if not header_found:
            continue
        parts = line.split(",")
        if len(parts) < 10:
            continue
        try:
            candidates.append({
                "ts_code": parts[0],
                "name": parts[1],
                "pct_chg": float(parts[2]),
                "close": float(parts[3]),
                "amount": float(parts[4]),
                "turnover_rate": float(parts[5]),
                "volume_ratio": float(parts[6]),
                "pe": float(parts[7]),
                "pb": float(parts[8]),
                "total_mv": float(parts[9]),
            })
        except (ValueError, IndexError):
            continue

    if not candidates:
        return "# No candidates after parsing top gainers.\n"

    codes = [c["ts_code"] for c in candidates]
    n = len(candidates)
    logger.info("Multi-factor: scoring %d candidates", n)

    # ── Step 2: RPS scores ──
    rps_scores = {}
    try:
        from quantconclave.sector_scan.rps import compute_rps
        rps_all = compute_rps(lookback_days=120)
        for code in codes:
            v = rps_all.get(code)
            rps_scores[code] = v if v is not None else 50.0  # neutral default
    except Exception as e:
        logger.warning("RPS failed: %s, using neutral 50", e)
        rps_scores = {code: 50.0 for code in codes}

    # ── Step 3: Main force net inflow (5-day, batch fetch) ──
    inflow_scores = {}
    try:
        from quantconclave.dataflows.eastmoney_sector import get_stock_moneyflow
        for c in candidates:
            code = c["ts_code"]
            try:
                mf = get_stock_moneyflow(code, days=5)
                # net_amount is in 万元, convert to 亿 for readability
                inflow_scores[code] = mf.get("net_amount", 0.0)
            except Exception:
                inflow_scores[code] = 0.0
    except Exception as e:
        logger.warning("Money flow batch failed: %s", e)
        inflow_scores = {code: 0.0 for code in codes}

    # ── Step 4: Normalize and compute composite ──
    # Min-max normalization helpers
    def _minmax(values: list[float]) -> list[float]:
        mn, mx = min(values), max(values)
        if mx == mn:
            return [0.5] * len(values)
        return [(v - mn) / (mx - mn) for v in values]

    pct_vals = [c["pct_chg"] for c in candidates]
    rps_vals = [rps_scores.get(c["ts_code"], 50.0) for c in candidates]
    inflow_vals = [inflow_scores.get(c["ts_code"], 0.0) for c in candidates]
    vol_vals = [c["volume_ratio"] for c in candidates]

    pct_norm = _minmax(pct_vals)
    rps_norm = _minmax(rps_vals)
    inflow_norm = _minmax(inflow_vals)
    vol_norm = _minmax(vol_vals)

    for i, c in enumerate(candidates):
        composite = (
            pct_norm[i] * 0.30
            + rps_norm[i] * 0.25
            + inflow_norm[i] * 0.25
            + vol_norm[i] * 0.20
        )
        c["rps"] = round(rps_vals[i], 1)
        c["net_inflow"] = round(inflow_vals[i], 2)  # 万元
        c["score_momentum"] = round(pct_norm[i] * 100, 1)
        c["score_rps"] = round(rps_norm[i] * 100, 1)
        c["score_inflow"] = round(inflow_norm[i] * 100, 1)
        c["score_volume"] = round(vol_norm[i] * 100, 1)
        c["composite"] = round(composite * 100, 1)

    candidates.sort(key=lambda x: x["composite"], reverse=True)
    top = candidates[:top_n]

    # ── Produce CSV output ──
    lines = [
        f"# Multi-Factor Ranking — Top {len(top)} — {trade_date}",
        f"# Weights: Momentum 30% | RPS(120d) 25% | 5d Net Inflow 25% | Volume Ratio 20%",
        f"# All scores normalized 0-100. Composite = weighted sum.",
        "",
        "ts_code,name,pct_chg,close,amount,turnover_rate,volume_ratio,pe,pb,total_mv,"
        "rps,net_inflow_wan,score_momentum,score_rps,score_inflow,score_volume,composite",
    ]
    for s in top:
        lines.append(
            f"{s['ts_code']},{s['name']},{s['pct_chg']:.2f},{s['close']:.2f},"
            f"{s['amount']:.0f},{s['turnover_rate']:.2f},{s['volume_ratio']:.2f},"
            f"{s['pe']:.2f},{s['pb']:.2f},{s['total_mv']:.0f},"
            f"{s['rps']},{s['net_inflow']:.2f},"
            f"{s['score_momentum']},{s['score_rps']},{s['score_inflow']},{s['score_volume']},"
            f"{s['composite']}"
        )

    logger.info("Multi-factor ranking: scored %d, returned %d", n, len(top))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top Net Inflow — pure capital flow ranking
# ---------------------------------------------------------------------------

def get_top_net_inflow(
    trade_date: str = None,
    top_n: int = 50,
    min_amount: float = 3000000.0,  # 300万 — works with free-tier tushare data
) -> str:
    """Get today's TOP stocks by daily MAIN FORCE NET INFLOW across the ENTIRE A-share market.

    Ranks ALL ~5000 actively listed stocks purely by 主力净流入额 (net main-force
    capital inflow in CNY).  Filters ST stocks and illiquid stocks.

    This is different from get_multi_factor_ranking: it ranks by RAW net inflow,
    not a composite score.  Use this when the user wants to see "which stocks are
    attracting the most institutional money TODAY."

    Args:
        trade_date: Date in YYYYMMDD format.  None = latest trading day.
        top_n: How many top inflow stocks to return.
        min_amount: Minimum turnover (成交额) in yuan to filter illiquid stocks.

    Returns CSV with: ts_code, name, net_amount, pct_chg, close, amount,
    turnover_rate, volume_ratio, pe, pb, total_mv.
    """
    pro = _get_pro()

    # Resolve trade date
    recent_dates = _find_latest_trade_dates(pro)
    if trade_date is None:
        trade_date = recent_dates[0]

    try:
        idx = recent_dates.index(trade_date)
        prev_date = recent_dates[idx + 1] if idx + 1 < len(recent_dates) else recent_dates[0]
    except ValueError:
        prev_date = recent_dates[1] if len(recent_dates) > 1 else trade_date

    logger.info("Top net inflow: trade_date=%s prev=%s top_n=%d", trade_date, prev_date, top_n)

    # ── Step 1: Fetch full-market moneyflow for the target date ──
    try:
        df_mf = pro.moneyflow(trade_date=trade_date)
    except Exception as e:
        return f"# Moneyflow query failed for {trade_date}: {e}\n"

    if df_mf is None or df_mf.empty:
        # Auto-fallback: try previous trading day(s) when no data for requested date
        found_fb = False
        fallback_idx = recent_dates.index(trade_date) if trade_date in recent_dates else -1
        for offset in range(1, min(5, len(recent_dates))):
            try:
                fb_date = recent_dates[fallback_idx + offset] if fallback_idx >= 0 else recent_dates[offset]
            except IndexError:
                break
            try:
                df_mf = pro.moneyflow(trade_date=fb_date)
                if df_mf is not None and not df_mf.empty:
                    logger.info("Top net inflow: fell back from %s to %s (%d stocks)", trade_date, fb_date, len(df_mf))
                    trade_date = fb_date  # update for downstream display
                    found_fb = True
                    break
            except Exception:
                continue
        if not found_fb:
            return f"# No moneyflow data for {trade_date} or recent trading days.\n"
        # Recalculate prev_date relative to new trade_date after fallback
        try:
            idx = recent_dates.index(trade_date)
            prev_date = recent_dates[idx + 1] if idx + 1 < len(recent_dates) else recent_dates[0]
        except ValueError:
            prev_date = recent_dates[1] if len(recent_dates) > 1 else trade_date
        logger.info("Top net inflow (after fallback): trade_date=%s prev_date=%s", trade_date, prev_date)
 
    logger.info("Top net inflow: %d stocks in moneyflow", len(df_mf))

    # ── Step 2: Fetch daily_basic for today + previous day (price, PE, PB, etc.) ──
    df_db = None
    df_db_prev = None
    for d in [trade_date] + recent_dates[:2]:
        try:
            df_db = pro.daily_basic(trade_date=d,
                                    fields="ts_code,close,total_mv,pe,pb,turnover_rate,volume_ratio")
            if df_db is not None and not df_db.empty:
                trade_date_actual = d
                break
        except Exception:
            continue

    if df_db is None or df_db.empty:
        return f"# No daily_basic data for {trade_date} or fallback dates.\n"

    for d in [prev_date] + recent_dates[1:3]:
        try:
            df_db_prev = pro.daily_basic(trade_date=d, fields="ts_code,close")
            if df_db_prev is not None and not df_db_prev.empty:
                prev_date_actual = d
                break
        except Exception:
            continue

    if df_db_prev is None or df_db_prev.empty:
        return f"# No previous-day daily_basic data.\n"

    # ── Step 3: Build name map ──
    all_codes = set(df_mf["ts_code"].tolist())
    name_map = _build_name_map(pro, list(all_codes))

    # ── Step 4: Filter ST ──
    st_set = _filter_st_codes(pro, list(all_codes))

    # ── Step 5: Parse daily_basic into dicts ──
    prev_closes = {}
    for _, row in df_db_prev.iterrows():
        try:
            prev_closes[row["ts_code"]] = float(row.get("close", 0) or 0)
        except (ValueError, TypeError):
            continue

    basics = {}
    for _, row in df_db.iterrows():
        code = str(row["ts_code"])
        try:
            close = float(row.get("close", 0) or 0)
            basics[code] = {
                "close": close,
                "total_mv": float(row.get("total_mv", 0) or 0),
                "pe": float(row.get("pe", 0) or 0),
                "pb": float(row.get("pb", 0) or 0),
                "turnover_rate": float(row.get("turnover_rate", 0) or 0),
                "volume_ratio": float(row.get("volume_ratio", 0) or 0),
            }
        except (ValueError, TypeError):
            continue

    # ── Step 6: Build candidate list from moneyflow ──
    candidates = []
    for _, row in df_mf.iterrows():
        code = str(row.get("ts_code", ""))
        # Skip ST
        if code in st_set:
            continue
        net_amount = float(row.get("net_mf_amount", 0) or 0)
        # Estimate turnover from big-order buy+sell amounts
        buy_elg = float(row.get("buy_elg_amount", 0) or 0)
        sell_elg = float(row.get("sell_elg_amount", 0) or 0)
        buy_lg = float(row.get("buy_lg_amount", 0) or 0)
        sell_lg = float(row.get("sell_lg_amount", 0) or 0)
        buy_md = float(row.get("buy_md_amount", 0) or 0)
        sell_md = float(row.get("sell_md_amount", 0) or 0)
        buy_sm = float(row.get("buy_sm_amount", 0) or 0)
        sell_sm = float(row.get("sell_sm_amount", 0) or 0)
        estimated_amount = buy_elg + sell_elg + buy_lg + sell_lg + buy_md + sell_md + buy_sm + sell_sm

        if estimated_amount < min_amount:
            continue

        b = basics.get(code, {})
        close = b.get("close", 0.0)
        pre = prev_closes.get(code, 0.0)
        if pre > 0 and close > 0:
            pct_chg = (close - pre) / pre * 100
        else:
            pct_chg = 0.0

        candidates.append({
            "ts_code": code,
            "name": name_map.get(code, ""),
            "net_amount": net_amount,
            "pct_chg": round(pct_chg, 2),
            "close": close,
            "amount": estimated_amount,
            "turnover_rate": b.get("turnover_rate", 0.0),
            "volume_ratio": b.get("volume_ratio", 0.0),
            "pe": b.get("pe", 0.0),
            "pb": b.get("pb", 0.0),
            "total_mv": b.get("total_mv", 0.0),
        })

    if not candidates:
        return (
            f"# No candidates after filtering.\n"
            f"# Moneyflow stocks: {len(df_mf)}, "
            f"ST filtered: {len(st_set)}, "
            f"min_amount: {min_amount/1e8:.1f}亿\n"
        )

    # ── Step 7: Sort by net_amount descending ──
    candidates.sort(key=lambda x: x["net_amount"], reverse=True)

    # Separate positive vs negative
    positive = [c for c in candidates if c["net_amount"] > 0]
    if positive:
        top = positive[:top_n]
        label = f"Top {len(top)} by Net Inflow (positive flow only)"
    else:
        top = candidates[:top_n]
        label = f"Top {len(top)} by Net Inflow (all negative today)"

    # ── Produce CSV output ──
    lines = [
        f"# {label} — {trade_date}",
        f"# Raw main-force net inflow (主力净流入, CNY). Positive = institutional buying.",
        f"# Filter: min_amount >= {min_amount/1e8:.1f}亿, ST excluded",
        f"# Total market: {len(candidates)} stocks pass filters, {len(positive)} with positive inflow",
        "",
        "ts_code,name,net_amount,pct_chg,close,amount,turnover_rate,volume_ratio,pe,pb,total_mv",
    ]
    for s in top:
        lines.append(
            f"{s['ts_code']},{s['name']},{s['net_amount']:.0f},{s['pct_chg']:.2f},"
            f"{s['close']:.2f},{s['amount']:.0f},{s['turnover_rate']:.2f},"
            f"{s['volume_ratio']:.2f},{s['pe']:.2f},{s['pb']:.2f},{s['total_mv']:.0f}"
        )

    logger.info("Top net inflow: %d candidates, %d positive, returned %d",
                len(candidates), len(positive), len(top))
    return "\n".join(lines)


# ── East Money real-time rankings (no token needed) ──────────────────

def get_em_rankings(sort_field: str = "f3", top_n: int = 15) -> str:
    """Fetch real-time A-share rankings from East Money public API.

    Args:
        sort_field: f3=pct_chg, f62=main_force_net, f8=turnover, f20=mkt_cap
        top_n: Number of results (max ~100)

    Returns CSV string: ts_code,name,pct_chg,close,amount,total_mv,pe
    """
    import requests as _req
    url = "http://push2.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "http://quote.eastmoney.com/"}
    params = {
        "pn": "1", "pz": str(top_n), "po": "0",
        "fid": sort_field, "fs": "m:0+t:6,m:0+t:80",
        "fields": "f2,f3,f12,f14,f15,f20",
        "np": "1", "fltt": "2", "invt": "2",
    }
    try:
        resp = _req.get(url, params=params, headers=headers, timeout=10, proxies={"http": None, "https": None})
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        if not items:
            return "# No data from East Money"
        lines = ["ts_code,name,pct_chg,close,amount,total_mv,pe"]
        for r in items:
            code = r.get("f12", "")
            market = {0: "SZ", 1: "SH"}.get(r.get("f13", 0), "")
            ts_code = f"{code}.{market}" if market else code
            name = r.get("f14", "")
            pct = r.get("f3", 0)
            close = r.get("f2", 0)
            amt = r.get("f15", 0)  # already in yuan
            mv = r.get("f20", 0)   # total market value in yi
            pe = r.get("f115", 0)
            lines.append(f"{ts_code},{name},{pct},{close},{amt},{mv},{pe}")
        return "\n".join(lines)
    except Exception as e:
        return f"# Error: {e}"


def get_em_top_gainers(top_n: int = 15) -> str:
    """Get top gainers from East Money (real-time, no token)."""
    return get_em_rankings("f3", top_n)


def get_em_top_inflow(top_n: int = 15) -> str:
    """Get top by main force net inflow from East Money (f62 field)."""
    return get_em_rankings("f62", top_n)
    return "\n".join(lines)
