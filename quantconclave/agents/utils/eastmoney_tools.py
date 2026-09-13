"""East Money (妙想/MX) API tools for QuantConclave.

Wraps the MX natural-language data engine via the shared client
(:mod:`quantconclave.dataflows.mx_client`). These tools give analysts REAL-TIME
主力资金 (DDX/DDY/DDZ), 暗盘/大宗交易, live quotes and fundamentals directly
from 东方财富 — complementing the delayed tushare EOD tools.

All tools are best-effort: on missing MX_APIKEY or API failure they return an
error string (never raise), so an analyst can fall back to tushare/akshare.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from quantconclave.dataflows import mx_client

logger = logging.getLogger(__name__)


def _cn_ticker(ticker: str) -> str:
    """Normalize a bare 6-digit CN code to ``000001.SZ`` / ``600519.SH`` form."""
    if ticker.isdigit() and len(ticker) == 6:
        return ticker + (".SH" if ticker[0] in "69" else ".SZ")
    return ticker


@tool
def get_eastmoney_money_flow(ticker: str) -> str:
    """Get REAL-TIME institutional capital flow from East Money (主力资金流向).

    Returns today's (or latest trading day's) snapshot with order-size split
    (超大单/大单/中单/小单), 主力净流入, and the DDX/DDY/DDZ technical flow
    indicators (including 3/5/10-day rolling DDX/DDY). Chinese A-share stocks.
    Cross-references the delayed tushare `get_money_flow` trend with today's
    live signal — use both for accumulation vs distribution judgement.
    """
    return mx_client.query_text(f"{_cn_ticker(ticker)} 主力资金流向")


@tool
def get_eastmoney_quote(ticker: str) -> str:
    """Get REAL-TIME quote data from East Money (最新行情).

    Live price, 涨跌幅, 量比, 换手率, valuation and technical levels
    (支撑位/压力位) for a Chinese A-share stock. Use when you need today's
    live price instead of the last daily close.
    """
    return mx_client.query_text(f"{_cn_ticker(ticker)} 实时行情 最新价 涨跌幅 量比 换手率 市盈率")


@tool
def get_eastmoney_fundamentals(ticker: str) -> str:
    """Get fundamental data from East Money (财务数据).

    Financial statements / key ratios (revenue, profit, PE/PB, margins, etc.)
    from 东方财富's authoritative database for a Chinese A-share stock.
    """
    return mx_client.query_text(f"{_cn_ticker(ticker)} 财务数据")


@tool
def get_eastmoney_data(query: str) -> str:
    """Query East Money financial data by natural language (妙想数据引擎).

    Covers ALL financial data types — hot concepts (热点题材), hot stocks,
    sector rankings, index data, fund flow, 大宗交易/暗盘, and more. Same
    authoritative database as 东方财富. Use when other tools can't answer the
    user's question or the question is not per-ticker (e.g. "今日主力资金净流入
    前十的股票", "沪深300今日大宗交易").
    """
    return mx_client.query_text(query)


@tool
def get_eastmoney_block_trades(ticker: str) -> str:
    """Get 暗盘/大宗交易 (off-exchange block trades) detail from East Money.

    Lists individual block trades with 成交价, 折/溢价率, 成交量/金额, and the
    BUYING/SELLING desks (机构专用 seats flagged). Signals:
    - 折价 + 机构专用买入 = 机构低位吸筹 (bullish)
    - 溢价 + 机构卖出 = 拉高出货 (bearish)
    - 连续多日同一营业部买入 = 主力建仓
    Chinese A-share stocks. Combine with `get_eastmoney_money_flow` for the
    full 主力 picture.
    """
    return mx_client.query_text(f"{_cn_ticker(ticker)} 大宗交易明细")
