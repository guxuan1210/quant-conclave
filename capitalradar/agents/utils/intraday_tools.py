"""LangChain tool wrappers for intraday / real-time market data.

These @tool-decorated functions are available to the Capital Flow Analyst and
other agents. Each delegates to route_to_vendor() which dispatches to the
configured vendor (yfinance for intraday OHLCV + real-time quotes).
"""

from langchain_core.tools import tool
from typing import Annotated
from capitalradar.dataflows.interface import route_to_vendor


@tool
def get_intraday_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    interval: Annotated[str, "intraday interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve intraday OHLCV candlestick data for a given ticker and interval.
    Use this to detect intraday accumulation/distribution patterns, VWAP
    deviations, and suspicious order flow not visible on daily candles.

    1m interval goes back ~7 days; 5m/15m/30m/60m intervals go back ~60 days.
    For Chinese A-shares, 5m or 15m is recommended during active trading hours
    (9:30-11:30, 13:00-15:00 CST).

    Args:
        symbol (str): Ticker symbol (e.g. '600519.SH' or 'AAPL')
        interval (str): Bar interval — one of 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format

    Returns:
        str: CSV-formatted OHLCV data with Date, Open, High, Low, Close, Volume columns
    """
    return route_to_vendor("get_intraday_data", symbol=symbol, interval=interval,
                          start_date=start_date, end_date=end_date)


@tool
def get_realtime_quote(
    symbol: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Retrieve a real-time market snapshot for a ticker: current price, bid/ask
    spread, session volume, day range, previous close, and market state
    (open/closed/pre-market/after-hours).

    Use this at the start of analysis to get the live market pulse before
    diving into historical data. During closed hours the marketState field
    will indicate CLOSED, PRE, or POST.

    Args:
        symbol (str): Ticker symbol (e.g. '600519.SH' or 'AAPL')

    Returns:
        str: Formatted text report with current price, bid/ask, volume, day range,
             and market state.
    """
    return route_to_vendor("get_realtime_quote", symbol=symbol)
