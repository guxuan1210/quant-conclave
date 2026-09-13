"""AKShare data vendor for Chinese A-share real-time and intraday market data.

Wraps AKShare (东方财富 public APIs) to provide near-real-time quotes,
intraday candlesticks, daily OHLCV, and money flow — all free, no API key.

Rate limiting: each function sleeps 0.5–2.0 s to avoid 东方财富 IP bans.
Non-CN tickers return ``# SKIP_VENDOR:`` so ``route_to_vendor`` falls back.
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Annotated

import akshare as ak
import pandas as pd


# ---------------------------------------------------------------------------
# Ticker helpers
# ---------------------------------------------------------------------------

def _is_cn_ticker(ticker: str) -> bool:
    """Return True when *ticker* looks like a Chinese A-share symbol (accepts .SS)."""
    t = ticker.strip().upper()
    if t.endswith((".SH", ".SZ", ".SS", ".BJ")):
        return True
    if t.isdigit() and len(t) == 6:
        return True
    return False


def _ticker_to_ak_symbol(ticker: str) -> tuple[str, str]:
    """Convert a QuantConclave ticker to an AKShare (code, exchange) pair.

    >>> _ticker_to_ak_symbol("000001.SZ")
    ("000001", "sz")
    >>> _ticker_to_ak_symbol("600519.SH")
    ("600519", "sh")
    >>> _ticker_to_ak_symbol("600519")
    ("600519", "sh")
    """
    t = ticker.strip().upper()
    code = t
    exchange = ""

    # Strip known suffix (.SS maps to sh, matching yfinance's SH convention)
    for suffix, exch in ((".SH", "sh"), (".SS", "sh"), (".SZ", "sz"), (".BJ", "bj")):
        if t.endswith(suffix):
            code = t[: -len(suffix)]
            exchange = exch
            break

    # Detect exchange from prefix when no suffix was present
    if not exchange:
        if not code.isdigit() or len(code) != 6:
            raise ValueError(f"Cannot determine exchange for ticker: {ticker!r}")
        first = code[0]
        if first in ("0", "3"):
            exchange = "sz"
        elif first in ("6", "9"):
            exchange = "sh"
        elif first in ("4", "8"):
            exchange = "bj"
        else:
            raise ValueError(f"Unrecognised ticker prefix: {ticker!r}")

    return code, exchange


def _skip_non_cn(ticker: str) -> str | None:
    """Return a ``# SKIP_VENDOR:`` message for non-CN tickers, or None."""
    if not _is_cn_ticker(ticker):
        return f"# SKIP_VENDOR: AKShare only supports Chinese A-share tickers (got {ticker!r})\n"
    return None


def _rate_limit():
    """Sleep a random interval so 东方财富 does not IP-ban us (>5 req/s)."""
    time.sleep(random.uniform(0.5, 2.0))


# ---------------------------------------------------------------------------
# Vendor functions
# ---------------------------------------------------------------------------

