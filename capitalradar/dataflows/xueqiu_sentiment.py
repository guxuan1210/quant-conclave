"""Xueqiu (雪球) sentiment data vendor for Chinese A-share market.

Provides retail/professional investor sentiment from China's largest
investment community. Each post carries a user-labeled bullish/bearish
sentiment tag and user verification level.

Two-tier fallback:
  Tier 1: Xueqiu public search API (no auth needed)
  Tier 2: Autocli Chrome scraping (requires logged-in Chrome session)
"""

import json
import re
import time
import urllib.parse
import urllib.request

from .autocli_utils import fetch_via_chrome, is_chrome_available, safe_autocli_fetch
from .config import get_config


def _ticker_to_xq_symbol(ticker: str) -> str:
    """Convert ticker to Xueqiu symbol format: SH600519 or SZ000001."""
    ticker = ticker.strip().upper()
    for suffix in (".SS", ".SH"):
        if ticker.endswith(suffix):
            return "SH" + ticker.split(".")[0]
    for suffix in (".SZ", ".BJ"):
        if ticker.endswith(suffix):
            return "SZ" + ticker.split(".")[0]
    if ticker.isdigit() and len(ticker) == 6:
        if ticker.startswith(("6", "9")):
            return "SH" + ticker
        else:
            return "SZ" + ticker
    return ticker


def _is_cn_ticker(ticker: str) -> bool:
    ticker = ticker.strip().upper()
    if any(ticker.endswith(s) for s in (".SH", ".SZ", ".BJ", ".SS")):
        return True
    if ticker.isdigit() and len(ticker) == 6:
        return True
    return False


def _fetch_xueqiu_api(symbol: str, max_posts: int) -> str:
    """Tier 1: Fetch sentiment from Xueqiu public search API."""
    try:
        # Step 1: search for stock to get internal ID
        search_url = (
            "https://xueqiu.com/stock/search.json?"
            + urllib.parse.urlencode({"code": symbol, "size": "1"})
        )
        search_req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        proxy_handler = urllib.request.ProxyHandler({})
        proxy_opener = urllib.request.build_opener(proxy_handler)
        search_resp = proxy_opener.open(search_req, timeout=15)
        search_data = json.loads(search_resp.read().decode("utf-8"))

        stocks = search_data.get("stocks", [])
        if not stocks:
            return ""

        stock_id = stocks[0].get("code", "")
        stock_name = stocks[0].get("name", "")

        # Step 2: get statuses (posts) for this stock
        status_url = (
            "https://xueqiu.com/statuses/search.json?"
            + urllib.parse.urlencode({"symbol": stock_id, "count": str(max_posts), "page": "1"})
        )
        status_req = urllib.request.Request(
            status_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": f"https://xueqiu.com/S/{symbol}",
            },
        )
        status_resp = proxy_opener.open(status_req, timeout=15)
        status_data = json.loads(status_resp.read().decode("utf-8"))

        posts = status_data.get("list", [])
        if not posts:
            return f"## 雪球情绪数据 — {symbol}\n暂无讨论数据\n"

        # Count bullish/bearish
        bullish = 0
        bearish = 0
        output = f"## 雪球情绪数据 — {symbol} ({stock_name})\n"
        output += f"**帖子数**: {len(posts)}\n"

        for p in posts[:max_posts]:
            text = p.get("text", "") or p.get("description", "")
            text = re.sub(r"<[^>]+>", "", str(text))
            if len(text) > 200:
                text = text[:200] + "..."

            user = p.get("user", {})
            username = user.get("screen_name", "unknown")
            verified = "V" if user.get("verified", False) else ""

            # Check for attitude flag
            attitude = p.get("attitude", "")
            if attitude == "1" or "看多" in str(text) or "买入" in str(text):
                bullish += 1
                tag = "[Bullish]"
            elif attitude == "-1" or "看空" in str(text) or "卖出" in str(text):
                bearish += 1
                tag = "[Bearish]"
            else:
                tag = ""

            like_count = p.get("like_count", 0) or p.get("reply_count", 0)
            output += f"**@{username}**{f' [{verified}]' if verified else ''}: {text} {tag}"
            if like_count:
                output += f" [赞:{like_count}]"
            output += "\n"

        total_sentiment = bullish + bearish
        if total_sentiment > 0:
            bull_pct = round(100 * bullish / total_sentiment)
            bear_pct = round(100 * bearish / total_sentiment)
            output += f"\n**多空比**: {bull_pct}% 看多 / {bear_pct}% 看空\n"
        else:
            output += "\n**多空比**: 无法判定（帖子未标注多空方向）\n"

        # Discussion heat level
        if len(posts) >= 15:
            output += "**讨论热度**: HIGH\n"
        elif len(posts) >= 5:
            output += "**讨论热度**: MEDIUM\n"
        else:
            output += "**讨论热度**: LOW\n"

        return output + "\n"

    except Exception:
        return ""


def _fetch_xueqiu_autocli(symbol: str) -> str:
    """Tier 2: Fallback scrape via Chrome session."""
    if not is_chrome_available():
        return ""

    xq_url = f"https://xueqiu.com/S/{symbol}"
    selectors = {
        "posts": ".status-list .status-item",
        "follower_count": ".stock-info .follow-count",
    }
    result = fetch_via_chrome(xq_url, selectors)
    if not result or not result.get("posts"):
        return ""

    posts_text = result["posts"]
    follower = result.get("follower_count", "")
    lines = posts_text.split("|||")[:20]

    output = f"## 雪球情绪数据 — {symbol} (via Chrome)\n"
    if follower:
        output += f"**关注者**: {follower}\n"
    output += f"**帖子数**: {len(lines)}\n"
    output += "### 热门讨论\n"
    for i, line in enumerate(lines, 1):
        short = line.strip()[:300]
        if short:
            output += f"{i}. {short}\n"
    return output + "\n"


def get_xueqiu_sentiment(ticker: str) -> str:
    """Retrieve Xueqiu sentiment data for a Chinese A-share stock.

    Args:
        ticker: Ticker in yfinance (.SS/.SZ), tushare (.SH/.SZ), or plain 6-digit format.

    Returns:
        Formatted markdown string with sentiment data, or empty string on total failure.
    """
    if not _is_cn_ticker(ticker):
        return f"# SKIP_VENDOR: Xueqiu only supports CN A-share tickers, not '{ticker}'"

    config = get_config()
    max_posts = config.get("autocli", {}).get("xueqiu_max_posts", 20)
    symbol = _ticker_to_xq_symbol(ticker)

    # Tier 1: Public API
    result = _fetch_xueqiu_api(symbol, max_posts)
    if result:
        return result

    # Tier 2: Autocli Chrome scraping
    result = safe_autocli_fetch(_fetch_xueqiu_autocli, symbol)
    if result:
        return result

    # Tier 3: Unavailable
    return f"<unavailable — Xueqiu fetch failed for {ticker}>"
