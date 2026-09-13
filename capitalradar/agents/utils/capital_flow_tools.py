"""LangChain tool wrappers for capital flow / money flow data.

These @tool-decorated functions are the ones the Capital Flow Analyst LLM calls.
Each delegates to route_to_vendor() which dispatches to the configured vendor
(tushare for A-share money flow, yfinance for institutional holdings).
"""

from langchain_core.tools import tool
from typing import Annotated
from capitalradar.dataflows.interface import route_to_vendor
from capitalradar.agents.utils.tool_sanitizer import sanitize_tool_args


@tool
def get_money_flow(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format (at least 60 days / 2 months before end_date for trend analysis)"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format (typically the analysis target date)"],
) -> str:
    """
    Retrieve individual stock money flow data (主力资金流向) for a given company over a date RANGE.
    Shows net capital inflow/outflow by order size: super-large (超大单), large (大单),
    medium (中单), and small (小单) orders.

    COLUMN SEMANTICS (important — read before citing 主力):
    - The CSV's `net_amount` is the vendor's raw 净流入额. Its meaning is NOT
      well-defined: tushare moneyflow defines it as 净流入额 = 超大单+大单+中单+小单,
      but the four bucket nets ALWAYS sum to ~0 (every trade is simultaneously a buy
      in one bucket and a sell in another), so raw net_amount is not a trustworthy
      主力 signal. 主力净额 = (buy_elg_amount - sell_elg_amount) +
      (buy_lg_amount - sell_lg_amount). Use buy/sell super-large + large columns
      to judge institutional direction, not the raw net_amount column.
    - Positive bucket net means buying (bullish); negative means selling.

    IMPORTANT: You MUST pass a start_date that is at least 60 days (2 months) before
    end_date to capture the full accumulation/distribution trend. A single day is
    insufficient — institutions accumulate/distribute over weeks, not days.

    Data source: 东方财富 via tushare or AKShare. Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')
        start_date (str): Start date in YYYYMMDD or YYYY-MM-DD format
        end_date (str): End date in YYYYMMDD or YYYY-MM-DD format

    Returns:
        str: A formatted report of money flow data with CSV content
    """
    a = sanitize_tool_args("get_money_flow", {"ticker": ticker, "start_date": start_date, "end_date": end_date})
    return route_to_vendor("get_money_flow", a["ticker"], a["start_date"], a["end_date"])