def get_realtime_quote_akshare(
    symbol: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Real-time A-share quote snapshot via Xueqiu single-stock API.

    Uses ``ak.stock_individual_spot_xq()`` (Xueqiu) instead of
    ``ak.stock_zh_a_spot_em()`` (Eastmoney) so we only fetch the
    requested stock instead of the entire 5000+ A-share market.
    """
    skip = _skip_non_cn(symbol)
    if skip:
        return skip

    _rate_limit()
    try:
        code, exchange = _ticker_to_ak_symbol(symbol)
        ak_symbol = exchange.upper() + code
        df = ak.stock_individual_spot_xq(symbol=ak_symbol, timeout=15.0)
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare realtime quote failed: {e}\n"

    # Build lookup dict from (item, value) pairs
    lookup = {}
    for _, row in df.iterrows():
        item = row["item"]
        val = row["value"]
        if pd.notna(val):
            lookup[str(item).strip()] = val

    name = lookup.get("名称", symbol.upper())
    latest = lookup.get("现价")
    pct = lookup.get("涨幅")

    lines = [
        f"# Real-Time Quote for {symbol.upper()} - {name}",
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Source: Xueqiu via AKShare",
        "",
    ]

    if latest is not None:
        change_str = ""
        if pct is not None:
            sign = "+" if float(pct) >= 0 else ""
            change_str = f"  Change: {sign}{pct}%"
        lines.append(f"Current Price: {latest}{change_str}")

    for cn_key, en_label in [
        ("今开", "Open"),
        ("最高", "Day High"),
        ("最低", "Day Low"),
        ("昨收", "Previous Close"),
        ("成交量", "Volume"),
        ("成交额", "Turnover"),
        ("振幅", "Amplitude %"),
        ("周转率", "Turnover Rate %"),
        ("市盈率(TTM)", "PE (TTM)"),
        ("市净率", "PB"),
        ("资产净值/总市值", "Total Market Cap"),
        ("流通值", "Circulating Market Cap"),
    ]:
        val = lookup.get(cn_key)
        if val is not None:
            lines.append(f"{en_label}: {val}")

    lines.append("")
    return "\n".join(lines)

def get_intraday_data_akshare(
    symbol: Annotated[str, "ticker symbol of the company"],
    interval: Annotated[str, "intraday interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Intraday minute-level OHLCV from 东方财富 via AKShare."""
    skip = _skip_non_cn(symbol)
    if skip:
        return skip

    # Map interval strings
    _interval_map = {
        "1m": "1", "5m": "5", "15m": "15", "30m": "30",
        "60m": "60", "1h": "60",
    }
    period = _interval_map.get(interval)
    if period is None:
        return (
            f"# SKIP_VENDOR: AKShare intraday does not support interval {interval!r}. "
            f"Supported: 1m, 5m, 15m, 30m, 60m, 1h\n"
        )

    _rate_limit()
    try:
        code, _exchange = _ticker_to_ak_symbol(symbol)
        # start/end date for akshare may include HH:MM:SS; pass as-is
        df = ak.stock_zh_a_hist_min_em(
            symbol=code,
            period=period,
            start_date=f"{start_date} 09:30:00",
            end_date=f"{end_date} 15:00:00",
        )
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare intraday data failed: {e}\n"

    if df is None or df.empty:
        return f"# No intraday data found for {symbol} ({interval}) from {start_date} to {end_date}\n"

    col_map = {
        "时间": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Turnover",
        "最新价": "Latest",
    }
    available = {k: v for k, v in col_map.items() if k in df.columns}
    df_out = df[list(available.keys())].rename(columns=available)

    header = (
        f"# Intraday OHLCV for {symbol.upper()} ({interval})\n"
        f"# Period: {start_date} → {end_date}\n"
        f"# Source: 东方财富 via AKShare\n"
        f"# Rows: {len(df_out)}\n\n"
    )
    return header + df_out.to_csv(index=False)


def get_stock_data_akshare(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Daily OHLCV (前复权) from 东方财富 via AKShare."""
    skip = _skip_non_cn(symbol)
    if skip:
        return skip

    _rate_limit()
    try:
        code, _exchange = _ticker_to_ak_symbol(symbol)
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare stock data failed: {e}\n"

    if df is None or df.empty:
        return f"# No daily data found for {symbol} from {start_date} to {end_date}\n"

    col_map = {
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Turnover",
        "振幅": "Amplitude",
        "涨跌幅": "ChangePct",
        "涨跌额": "Change",
        "换手率": "TurnoverRate",
    }
    available = {k: v for k, v in col_map.items() if k in df.columns}
    df_out = df[list(available.keys())].rename(columns=available)

    # Round numeric columns for clean CSV
    for col in ("Open", "Close", "High", "Low"):
        if col in df_out.columns:
            df_out[col] = df_out[col].round(2)
    if "Volume" in df_out.columns:
        df_out["Volume"] = df_out["Volume"].astype(int)

    header = (
        f"# Daily OHLCV (前复权) for {symbol.upper()}\n"
        f"# Period: {start_date} → {end_date}\n"
        f"# Source: 东方财富 via AKShare\n"
        f"# Rows: {len(df_out)}\n\n"
    )
    return header + df_out.to_csv(index=False)


def get_money_flow_akshare(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """Individual stock money flow (主力资金流向) from 东方财富 via AKShare.

    Returns data for the requested *start_date* to *end_date* range.
    """
    skip = _skip_non_cn(ticker)
    if skip:
        return skip

    _rate_limit()
    try:
        code, exchange = _ticker_to_ak_symbol(ticker)
        start_norm = start_date.replace("-", "").strip()
        end_norm = end_date.replace("-", "").strip()
        df = ak.stock_individual_fund_flow(stock=code, market=exchange)
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare money flow failed: {e}\n"

    if df is None or df.empty:
        return f"# No money flow data returned for {ticker}\n"

    # AKShare returns date as "YYYY-MM-DD" or "YYYYMMDD" depending on version
    date_col = "日期" if "日期" in df.columns else "trade_date"
    if date_col not in df.columns:
        return f"# Money flow data missing date column for {ticker}\n"

    # Normalise both sides for comparison
    df["_date_norm"] = df[date_col].astype(str).str.replace("-", "")
    mask = (df["_date_norm"] >= start_norm) & (df["_date_norm"] <= end_norm)
    match = df[mask]
    if match.empty:
        return (
            f"# No money flow data found for {ticker} from {start_norm} to {end_norm}. "
            f"Available dates: {sorted(df['_date_norm'].unique())}\n"
        )

    # EastMoney's daykline fund-flow API returns SIGNED NET amounts per size
    # bucket (positive=buy day, negative=sell day) — there are no separate
    # "净流出" columns. Split each net by sign into buy/sell magnitude columns so
    # the output matches the tushare schema that every SMS consumer expects
    # (abs(buy_elg)+abs(sell_elg) == abs(net 超大单) exactly).
    col_map = {
        "日期": "trade_date",
        "主力净流入-净额": "net_amount",
        "主力净流入-净占比": "net_amount_rate",
        "超大单净流入-净额": "buy_elg_amount",
        "超大单净流入-净占比": "buy_elg_amount_rate",
        "大单净流入-净额": "buy_lg_amount",
        "大单净流入-净占比": "buy_lg_amount_rate",
        "中单净流入-净额": "buy_md_amount",
        "中单净流入-净占比": "buy_md_amount_rate",
        "小单净流入-净额": "buy_sm_amount",
        "小单净流入-净占比": "buy_sm_amount_rate",
    }
    available = {k: v for k, v in col_map.items() if k in match.columns}
    df_out = match[list(available.keys())].rename(columns=available)
    df_out = df_out.drop(columns=["_date_norm"], errors="ignore")

    # Signed net → buy/sell magnitude split (values are YUAN, not 万元).
    def _split_buy_sell(series):
        return series.apply(lambda v: v if v > 0 else 0.0), series.apply(
            lambda v: -v if v < 0 else 0.0
        )

    for net_col, buy_col, sell_col in (
        ("buy_elg_amount", "buy_elg_amount", "sell_elg_amount"),
        ("buy_lg_amount", "buy_lg_amount", "sell_lg_amount"),
        ("buy_md_amount", "buy_md_amount", "sell_md_amount"),
        ("buy_sm_amount", "buy_sm_amount", "sell_sm_amount"),
    ):
        if net_col in df_out.columns:
            df_out[buy_col], df_out[sell_col] = _split_buy_sell(df_out[net_col])

    header = (
        f"# Money Flow (主力资金流向) for {ticker.upper()} on {end_norm}\n"
        f"# Source: 东方财富 via AKShare\n"
        f"# Columns: net_amount=主力净流入(元, signed), buy_elg/sell_elg=超大单买/卖, "
        f"buy_lg/sell_lg=大单买/卖, buy_md/sell_md=中单买/卖, buy_sm/sell_sm=小单买/卖\n"
        f"# net_amount_rate: net inflow as % of total turnover\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + df_out.to_csv(index=False)


# ---------------------------------------------------------------------------
# Industry / Board data (东方财富行业板块)
# ---------------------------------------------------------------------------


def get_industry_board_list_akshare() -> str:
    """A-share industry board list from 东方财富 via AKShare.

    Returns all industry boards with codes (e.g. 银行 BK1027).
    No API key required.
    """
    _rate_limit()
    try:
        df = ak.stock_board_industry_name_em()
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare industry board list failed: {e}\n"

    if df is None or df.empty:
        return "# No industry boards found\n"

    header = (
        f"# A-Share Industry Board List\n"
        f"# Source: 东方财富 via AKShare\n"
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Total: {len(df)} industries\n\n"
    )
    return header + df.to_csv(index=False)


def get_industry_board_daily_akshare(
    industry_name: Annotated[str, "Industry name in Chinese, e.g. 银行, 白酒"],
    start_date: Annotated[str, "Start date in yyyymmdd format, e.g. 20250601"],
    end_date: Annotated[str, "End date in yyyymmdd format, e.g. 20250604"],
) -> str:
    """Daily OHLCV history for an A-share industry board from 东方财富 via AKShare.

    Returns daily K-line data including 涨跌幅 (percent change).
    No API key required.
    """
    _rate_limit()
    try:
        df = ak.stock_board_industry_hist_em(
            symbol=str(industry_name),
            start_date=str(start_date),
            end_date=str(end_date),
            period="日k",
            adjust="",
        )
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare industry board daily failed: {e}\n"

    if df is None or df.empty:
        return f"# No industry board history found for '{industry_name}' from {start_date} to {end_date}\n"

    # Rename Chinese columns to English for clarity
    col_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "换手率": "turnover_rate",
    }
    available = {k: v for k, v in col_map.items() if k in df.columns}
    df_out = df[list(available.keys())].rename(columns=available)

    header = (
        f"# Industry Board Daily OHLCV — {industry_name}\n"
        f"# Period: {start_date} → {end_date}\n"
        f"# Source: 东方财富 via AKShare\n"
        f"# Rows: {len(df_out)}\n\n"
    )
    return header + df_out.to_csv(index=False)


def get_industry_board_cons_akshare(
    industry_name: Annotated[str, "Industry name in Chinese, e.g. 银行, 白酒"],
) -> str:
    """Constituent stocks of an A-share industry board from 东方财富 via AKShare.

    Returns stock list with code, name, and market cap.
    No API key required.
    """
    _rate_limit()
    try:
        df = ak.stock_board_industry_cons_em(symbol=str(industry_name))
    except Exception as e:
        return f"# SKIP_VENDOR: AKShare industry constituents failed: {e}\n"

    if df is None or df.empty:
        return f"# No constituents found for industry '{industry_name}'\n"

    # Columns vary by AKShare version; try to map common ones
    col_map = {
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "pct_change",
        "总市值": "total_mv",
        "流通市值": "float_mv",
        "市盈率-动态": "pe",
        "市净率": "pb",
    }
    available = {k: v for k, v in col_map.items() if k in df.columns}
    df_out = df[list(available.keys())].rename(columns=available)

    header = (
        f"# Industry Board Constituents — {industry_name}\n"
        f"# Source: 东方财富 via AKShare\n"
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Total: {len(df_out)} stocks\n\n"
    )
    return header + df_out.to_csv(index=False)
