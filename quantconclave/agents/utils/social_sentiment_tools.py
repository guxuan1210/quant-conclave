"""LangChain tool wrappers for social sentiment data from CN platforms.

These @tool-decorated functions expose Xueqiu and Guba sentiment data
as callable tools that the Sentiment Analyst LLM can invoke.
Each delegates to route_to_vendor() for vendor routing.
"""

from langchain_core.tools import tool
from typing import Annotated
from quantconclave.dataflows.interface import route_to_vendor


@tool
def get_xueqiu_sentiment(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Retrieve 雪球 (Xueqiu) investor community sentiment data for a given stock.
    Shows discussion heat, bullish/bearish ratio, and hot posts from China's
    largest investment community. Users on Xueqiu are more professional/semi-professional
    compared to Guba retail investors.

    Cross-reference: Xueqiu bullish + Guba panic = institutions leading narrative
    while retail panics (potential accumulation signal).

    Data source: 雪球 (xueqiu.com). Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')

    Returns:
        str: A formatted report of Xueqiu sentiment data
    """
    return route_to_vendor("get_xueqiu_sentiment", ticker)


@tool
def get_guba_sentiment(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Retrieve 东方财富股吧 (Eastmoney Guba) retail investor forum sentiment.
    Shows post volume, bullish/bearish ratio, and hot discussion threads from
    the stock-specific forum. Guba users are predominantly retail investors --
    this is the most direct window into retail sentiment.

    Cross-reference: Guba extreme bullishness (>90%) + declining price =
    retail bag-holding while institutions distribute (主力出货).

    Data source: 东方财富股吧 (guba.eastmoney.com). Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')

    Returns:
        str: A formatted report of Guba forum sentiment
    """
    return route_to_vendor("get_guba_sentiment", ticker)