@tool
def get_hsgt_flow(
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """
    Retrieve 沪深港通 (Northbound/Southbound) capital flow data.
    Shows daily northbound (沪股通+深股通, foreign money into A-shares) and
    southbound (港股通, mainland money into HK) fund flows in millions of RMB.
    Northbound inflow is a bullish signal for A-share markets.

    Data source: tushare. Best for Chinese A-share stocks.

    Args:
        start_date (str): Start date in YYYYMMDD or YYYY-MM-DD format
        end_date (str): End date in YYYYMMDD or YYYY-MM-DD format

    Returns:
        str: A formatted report of northbound/southbound flow data with CSV content
    """
    a = sanitize_tool_args("get_hsgt_flow", {"start_date": start_date, "end_date": end_date})
    return route_to_vendor("get_hsgt_flow", a["start_date"], a["end_date"])


@tool
def get_market_flow(
    trade_date: Annotated[str, "trade date in YYYYMMDD or YYYY-MM-DD format"],
) -> str:
    """
    Retrieve market-wide money flow data (大盘资金流向) for a given trading day.
    Shows overall capital flow for the entire market, useful for understanding
    the macro money flow environment.

    Data source: 东方财富 via tushare.

    Args:
        trade_date (str): Trade date in YYYYMMDD or YYYY-MM-DD format

    Returns:
        str: A formatted report of market-wide money flow data with CSV content
    """
    return route_to_vendor("get_market_flow", trade_date)


@tool
def get_institutional_holders(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Retrieve institutional holders data for a given company.
    Shows which banks, funds, and institutional investors hold the stock,
    their share counts, percentage held, and market value.
    Increasing institutional ownership is a bullish signal.

    Data source: yfinance. Best for US-listed stocks.

    Args:
        ticker (str): Ticker symbol of the company

    Returns:
        str: A formatted report of institutional holders with CSV content
    """
    return route_to_vendor("get_institutional_holders", ticker)


@tool
def get_major_holders(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Retrieve major holders percentage breakdown for a given company.
    Shows the percentage of shares held by institutions, insiders, and the public.
    High institutional ownership with low insider selling suggests confidence.

    Data source: yfinance. Best for US-listed stocks.

    Args:
        ticker (str): Ticker symbol of the company

    Returns:
        str: A formatted report of major holders breakdown with CSV content
    """
    return route_to_vendor("get_major_holders", ticker)


@tool
def get_margin_trading(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD or YYYY-MM-DD format (at least 60 days / 2 months before end_date)"],
    end_date: Annotated[str, "end date in YYYYMMDD or YYYY-MM-DD format (typically the analysis target date)"],
) -> str:
    """
    Retrieve 融资融券 (margin trading & short selling) data for a given stock over a date RANGE.
    Shows: margin balance (融资余额, leveraged long positions), short-selling balance
    (融券余额, borrowed shares sold short), margin buying amount (融资买入额),
    and short-selling volume (融券余量).

    Key interpretation:
    - High/increasing margin balance = bullish (investors borrowing to buy more)
    - High/increasing short-selling volume = bearish (investors borrowing shares to short)
    - Declining margin + rising short-selling = strong bearish signal
    - Declining short-selling + rising margin = strong bullish signal

    IMPORTANT: You MUST pass a start_date that is at least 60 days (2 months) before
    end_date to detect the accumulation/distribution TREND over time.

    Data source: tushare. Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')
        start_date (str): Start date in YYYYMMDD or YYYY-MM-DD format
        end_date (str): End date in YYYYMMDD or YYYY-MM-DD format

    Returns:
        str: A formatted report of margin trading data with CSV content
    """
    a = sanitize_tool_args("get_margin_trading", {"ticker": ticker, "start_date": start_date, "end_date": end_date})
    return route_to_vendor("get_margin_trading", a["ticker"], a["start_date"], a["end_date"])


@tool
def get_analyst_recommendations(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Retrieve analyst recommendations and recent rating changes for a given company.
    Shows historical analyst ratings (buy/hold/sell) and recent upgrades/downgrades.
    Analyst downgrades can trigger institutional selling; upgrades attract buying.

    Data source: yfinance. Best for US-listed stocks.

    Args:
        ticker (str): Ticker symbol of the company

    Returns:
        str: A formatted report of analyst recommendations and rating changes with CSV content
    """
    return route_to_vendor("get_analyst_recommendations", ticker)


@tool
def get_dragon_tiger_list(
    ticker: Annotated[str, "ticker symbol of the company"],
    trade_date: Annotated[str, "trade date in YYYY-MM-DD format"],
) -> str:
    """
    Retrieve 龙虎榜 (Dragon-Tiger List) data for a given stock.
    Shows which institutional and hot-money (游资) trading desks bought or sold
    the stock on days when it made the daily price-move threshold.
    Institutional desk (机构专用) net buying = strong bullish confirmation.
    Hot-money desk net buying without institutional participation = short-term
    speculation, not accumulation.

    Data source: 新浪财经 (Sina Finance). Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')
        trade_date (str): Trade date in YYYY-MM-DD format

    Returns:
        str: A formatted report of Dragon-Tiger List trading desk data
    """
    return route_to_vendor("get_dragon_tiger_list", ticker, trade_date)


@tool
def get_share_pledge(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get 股权质押 (Stock Pledge Statistics) from tushare.

    Shows how many shares are pledged as collateral, the pledge ratio,
    and whether pledges are restricted or unrestricted.
    IMPORTANT: A high pledge ratio (>30%) signals risk — if the stock price
    falls, the controlling shareholder may face margin calls, triggering
    forced selling and further price declines.

    Data source: tushare. Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')

    Returns:
        str: A formatted report of share pledge data with CSV content
    """
    return route_to_vendor("get_share_pledge", ticker)


@tool
def get_share_unlock(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get 限售解禁 (Lockup Expiration / Share Unlock) from tushare.

    Shows upcoming and historical unlocked shares, unlock dates, and holder names.
    Large upcoming unlock events are bearish — newly tradable shares increase supply,
    and insiders often sell after the lockup period ends.
    Use this to assess near-term dilution risk.

    Data source: tushare. Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')

    Returns:
        str: A formatted report of share unlock data with CSV content
    """
    return route_to_vendor("get_share_unlock", ticker)


@tool
def get_stock_buyback(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get 股份回购 (Stock Buyback / Repurchase) from tushare.

    Shows the company's own share repurchase history: announcement date,
    progress status, buyback volume, amount spent, and price range.
    Active buyback programs are generally bullish — the company signals
    that its shares are undervalued and is returning cash to shareholders.
    Completion of buyback = confidence signal.

    Data source: tushare. Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')

    Returns:
        str: A formatted report of stock buyback data with CSV content
    """
    return route_to_vendor("get_stock_buyback", ticker)


@tool
def get_holder_changes(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYY-MM-DD format (beginning of lookback window)"],
    end_date: Annotated[str, "end date in YYYY-MM-DD format (analysis target date)"],
) -> str:
    """Get 股东增减持 (Shareholder Holding Changes) from tushare.

    Shows individual shareholder transactions: who increased/decreased their holdings,
    the volume of shares changed, the ratio change, and average transaction price.
    KEY SIGNALS:
    - Multiple shareholders increasing holdings (IN) = accumulation, bullish
    - Major shareholder decreasing (DE) = possible overvaluation or insider concern
    - Large insider buying near support levels = strong confidence signal
    - Insider selling after a large run-up = distribution, potentially bearish

    Data source: tushare. Best for Chinese A-share stocks.

    Args:
        ticker (str): Ticker symbol of the company (e.g. '600519' or '600519.SH')
        start_date (str): Start date in YYYY-MM-DD format
        end_date (str): End date in YYYY-MM-DD format

    Returns:
        str: A formatted report of shareholder holding changes with CSV content
    """
    return route_to_vendor("get_holder_changes", ticker, start_date, end_date)


@tool
def get_macro_context(
) -> str:
    """
    Retrieve China macroeconomic context: CPI (inflation/purchasing power),
    PMI (manufacturing health - above 50=expansion, below 50=contraction),
    M2 money supply (liquidity - high M2=loose monetary policy favorable for stocks),
    and LPR loan prime rate (borrowing cost - low LPR=stimulus, high LPR=tightening).

    MUST be called FIRST before any other analysis. Macro backdrop determines
    whether capital inflows are genuine accumulation or defensive rotation.
    - Loose monetary policy (falling rates, rising M2) + capital inflow = strong buy signal
    - Tight monetary policy (rising rates) + capital inflow = potentially a trap
    - PMI contraction + capital outflow = sector rotation, not necessarily bearish

    Data source: tushare macro APIs.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_macro_context")


@tool
def get_broker_recommend(
    ticker: Annotated[str, "ticker symbol of the company"],
    month: Annotated[str, "month in YYYYMM format (e.g. 202601)"] = None,
) -> str:
    """
    Get broker gold-stock recommendations (????) for a specific stock.
    Shows which major brokerages recommended this stock each month.

    Signal interpretation:
    - 3+ brokers recommending simultaneously = STRONG institutional consensus
    - Capital inflow + broker consensus = HIGH CONFIDENCE, genuine accumulation
    - Capital inflow + NO broker coverage = potentially speculative/manipulative flow
    - Capital outflow + broker downgrades = confirmed distribution

    MUST cross-reference with get_money_flow to validate capital movements.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_broker_recommend", ticker=ticker, month=month)


@tool
def get_weekly_data(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD format (at least 60 days / 2 months before end_date)"],
    end_date: Annotated[str, "end date in YYYYMMDD format (typically the analysis target date)"],
) -> str:
    """
    Get weekly OHLCV bars (??) for medium-term trend confirmation.

    Weekly timeframe reveals institutional accumulation/distribution patterns
    that daily noise obscures. Daily signals must be confirmed on weekly:
    - Weekly uptrend + daily capital inflow = genuine accumulation
    - Weekly downtrend + daily capital inflow = likely dead cat bounce / distribution trap

    MUST be called alongside get_stock_data (daily) for multi-timeframe analysis.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_weekly_data", ticker=ticker, start_date=start_date, end_date=end_date)


@tool
def get_monthly_data(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in YYYYMMDD format (at least 180 days / 6 months before end_date)"],
    end_date: Annotated[str, "end date in YYYYMMDD format (typically the analysis target date)"],
) -> str:
    """
    Get monthly OHLCV bars (??) for long-term trend identification.

    Monthly bars show the DOMINANT trend. The monthly trend is the tide:
    - Rising monthly trend + weekly pullback + daily inflow = buying opportunity
    - Falling monthly trend + weekly rally + daily inflow = selling opportunity (bounce to sell)

    ALWAYS check the monthly trend before making any directional call.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_monthly_data", ticker=ticker, start_date=start_date, end_date=end_date)


@tool
def get_fund_holdings(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """
    Get mutual fund portfolio holdings (????) for cross-validating capital flow signals.

    This is the most DIRECT evidence of institutional behavior:
    - Shows which funds hold this stock and their position weight
    - High fund concentration + capital inflow = STRONG BULLISH (institutions buying together)
    - Declining fund positions + capital inflow = DISTRIBUTION TRAP (retail buying, institutions selling)
    - No fund coverage + capital inflow = speculative or manipulative flow

    MUST be the FINAL verification step after capital flow and technical analysis.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_fund_holdings", ticker=ticker)


@tool
def detect_ah_relationship(
    ticker: Annotated[str, "A-share or H-share ticker symbol"],
) -> str:
    """
    Detect if a stock has A+H dual listing. MUST call FIRST for any A-share analysis.

    Returns the A-share and H-share codes if dual-listed, or confirms single-listing.
    If dual-listed, you MUST then cross-check:
    - get_ah_premium: valuation gap between A and H shares
    - get_hk_stock_data: H-share price/volume trends
    - get_southbound_flow: mainland capital flowing into the H-share
    - Compare with get_hsgt_flow (north-bound) to detect cross-market divergence
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("detect_ah_relationship", ticker=ticker)


@tool
def get_hk_stock_data(
    ticker: Annotated[str, "H-share ticker (e.g. 03968.HK) or A-share ticker (auto-resolves)"],
    start_date: Annotated[str, "start date in YYYYMMDD format (at least 60 days before end_date)"],
    end_date: Annotated[str, "end date in YYYYMMDD format"],
) -> str:
    """
    Get Hong Kong stock daily OHLCV for A+H comparison.

    H-share prices often lead A-share by 1-3 days because HK institutional
    investors price more efficiently. If H-share breaks out first, A-share
    is likely to follow. If H-share is declining while A-share rallies,
    the A-share move may be speculative and unsustainable.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_hk_stock_data", ticker=ticker, start_date=start_date, end_date=end_date)


@tool
def get_ah_premium(
    ticker: Annotated[str, "A-share ticker symbol"],
) -> str:
    """
    Calculate A-H premium/discount ratio. Shows whether A-share is overvalued
    relative to its H-share counterpart.

    - Premium >30%: A-share expensive vs H-share. Risk of mean reversion.
    - Premium 5-25%: Normal A-share premium range.
    - Premium <5% or negative: A-share cheap vs H-share. Accumulation opportunity.

    MUST call after detect_ah_relationship confirms dual listing.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_ah_premium", ticker=ticker)


@tool
def get_southbound_flow(
    ticker: Annotated[str, "H-share or A-share ticker (auto-resolves to H-share)"],
    start_date: Annotated[str, "start date in YYYYMMDD format"],
    end_date: Annotated[str, "end date in YYYYMMDD format"],
) -> str:
    """
    Get south-bound capital flow (????) - mainland money flowing into HK stocks.

    CRITICAL: Compare with get_hsgt_flow (north-bound, foreign -> A-share):
    - Both north AND south buying = genuine institutional interest across markets
    - North buying + South selling = potential A-share speculation/manipulation, BE CAUTIOUS
    - South buying + North selling = institutions prefer H-share valuation over A-share

    This is the definitive test for whether A-share capital inflow is genuine
    or potentially manufactured by speculative players.
    """
    from capitalradar.dataflows.interface import route_to_vendor
    return route_to_vendor("get_southbound_flow", ticker=ticker, start_date=start_date, end_date=end_date)
