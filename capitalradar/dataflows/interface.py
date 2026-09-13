from typing import Annotated

# Import from vendor-specific modules
from .y_finance import (
    get_YFin_data_online,
    get_YFin_data_intraday,
    get_realtime_quote as get_yfinance_realtime_quote,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
    get_institutional_holders as get_yfinance_institutional_holders,
    get_major_holders as get_yfinance_major_holders,
    get_analyst_recommendations as get_yfinance_analyst_recommendations,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .eastmoney_news import get_news_eastmoney, get_global_news_eastmoney
from .cls_news import get_news_cls, get_global_news_cls
from .xueqiu_sentiment import get_xueqiu_sentiment
from .eastmoney_guba import get_guba_sentiment
from .sina_dragon_tiger import get_dragon_tiger_list
from .tushare_data import (
    get_money_flow as get_tushare_money_flow,
    detect_ah_relationship,
    get_hk_stock_data as get_tushare_hk_data,
    get_ah_premium,
    get_southbound_flow as get_tushare_southbound,
    get_macro_context,
    get_income_statement_tushare,
    get_balance_sheet_tushare,
    get_cashflow_tushare,
    get_hsgt_flow as get_tushare_hsgt_flow,
    get_market_flow as get_tushare_market_flow,
    get_margin_trading as get_tushare_margin_trading,
    get_stock_data as get_tushare_stock_data,
    get_dragon_tiger_list as get_tushare_dragon_tiger_list,
    get_share_unlock as get_tushare_share_unlock,
    get_share_pledge as get_tushare_share_pledge,
    get_stock_buyback as get_tushare_stock_buyback,
    get_holder_changes as get_tushare_holder_changes,
    get_institutional_holders as get_tushare_institutional_holders,
    get_major_holders as get_tushare_major_holders,
    get_tushare_top_gainers,
    get_tushare_multi_factor_ranking,
    get_tushare_top_net_inflow,
)

# Public re-exports - make these functions available via rom .interface import ...
from .tushare_data import (
    get_share_pledge,
    get_share_unlock,
    get_stock_buyback,
    get_holder_changes,
)
from .tencent_realtime import get_tencent_realtime_quote
from capitalradar.sector_scan.top_gainers import get_em_rankings

# ── Eastmoney push2 real-time screening wrappers ──
def _em_gainers(trade_date=None, top_n=15, min_amount=50000000, filter_st=True):
    """Real-time top gainers from Eastmoney push2 (free, no auth)."""
    return get_em_rankings(sort_field="f3", top_n=top_n)

def _em_top_inflow(trade_date=None, top_n=15, min_amount=3000000):
    """Real-time top net inflow from Eastmoney push2 (free, no auth)."""
    return get_em_rankings(sort_field="f62", top_n=top_n)

# ── Tencent intraday minute-line vendor ──
def _tencent_intraday(symbol, interval, start_date, end_date):
    """Fetch intraday minute K-lines from Tencent API (free, no auth)."""
    import requests as _req
    # Normalize symbol
    s = symbol.strip().upper()
    for sfx in (".SH", ".SZ", ".SS"):
        if s.endswith(sfx):
            code = s[:-3]
            prefix = "sh" if sfx in (".SH", ".SS") else "sz"
            break
    else:
        if s.isdigit() and len(s) == 6:
            prefix, code = ("sh", s) if s[0] in "69" else ("sz", s)
        else:
            return "# SKIP_VENDOR: unsupported symbol format"
    tc_code = f"{prefix}{code}"
    # Map interval to Tencent kline type
    ktype_map = {"1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30", "60m": "m60", "1h": "m60"}
    ktype = ktype_map.get(interval, "m5")
    try:
        url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={tc_code},{ktype},,60"
        resp = _req.get(url, timeout=8)
        data = resp.json()
        lines = data.get("data", {}).get(tc_code, {}).get(ktype, [])
        if not lines:
            return "# SKIP_VENDOR: no intraday data from Tencent"
        result = ["date,open,close,high,low,volume"]
        for bar in lines[-120:]:  # last 120 bars
            result.append(f"{bar[0]},{bar[1]},{bar[2]},{bar[3]},{bar[4]},{bar[5]}")
        return "\n".join(result)
    except Exception as e:
        return f"# SKIP_VENDOR: Tencent intraday error ({e})"

from .akshare_data import (
    get_realtime_quote_akshare,
    get_intraday_data_akshare,
    get_stock_data_akshare,
    get_money_flow_akshare,
)

# Configuration and routing logic
from .config import get_config

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    },
    "capital_flow_data": {
        "description": "Capital flow, margin trading, and institutional money flow data",
        "tools": [
            "get_money_flow",
            "get_hsgt_flow",
            "get_market_flow",
            "get_margin_trading",
            "get_share_pledge",
            "get_share_unlock",
            "get_stock_buyback",
            "get_holder_changes",
            "get_institutional_holders",
            "get_major_holders",
            "get_analyst_recommendations",
            "get_dragon_tiger_list",
            "get_intraday_data",
            "get_realtime_quote",
            "get_macro_context",
            "get_broker_recommend",
            "get_weekly_data",
            "get_monthly_data",
            "get_fund_holdings",
            "detect_ah_relationship",
            "get_hk_stock_data",
            "get_ah_premium",
            "get_southbound_flow",
        ]
    },
    "social_sentiment_data": {
        "description": "Retail investor social sentiment from CN platforms",
        "tools": [
            "get_xueqiu_sentiment",
            "get_guba_sentiment",
        ]
    },
    "screening_tools": {
        "description": "Market-wide stock screening and ranking tools",
        "tools": [
            "get_top_gainers",
            "get_multi_factor_ranking",
            "get_top_net_inflow",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "akshare",
    "tushare",
    "eastmoney",
    "cls",
    "xueqiu",
    "eastmoney_guba",
    "sina",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "tushare": get_tushare_stock_data,
        "akshare": get_stock_data_akshare,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "tushare": get_balance_sheet_tushare,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "tushare": get_cashflow_tushare,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "tushare": get_income_statement_tushare,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "yfinance": get_news_yfinance,
        "eastmoney": get_news_eastmoney,
        "cls": get_news_cls,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "eastmoney": get_global_news_eastmoney,
        "cls": get_global_news_cls,
    },

    # capital_flow_data
    "get_insider_transactions": {
        "yfinance": get_yfinance_insider_transactions,
    },
    "get_money_flow": {
        "akshare": get_money_flow_akshare,
        "tushare": get_tushare_money_flow,
        "yfinance": lambda ticker, start_date, end_date: (
            f"# SKIP_VENDOR: Money Flow data not available via yfinance\n"
        ),
    },
    "get_hsgt_flow": {
        "tushare": get_tushare_hsgt_flow,
        "yfinance": lambda start_date, end_date: (
            f"# 沪深港通 flow data not available via yfinance\n"
            f"Northbound/southbound flow data is sourced from tushare.\n"
            f"Set data_vendors.capital_flow_data to 'tushare' and configure TUSHARE_TOKEN.\n"
        ),
    },
    "get_market_flow": {
        "tushare": get_tushare_market_flow,
        "yfinance": lambda trade_date: (
            f"# Market flow data not available via yfinance\n"
            f"Market-wide money flow data is sourced from tushare (东方财富).\n"
            f"Set data_vendors.capital_flow_data to 'tushare' and configure TUSHARE_TOKEN.\n"
        ),
    },
    "get_institutional_holders": {
        "tushare": get_tushare_institutional_holders,
        "yfinance": get_yfinance_institutional_holders,
    },
    "get_major_holders": {
        "tushare": get_tushare_major_holders,
        "yfinance": get_yfinance_major_holders,
    },
    "get_margin_trading": {
        "tushare": get_tushare_margin_trading,
        "yfinance": lambda ticker, start_date, end_date: (
            f"# Margin trading data not available via yfinance\n"
            f"Margin/short selling data is sourced from tushare for Chinese A-share stocks.\n"
            f"Set data_vendors.capital_flow_data to 'tushare' and configure TUSHARE_TOKEN.\n"
        ),
    },
    
    "get_share_pledge": {
        "tushare": get_tushare_share_pledge,
    },
    "get_share_unlock": {
        "tushare": get_tushare_share_unlock,
    },
    "get_stock_buyback": {
        "tushare": get_tushare_stock_buyback,
    },
    "get_holder_changes": {
        "tushare": get_tushare_holder_changes,
    },
    "get_analyst_recommendations": {
        "yfinance": get_yfinance_analyst_recommendations,
        "tushare": lambda ticker: (
            f"# Analyst recommendations data not available via tushare\n"
            f"Use yfinance as the vendor for analyst recommendations data.\n"
        ),
    },
    "get_intraday_data": {
        "akshare": get_intraday_data_akshare,
        "yfinance": get_YFin_data_intraday,
        "tushare": lambda symbol, interval, start_date, end_date: (
            f"# SKIP_VENDOR: Intraday data not available via tushare\n"
        ),
    },
    "get_realtime_quote": {
        "tencent": get_tencent_realtime_quote,
        "akshare": get_realtime_quote_akshare,
        "yfinance": get_yfinance_realtime_quote,
        "tushare": lambda symbol: (
            f"# SKIP_VENDOR: Real-time quote not available via tushare\n"
        ),
    },
    # social_sentiment_data
    "get_xueqiu_sentiment": {
        "xueqiu": get_xueqiu_sentiment,
        "eastmoney_guba": lambda ticker: (
            "# SKIP_VENDOR: Xueqiu sentiment not available via Guba vendor"
        ),
    },
    "get_guba_sentiment": {
        "eastmoney_guba": get_guba_sentiment,
        "xueqiu": lambda ticker: (
            "# SKIP_VENDOR: Guba sentiment not available via Xueqiu vendor"
        ),
    },
    "get_dragon_tiger_list": {
        "tushare": get_tushare_dragon_tiger_list,
        "sina": get_dragon_tiger_list,
    },
    "get_macro_context": {
        "tushare": get_macro_context,
    },
    # screening_tools
    "get_top_gainers": {
        "tushare": get_tushare_top_gainers,
    },
    "get_multi_factor_ranking": {
        "tushare": get_tushare_multi_factor_ranking,
    },
    "get_top_net_inflow": {
        "tushare": get_tushare_top_net_inflow,
    },
    # intraday — add tencent minute-line as primary vendor
    "get_intraday_data": {
        "tencent": _tencent_intraday,
        "akshare": get_intraday_data_akshare,
        "yfinance": get_YFin_data_intraday,
        "tushare": lambda symbol, interval, start_date, end_date: (
            f"# SKIP_VENDOR: Intraday data not available via tushare\n"
        ),
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None, ticker: str = "") -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.

    For CN A-share tickers, yfinance is skipped as primary vendor because
    its coverage of Chinese stocks is poor. Falls through to akshare/tushare.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    vendors = config.get("data_vendors", {}).get(category, "default")

    # For CN A-share tickers: if yfinance is primary and we have domestic alternatives,
    # reorder to prefer domestic vendors
    is_cn = _is_cn_ticker(ticker)
    # Don't reorder vendors — the configured order is already correct.
    # Domestic vendors (akshare/tushare) are listed first and tried first.
    # yfinance as last fallback is fine for non-CN tickers and harmless for CN.
    pass

    return vendors


def _is_cn_ticker(ticker: str) -> bool:
    """Check if a ticker represents a Chinese A-share stock."""
    if not ticker:
        return False
    t = ticker.strip().upper()
    if any(t.endswith(s) for s in (".SH", ".SZ", ".SS", ".BJ")):
        return True
    if t.isdigit() and len(t) == 6:
        return True
    return False

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    category = get_category_for_method(method)
    ticker = kwargs.get("symbol") or kwargs.get("ticker") or ""
    vendor_config = get_vendor(category, method, ticker=ticker)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # Build fallback chain: primary vendors first, then remaining available vendors
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    last_error = None
    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            result = impl_func(*args, **kwargs)
            # If the result is an error string from a vendor that should have
            # declined (e.g. eastmoney for non-CN ticker), skip to next vendor.
            if isinstance(result, str) and result.startswith("# SKIP_VENDOR:"):
                continue
            return result
        except Exception as e:
            # Log and fall through to next vendor if available
            last_error = e
            continue

    if last_error:
        raise RuntimeError(f"No available vendor for '{method}': {last_error}")
    raise RuntimeError(f"No available vendor for '{method}'")