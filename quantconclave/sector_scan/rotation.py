"""RRG (Relative Rotation Graph) engine for A-share industry rotation detection.

Computes RS-Ratio and RS-Momentum from industry-level data. Two modes:
  - capital: RS based on net fund flow / market cap (via Tushare)
  - price:   RS based on sector index price change vs benchmark (via AKShare/Eastmoney)

Data sources (both bypass proxy — Chinese domestic APIs):
  - Primary: AKShare (东方财富 public APIs, no token required)
  - Fallback: Tushare (requires TUSHARE_TOKEN)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known Chinese exchange holidays (non-weekend market closures)
# Updated through 2026. Extend as needed.
# ---------------------------------------------------------------------------
_CHINA_HOLIDAYS: set[str] = set()

def _init_holidays():
    """Populate known Chinese market holidays through end of 2026."""
    global _CHINA_HOLIDAYS
    if _CHINA_HOLIDAYS:
        return
    holidays = [
        # 2025
        "20250101", "20250128", "20250129", "20250130", "20250131",
        "20250203", "20250204",  # 春节
        "20250404", "20250407",  # 清明节
        "20250501", "20250502", "20250505",  # 劳动节
        "20250602",  # 端午节
        "20251001", "20251002", "20251003", "20251006", "20251007", "20251008",  # 国庆+中秋
        # 2026
        "20260101", "20260102",
        "20260216", "20260217", "20260218", "20260219", "20260220",  # 春节 (approx)
        "20260406",  # 清明节
        "20260501", "20260504",  # 劳动节
        "20260622",  # 端午节
        "20261001", "20261002", "20261005", "20261006", "20261007", "20261008",  # 国庆
    ]
    _CHINA_HOLIDAYS.update(holidays)


def _generate_trading_dates(lookback: int = 30) -> list[str]:
    """Generate recent trading dates without any API dependency.

    Filters weekdays Monday–Friday and removes known Chinese holidays.
    Returns dates in YYYYMMDD format, newest first.
    """
    _init_holidays()
    today = datetime.now()
    dates = []
    cursor = today
    # Walk back far enough to get `lookback` trading days plus buffer
    needed = lookback + 15
    while len(dates) < needed:
        ds = cursor.strftime("%Y%m%d")
        if cursor.weekday() < 5 and ds not in _CHINA_HOLIDAYS:
            dates.append(ds)
        cursor -= timedelta(days=1)
    return dates


# ---------------------------------------------------------------------------
# AKShare helpers (Eastmoney — no token required)
# ---------------------------------------------------------------------------

def _get_industry_data_akshare(lookback: int = 10) -> pd.DataFrame | None:
    """Fetch daily industry board OHLCV from 东方财富 via AKShare.

    Returns DataFrame with columns:
        trade_date, name, pct_change, net_amount(0), _trade_date
    or None on failure.
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("AKShare not installed; falling back to Tushare")
        return None

    # Temporarily bypass proxy for domestic API calls — akshare uses requests
    # which may pick up HTTP_PROXY from env and route domestic traffic through proxy.
    _saved_proxy = {k: os.environ.pop(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy") if k in os.environ}
    try:
        trading_dates = _generate_trading_dates(lookback + 5)
        start_date = trading_dates[-1]
        end_date = trading_dates[0]

        names_df = ak.stock_board_industry_name_em()
        if names_df is None or names_df.empty:
            return None

        name_col = names_df.columns[0]
        code_col = names_df.columns[1]

        all_rows = []
        for _, row in names_df.iterrows():
            name = str(row[name_col])
            try:
                hist = ak.stock_board_industry_hist_em(
                    symbol=name, start_date=start_date, end_date=end_date,
                    period="日k", adjust="",
                )
            except Exception as e:
                logger.debug("AKShare hist failed for %s: %s", name, e)
                continue

            if hist is None or hist.empty:
                continue

            date_col = "日期" if "日期" in hist.columns else hist.columns[0]
            pct_col = "涨跌幅" if "涨跌幅" in hist.columns else None
            if pct_col is None:
                continue

            for _, hr in hist.iterrows():
                trade_date = str(hr[date_col]).replace("-", "")[:8]
                if trade_date not in set(trading_dates[:lookback + 5]):
                    continue
                try:
                    pct_change = float(hr[pct_col])
                except (ValueError, TypeError):
                    pct_change = 0.0
                all_rows.append({
                    "name": name, "pct_change": pct_change,
                    "net_amount": 0.0, "_trade_date": trade_date,
                })
            time.sleep(0.1)

        if not all_rows:
            return None
        return pd.DataFrame(all_rows)
    except Exception as e:
        logger.warning("AKShare industry data failed: %s", e)
        return None
    finally:
        os.environ.update(_saved_proxy)


# ---------------------------------------------------------------------------
# Tushare helpers (fallback — requires TUSHARE_TOKEN)
# ---------------------------------------------------------------------------

_TS_PRO = None
_TUSHARE_LAST_CALL = 0.0
_TUSHARE_MIN_INTERVAL = 0.35


def _get_pro():
    global _TS_PRO, _TUSHARE_LAST_CALL
    elapsed = time.time() - _TUSHARE_LAST_CALL
    if elapsed < _TUSHARE_MIN_INTERVAL:
        time.sleep(_TUSHARE_MIN_INTERVAL - elapsed)
    if _TS_PRO is None:
        import tushare as ts
        token = os.environ.get("TUSHARE_TOKEN", "")
        _TS_PRO = ts.pro_api(token)
    _TUSHARE_LAST_CALL = time.time()
    return _TS_PRO


def _get_industry_data_tushare(lookback: int = 10) -> pd.DataFrame | None:
    """Fetch daily industry moneyflow from Tushare (fallback).

    Returns DataFrame with columns:
        trade_date, name, pct_change, net_amount, _trade_date
    or None on failure.
    """
    try:
        import tushare as ts
    except ImportError:
        return None

    pro = _get_pro()

    trading_dates = _generate_trading_dates(lookback + 5)

    # Fetch moneyflow for each trading day
    all_dfs = []
    tried = 0
    for d in trading_dates:
        if tried >= lookback + 3:
            break
        try:
            df = pro.moneyflow_ind_dc(trade_date=d)
            if df is not None and not df.empty:
                df = df.copy()
                df["_trade_date"] = d
                all_dfs.append(df)
                tried += 1
        except Exception as e:
            logger.debug("Tushare moneyflow_ind_dc failed for %s: %s", d, e)

    if not all_dfs:
        return None

    combined = pd.concat(all_dfs, ignore_index=True)
    # Keep only needed columns
    keep_cols = ["name", "pct_change", "net_amount", "_trade_date"]
    available = [c for c in keep_cols if c in combined.columns]
    return combined[available].copy()


def _get_industry_stock_counts_akshare() -> dict[str, int]:
    """Get stock count per industry via AKShare."""
    try:
        import akshare as ak
        # Get all industry names first
        names_df = ak.stock_board_industry_name_em()
        name_col = names_df.columns[0]
        counts = {}
        for _, row in names_df.iterrows():
            name = str(row[name_col])
            try:
                cons = ak.stock_board_industry_cons_em(symbol=name)
                counts[name] = len(cons) if cons is not None else 0
            except Exception:
                counts[name] = 0
            time.sleep(0.05)
        return counts
    except Exception:
        return {}


def _get_industry_stock_counts_tushare() -> dict[str, int]:
    """Get stock count per industry via Tushare stock_basic."""
    try:
        pro = _get_pro()
        stocks = pro.stock_basic(exchange="", list_status="L", fields="ts_code,industry")
        return stocks.groupby("industry").size().to_dict()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Core RS computation (unchanged algorithm)
# ---------------------------------------------------------------------------

def _compute_daily_rs(combined, ind_counts, mode, name, grp, lookback):
    """Compute daily RS-Ratio and RS-Momentum for one industry across all dates."""
    grp_sorted = grp.sort_values("_trade_date")
    dates = grp_sorted["_trade_date"].tolist()

    tail = []
    for i in range(len(dates)):
        window_end = i + 1
        window_start = max(0, i - lookback + 1)
        recent = grp_sorted.iloc[window_start:window_end + 1]
        if len(recent) < 3:
            continue

        half = len(recent) // 2
        recent_half = recent.iloc[half:]
        older_half = recent.iloc[:half]

        if mode == "capital":
            count = ind_counts.get(name, 50)
            nf = max(count, 1)
            rs_ratio = round(float(recent_half["net_amount"].mean()) / nf / 1e8, 4)
            rs_ratio_old = round(float(older_half["net_amount"].mean()) / nf / 1e8, 4)
        else:
            rs_ratio = round(float(recent_half["pct_change"].mean()), 4)
            rs_ratio_old = round(float(older_half["pct_change"].mean()), 4)

        rs_momentum = round(rs_ratio - rs_ratio_old, 4)
        if rs_momentum > 0 and rs_ratio > 0:
            quadrant = "leading"
        elif rs_momentum > 0 and rs_ratio <= 0:
            quadrant = "improving"
        elif rs_momentum <= 0 and rs_ratio <= 0:
            quadrant = "lagging"
        else:
            quadrant = "weakening"

        tail.append({
            "date": dates[i],
            "rs_ratio": rs_ratio,
            "rs_momentum": rs_momentum,
            "quadrant": quadrant,
        })

    latest_row = grp_sorted.iloc[-1]
    fund_flow = float(latest_row.get("net_amount", 0) or 0)
    change_pct = float(latest_row.get("pct_change", 0) or 0)

    latest_point = tail[-1] if tail else {"rs_ratio": 0, "rs_momentum": 0, "quadrant": "lagging"}
    return {
        "name": name,
        "rs_ratio": latest_point["rs_ratio"],
        "rs_momentum": latest_point["rs_momentum"],
        "quadrant": latest_point["quadrant"],
        "fund_flow": round(fund_flow / 1e8, 2),
        "change_pct": round(change_pct, 2),
        "stock_count": ind_counts.get(name, 0),
        "tail": tail,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_rrg_data(lookback: int = 10, mode: str = "price", tails: int = 8) -> dict:
    """Compute RRG data for all A-share industries using daily_basic + stock_basic.

    Free-tier Tushare compatible — no moneyflow_ind_dc needed.
    Aggregates per-stock pct_chg from daily_basic to industry level.
    """
    pro = _get_pro()
    today = datetime.now().strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
    open_dates = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
    if len(open_dates) < lookback + 5:
        return {"industries": [], "date": today, "mode": mode, "error": "Not enough trading days"}

    try:
        stocks = pro.stock_basic(exchange="", list_status="L", fields="ts_code,industry")
        ts_to_industry = dict(zip(stocks["ts_code"], stocks["industry"]))
    except Exception:
        return {"industries": [], "date": today, "mode": mode, "error": "stock_basic failed"}

    prev_close = {}
    industry_rows = []
    for d in open_dates[:lookback + 5]:
        try:
            df = pro.daily_basic(trade_date=d, fields="ts_code,close")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df["pct_chg"] = 0.0
        for idx, row in df.iterrows():
            code = row["ts_code"]
            prev_c = prev_close.get(code, float(row["close"]))
            if prev_c > 0:
                df.at[idx, "pct_chg"] = (float(row["close"]) - prev_c) / prev_c * 100
            prev_close[code] = float(row["close"])
        df["industry"] = df["ts_code"].map(ts_to_industry).fillna("其他")
        grp = df.groupby("industry")["pct_chg"].mean().reset_index()
        grp["_trade_date"] = d
        industry_rows.append(grp)
        if len(industry_rows) >= lookback + 1:
            break
        time.sleep(0.3)

    if not industry_rows:
        return {"industries": [], "date": today, "mode": mode, "error": "No daily_basic data"}

    combined = pd.concat(industry_rows, ignore_index=True)
    latest_date = max(combined["_trade_date"])

    # Compute market average per day for relative comparison
    market_avg = combined.groupby("_trade_date")["pct_chg"].mean().to_dict()

    industries = []
    for name, grp in combined.groupby("industry"):
        grp_sorted = grp.sort_values("_trade_date")
        if len(grp_sorted) < 3:
            continue
        # Compute RS as industry return RELATIVE to market average
        rel_chgs = []
        for _, row in grp_sorted.iterrows():
            mkt = market_avg.get(row["_trade_date"], 0)
            rel_chgs.append(row["pct_chg"] - mkt)
        half = len(rel_chgs) // 2
        recent_avg = sum(rel_chgs[half:]) / (len(rel_chgs) - half)
        older_avg = sum(rel_chgs[:half]) / half if half > 0 else 0
        rs_ratio = round(recent_avg, 4)
        rs_momentum = round(recent_avg - older_avg, 4)
        if rs_momentum > 0 and rs_ratio > 0: quadrant = "leading"
        elif rs_momentum > 0 and rs_ratio <= 0: quadrant = "improving"
        elif rs_momentum <= 0 and rs_ratio <= 0: quadrant = "lagging"
        else: quadrant = "weakening"
        tail = []
        for i in range(len(rel_chgs)):
            window = rel_chgs[max(0, i - lookback + 1):i + 1]
            if len(window) < 2: continue
            h = len(window) // 2
            r = sum(window[h:]) / (len(window) - h)
            o = sum(window[:h]) / h if h > 0 else 0
            m = r - o
            q = "leading" if m > 0 and r > 0 else "improving" if m > 0 else "lagging" if m <= 0 and r <= 0 else "weakening"
            tail.append({"date": grp_sorted.iloc[i]["_trade_date"], "rs_ratio": round(r, 4), "rs_momentum": round(m, 4), "quadrant": q})
        industries.append({"name": name, "rs_ratio": rs_ratio, "rs_momentum": rs_momentum,
                           "quadrant": quadrant, "fund_flow": 0, "change_pct": round(recent_avg, 2),
                           "stock_count": 0, "tail": tail[-tails:] if tails > 0 else tail})

    industries.sort(key=lambda x: x["rs_ratio"], reverse=True)
    return {
        "industries": industries, "date": latest_date, "mode": mode, "source": "daily_basic",
        "quadrant_counts": {
            "leading": sum(1 for i in industries if i["quadrant"] == "leading"),
            "improving": sum(1 for i in industries if i["quadrant"] == "improving"),
            "lagging": sum(1 for i in industries if i["quadrant"] == "lagging"),
            "weakening": sum(1 for i in industries if i["quadrant"] == "weakening"),
        },
    }