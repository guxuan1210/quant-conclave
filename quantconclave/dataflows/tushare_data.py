"""Tushare data vendor for capital flow / money flow analysis.

Provides A-share market data via tushare Pro API, sourced from East Money (东方财富).
"""

from typing import Annotated
from datetime import datetime, timedelta
import logging
import os
import pandas as pd
import tushare as ts

logger = logging.getLogger(__name__)


def _init_tushare():
    """Initialize tushare with token from env or config."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        ts.set_token(token)
    return token


def _get_pro():
    """Get a tushare pro API instance, initializing if needed."""
    _init_tushare()
    return ts.pro_api()


def _format_ticker_ts(ticker: str) -> str:
    """Convert a ticker to tushare format (e.g. 600519 -> 600519.SH, 000001 -> 000001.SZ)."""
    ticker = ticker.strip().upper()
    # Normalize non-standard suffixes to Tushare format
    if ticker.endswith('.SS'):
        ticker = ticker[:-3] + '.SH'
    elif ticker.endswith('.SHE') or ticker.endswith('.SZE'):
        ticker = ticker[:-4] + '.SZ'
    if "." in ticker:
        return ticker
    if ticker.isdigit():
        if len(ticker) == 6:
            if ticker.startswith(("6", "9")):
                return f"{ticker}.SH"
            elif ticker.startswith(("0", "3")):
                return f"{ticker}.SZ"
            elif ticker.startswith(("4", "8")):
                return f"{ticker}.BJ"
    return ticker


def _is_cn_ticker(ticker: str) -> bool:
    """Check if a ticker is a Chinese A-share ticker (accepts .SS too)."""
    ticker = ticker.strip().upper()
    if ticker.endswith((".SH", ".SZ", ".SS", ".BJ")):
        return True
    if ticker.isdigit() and len(ticker) == 6:
        return True
    return False


def get_money_flow(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """Get individual stock money flow data (??????) from tushare/????.

    Uses tushare `moneyflow` API (doc_id=25) which returns buy/sell volumes
    AND amounts for super-large, large, medium, and small orders separately,
    plus net money flow amount.  Computes per-tier net flows from buy-sell diffs.

    Unit contract: tushare returns all amount fields in 万元; this function
    normalizes them ×1e4 to 元 before returning, so every downstream consumer
    (SMS engine, verify_moneyflow, analyst/advisor prompts, Raw Flow display)
    reads the CSV in ONE convention — 元, as the header documents.

    Available via the user's current Tushare Pro plan (???? feature).
    """
    try:
        if not _is_cn_ticker(ticker):
            return (
                f"# Money Flow data not available for '{ticker}'\n"
                f"Money flow data via tushare is only available for Chinese A-share stocks.\n"
            )

        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()

        start_date = start_date.replace("-", "").strip()
        end_date = end_date.replace("-", "").strip()

        # Use moneyflow API (doc_id=25) for full buy/sell breakdown
        data = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)

        if data is None or data.empty:
            return f"No money flow data found for {ts_code} from {start_date} to {end_date}"

        # Rename for internal consistency
        cols = {
            "ts_code": "ts_code",
            "trade_date": "trade_date",
            "net_mf_amount": "net_amount",
            "buy_elg_amount": "buy_elg_amount",
            "buy_elg_vol": "buy_elg_vol",
            "sell_elg_amount": "sell_elg_amount",
            "sell_elg_vol": "sell_elg_vol",
            "buy_lg_amount": "buy_lg_amount",
            "buy_lg_vol": "buy_lg_vol",
            "sell_lg_amount": "sell_lg_amount",
            "sell_lg_vol": "sell_lg_vol",
            "buy_md_amount": "buy_md_amount",
            "buy_md_vol": "buy_md_vol",
            "sell_md_amount": "sell_md_amount",
            "sell_md_vol": "sell_md_vol",
            "buy_sm_amount": "buy_sm_amount",
            "buy_sm_vol": "buy_sm_vol",
            "sell_sm_amount": "sell_sm_amount",
            "sell_sm_vol": "sell_sm_vol",
        }
        avail = {k: v for k, v in cols.items() if k in data.columns}
        df = data[list(avail.keys())].rename(columns=avail)

        # Compute per-tier net flow (buy - sell) for each order size
        if "buy_elg_amount" in df.columns and "sell_elg_amount" in df.columns:
            df["net_elg_amount"] = df["buy_elg_amount"] - df["sell_elg_amount"]
            df["net_elg_vol"] = df["buy_elg_vol"] - df["sell_elg_vol"]
        if "buy_lg_amount" in df.columns and "sell_lg_amount" in df.columns:
            df["net_lg_amount"] = df["buy_lg_amount"] - df["sell_lg_amount"]
            df["net_lg_vol"] = df["buy_lg_vol"] - df["sell_lg_vol"]
        if "buy_md_amount" in df.columns and "sell_md_amount" in df.columns:
            df["net_md_amount"] = df["buy_md_amount"] - df["sell_md_amount"]
            df["net_md_vol"] = df["buy_md_vol"] - df["sell_md_vol"]
        if "buy_sm_amount" in df.columns and "sell_sm_amount" in df.columns:
            df["net_sm_amount"] = df["buy_sm_amount"] - df["sell_sm_amount"]
            df["net_sm_vol"] = df["buy_sm_vol"] - df["sell_sm_vol"]

        # tushare moneyflow (doc_id=25) returns ALL amount fields in 万元.
        # Normalize ×1e4 → 元 so the whole pipeline holds ONE unit convention —
        # this makes the "All values in yuan" header below TRUE and every
        # consumer correct at once: SMS engine (_fetch_flow_data), the
        # verify_moneyflow tushare side, ai_pick Raw Flow, and the raw CSV the
        # capital-flow analyst / advisor LLM sees. Volume fields (手) stay raw.
        _AMOUNT_COLS = [
            "net_amount",
            "net_elg_amount", "net_lg_amount", "net_md_amount", "net_sm_amount",
            "buy_elg_amount", "sell_elg_amount",
            "buy_lg_amount", "sell_lg_amount",
            "buy_md_amount", "sell_md_amount",
            "buy_sm_amount", "sell_sm_amount",
        ]
        for _col in _AMOUNT_COLS:
            if _col in df.columns:
                df[_col] = df[_col] * 1e4

        # Sort newest first
        df = df.sort_values("trade_date", ascending=False)

        # --- Summary Statistics ---
        try:
            recent5 = df.head(5)
            older5 = df.tail(5) if len(df) >= 10 else recent5
            mf_recent = float(recent5.get("net_elg_amount", pd.Series([0])).sum() + recent5.get("net_lg_amount", pd.Series([0])).sum())
            mf_older = float(older5.get("net_elg_amount", pd.Series([0])).sum() + older5.get("net_lg_amount", pd.Series([0])).sum())
            mf_total = float(df.get("net_elg_amount", pd.Series([0])).sum() + df.get("net_lg_amount", pd.Series([0])).sum())
            retail_total = float(df.get("net_md_amount", pd.Series([0])).sum() + df.get("net_sm_amount", pd.Series([0])).sum())
            buy_elg = float(df.get("buy_elg_amount", pd.Series([0])).sum())
            sell_elg = float(df.get("sell_elg_amount", pd.Series([0])).sum())
            all_cols = [c for c in ["buy_elg_amount","sell_elg_amount","buy_lg_amount","sell_lg_amount","buy_md_amount","sell_md_amount","buy_sm_amount","sell_sm_amount"] if c in df.columns]
            total_vol = float(df[all_cols].values.sum().item()) if all_cols else 1
            mf_ratio = round(abs(mf_total) / max(abs(retail_total), 0.01), 2)
            elg_pct = round(buy_elg / max(total_vol, 0.01) * 100, 1)
            if abs(mf_older) > 0.01 and mf_recent > mf_older * 1.2:
                trend = "accelerating"
            elif abs(mf_older) > 0.01 and mf_recent < mf_older * 0.8:
                trend = "weakening"
            else:
                trend = "stable"
            summary = f"# SUMMARY | Period: {len(df)}d | MainForce: {mf_total:+.0f} | Retail: {retail_total:+.0f} | MF_Ratio: {mf_ratio} | ELG%: {elg_pct}% | Trend: {trend}\n"
            summary += f"# Recent5d MainForce: {mf_recent:+.0f} | Prev5d: {mf_older:+.0f} | Trend: {trend}\n"
        except Exception:
            summary = ""

        csv_string = df.to_csv(index=False)

        header = f"# Money Flow (主力资金流向) for {ts_code} from {start_date} to {end_date}\n"
        header += f"# Source: 东方财富 via tushare (moneyflow doc_id=25)\n"
        header += f"# net_elg=超大单(elg), net_lg=大单(lg), net_md=中单(md), net_sm=小单(sm)\n"
        header += f"# MainForce = elg + lg | Retail = md + sm | MF_Ratio > 2 = institutional dominant\n"
        header += f"# POSITIVE = net inflow, NEGATIVE = net outflow. All values in yuan (×1e4 from tushare 万元).\n"
        header += f"# Rows: {len(df)} trading days\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + summary + csv_string

    except Exception as e:
        return f"# SKIP_VENDOR: Tushare money flow failed: {e}"


def get_money_flow_dc(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """Get individual stock money flow from 同花顺 (THS) via tushare ``moneyflow_dc``.

    This is a genuinely INDEPENDENT pipeline from ``get_money_flow`` (EastMoney
    doc_id=25): 同花顺 computes 主力资金 by its own 特大单/大单 grouping, so a
    large drift between the two is a real cross-validation signal, not a
    round-off. verify_moneyflow uses this as its verifier side.

    Unit contract mirrors ``get_money_flow``: all ``*_amount`` fields are 万元,
    normalized ×1e4 to 元 before returning; ``*_rate``/``pct_change`` columns are
    percentages and ``close`` is a price, left untouched. Sorted newest-first.
    """
    try:
        if not _is_cn_ticker(ticker):
            return (
                f"# Money Flow DC (同花顺) data not available for '{ticker}'\n"
                f"Money flow data via tushare is only available for Chinese A-share stocks.\n"
            )

        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()

        start_date = start_date.replace("-", "").strip()
        end_date = end_date.replace("-", "").strip()

        data = pro.moneyflow_dc(ts_code=ts_code, start_date=start_date, end_date=end_date)

        if data is None or data.empty:
            return f"No money flow DC data found for {ts_code} from {start_date} to {end_date}"

        # Same unit contract as get_money_flow: tushare moneyflow_dc returns all
        # *_amount fields in 万元 → ×1e4 to 元. *_rate / pct_change are percent,
        # close is a price — leave them alone.
        _AMOUNT_COLS = ["net_amount", "buy_elg_amount", "buy_lg_amount",
                        "buy_md_amount", "buy_sm_amount"]
        for _col in _AMOUNT_COLS:
            if _col in data.columns:
                data[_col] = data[_col] * 1e4

        # Sort newest first (same convention as get_money_flow).
        data = data.sort_values("trade_date", ascending=False)

        csv_string = data.to_csv(index=False)

        header = f"# Money Flow DC (同花顺) for {ts_code} from {start_date} to {end_date}\n"
        header += f"# Source: 同花顺 via tushare (moneyflow_dc)\n"
        header += f"# net_amount=净流入(元), *_rate=各档净占比(%), pct_change=涨跌幅(%), close=收盘价(元)\n"
        header += f"# POSITIVE = net inflow, NEGATIVE = net outflow. All amount values in yuan (×1e4 from tushare 万元).\n"
        header += f"# Rows: {len(data)} trading days\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"# SKIP_VENDOR: Tushare money flow DC failed: {e}"


def get_hsgt_flow(
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """Get 沪深港通 (Northbound/Southbound) capital flow data from tushare.

    Shows daily northbound (沪股通+深股通) and southbound (港股通) fund flows.
    """
    try:
        pro = _get_pro()

        start_date = start_date.replace("-", "").strip()
        end_date = end_date.replace("-", "").strip()

        data = pro.moneyflow_hsgt(start_date=start_date, end_date=end_date)

        if data is None or data.empty:
            return f"No 沪深港通 flow data found from {start_date} to {end_date}"

        csv_string = data.to_csv(index=False)

        header = f"# 沪深港通 Capital Flow from {start_date} to {end_date}\n"
        header += f"# Source: tushare\n"
        header += f"# Columns: north_money=北向资金(百万元), south_money=南向资金(百万元)\n"
        header += f"# hgt=沪股通, sgt=深股通, ggt_ss=港股通(沪), ggt_sz=港股通(深)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving 沪深港通 flow: {str(e)}"


def get_margin_trading(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """Get 融资融券 (margin trading & short selling) data for a stock from tushare.

    Returns margin balance (融资余额), short selling balance (融券余额),
    margin buying amount (融资买入额), short selling volume (融券余量), etc.
    over the specified date range.
    """
    try:
        if not _is_cn_ticker(ticker):
            return (
                f"# Margin trading data not available for '{ticker}'\n"
                f"Margin trading data via tushare is only available for Chinese A-share stocks.\n"
            )

        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()

        start_date = start_date.replace("-", "").strip()
        end_date = end_date.replace("-", "").strip()

        data = pro.margin_detail(ts_code=ts_code, start_date=start_date, end_date=end_date)

        if data is None or data.empty:
            return f"No margin trading data found for {ts_code} from {start_date} to {end_date}"

        # Sort by trade_date descending (newest first)
        if "trade_date" in data.columns:
            data = data.sort_values("trade_date", ascending=False)

        csv_string = data.to_csv(index=False)

        header = f"# Margin Trading & Short Selling (融资融券) for {ts_code} from {start_date} to {end_date}\n"
        header += f"# Source: tushare\n"
        header += f"# Columns: rzye=融资余额(元), rqye=融券余额(元), rzmre=融资买入额(元),\n"
        header += f"#   rqyl=融券余量(股), rqchl=融券偿还量(股), rzrqye=融资融券余额(元)\n"
        header += f"# Analysis: High margin balance + increasing = bullish (leveraged buying);\n"
        header += f"#   High short-selling volume = bearish (borrowing to short).\n"
        header += f"#   Track TREND over the period, not just the latest value.\n"
        header += f"# Rows: {len(data)} trading days\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving margin trading for {ticker}: {str(e)}"


def get_market_flow(
    trade_date: Annotated[str, "trade date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """Get market-wide money flow data (大盘资金流向) from tushare/东方财富.

    Shows overall market capital flow for the given trading day.
    """
    try:
        pro = _get_pro()

        trade_date = trade_date.replace("-", "").strip()

        data = pro.moneyflow_mkt_dc(trade_date=trade_date)

        if data is None or data.empty:
            return f"No market flow data found for {trade_date}"

        csv_string = data.to_csv(index=False)

        header = f"# Market Money Flow (大盘资金流向) on {trade_date}\n"
        header += f"# Source: 东方财富 via tushare\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving market flow for {trade_date}: {str(e)}"

def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
    _ma: Annotated[int, "moving average period (unused, for compatibility)"] = None,
) -> str:
    """Get daily OHLCV stock data from tushare (A-share ????).

    Uses pro.daily() - available via ???? in the user's Tushare Pro plan.
    """
    try:
        if not _is_cn_ticker(symbol):
            return f"# SKIP_VENDOR: Tushare stock data only supports Chinese A-share tickers (got {symbol!r})\n"
        ts_code = _format_ticker_ts(symbol)
        pro = _get_pro()
        start = start_date.replace("-", "").strip()
        end = end_date.replace("-", "").strip()
        data = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if data is None or data.empty:
            return f"No stock data found for {ts_code} from {start} to {end}"
        data = data.sort_values("trade_date", ascending=False)
        # 换手率 lives in daily_basic, not daily — join it so the OHLCV CSV feeds
        # turnover-based prompt checks (换手<8% / 高换手>10% in the watchlist &
        # index-picking prompts). Non-fatal: when daily_basic fails the CSV is
        # emitted without a turnover column and callers degrade gracefully.
        try:
            basics = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end)
            if basics is not None and not basics.empty:
                basics = basics[["trade_date", "turnover_rate"]].sort_values(
                    "trade_date", ascending=False)
                data = data.merge(basics, on="trade_date", how="left")
        except Exception as e:
            logger.warning("daily_basic turnover join failed for %s: %s", ts_code, e)
        csv_string = data.to_csv(index=False)
        header = f"# Stock Data (????) for {ts_code} from {start} to {end}\n"
        header += f"# Source: tushare (daily - ????)\n"
        header += f"# Rows: {len(data)} trading days\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare stock data failed: {e}\n"


def get_dragon_tiger_list(
    ticker: Annotated[str, "ticker symbol of the company"],
    trade_date: Annotated[str, "trade date in YYYY-MM-DD format (analysis date; function scans backward for up to 60 trading days if no data found on this exact date)"],
) -> str:
    """Get ??? (Dragon-Tiger List) from tushare ? scans up to 60 trading days backward.

    Uses pro.top_list() per trading day.  If the exact trade_date has no data,
    iterates backward day-by-day (skipping weekends) until data is found or
    the 60-day lookback window is exhausted.

    Available via the user's current Tushare Pro plan (???? ? ???).
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Dragon-Tiger List not available for '{ticker}'\nOnly Chinese A-share stocks supported.\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()

        # Parse the base date
        from datetime import datetime, timedelta
        base = datetime.strptime(trade_date.replace("-", "").strip(), "%Y%m%d")
        lookback_days = 60
        results = []

        for offset in range(lookback_days):
            d = base - timedelta(days=offset)
            td_str = d.strftime("%Y%m%d")
            # Skip weekends
            if d.weekday() >= 5:
                continue
            try:
                data = pro.top_list(trade_date=td_str)
                if data is None or data.empty:
                    continue
                mask = data["ts_code"] == ts_code
                match = data[mask]
                if not match.empty:
                    results.append((td_str, match))
            except Exception:
                # Individual date failure is non-fatal; keep scanning
                continue

        if not results:
            return (
                f"Stock {ts_code} did not appear on the Dragon-Tiger List in the "
                f"60 trading days before {trade_date}.\n"
                f"This is normal ? most stocks do not appear on the list every day.\n"
                f"The Dragon-Tiger List only includes stocks with extreme price "
                f"movements (>7% up/down) or unusual turnover.\n"
            )

        # Build output
        lines = []
        a = lines.append
        a(f"# Dragon-Tiger List (???) for {ts_code}")
        a(f"# Source: tushare (top_list ? ????)")
        a(f"# Lookback window: 60 trading days before {trade_date}")
        a(f"# Dates found: {len(results)} trading day(s)")
        a(f"# l_buy = institutional buy amount, l_sell = institutional sell amount")
        a(f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        a("")

        for td_str, match in results:
            a(f"--- {td_str} ---")
            a(match.to_csv(index=False))
            a("")

        return "\n".join(lines)

    except Exception as e:
        return f"# SKIP_VENDOR: Tushare dragon-tiger list failed: {e}\n"


def get_share_unlock(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get 限售解禁 (Lockup Expiration / Share Float) from tushare.

    Uses pro.share_float() - available via the user's plan (参考数据 - 解禁).
    Returns announcement date, unlock date, unlocked shares, ratio, and holder name.
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Share unlock data not available via tushare for '{ticker}'\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        data = pro.share_float(ts_code=ts_code)
        if data is None or data.empty:
            return f"No share unlock data found for {ts_code}"
        cols = {"ts_code": "ts_code", "ann_date": "ann_date", "float_date": "float_date",
                "float_share": "float_share", "float_ratio": "float_ratio", "holder_name": "holder_name",
                "share_type": "share_type"}
        avail = {k: v for k, v in cols.items() if k in data.columns}
        df = data[list(avail.keys())].rename(columns=avail)
        df = df.sort_values("ann_date", ascending=False) if "ann_date" in df.columns else df
        csv_string = df.to_csv(index=False)
        header = f"# Share Unlock / Lockup Expiration (解禁) for {ts_code}\n"
        header += f"# Source: tushare (share_float - 参考数据)\n"
        header += f"# float_share = shares unlocked, float_ratio = % of total shares\n"
        header += f"# float_date = unlock date, holder_name = shareholder whose shares were unlocked\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare share unlock failed: {e}\n"


def get_share_pledge(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get 股权质押 (Stock Pledge Statistics) from tushare.

    Uses pro.pledge_stat() - available via the user's plan (参考数据 - 质押).
    Returns pledge count, unrestricted pledged shares, restricted pledged shares,
    total shares, and pledge ratio by end_date.
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Share pledge data not available via tushare for '{ticker}'\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        data = pro.pledge_stat(ts_code=ts_code)
        if data is None or data.empty:
            return f"No pledge data found for {ts_code}"
        csv_string = data.to_csv(index=False)
        header = f"# Share Pledge Statistics (股权质押) for {ts_code}\n"
        header += f"# Source: tushare (pledge_stat - 参考数据)\n"
        header += f"# pledge_count = number of active pledges\n"
        header += f"# unrest_pledge = unrestricted shares pledged (10k shares)\n"
        header += f"# rest_pledge = restricted shares pledged (10k shares)\n"
        header += f"# total_share = total shares (10k), pledge_ratio = pledge_ratio %\n"
        header += f"# Higher pledge_ratio = higher risk (controlling shareholder may face margin call)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare share pledge failed: {e}\n"


def get_stock_buyback(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get 股份回购 (Stock Buyback / Repurchase) from tushare.

    Uses pro.repurchase() - available via the user's plan (参考数据 - 回购).
    Returns announcement date, progress status, buyback volume, amount, and price limits.
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Stock buyback data not available via tushare for '{ticker}'\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        data = pro.repurchase(ts_code=ts_code)
        if data is None or data.empty:
            return f"No buyback data found for {ts_code}"
        csv_string = data.to_csv(index=False)
        header = f"# Stock Buyback / Repurchase (回购) for {ts_code}\n"
        header += f"# Source: tushare (repurchase - 参考数据)\n"
        header += f"# ann_date = announcement date, proc = progress status\n"
        header += f"# vol = buyback volume (shares), amount = buyback amount (RMB)\n"
        header += f"# high_limit/low_limit = buyback price range\n"
        header += f"# Active buyback programs are usually bullish (signals management confidence)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare stock buyback failed: {e}\n"


def get_holder_changes(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYY-MM-DD format"],
) -> str:
    """Get 股东增减持 (Shareholder Holding Changes) from tushare.

    Uses pro.stk_holdertrade() - available via the user's plan (参考数据 - 增减持).
    Returns individual holder transactions: who changed holdings, direction (increase/decrease),
    volume, ratio change, average price.
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Holder change data not available via tushare for '{ticker}'\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        sd = start_date.replace("-", "").strip()
        ed = end_date.replace("-", "").strip()
        data = pro.stk_holdertrade(ts_code=ts_code, start_date=sd, end_date=ed)
        if data is None or data.empty:
            return f"No holder change data found for {ts_code} from {sd} to {ed}"
        if "ann_date" in data.columns:
            data = data.sort_values("ann_date", ascending=False)
        csv_string = data.to_csv(index=False)
        header = f"# Shareholder Holding Changes (股东增减持) for {ts_code}\n"
        header += f"# Source: tushare (stk_holdertrade - 参考数据)\n"
        header += f"# Period: {start_date} to {end_date}\n"
        header += f"# in_de = IN (increase holdings) / DE (decrease holdings)\n"
        header += f"# change_vol = shares changed, change_ratio = % change\n"
        header += f"# avg_price = average transaction price\n"
        header += f"# Insider buying (IN) is bullish; insider selling (DE) may signal overvaluation\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare holder changes failed: {e}\n"


def get_institutional_holders(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get top 10 holders (????) from tushare.

    Uses pro.top10_holders() - available via the user's plan.
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Institutional holders data not available via tushare for '{ticker}'\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        data = pro.top10_holders(ts_code=ts_code)
        if data is None or data.empty:
            return f"No top-10 holders data found for {ts_code}"
        csv_string = data.to_csv(index=False)
        header = f"# Top 10 Shareholders (????) for {ts_code}\n"
        header += f"# Source: tushare (top10_holders)\n"
        header += f"# hold_amount = shares held, hold_ratio = % of total\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare institutional holders failed: {e}\n"


def get_major_holders(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get top 10 floated holders (??????) from tushare.

    Uses pro.top10_floatholders() - available via the user's plan.
    """
    try:
        if not _is_cn_ticker(ticker):
            return f"# Major holders data not available via tushare for '{ticker}'\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        data = pro.top10_floatholders(ts_code=ts_code)
        if data is None or data.empty:
            return f"No top-10 floated holders data found for {ts_code}"
        csv_string = data.to_csv(index=False)
        header = f"# Top 10 Floated Shareholders (??????) for {ts_code}\n"
        header += f"# Source: tushare (top10_floatholders)\n"
        header += f"# hold_amount = shares held, hold_ratio = % of floated shares\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare major holders failed: {e}\n"



# ---- Financial Statements (????) via tushare ----


def get_income_statement_tushare(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
) -> str:
    """Get income statement (???) from tushare.
    
    Uses pro.income() - returns revenue, costs, profits, EPS etc.
    More accurate for A-share stocks than yfinance.
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Tushare income statement only available for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        
        # Determine report type from freq
        period = "Q" if freq.lower().startswith("q") else "A"
        
        # Get last 8 reporting periods
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5*365)).strftime("%Y%m%d")
        
        data = pro.income(
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            limit=8
        )
        
        if data is None or data.empty:
            return f"No income statement data found for {ts_code}"
        
        # Sort by report date descending
        if "end_date" in data.columns:
            data = data.sort_values("end_date", ascending=False)
        elif "f_ann_date" in data.columns:
            data = data.sort_values("f_ann_date", ascending=False)
        
        csv_string = data.to_csv(index=False)
        header = f"# Income Statement (???) for {ts_code} [{period}]\n"
        header += f"# Source: tushare (income)\n"
        header += f"# Key fields: revenue=????, total_cogs=????, operate_profit=????\n"
        header += f"# n_income=???, eps=????, basic_eps=??????\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare income statement failed: {e}\n"


def get_balance_sheet_tushare(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
) -> str:
    """Get balance sheet (?????) from tushare.
    
    Uses pro.balancesheet() - returns assets, liabilities, equity etc.
    More accurate for A-share stocks than yfinance.
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Tushare balance sheet only available for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        
        period = "Q" if freq.lower().startswith("q") else "A"
        
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5*365)).strftime("%Y%m%d")
        
        data = pro.balancesheet(
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            limit=8
        )
        
        if data is None or data.empty:
            return f"No balance sheet data found for {ts_code}"
        
        if "end_date" in data.columns:
            data = data.sort_values("end_date", ascending=False)
        elif "f_ann_date" in data.columns:
            data = data.sort_values("f_ann_date", ascending=False)
        
        csv_string = data.to_csv(index=False)
        header = f"# Balance Sheet (?????) for {ts_code} [{period}]\n"
        header += f"# Source: tushare (balancesheet)\n"
        header += f"# Key fields: total_assets=???, total_liab=???, total_hldr_eqy_exc_min_int=????\n"
        header += f"# current_assets=????, current_liab=????, money_cap=????\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare balance sheet failed: {e}\n"


def get_cashflow_tushare(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
) -> str:
    """Get cash flow statement (?????) from tushare.
    
    Uses pro.cashflow() - returns operating, investing, financing cash flows.
    More accurate for A-share stocks than yfinance.
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Tushare cashflow only available for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        
        period = "Q" if freq.lower().startswith("q") else "A"
        
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5*365)).strftime("%Y%m%d")
        
        data = pro.cashflow(
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            limit=8
        )
        
        if data is None or data.empty:
            return f"No cashflow data found for {ts_code}"
        
        if "end_date" in data.columns:
            data = data.sort_values("end_date", ascending=False)
        elif "f_ann_date" in data.columns:
            data = data.sort_values("f_ann_date", ascending=False)
        
        csv_string = data.to_csv(index=False)
        header = f"# Cash Flow Statement (?????) for {ts_code} [{period}]\n"
        header += f"# Source: tushare (cashflow)\n"
        header += f"# Key fields: n_cashflow_act=???????, n_cashflow_inv_act=???????\n"
        header += f"# n_cashflow_fin_act=???????, cce_add=??????\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare cashflow failed: {e}\n"


# ---- Macroeconomic Context (????) ----

def get_macro_context() -> str:
    """Get macroeconomic context summary for A-share analysis.

    Uses tushare macro APIs: CPI, PMI, M2 money supply, LPR interest rate.
    Generates a concise summary for injection into analyst system prompts
    so every agent has the macro backdrop when evaluating individual stocks.
    """
    try:
        pro = _get_pro()
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")

        lines = []
        lines.append("# Macroeconomic Context (??????)")
        lines.append(f"# Source: tushare, retrieved {datetime.now().strftime('%Y-%m-%d')}")
        lines.append("")

        # CPI - Consumer Price Index
        try:
            cpi = pro.cn_cpi(start_m=start_date[:6], end_m=end_date[:6])
            if cpi is not None and not cpi.empty:
                cpi = cpi.sort_values("month", ascending=False)
                latest = cpi.iloc[0]
                cpi_val = latest.get("cpi_yoy", "N/A")
                lines.append(f"**CPI (YoY)**: {cpi_val}% | Latest month: {latest.get('month', 'N/A')}")
                # Trend: last 6 months
                recent = cpi.head(6)["cpi_yoy"].dropna()
                if len(recent) >= 3:
                    trend = "rising" if recent.iloc[0] > recent.iloc[-1] else "falling"
                    lines.append(f"  Trend (6m): {trend}, range {recent.min():.1f}% ~ {recent.max():.1f}%")
        except Exception:
            lines.append("**CPI**: Data unavailable")

        # PMI - Purchasing Managers Index
        try:
            pmi = pro.cn_pmi(start_m=start_date[:6], end_m=end_date[:6])
            if pmi is not None and not pmi.empty:
                pmi = pmi.sort_values("month", ascending=False)
                latest = pmi.iloc[0]
                pmi_val = latest.get("pmi0101", latest.get("pmi", "N/A"))
                pmi_status = "expansion" if float(pmi_val) >= 50 else "contraction" if pmi_val != "N/A" else "unknown"
                lines.append(f"**Manufacturing PMI**: {pmi_val} ({pmi_status}) | Latest: {latest.get('month', 'N/A')}")
        except Exception:
            lines.append("**PMI**: Data unavailable")

        # M2 Money Supply
        try:
            m2 = pro.cn_m(start_m=start_date[:6], end_m=end_date[:6])
            if m2 is not None and not m2.empty:
                m2 = m2.sort_values("month", ascending=False)
                latest = m2.iloc[0]
                m2_val = latest.get("m2_yoy", "N/A")
                lines.append(f"**M2 Money Supply (YoY)**: {m2_val}% | Latest: {latest.get('month', 'N/A')}")
        except Exception:
            lines.append("**M2**: Data unavailable")

        # LPR - Loan Prime Rate
        try:
            lpr = pro.shibor_lpr()
            if lpr is not None and not lpr.empty:
                lpr_1y = lpr[lpr["term"] == "1Y"]
                if not lpr_1y.empty:
                    latest_lpr = lpr_1y.sort_values("date", ascending=False).iloc[0]
                    lines.append(f"**LPR (1Y)**: {latest_lpr.get('rate', 'N/A')}% | {latest_lpr.get('date', 'N/A')}")
        except Exception:
            lines.append("**LPR**: Data unavailable")

        lines.append("")
        lines.append("---")
        lines.append("*Use this macro context to interpret capital flows, fundamentals, and market signals.*")
        lines.append("*Monetary easing + capital inflow = bullish; tightening + outflow = bearish.*")

        return "\n".join(lines)
    except Exception as e:
        return f"# Macro context unavailable: {e}\n"


# ---- Broker Recommendations (????) ----

def get_broker_recommend(
    ticker: Annotated[str, "ticker symbol of the company"],
    month: Annotated[str, "month in YYYYMM format (e.g. 202601)"] = None,
) -> str:
    """Get broker gold-stock recommendations (????) from tushare.

    Returns monthly recommended stocks from major brokerages. This is a strong
    institutional consensus signal - when multiple brokers recommend the same
    stock simultaneously, it often precedes institutional buying programs.

    Use this to validate capital flow signals: if capital is flowing in AND
    brokers are recommending the stock, it is a high-confidence bullish signal.
    If capital is flowing in but NO broker recommends it, the inflow may be
    speculative or manipulative.
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Broker recommendations only available for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()

        if month is None:
            month = datetime.now().strftime("%Y%m")

        data = pro.broker_recommend(month=month)
        if data is None or data.empty:
            return f"No broker recommendations found for {month}"

        # Filter for this ticker
        ticker_data = data[data["ts_code"] == ts_code]
        if ticker_data.empty:
            return f"# Broker Recommendations for {ts_code}\nNo broker recommended {ts_code} in {month}\n"

        # Count recommendations and list brokers
        broker_list = ticker_data["broker"].unique() if "broker" in ticker_data.columns else []
        count = len(ticker_data)

        csv_string = ticker_data.to_csv(index=False)
        header = f"# Broker Gold-Stock Recommendations (????) for {ts_code}\n"
        header += f"# Source: tushare (broker_recommend)\n"
        header += f"# Month: {month} | Brokers recommending: {count}\n"
        header += f"# Brokers: {', '.join(str(b) for b in broker_list[:10])}\n"
        header += f"# Signal: {'STRONG BULLISH' if count >= 3 else 'BULLISH' if count >= 1 else 'NEUTRAL'}\n"
        header += f"# Multiple broker consensus often precedes institutional buying.\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare broker recommend failed: {e}\n"


# ---- Weekly/Monthly OHLCV (??/??) ----

def get_weekly_data(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD format"],
    end_date: Annotated[str, "end date in YYYYMMDD format"],
) -> str:
    """Get weekly OHLCV data (??) for medium-term trend confirmation.

    Weekly bars filter out daily noise and reveal institutional accumulation/distribution
    patterns. Cross-reference with daily money flow: weekly uptrend + daily inflow = genuine
    accumulation; weekly downtrend + daily inflow = likely dead cat bounce.
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Weekly data via tushare only for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        sd = start_date.replace("-", "").strip()
        ed = end_date.replace("-", "").strip()
        data = pro.weekly(ts_code=ts_code, start_date=sd, end_date=ed)
        if data is None or data.empty:
            return f"No weekly data found for {ts_code}"
        data = data.sort_values("trade_date", ascending=False)
        csv_string = data.to_csv(index=False)
        header = f"# Weekly OHLCV (??) for {ts_code}\n"
        header += f"# Source: tushare (weekly)\n"
        header += f"# Period: {start_date} to {end_date}\n"
        header += f"# Use for medium-term trend confirmation. Weekly bars = institutional timeframe.\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare weekly data failed: {e}\n"


def get_monthly_data(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD format"],
    end_date: Annotated[str, "end date in YYYYMMDD format"],
) -> str:
    """Get monthly OHLCV data (??) for long-term trend identification.

    Monthly bars show the dominant trend that daily and weekly fluctuations
    ride on top of. The monthly trend is the tide - trade with it, not against it.
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Monthly data via tushare only for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()
        sd = start_date.replace("-", "").strip()
        ed = end_date.replace("-", "").strip()
        data = pro.monthly(ts_code=ts_code, start_date=sd, end_date=ed)
        if data is None or data.empty:
            return f"No monthly data found for {ts_code}"
        data = data.sort_values("trade_date", ascending=False)
        csv_string = data.to_csv(index=False)
        header = f"# Monthly OHLCV (??) for {ts_code}\n"
        header += f"# Source: tushare (monthly)\n"
        header += f"# Period: {start_date} to {end_date}\n"
        header += f"# Monthly trend = dominant tide. Trade with it.\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare monthly data failed: {e}\n"


# ---- Fund Portfolio Holdings (??????) ----

def get_fund_holdings(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get mutual fund holdings for this stock (????).

    Queries tushare fund_portfolio to find which funds hold this stock
    and whether they increased or decreased positions in the latest reporting period.

    This is the most DIRECT validation of major capital behavior:
    - If capital flow analysis shows institutional buying AND fund holdings confirm
      increased positions = HIGH CONFIDENCE bullish signal
    - If capital flow shows buying but funds are reducing positions = potential
      distribution (????), treat the capital flow signal with skepticism
    """
    try:
        if not _is_cn_ticker(ticker):
            return "# SKIP_VENDOR: Fund holdings via tushare only for CN A-share stocks\n"
        ts_code = _format_ticker_ts(ticker)
        pro = _get_pro()

        data = pro.fund_portfolio(ts_code=ts_code)
        if data is None or data.empty:
            return f"No fund portfolio data found for {ts_code}"

        # Sort by report date descending
        if "end_date" in data.columns:
            data = data.sort_values("end_date", ascending=False)
        elif "f_ann_date" in data.columns:
            data = data.sort_values("f_ann_date", ascending=False)

        csv_string = data.to_csv(index=False)
        header = f"# Fund Portfolio Holdings (????) for {ts_code}\n"
        header += f"# Source: tushare (fund_portfolio)\n"
        header += f"# This shows which mutual funds hold this stock and their position weight.\n"
        header += f"# High fund concentration = institutional endorsement = validates capital flow signals.\n"
        header += f"# Declining fund positions + apparent inflow = potential distribution trap.\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare fund holdings failed: {e}\n"


# ---- A+H Cross-Market Tools (A+H ??????) ----

# Static mapping of common A+H pairs (A-code -> H-code)
_AH_PAIRS = {
    "600036.SH": "03968.HK",  # ????
    "601398.SH": "01398.HK",  # ????
    "601939.SH": "00939.HK",  # ????
    "601288.SH": "01288.HK",  # ????
    "601988.SH": "03988.HK",  # ????
    "601318.SH": "02318.HK",  # ????
    "601628.SH": "02628.HK",  # ????
    "601601.SH": "02601.HK",  # ????
    "601857.SH": "00857.HK",  # ????
    "600028.SH": "00386.HK",  # ????
    "601088.SH": "01088.HK",  # ????
    "600585.SH": "00914.HK",  # ????
    "000002.SZ": "02202.HK",  # ??
    "000063.SZ": "00763.HK",  # ????
    "000338.SZ": "02338.HK",  # ????
    "000776.SZ": "01776.HK",  # ????
    "002594.SZ": "01211.HK",  # ???
    "600519.SH": "",          # ?? - ?A?
    "000858.SZ": "",          # ??? - ?A?
}


def _resolve_ah_pair(ticker: str) -> dict | None:
    """Resolve A+H pair for a ticker. Returns {a_code, h_code, name} or None."""
    ts_code = _format_ticker_ts(ticker)

    # Direct lookup
    hk = _AH_PAIRS.get(ts_code)
    if hk is not None:
        return {"a_code": ts_code, "h_code": hk} if hk else None

    # Reverse lookup (H-code -> A-code)
    for a_code, h_code in _AH_PAIRS.items():
        if h_code == ts_code:
            return {"a_code": a_code, "h_code": h_code}

    # Try tushare dynamic lookup: search stock_basic for same name in HK
    try:
        pro = _get_pro()
        # Get the A-share name
        a_info = pro.stock_basic(ts_code=ts_code, fields="ts_code,name")
        if a_info is not None and not a_info.empty:
            a_name = a_info.iloc[0]["name"]
            # Search HK for same name
            hk_info = pro.hk_basic(name=a_name)
            if hk_info is not None and not hk_info.empty:
                hk_code = hk_info.iloc[0]["ts_code"]
                # Cache for future
                _AH_PAIRS[ts_code] = hk_code
                return {"a_code": ts_code, "h_code": hk_code}
    except Exception:
        pass

    return None


def detect_ah_relationship(
    ticker: Annotated[str, "A-share or H-share ticker symbol"],
) -> str:
    """Detect if a stock has A+H dual listing and return the pair.

    This is the FIRST tool to call when analyzing any A-share stock.
    If the stock is dual-listed, you MUST cross-check:
    - A-H premium: is A-share overvalued vs H-share?
    - South-bound flow: is mainland capital buying the H-share too?
    - H-share lead: did H-share move first (more efficient pricing)?

    Returns the pair codes or confirms single-listing.
    """
    try:
        pair = _resolve_ah_pair(ticker)
        if pair and pair.get("h_code"):
            return (
                f"# A+H Dual Listing Detected\n"
                f"A-Share: {pair['a_code']}\n"
                f"H-Share: {pair['h_code']}\n\n"
                f"ACTION REQUIRED:\n"
                f"1. Call get_ah_premium to check A-H valuation gap\n"
                f"2. Call get_hk_stock_data for H-share price/volume\n"
                f"3. Call get_southbound_flow for mainland->HK capital\n"
                f"4. Compare with get_hsgt_flow (north-bound) to detect divergence\n"
            )
        else:
            return (
                f"# Single Listing: {ticker}\n"
                f"No H-share counterpart detected. Analyze as A-share only.\n"
            )
    except Exception as e:
        return f"# A+H detection failed: {e}\nProceed with A-share-only analysis.\n"


def get_hk_stock_data(
    ticker: Annotated[str, "H-share ticker (e.g. 03968.HK) or A-share ticker"],
    start_date: Annotated[str, "start date in YYYYMMDD format"],
    end_date: Annotated[str, "end date in YYYYMMDD format"],
) -> str:
    """Get Hong Kong stock daily OHLCV data.

    For A+H dual-listed stocks, H-share prices often lead A-share moves
    because HK institutional investors price more efficiently.
    Compare H-share trend with A-share: if H-share breaks out first,
    A-share is likely to follow.
    """
    try:
        pair = _resolve_ah_pair(ticker)
        hk_code = pair["h_code"] if pair else ticker

        pro = _get_pro()
        sd = start_date.replace("-", "").strip()
        ed = end_date.replace("-", "").strip()

        data = pro.hk_daily(ts_code=hk_code, start_date=sd, end_date=ed)
        if data is None or data.empty:
            return f"No HK daily data found for {hk_code}"

        data = data.sort_values("trade_date", ascending=False)
        csv_string = data.to_csv(index=False)
        header = f"# HK Stock Daily: {hk_code}\n"
        header += f"# Source: tushare (hk_daily)\n"
        header += f"# Period: {start_date} to {end_date}\n"
        header += f"# Compare with A-share: H-share often leads A-share by 1-3 days.\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"# SKIP_VENDOR: HK stock data failed: {e}\n"


def get_ah_premium(
    ticker: Annotated[str, "A-share ticker symbol"],
) -> str:
    """Calculate A-H premium/discount ratio.

    A-H premium = (A-share price / H-share price / FX) - 1
    - Premium > 30%: A-share is overvalued relative to H-share (risky)
    - Premium 5-25%: Normal range for A-shares
    - Premium < 5% or negative: A-share is cheap vs H-share (opportunity)

    High premium means mainland investors are paying a big markup
    vs what international investors pay for the same company.
    """
    try:
        pair = _resolve_ah_pair(ticker)
        if not pair or not pair.get("h_code"):
            return f"No H-share counterpart for {ticker}. A-H premium not applicable.\n"

        pro = _get_pro()

        # Get latest A-share price
        a_data = pro.daily(ts_code=pair["a_code"], limit=1)
        if a_data is None or a_data.empty:
            return f"A-share price unavailable for {pair['a_code']}"
        a_price = float(a_data.iloc[0]["close"])

        # Get latest H-share price
        h_data = pro.hk_daily(ts_code=pair["h_code"], limit=1)
        if h_data is None or h_data.empty:
            return f"H-share price unavailable for {pair['h_code']}"
        h_price = float(h_data.iloc[0]["close"])

        # Approximate FX: 1 HKD ? 0.92 RMB
        fx = 0.92
        a_in_hkd = a_price / fx
        premium_pct = (a_in_hkd / h_price - 1) * 100

        # Get historical premium range
        try:
            hist_a = pro.daily(ts_code=pair["a_code"], limit=120)
            hist_h = pro.hk_daily(ts_code=pair["h_code"], limit=120)
            if hist_a is not None and hist_h is not None:
                merged_dates = set(hist_a["trade_date"]) & set(hist_h["trade_date"])
                premiums = []
                for d in sorted(merged_dates)[-60:]:
                    ap = float(hist_a[hist_a["trade_date"]==d]["close"].iloc[0]) if len(hist_a[hist_a["trade_date"]==d]) > 0 else 0
                    hp = float(hist_h[hist_h["trade_date"]==d]["close"].iloc[0]) if len(hist_h[hist_h["trade_date"]==d]) > 0 else 0
                    if hp > 0:
                        premiums.append((ap/fx/hp - 1) * 100)
                if premiums:
                    avg_prem = sum(premiums) / len(premiums)
                    min_prem = min(premiums)
                    max_prem = max(premiums)
        except Exception:
            avg_prem = premium_pct
            min_prem = premium_pct
            max_prem = premium_pct

        signal = (
            "HIGH - A-share overvalued, consider H-share or wait" if premium_pct > 30
            else "NORMAL" if premium_pct >= 5
            else "LOW/DISCOUNT - A-share undervalued, opportunity"
        )

        return (
            f"# A-H Premium Analysis: {pair['a_code']} vs {pair['h_code']}\n\n"
            f"A-Share Price: {a_price:.2f} RMB\n"
            f"H-Share Price: {h_price:.2f} HKD (?{h_price*fx:.2f} RMB)\n"
            f"**A-H Premium: {premium_pct:.1f}%** [{signal}]\n\n"
            f"Historical Range (60d): {min_prem:.1f}% ~ {max_prem:.1f}% (avg: {avg_prem:.1f}%)\n\n"
            f"Interpretation:\n"
            f"- Premium >30%: A-share expensive. Favor H-share or wait for pullback.\n"
            f"- Premium 5-25%: Normal A-share premium range.\n"
            f"- Premium <5%: A-share cheap vs H-share. Accumulation opportunity.\n"
        )
    except Exception as e:
        return f"# A-H premium calculation failed: {e}\n"


def get_southbound_flow(
    ticker: Annotated[str, "H-share ticker or A-share ticker (auto-resolves)"],
    start_date: Annotated[str, "start date in YYYYMMDD format"],
    end_date: Annotated[str, "end date in YYYYMMDD format"],
) -> str:
    """Get south-bound capital flow (????) for H-shares.

    South-bound = mainland Chinese capital flowing into Hong Kong stocks
    via ??? (Shanghai/Shenzhen-HK Stock Connect).

    For A+H stocks, compare south-bound (H-share) vs north-bound (A-share):
    - Both buying: genuine institutional interest across markets
    - North buying + South selling: potential A-share speculation, be cautious
    - South buying + North selling: institutions prefer H-share valuation

    Uses tushare moneyflow_hsgt API with south-bound focus.
    """
    try:
        pair = _resolve_ah_pair(ticker)
        hk_code = pair["h_code"] if pair else ticker

        pro = _get_pro()
        sd = start_date.replace("-", "").strip()
        ed = end_date.replace("-", "").strip()

        # Use south-bound money flow
        data = pro.moneyflow_hsgt(start_date=sd, end_date=ed)
        if data is None or data.empty:
            return f"No south-bound flow data available for period {sd}-{ed}"

        # Focus on south-bound fields
        south_fields = [c for c in data.columns if "south" in c.lower() or "ggt" in c.lower()]
        if south_fields:
            data = data.sort_values("trade_date", ascending=False)
            recent = data.head(10)
            csv_string = recent.to_csv(index=False)

            # Calculate net south flow trend
            if "south_net" in data.columns or "ggt_ss" in data.columns:
                net_col = "south_net" if "south_net" in data.columns else "ggt_ss"
                total_net = data[net_col].head(20).sum()
                trend = "INFLOW (mainland buying HK)" if total_net > 0 else "OUTFLOW (mainland selling HK)"
            else:
                trend = "unknown"

            header = f"# South-Bound Capital Flow (????)\n"
            header += f"# Target: {hk_code}\n"
            header += f"# Period: {start_date} to {end_date}\n"
            header += f"# Net Flow (20d): {trend}\n"
            header += f"# Source: tushare (moneyflow_hsgt)\n\n"
            header += f"# COMPARE with get_hsgt_flow() (north-bound) to detect:\n"
            header += f"#   Both buying = genuine institutional interest\n"
            header += f"#   North buy + South sell = potential A-share speculation\n"
            header += f"#   South buy + North sell = institutions prefer H-share valuation\n"
            header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            return header + csv_string
        else:
            return "# South-bound flow data structure unexpected. Use get_hsgt_flow for north-bound.\n"
    except Exception as e:
        return f"# SKIP_VENDOR: South-bound flow failed: {e}\n"


# ---------------------------------------------------------------------------
# Market-wide screening tools (top gainers, multi-factor ranking)
# ---------------------------------------------------------------------------

def get_tushare_top_gainers(
    trade_date=None, top_n=50, min_amount=50000000.0, filter_st=True,
) -> str:
    """Get top gainers across the entire A-share market via Tushare."""
    try:
        from quantconclave.sector_scan.top_gainers import get_top_gainers
        return get_top_gainers(
            trade_date=trade_date, top_n=top_n,
            min_amount=min_amount, filter_st=filter_st,
        )
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare top gainers failed: {e}\n"


def get_tushare_multi_factor_ranking(
    trade_date=None, top_n=50, min_amount=50000000.0,
) -> str:
    """Multi-factor composite ranking across entire A-share market via Tushare."""
    try:
        from quantconclave.sector_scan.top_gainers import get_multi_factor_ranking
        return get_multi_factor_ranking(
            trade_date=trade_date, top_n=top_n, min_amount=min_amount,
        )
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare multi-factor ranking failed: {e}\n"


def get_tushare_top_net_inflow(
    trade_date=None, top_n=50, min_amount=3000000.0,
) -> str:
    """Top stocks by daily main-force net inflow across entire A-share market via Tushare."""
    try:
        from quantconclave.sector_scan.top_gainers import get_top_net_inflow
        return get_top_net_inflow(
            trade_date=trade_date, top_n=top_n, min_amount=min_amount,
        )
    except Exception as e:
        return f"# SKIP_VENDOR: Tushare top net inflow failed: {e}\n"
