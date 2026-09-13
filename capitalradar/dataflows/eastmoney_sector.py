"""A-share sector rotation scanner data layer.

Uses Tushare for industry classification, stock lists, K-line data,
money flow, and MACD golden-cross detection.
Requires TUSHARE_TOKEN in .env.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tushare client (lazy init)
# ---------------------------------------------------------------------------

_TS_PRO = None
_LAST_CALL = 0.0
_MIN_INTERVAL = 0.35  # ~170 calls/min, well under 200 limit


def _get_pro():
    global _TS_PRO, _LAST_CALL
    # Rate limit: ensure minimum interval between Tushare API calls
    import time as _time
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
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SectorInfo:
    name: str          # 银行 (申万行业名称)
    stock_count: int   # number of stocks
    avg_pct_chg: float = 0.0  # average daily change (proxy for fund flow direction)


@dataclass
class StockCandidate:
    code: str              # 000001
    name: str              # 平安银行
    ts_code: str           # 000001.SZ
    close: float
    change_pct: float
    market_cap: float      # total market cap (万元)
    main_net_inflow: float  # 主力净流入 (万元)
    main_inflow_ratio: float  # 主力净流入占比
    golden_cross: bool = False
    cross_strength: float = 0.0
    score: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_sector_list() -> list[SectorInfo]:
    """Return all A-share industries from stock_basic (申万分类).

    Sorted by stock count descending — more stocks = more significant industry.
    """
    pro = _get_pro()

    try:
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,industry",
        )
    except Exception as e:
        logger.warning("stock_basic failed: %s", e)
        return []

    if df is None or df.empty:
        return []

    # Count stocks per industry
    industry_counts = df["industry"].value_counts()
    sectors = []
    for ind_name, count in industry_counts.items():
        if not ind_name or ind_name == "nan":
            continue
        sectors.append(SectorInfo(
            name=ind_name,
            stock_count=count,
        ))

    sectors.sort(key=lambda s: s.stock_count, reverse=True)
    return sectors


def get_industry_stocks(industry_name: str) -> list[dict]:
    """Return all active stocks in a given industry (申万分类)."""
    pro = _get_pro()

    try:
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,industry",
        )
    except Exception as e:
        logger.warning("stock_basic failed: %s", e)
        return []

    if df is None or df.empty:
        return []

    matched = df[df["industry"] == industry_name]
    stocks = []
    for _, row in matched.iterrows():
        stocks.append({
            "ts_code": row["ts_code"],
            "symbol": row["symbol"],
            "name": row["name"],
        })
    return stocks


def get_stock_daily(ts_code: str, days: int = 90) -> list[dict]:
    """Get daily K-line data via Tushare.

    Returns list of {date, open, close, high, low, volume}.
    """
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 15)).strftime("%Y%m%d")

    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("daily failed for %s: %s", ts_code, e)
        return []

    if df is None or df.empty:
        return []

    df = df.sort_values("trade_date")
    result = []
    for _, row in df.iterrows():
        result.append({
            "date": row["trade_date"],
            "open": float(row["open"]),
            "close": float(row["close"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "volume": float(row["vol"]),
        })
    return result


def get_daily_basic_batch(ts_codes: list[str]) -> dict[str, dict]:
    """Fetch daily basic data for the given ts_codes.

    Downloads ALL stocks for the latest trade date (one API call),
    then filters to the requested codes. Much faster than per-stock calls.

    Returns {ts_code: {close, pct_chg, total_mv, pe, pb}}.

    Note: pct_chg is computed from (close - pre_close) / pre_close
    using the daily API since daily_basic doesn't include it.
    """
    pro = _get_pro()
    today = datetime.now().strftime("%Y%m%d")

    # Find latest trade dates (try up to 3 recent open days)
    try:
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
        cal = cal[cal["is_open"] == 1]
        recent_dates = sorted(cal["cal_date"].tolist(), reverse=True)[:5]
    except Exception:
        recent_dates = [today]

    # Fetch all stocks' daily_basic, retrying earlier dates if empty
    df = None
    for trade_date in recent_dates[:3]:
        try:
            df = pro.daily_basic(trade_date=trade_date, fields="ts_code,close,total_mv,pe,pb")
        except Exception:
            continue
        if df is not None and not df.empty:
            break

    if df is None or df.empty:
        logger.warning("daily_basic returned empty for all retry dates")
        return {}

    # Index by ts_code for fast lookup
    target_set = set(ts_codes)
    result = {}
    for _, row in df.iterrows():
        code = row["ts_code"]
        if code in target_set:
            result[code] = {
                "close": float(row.get("close", 0) or 0),
                "pct_chg": 0.0,
                "total_mv": float(row.get("total_mv", 0) or 0),
                "pe": float(row.get("pe", 0) or 0),
                "pb": float(row.get("pb", 0) or 0),
            }

    return result


def get_stock_moneyflow(ts_code: str, days: int = 5) -> dict:
    """Get recent money flow for a single stock with detailed breakdown.

    Returns dict with:
      net_amount (float)  主力净流入 (万元, backward-compatible)
      main_force_net     超大单+大单净额 (万元)
      retail_net         中单+小单净额 (万元, 散户方向)
      mf_ratio           主散比 (>2=机构主导)
      buy_elg_ratio      超大单成交占比 (%)
      net_1d             最近1日净额 (万元)
      net_5d             最近5日净额 (万元)
      net_20d            最近20日净额 (万元)
      net_60d            最近60日净额 (万元)
      multi_horizon      {h: {main, elg, lg, all, pos_days, days}} h=1/5/20/60
      total_turnover     总成交额 (万元)
      trend              accelerating|stable|weakening
    """
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    # net_60d + multi_horizon need 60 trading days ≈ 90 calendar days (~2× trading
    # days in calendar terms). Window must not shrink with the `days` param, or a
    # days=5 caller silently aggregates only ~35 trading rows into "60日".
    start_date = (datetime.now() - timedelta(days=max(days, 60) * 2)).strftime("%Y%m%d")
    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("moneyflow failed for %s: %s", ts_code, e)
        return {"net_amount": 0.0}
    if df is None or df.empty:
        return {"net_amount": 0.0}
    recent = df.head(days)
    buy_lg = float(recent["buy_lg_amount"].sum()) if "buy_lg_amount" in recent.columns else 0.0
    sell_lg = float(recent["sell_lg_amount"].sum()) if "sell_lg_amount" in recent.columns else 0.0
    buy_elg = float(recent["buy_elg_amount"].sum()) if "buy_elg_amount" in recent.columns else 0.0
    sell_elg = float(recent["sell_elg_amount"].sum()) if "sell_elg_amount" in recent.columns else 0.0
    buy_md = float(recent["buy_md_amount"].sum()) if "buy_md_amount" in recent.columns else 0.0
    sell_md = float(recent["sell_md_amount"].sum()) if "sell_md_amount" in recent.columns else 0.0
    buy_sm = float(recent["buy_sm_amount"].sum()) if "buy_sm_amount" in recent.columns else 0.0
    sell_sm = float(recent["sell_sm_amount"].sum()) if "sell_sm_amount" in recent.columns else 0.0
    net = buy_lg - sell_lg + buy_elg - sell_elg
    main_force = buy_lg - sell_lg + buy_elg - sell_elg
    retail = buy_md - sell_md + buy_sm - sell_sm
    total = buy_lg + sell_lg + buy_elg + sell_elg + buy_md + sell_md + buy_sm + sell_sm
    # Multi-horizon 主力 composition (tushare raw 万元 — aggregator is unit-
    # agnostic over buy/sell amounts; the caller converts once for display).
    multi_horizon = aggregate_moneyflow_wan(_df_to_moneyflow_rows(df))
    net_1d = multi_horizon["1"]["main"]
    net_5d = multi_horizon["5"]["main"]
    net_20d = multi_horizon["20"]["main"]
    net_60d = multi_horizon["60"]["main"]
    # Day-by-day 主力 net helper (万元)
    def _net(d):
        b_lg = float(d["buy_lg_amount"].sum()) if "buy_lg_amount" in d.columns else 0.0
        s_lg = float(d["sell_lg_amount"].sum()) if "sell_lg_amount" in d.columns else 0.0
        b_elg = float(d["buy_elg_amount"].sum()) if "buy_elg_amount" in d.columns else 0.0
        s_elg = float(d["sell_elg_amount"].sum()) if "sell_elg_amount" in d.columns else 0.0
        return b_lg - s_lg + b_elg - s_elg
    # Trend: compare recent vs older
    if len(df) >= 10:
        recent_5d = df.head(5)
        older_5d = df.iloc[5:10]
        recent_net = _net(recent_5d)
        older_net = _net(older_5d)
        if recent_net > older_net * 1.2 and abs(older_net) > 0.01:
            trend = "accelerating"
        elif recent_net < older_net * 0.8 and abs(older_net) > 0.01:
            trend = "weakening"
        else:
            trend = "stable"
    else:
        trend = "stable"
    return {
        "net_amount": net,
        "main_force_net": main_force,
        "retail_net": retail,
        "mf_ratio": round(abs(main_force) / max(abs(retail), 0.01), 2),
        "buy_elg_ratio": round(buy_elg / max(total, 0.01) * 100, 1),
        "net_inflow_ratio": round(net / max(total, 0.01) * 100, 2),
        "net_1d": net_1d,
        "net_5d": net_5d,
        "net_20d": net_20d,
        "net_60d": net_60d,
        "multi_horizon": multi_horizon,
        "total_turnover": total,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Multi-horizon 主力资金聚合 (当日/5/20/60日 + 构成)
# ---------------------------------------------------------------------------

_MF_AMOUNT_KEYS = [
    "buy_elg_amount", "sell_elg_amount",
    "buy_lg_amount", "sell_lg_amount",
    "buy_md_amount", "sell_md_amount",
    "buy_sm_amount", "sell_sm_amount",
]


def _moneyflow_row_to_float(v) -> float:
    """Coerce a moneyflow amount cell to float; None/NaN/blank → 0.0."""
    try:
        f = float(v)
        return 0.0 if f != f else f  # NaN != NaN
    except (TypeError, ValueError):
        return 0.0


def _df_to_moneyflow_rows(df) -> list:
    """Convert a tushare moneyflow DataFrame to list of daily dict rows.

    Values are raw floats in the frame's own unit (tushare = 万元); NaN → 0.0.
    Row order is preserved (tushare returns newest-first); the aggregator sorts.
    """
    rows = []
    for _, row in df.iterrows():
        r = {"trade_date": str(row.get("trade_date", ""))}
        for k in _MF_AMOUNT_KEYS:
            r[k] = _moneyflow_row_to_float(row.get(k))
        rows.append(r)
    return rows


def aggregate_moneyflow_wan(rows) -> dict:
    """Aggregate per-horizon 主力 money flow from daily dict rows.

    **Unit-agnostic**: operates on whatever unit the caller passes (元 or 万元);
    the /1e4 conversion happens once at the call site, never here. ``rows`` is a
    list of daily dicts with ``trade_date`` + the four-tier buy/sell amount keys.
    Returns ``{h: {...}}`` for h in "1"/"5"/"20"/"60" where each entry has:

      main         (超大单+大单净额)
      elg          (超大单净额)
      lg           (大单净额)
      all          (四档净额 = main + 中单 + 小单; 市场撮合下≈0, 不构成信号)
      avg_turnover (窗口内日均成交额 = 四档买卖总和均值 — 流动性/可交易量锚点)
      ratio        (主力净额占窗口内累计成交额 % — 相对强度, 与市值规模可比)
      pos_days     (窗口内主力净流入的天数)
      days         (窗口实际有效行数, 可能 < h)

    主力方向与构成是信号, 但绝对净额必须相对该股流动性才有意义: 同一 +5000万,
    27亿微盘 ≈ 强吸筹, 2000亿大盘 ≈ 噪音. ``ratio`` 是这一比较的载体 — 用窗口内
    累计成交额作分母 (而非单日均值), 使常数净流下各周期强度一致、跨周期可比.
    ``all`` (全口径) 因每笔成交同时是某档的买和另一档的卖而≈0 (002696 教训),
    只保留字段不进入展示.
    """
    def _net(r, buy, sell):
        return _moneyflow_row_to_float(r.get(buy)) - _moneyflow_row_to_float(r.get(sell))

    def _day_turnover(r):
        return sum(_moneyflow_row_to_float(r.get(k)) for k in _MF_AMOUNT_KEYS)

    ordered = sorted(rows, key=lambda r: str(r.get("trade_date", "")), reverse=True)
    out = {}
    for h in (1, 5, 20, 60):
        win = ordered[:h]
        if not win:
            out[str(h)] = {"main": 0.0, "elg": 0.0, "lg": 0.0, "all": 0.0,
                           "avg_turnover": 0.0, "ratio": 0.0,
                           "pos_days": 0, "days": 0}
            continue
        elg = sum(_net(r, "buy_elg_amount", "sell_elg_amount") for r in win)
        lg = sum(_net(r, "buy_lg_amount", "sell_lg_amount") for r in win)
        main = elg + lg
        md = sum(_net(r, "buy_md_amount", "sell_md_amount") for r in win)
        sm = sum(_net(r, "buy_sm_amount", "sell_sm_amount") for r in win)
        _turn_total = sum(_day_turnover(r) for r in win)
        pos_days = sum(
            1 for r in win
            if (_net(r, "buy_elg_amount", "sell_elg_amount")
                + _net(r, "buy_lg_amount", "sell_lg_amount")) > 0
        )
        out[str(h)] = {
            "main": round(main, 1),
            "elg": round(elg, 1),
            "lg": round(lg, 1),
            "all": round(main + md + sm, 1),
            "avg_turnover": round(_turn_total / len(win), 1),
            # cumulative 主力净额 / 窗口内累计成交额 — invariant to window length,
            # comparable across stocks regardless of 盘面规模.
            "ratio": round(main / _turn_total * 100, 2) if _turn_total > 0 else 0.0,
            "pos_days": pos_days,
            "days": len(win),
        }
    return out


def get_moneyflow_multi_horizon(ts_code: str, lookback_days: int = 60) -> dict:
    """Multi-horizon (当日/5/20/60日) 主力净流入 + 构成, tushare 万元 native.

    Direct ``pro.moneyflow`` (万元), NOT the web moneyflow_cache (元). Returns
    ``aggregate_moneyflow_wan`` output keyed by "1"/"5"/"20"/"60" plus meta keys
    ``end_date`` and ``source_rows`` (0 = no data), and best-effort ``circ_mv_wan``
    (流通市值, 万元) from daily_basic — the scale anchor for reading absolute 净额
    (同一 +5000万 对 27亿小盘 ≈ 强吸筹, 对 2000亿大盘 ≈ 噪音). For the advisor tool.
    """
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    # ~2× trading days in calendar days keeps head(60) safe.
    start_date = (datetime.now() - timedelta(days=lookback_days * 2)).strftime("%Y%m%d")
    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("moneyflow multi-horizon failed for %s: %s", ts_code, e)
        df = None
    rows = _df_to_moneyflow_rows(df) if (df is not None and not df.empty) else []
    agg = aggregate_moneyflow_wan(rows)
    agg["end_date"] = end_date
    agg["source_rows"] = len(rows)
    # Best-effort 流通市值 (万元), always present (0 = unavailable). Anchor on the
    # newest moneyflow trade date (a real trading day) rather than today —
    # daily_basic(trade_date=today) is empty pre-open and on holidays.
    agg["circ_mv_wan"] = 0.0
    _td = rows[0]["trade_date"] if rows else end_date
    try:
        _db = pro.daily_basic(ts_code=ts_code, trade_date=_td, fields="ts_code,circ_mv")
        if _db is not None and not _db.empty:
            agg["circ_mv_wan"] = float(_db.iloc[0]["circ_mv"] or 0)
    except Exception as e:
        logger.debug("daily_basic circ_mv failed for %s@%s: %s", ts_code, _td, e)
    return agg


_MH_LABELS = {"1": "当日", "5": "5日", "20": "20日", "60": "60日"}


def moneyflow_multi_horizon_direction(mh: dict) -> tuple[str, str]:
    """Pre-computed 主力 sign pattern across 1/5/20/60d horizons.

    Returns ``(pattern, label)`` where pattern is e.g.
    ``60日[-]→20日[-]→5日[+]→当日[+]`` (empty horizons render "0"). The label is a
    **descriptive sign-pattern name** (回流初期 / 短线转弱 / 分化…), NOT a strength
    or buy/sell verdict — the same direction means very different things depending
    on 流通市值, 占日均成交额比例 and 股价位置 (低位吸筹 vs 高位派发). The ⚠️ note
    in ``format_moneyflow_multi_horizon`` tells the model to weigh those before
    concluding. 万元 semantics live in the caller (aggregator is unit-agnostic).
    """
    def _sign(h):
        e = mh.get(h) or {}
        if not e.get("days"):
            return 0
        return 1 if e["main"] > 0 else -1

    s60, s20, s5, s1 = _sign("60"), _sign("20"), _sign("5"), _sign("1")
    _ch = lambda s: "+" if s > 0 else ("-" if s < 0 else "0")
    pat = f"60日[{_ch(s60)}]→20日[{_ch(s20)}]→5日[{_ch(s5)}]→当日[{_ch(s1)}]"
    if all(s == 1 for s in (s60, s20, s5, s1)):
        label = "各周期一致净流入"
    elif all(s == -1 for s in (s60, s20, s5, s1)):
        label = "各周期一致净流出"
    elif (s60 < 0 and s20 < 0) and (s5 > 0 or s1 > 0):
        label = "60/20日净流出、近5日转正（回流初期形态）"
    elif (s60 > 0 and s20 > 0) and (s5 < 0 or s1 < 0):
        label = "中长期净流入、短线转弱（关注高位派发风险）"
    elif s60 > 0 and s20 > 0 and s5 > 0 and s1 < 0:
        label = "长中短净流入、仅当日流出（单日扰动）"
    elif s60 < 0 and s20 < 0 and s5 < 0 and s1 > 0:
        label = "长中短净流出、仅当日流入（单日回流或技术性反弹）"
    else:
        label = "多周期分化"
    return pat, label


def format_moneyflow_multi_horizon(mh: dict) -> str:
    """Compact multi-horizon 主力 summary text in 万元.

    Shared renderer for the watchlist 【资金多周期】 prompt block and the advisor
    agent tool. Empty/partial windows render as 数据不足. All values must be
    万元 (the caller converts once; the aggregator is unit-agnostic).

    Strength is shown as **主力净额占日均成交额%** — the liquidity/scale-relative
    measure. A raw +5000万 means nothing by itself: it is strong for a 27亿 micro-cap
    and noise for a 2000亿 mega-cap, so the ⚠️ line tells the model to weigh 市值/
    占成交额比例/股价位置 before concluding direction. ``circ_mv_wan`` (万元) is
    optional; when present it anchors the block.
    """
    lines = []
    for _h in ("1", "5", "20", "60"):
        _e = mh.get(_h) or {}
        _lab = _MH_LABELS[_h]
        if not _e.get("days"):
            lines.append(f"  {_lab}  数据不足")
            continue
        lines.append(
            f"  {_lab}  主力{_e['main']:+.0f}万(超大{_e['elg']:+.0f}/大单{_e['lg']:+.0f})"
            f" 占成交额{_e['ratio']:+.1f}% | {_e['pos_days']}/{_e['days']}日净流入"
        )
    _ctx = []
    _cmv = mh.get("circ_mv_wan")
    if _cmv:
        _ctx.append(f"流通市值{_cmv / 1e4:.1f}亿")
    for _h in ("60", "20", "5"):
        _e = mh.get(_h) or {}
        if _e.get("days"):
            _ctx.append(f"近{_e['days']}日日均成交额{_e['avg_turnover'] / 1e4:.2f}亿")
            break
    _ctx_txt = ("  " + " | ".join(_ctx) + "\n") if _ctx else ""
    _pat, _label = moneyflow_multi_horizon_direction(mh)
    return (
        "(万元, 主力=超大单+大单; 强度=主力净额/窗口内日均成交额, 非绝对额; 程序逐日聚合, 引用时逐字一致, 禁止心算)\n"
        + _ctx_txt
        + "\n".join(lines)
        + f"\n  主力方向(程序预计算): {_pat} = {_label}\n"
        + "  ⚠️ 强度须结合市值/占成交额比例/股价位置: 小盘高占比才为强信号, 大盘同额占比弱; 同一方向在低位(吸筹)与高位(派发)含义相反, 勿仅凭绝对额或单一周期下结论"
    )
def get_moneyflow_trend(ts_code: str, days: int = 10) -> dict:
    """Get multi-day money flow trend for a single stock.

    Returns dict with:
        net_amount: latest day main force net (万元)
        cum_3d, cum_5d, cum_10d: cumulative net over windows (万元)
        consecutive_inflow: consecutive days of positive net flow
        big_order_divergence: True if 大单净买 but 小单净卖 (smart/retail divergence)
        daily_flows: list of {date, net} for trend analysis
    """
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 15)).strftime("%Y%m%d")

    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("moneyflow trend failed for %s: %s", ts_code, e)
        return {"net_amount": 0.0, "cum_3d": 0.0, "cum_5d": 0.0, "cum_10d": 0.0,
                "consecutive_inflow": 0, "big_order_divergence": False, "daily_flows": []}

    if df is None or df.empty:
        return {"net_amount": 0.0, "cum_3d": 0.0, "cum_5d": 0.0, "cum_10d": 0.0,
                "consecutive_inflow": 0, "big_order_divergence": False, "daily_flows": []}

    df = df.sort_values("trade_date", ascending=False).head(days)

    daily_flows = []
    for _, row in df.iterrows():
        buy_lg = float(row.get("buy_lg_amount", 0) or 0)
        sell_lg = float(row.get("sell_lg_amount", 0) or 0)
        buy_elg = float(row.get("buy_elg_amount", 0) or 0)
        sell_elg = float(row.get("sell_elg_amount", 0) or 0)
        net = buy_lg - sell_lg + buy_elg - sell_elg
        daily_flows.append({
            "date": str(row["trade_date"]),
            "net": net,
        })

    # Cumulative flows
    cum_3d = sum(f["net"] for f in daily_flows[:3])
    cum_5d = sum(f["net"] for f in daily_flows[:5])
    cum_10d = sum(f["net"] for f in daily_flows[:10])

    # Consecutive inflow days
    consecutive = 0
    for f in daily_flows:
        if f["net"] > 0:
            consecutive += 1
        else:
            break

    # Big order vs small order divergence (latest day)
    latest = df.iloc[0]
    big_buy = float(latest.get("buy_lg_amount", 0) or 0) + float(latest.get("buy_elg_amount", 0) or 0)
    big_sell = float(latest.get("sell_lg_amount", 0) or 0) + float(latest.get("sell_elg_amount", 0) or 0)
    small_buy = float(latest.get("buy_sm_amount", 0) or 0)
    small_sell = float(latest.get("sell_sm_amount", 0) or 0)
    big_net = big_buy - big_sell
    small_net = small_buy - small_sell
    divergence = big_net > 0 and small_net < 0

    return {
        "net_amount": daily_flows[0]["net"] if daily_flows else 0.0,
        "cum_3d": round(cum_3d, 2),
        "cum_5d": round(cum_5d, 2),
        "cum_10d": round(cum_10d, 2),
        "consecutive_inflow": consecutive,
        "big_order_divergence": divergence,
        "daily_flows": daily_flows,
    }


# ---------------------------------------------------------------------------
# MACD & Golden Cross
# ---------------------------------------------------------------------------


def calc_macd(klines: list[dict], fast=12, slow=26, signal=9) -> list[dict]:
    """Calculate MACD (DIF, DEA, MACD histogram) for kline data."""
    if len(klines) < slow:
        return klines

    closes = [k["close"] for k in klines]

    def _ema(values, period):
        result = []
        k = 2 / (period + 1)
        ema_val = sum(values[:period]) / period
        for i, v in enumerate(values):
            if i == period - 1:
                result.append(ema_val)
            elif i >= period:
                ema_val = v * k + ema_val * (1 - k)
                result.append(ema_val)
            else:
                result.append(None)
        return result

    ema12 = _ema(closes, fast)
    ema26 = _ema(closes, slow)

    dif = [e12 - e26 if (e12 is not None and e26 is not None) else None
           for e12, e26 in zip(ema12, ema26)]

    valid_dif = [d for d in dif if d is not None]
    dea_vals = []
    dea_k = 2 / (signal + 1)
    for i, d in enumerate(dif):
        if d is None:
            dea_vals.append(None)
            continue
        idx = sum(1 for x in dif[:i] if x is not None)
        if idx < signal - 1:
            dea_vals.append(None)
        elif idx == signal - 1:
            dea_vals.append(sum(valid_dif[:signal]) / signal)
        else:
            dea_vals.append(d * dea_k + dea_vals[-1] * (1 - dea_k))

    for i, k in enumerate(klines):
        d = dif[i] if i < len(dif) else None
        dea = dea_vals[i] if i < len(dea_vals) else None
        k["dif"] = d
        k["dea"] = dea
        k["macd"] = (d - dea) * 2 if (d is not None and dea is not None) else None

    return klines


def detect_golden_cross(klines: list[dict], lookback: int = 5) -> tuple[bool, float]:
    """Detect MACD golden cross in recent `lookback` days.

    Returns (has_cross, cross_strength).
    """
    if len(klines) < 30:
        return False, 0.0

    recent = klines[-lookback:]
    for i in range(1, len(recent)):
        prev = recent[i - 1]
        curr = recent[i]
        if (prev.get("dif") is not None and prev.get("dea") is not None and
                curr.get("dif") is not None and curr.get("dea") is not None):
            if prev["dif"] <= prev["dea"] and curr["dif"] > curr["dea"]:
                strength = max(
                    (r.get("dif", 0) or 0) - (r.get("dea", 0) or 0)
                    for r in recent
                )
                return True, strength
    return False, 0.0


def detect_macd_convergence(klines: list[dict], lookback: int = 5,
                            min_narrow_days: int = 2) -> tuple[bool, float]:
    """Detect MACD convergence (即将金叉): DIF approaching DEA from below.

    Returns (has_convergence, signal_strength) where strength is higher when
    the gap is closing faster and the lines are closer to crossing.
    """
    if len(klines) < 30:
        return False, 0.0

    recent = klines[-lookback:]
    dif_vals = []
    dea_vals = []
    for k in recent:
        d = k.get("dif")
        e = k.get("dea")
        if d is None or e is None:
            return False, 0.0
        dif_vals.append(d)
        dea_vals.append(e)

    # Must not have crossed yet: DIF still below DEA throughout
    for d, e in zip(dif_vals, dea_vals):
        if d > e:
            return False, 0.0

    # DIF must be trending upward (rising toward DEA)
    if dif_vals[-1] <= dif_vals[0]:
        return False, 0.0

    # Gap must be narrowing for at least min_narrow_days consecutive days
    gaps = [abs(dif_vals[i] - dea_vals[i]) for i in range(len(dif_vals))]
    narrow_streak = 0
    for i in range(1, len(gaps)):
        if gaps[i] < gaps[i - 1]:
            narrow_streak += 1
        else:
            narrow_streak = 0
    if narrow_streak < min_narrow_days:
        return False, 0.0

    # Strength: gap reduction rate; bonus when DIF is below zero (零轴下方)
    gap_reduction = gaps[0] - gaps[-1] if gaps[0] > 0 else 0
    gap_reduction_rate = gap_reduction / gaps[0] if gaps[0] > 0 else 0
    zero_line_bonus = 1.2 if dif_vals[-1] < 0 else 1.0

    strength = round(gap_reduction_rate * zero_line_bonus, 4)
    if strength <= 0:
        return False, 0.0
    return True, strength


def detect_ma_cross(klines: list[dict], fast=5, slow=20, lookback: int = 5) -> tuple[bool, float]:
    """Detect MA golden cross: fast MA crosses above slow MA."""
    if len(klines) < slow + lookback:
        return False, 0.0
    closes = [k["close"] for k in klines]

    def _sma(values, period):
        result = [None] * (period - 1) + [sum(values[i-period+1:i+1]) / period for i in range(period-1, len(values))]
        return result

    fast_ma = _sma(closes, fast)
    slow_ma = _sma(closes, slow)
    recent = list(zip(fast_ma[-lookback-1:], slow_ma[-lookback-1:]))
    for i in range(1, len(recent)):
        pf, ps = recent[i-1]
        cf, cs = recent[i]
        if pf is not None and ps is not None and cf is not None and cs is not None:
            if pf <= ps and cf > cs:
                return True, round((cf - cs) / cs * 100, 4)
    return False, 0.0


def detect_volume_breakout(klines: list[dict], multiple: float = 2.0, lookback: int = 5) -> tuple[bool, float]:
    """Detect volume breakout: volume > multiple × 20-day avg volume, and close > open."""
    if len(klines) < 25:
        return False, 0.0
    volumes = [k["volume"] for k in klines]
    avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes[:-1]) / max(len(volumes)-1, 1)
    recent = klines[-lookback:]
    for k in reversed(recent):
        if k["volume"] > avg_vol * multiple and k["close"] > k["open"]:
            return True, round(k["volume"] / avg_vol, 2)
    return False, 0.0


def detect_rsi_oversold(klines: list[dict], threshold: float = 30) -> tuple[bool, float]:
    """Detect RSI oversold: RSI(14) < threshold."""
    if len(klines) < 15:
        return False, 50.0
    closes = [k["close"] for k in klines]
    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    if avg_loss == 0:
        rsi = 100.0 if avg_gain > 0 else 50.0
    else:
        rsi = 100 - 100 / (1 + avg_gain / avg_loss)
    return rsi < threshold, round(rsi, 1)


def detect_kdj_cross(klines: list[dict], lookback: int = 5) -> tuple[bool, float]:
    """Detect KDJ golden cross: K line crosses above D line, both in oversold zone (< 50)."""
    if len(klines) < 9 + lookback:
        return False, 0.0

    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    closes = [k["close"] for k in klines]

    k_vals = [50] * 8
    d_vals = [50] * 8
    for i in range(8, len(closes)):
        hh = max(highs[i-8:i+1])
        ll = min(lows[i-8:i+1])
        rsv = (closes[i] - ll) / (hh - ll) * 100 if hh != ll else 50
        k_vals.append(2/3 * k_vals[-1] + 1/3 * rsv)
        d_vals.append(2/3 * d_vals[-1] + 1/3 * k_vals[-1])

    recent_k = k_vals[-lookback-1:]
    recent_d = d_vals[-lookback-1:]
    for i in range(1, len(recent_k)):
        if recent_k[i-1] <= recent_d[i-1] and recent_k[i] > recent_d[i] and recent_k[i] < 50:
            return True, round(recent_k[i], 1)
    return False, 50.0


def detect_bollinger_bottom(klines: list[dict], period: int = 20, lookback: int = 3) -> tuple[bool, float]:
    """Detect price near Bollinger lower band (within 5% of the band)."""
    if len(klines) < period + 1:
        return False, 0.0
    closes = [k["close"] for k in klines]
    recent = closes[-period-1:-1] if len(closes) > period else closes[:-1]
    ma = sum(recent[-period:]) / period
    variance = sum((x - ma)**2 for x in recent[-period:]) / period
    std = variance ** 0.5
    lower = ma - 2 * std
    last_close = closes[-1]
    if ma > 0 and lower > 0:
        dist_pct = (last_close - lower) / ma * 100
        if abs(dist_pct) < 5:
            return True, round(dist_pct, 2)
    return False, 0.0


def detect_consecutive_inflow(ts_code: str, days: int = 3) -> tuple[bool, float]:
    """Detect consecutive positive main-capital net inflow for N days."""
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception:
        return False, 0.0
    if df is None or df.empty or len(df) < days:
        return False, 0.0
    df = df.sort_values("trade_date", ascending=False)
    recent = df.head(days)
    consecutive = 0
    total_net = 0.0
    for _, row in recent.iterrows():
        buy = float(row.get("buy_lg_amount", 0) or 0) + float(row.get("buy_elg_amount", 0) or 0)
        sell = float(row.get("sell_lg_amount", 0) or 0) + float(row.get("sell_elg_amount", 0) or 0)
        net = buy - sell
        if net > 0:
            consecutive += 1
            total_net += net
        else:
            break
    return consecutive >= days, round(total_net, 2)


def detect_institutional_increase(ts_code: str) -> tuple[bool, str]:
    """Check if institutional holders data indicates accumulation.
    Simple heuristic: look for 'increase'/'增持' in holder data.
    """
    _ensure_tools_for_detect()
    if not _get_institutional_holders:
        return False, ""
    try:
        result = _get_institutional_holders.invoke({"ticker": ts_code})
        if result and isinstance(result, str):
            if any(kw in result for kw in ["增持", "新进", "increase", "buy"]):
                return True, result[:200]
    except Exception:
        pass
    return False, ""


def detect_no_insider_selling(ts_code: str) -> tuple[bool, str]:
    """Check for absence of significant insider selling recently."""
    _ensure_tools_for_detect()
    if not _get_insider_transactions:
        return True, ""
    try:
        result = _get_insider_transactions.invoke({"ticker": ts_code})
        if result and isinstance(result, str):
            if any(kw in result for kw in ["Sale", "卖出"]):
                return False, result[:300]
            return True, "No insider selling detected"
    except Exception:
        pass
    return True, ""


# Lazy imports for detection functions
_DETECT_TOOLS_LOADED = False
_get_institutional_holders = None
_get_insider_transactions = None


def _ensure_tools_for_detect():
    global _DETECT_TOOLS_LOADED, _get_institutional_holders, _get_insider_transactions
    if _DETECT_TOOLS_LOADED:
        return
    try:
        from capitalradar.agents.utils.agent_utils import (
            get_institutional_holders as _gih,
            get_insider_transactions as _git,
        )
        _get_institutional_holders = _gih
        _get_insider_transactions = _git
    except ImportError:
        pass
    _DETECT_TOOLS_LOADED = True


_INDUSTRY_PE_PB_CACHE: dict[str, dict] = {}


def get_industry_pe_pb(industry_name: str) -> dict:
    if industry_name in _INDUSTRY_PE_PB_CACHE:
        return _INDUSTRY_PE_PB_CACHE[industry_name]
    """Get industry median PE and PB for valuation comparison.

    Fetches daily_basic for all stocks in the industry and computes medians.
    Returns {"pe_median": float, "pb_median": float} or empty dict on failure.
    """
    stocks = get_industry_stocks(industry_name)
    if not stocks:
        return {}
    ts_codes = [s["ts_code"] for s in stocks]
    basics = get_daily_basic_batch(ts_codes)
    if not basics:
        return {}
    pes, pbs = [], []
    for s in stocks:
        b = basics.get(s["ts_code"], {})
        pe = b.get("pe") if isinstance(b, dict) else getattr(b, "pe", None)
        pb = b.get("pb") if isinstance(b, dict) else getattr(b, "pb", None)
        if pe is not None and float(pe) > 0:
            pes.append(float(pe))
        if pb is not None and float(pb) > 0:
            pbs.append(float(pb))
    if not pes and not pbs:
        return {}
    import statistics
    result = {
        "pe_median": round(statistics.median(pes), 2) if pes else 999,
        "pb_median": round(statistics.median(pbs), 2) if pbs else 999,
    }
    _INDUSTRY_PE_PB_CACHE[industry_name] = result
    return result


def detect_low_valuation(ts_code: str, industry_name: str = "") -> tuple[bool, float]:
    """Check if stock PE and PB are below industry median."""
    pro = _get_pro()
    today = datetime.now().strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today)
        cal = cal[cal["is_open"] == 1]
        trade_date = cal["cal_date"].max()
        df = pro.daily_basic(ts_code=ts_code, trade_date=trade_date, fields="ts_code,pe,pb")
    except Exception:
        return False, 0.0
    if df is None or df.empty:
        return False, 0.0
    row = df.iloc[0]
    pe = float(row.get("pe", 0) or 0)
    pb = float(row.get("pb", 0) or 0)

    industry_med = get_industry_pe_pb(industry_name) if industry_name else {}
    pe_med = industry_med.get("pe_median", 999)
    pb_med = industry_med.get("pb_median", 999)

    score = 0.0
    if pe > 0 and pe < pe_med:
        score += 0.5
    if pb > 0 and pb < pb_med:
        score += 0.5

    return score > 0, round(score, 2)


def detect_fund_turnaround(ts_code: str, days: int = 5) -> tuple[bool, float]:
    """Check if main capital flow is turning positive (recent days vs older)."""
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception:
        return False, 0.0
    if df is None or df.empty or len(df) < days:
        return False, 0.0
    df = df.sort_values("trade_date", ascending=False)
    recent = df.head(days)
    older = df.iloc[days:days * 2] if len(df) >= days * 2 else df.iloc[days:]

    def _net(row):
        return float(row.get("buy_lg_amount", 0) or 0) + float(row.get("buy_elg_amount", 0) or 0) - float(row.get("sell_lg_amount", 0) or 0) - float(row.get("sell_elg_amount", 0) or 0)

    recent_net = sum(_net(r) for _, r in recent.iterrows())
    older_net = sum(_net(r) for _, r in older.iterrows()) if len(older) > 0 else 0

    # Turnaround: recent positive, or switching from negative to positive
    improved = recent_net > older_net
    positive_now = recent_net > 0

    if positive_now and improved:
        return True, round(recent_net / 1e4, 2)
    elif positive_now:
        return True, round(recent_net / 1e4, 2) * 0.5
    return False, 0.0


def detect_bottom_breakout(klines: list[dict], volume_mult: float = 1.3, price_ma: bool = True) -> tuple[bool, float]:
    """Check if stock is breaking out from a bottom area with volume.

    Conditions:
    - Recent volume > N × 20-day avg volume
    - Price > MA20 (trend confirmation)
    - Recent low within 15% of current price (not chasing high)
    """
    if len(klines) < 25:
        return False, 0.0
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    ma20 = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else sum(closes[:-1]) / max(len(closes) - 1, 1)
    avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes[:-1]) / max(len(volumes) - 1, 1)

    latest_close = closes[-1]
    recent_vol = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else volumes[-1]
    low_20 = min(closes[-20:]) if len(closes) >= 20 else min(closes)

    score = 0.0
    if recent_vol > avg_vol * volume_mult:
        score += 0.4
    if price_ma and latest_close > ma20:
        score += 0.3
    if (latest_close - low_20) / low_20 < 0.15:
        score += 0.3

    return score >= 0.5, round(score, 2)


def detect_macd_divergence(klines: list[dict], lookback: int = 40) -> tuple[bool, float]:
    """Detect MACD bullish divergence: price makes lower low but DIF makes higher low.

    Looks at two windows (first half vs second half of lookback period):
      - Price: second half low < first half low (price declining)
      - DIF: second half low > first half low (momentum improving)
    This = smart money accumulating while price drops.
    """
    if len(klines) < lookback:
        return False, 0.0
    klines = calc_macd(klines)
    half = lookback // 2
    recent = klines[-half:]
    older = klines[-lookback:-half]

    recent_lows = [k["low"] for k in recent]
    older_lows = [k["low"] for k in older]
    recent_difs = [k.get("dif", 0) or 0 for k in recent]
    older_difs = [k.get("dif", 0) or 0 for k in older]

    price_new_low = min(recent_lows)
    price_old_low = min(older_lows)
    dif_new_low = min(recent_difs)
    dif_old_low = min(older_difs)

    # Bullish divergence: price lower, DIF higher
    if price_old_low > 0 and price_new_low < price_old_low and dif_new_low > dif_old_low:
        strength = (dif_new_low - dif_old_low) / abs(dif_old_low) * 100 if dif_old_low != 0 else 100
        return True, round(strength, 2)

    return False, 0.0


# ---------------------------------------------------------------------------
# Full Industry Scan
# ---------------------------------------------------------------------------


def scan_candidates(industry_name: str, strategy_groups: list[dict] | None = None,
                    cap_percent: int = 20, require_positive_inflow: bool = False) -> list[StockCandidate]:
    """Full scan: find candidates in an industry using configurable strategy groups.

    Args:
        industry_name: 申万行业名称
        strategy_groups: List of strategy group dicts:
            [{"conditions": [{"name": "macd_golden_cross", "must": True, "param": null}, ...],
              "logic": "AND"}, ...]
            When None, defaults to single MACD golden cross group (backward compat).
        cap_percent: Top market-cap percentile to scan (default 20).
        require_positive_inflow: If True, require main_net_inflow > 0.

    Returns scored & ranked candidates.
    """
    if strategy_groups is None:
        strategy_groups = [{"logic": "AND", "conditions": [{"name": "macd_golden_cross", "must": True, "param": None}]}]

    stocks = get_industry_stocks(industry_name)
    if not stocks:
        logger.info("No stocks found for industry: %s", industry_name)
        return []

    ts_codes = [s["ts_code"] for s in stocks]
    logger.info("Industry '%s': %d stocks, fetching daily data...", industry_name, len(ts_codes))

    basics = get_daily_basic_batch(ts_codes)
    if not basics:
        logger.warning("No daily_basic data for industry %s", industry_name)
        return []

    stock_info = []
    for s in stocks:
        ts_code = s["ts_code"]
        basic = basics.get(ts_code)
        if basic is None or basic.get("total_mv", 0) <= 0:
            continue
        stock_info.append({**s, "close": basic["close"], "total_mv": basic["total_mv"]})

    if not stock_info:
        return []

    # Cap-top filter
    sorted_by_cap = sorted(stock_info, key=lambda x: x["total_mv"], reverse=True)
    cap_cutoff = max(int(len(sorted_by_cap) * cap_percent / 100), 5)
    cap_top = sorted_by_cap[:cap_cutoff]
    logger.info("Cap-top %d stocks, applying strategies...", len(cap_top))

    # Fetch K-lines and apply strategies
    candidates = []
    for s in cap_top:
        klines = get_stock_daily(s["ts_code"], days=90)
        if len(klines) < 30:
            continue
        klines = calc_macd(klines)

        result = _evaluate_strategy_groups(s, klines, strategy_groups, industry_name)
        if result["passed"]:
            candidates.append(result["candidate"])

    if not candidates:
        logger.info("No candidates in %s", industry_name)
        return []

    # Money flow for scoring
    for c in candidates:
        try:
            mf = get_stock_moneyflow(c.ts_code, days=5)
            c.main_net_inflow = mf["net_amount"]
        except Exception:
            pass

    # Filter by positive inflow if required
    if require_positive_inflow:
        positive = [c for c in candidates if c.main_net_inflow > 0]
        filtered = positive if positive else candidates
    else:
        filtered = candidates

    # Score & rank
    if filtered:
        max_cap = max((c.market_cap for c in filtered), default=1)
        max_inflow = max((abs(c.main_net_inflow) for c in filtered), default=1)
        max_strength = max((c.cross_strength for c in filtered), default=1)

        for c in filtered:
            cap_score = (c.market_cap / max_cap) * 0.3 if max_cap > 0 else 0
            inflow_score = (c.main_net_inflow / max_inflow) * 0.4 if max_inflow > 0 else 0
            signal_score = (c.cross_strength / max_strength) * 0.3 if max_strength > 0 else 0
            c.score = round(cap_score + inflow_score + signal_score, 4)

        filtered.sort(key=lambda c: c.score, reverse=True)

    return filtered


def _evaluate_strategy_groups(s: dict, klines: list[dict],
                               strategy_groups: list[dict],
                               industry_name: str = "") -> dict:
    """Evaluate all strategy groups for a stock. Returns {passed, candidate}."""
    strategy_hits = []
    best_group_score = 0.0

    for grp in strategy_groups:
        conditions = grp.get("conditions", [])
        must_results = []
        opt_results = []
        group_total = 0.0
        group_hits = 0

        for cond in conditions:
            name = cond.get("name", "")
            param = cond.get("param")
            is_must = cond.get("must", True)

            hit, value = _evaluate_condition(name, param, s, klines, industry_name)
            if is_must:
                must_results.append((name, hit, value))
            else:
                opt_results.append((name, hit, value))

            if hit:
                group_hits += 1
                group_total += min(value if isinstance(value, (int, float)) else 1, 100)

        must_pass = all(r[1] for r in must_results) if must_results else True
        opt_pass = any(r[1] for r in opt_results) if opt_results else True
        group_pass = must_pass and opt_pass

        if group_pass:
            group_score = group_total / max(len(conditions), 1)
            best_group_score = max(best_group_score, group_score)
            for name, _, _ in must_results + opt_results:
                strategy_hits.append(name)
                if name == "macd_golden_cross":
                    strategy_hits.append("golden_cross")

    if best_group_score == 0:
        return {"passed": False, "candidate": None}

    cross_strength = 0.0
    golden_cross = "golden_cross" in strategy_hits or "macd_golden_cross" in strategy_hits
    if golden_cross and klines:
        _, cross_strength = detect_golden_cross(klines)

    c = StockCandidate(
        code=s["symbol"], name=s["name"], ts_code=s["ts_code"],
        close=s.get("close", 0), change_pct=0.0, market_cap=s.get("total_mv", 0),
        main_net_inflow=0.0, main_inflow_ratio=0.0,
        golden_cross=golden_cross, cross_strength=cross_strength,
        score=round(best_group_score, 4),
    )
    return {"passed": True, "candidate": c}


def _evaluate_condition(name: str, param, s: dict, klines: list[dict], industry_name: str = "") -> tuple[bool, float]:
    """Evaluate a single screening condition. Returns (hit, value)."""
    if name == "macd_golden_cross":
        hit, val = detect_golden_cross(klines)
        return hit, float(val)
    elif name == "macd_convergence":
        lookback = int((param or {}).get("lookback", 5)) if isinstance(param, dict) else 5
        min_days = int((param or {}).get("min_narrow_days", 2)) if isinstance(param, dict) else 2
        return detect_macd_convergence(klines, lookback=lookback, min_narrow_days=min_days)
    elif name == "ma_cross":
        fast, slow = (param or {}).get("fast", 5), (param or {}).get("slow", 20)
        return detect_ma_cross(klines, fast=int(fast), slow=int(slow))
    elif name == "volume_breakout":
        mult = float((param or {}).get("multiple", 2.0)) if isinstance(param, dict) else float(param or 2.0)
        return detect_volume_breakout(klines, multiple=mult)
    elif name == "rsi_oversold":
        thresh = float((param or {}).get("threshold", 30)) if isinstance(param, dict) else float(param or 30)
        return detect_rsi_oversold(klines, threshold=thresh)
    elif name == "kdj_cross":
        return detect_kdj_cross(klines)
    elif name == "bollinger_bottom":
        return detect_bollinger_bottom(klines)
    elif name == "consecutive_inflow":
        days = int((param or {}).get("days", 3)) if isinstance(param, dict) else int(param or 3)
        return detect_consecutive_inflow(s["ts_code"], days=days)
    elif name == "main_net_inflow":
        thresh = float((param or {}).get("min_amount", 5000)) if isinstance(param, dict) else float(param or 5000)
        try:
            mf = get_stock_moneyflow(s["ts_code"], days=5)
            net = mf["net_amount"]
            return net > thresh, round(net, 2)
        except Exception:
            return False, 0.0
    elif name == "institutional_increase":
        hit, _ = detect_institutional_increase(s["ts_code"])
        return hit, 1.0
    elif name == "no_insider_selling":
        hit, _ = detect_no_insider_selling(s["ts_code"])
        return hit, 1.0
    elif name == "low_valuation":
        return detect_low_valuation(s["ts_code"], industry_name)
    elif name == "fund_turnaround":
        days = int((param or {}).get("days", 5)) if isinstance(param, dict) else int(param or 5)
        return detect_fund_turnaround(s["ts_code"], days=days)
    elif name == "bottom_breakout":
        mult = float((param or {}).get("volume_mult", 1.3)) if isinstance(param, dict) else float(param or 1.3)
        return detect_bottom_breakout(klines, volume_mult=mult)
    elif name == "macd_divergence":
        return detect_macd_divergence(klines)
    else:
        return False, 0.0
