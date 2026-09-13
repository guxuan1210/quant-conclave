"""Eastmoney Guba (东方财富股吧) forum sentiment vendor.

Provides retail investor forum sentiment for Chinese A-share stocks.
Each stock has its own forum (股吧) on guba.eastmoney.com where retail
investors discuss price movements, share rumors, and express sentiment.

Two-tier fallback:
  Tier 1: Eastmoney push API (public, JSON endpoint)
  Tier 2: Autocli Chrome scraping (forum HTML page)
"""

import json
import re
import time
import urllib.parse
import urllib.request

from .autocli_utils import fetch_via_chrome, is_chrome_available, safe_autocli_fetch
from .config import get_config


def _ticker_to_code(ticker: str) -> str:
    """Extract plain 6-digit code from any ticker format."""
    ticker = ticker.strip().upper()
    for suffix in (".SS", ".SH", ".SZ", ".BJ"):
        if ticker.endswith(suffix):
            return ticker.split(".")[0]
    return ticker


def _is_cn_ticker(ticker: str) -> bool:
    ticker = ticker.strip().upper()
    if any(ticker.endswith(s) for s in (".SH", ".SZ", ".BJ", ".SS")):
        return True
    if ticker.isdigit() and len(ticker) == 6:
        return True
    return False


def _fetch_guba_api(code: str, max_posts: int) -> str:
    """Scrape Guba HTML page directly (simple, no API key needed)."""
    try:
        url = f"https://guba.eastmoney.com/list,{code}.html"
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp = opener.open(req, timeout=15)
        html = resp.read().decode("gbk", errors="ignore")

        # Extract post titles from HTML links
        titles = re.findall(r'/news,\d+,\d+\.html"[^>]*>([^<]+)</a>', html)
        # Extract read counts
        reads = re.findall(r'<cite>(\d+)</cite>', html)

        if not titles:
            return ""

        output = f"## Guba Sentiment — {code}\n"
        output += f"**Posts**: {len(titles)}\n\n"

        bullish_kw = ["涨", "利好", "涨停", "翻倍", "起飞", "看好", "抄底", "买入", "牛", "突破"]
        bearish_kw = ["跌", "利空", "跌停", "腰斩", "暴跌", "出货", "割肉", "卖出", "熊", "套"]
        bull = sum(1 for t in titles[:max_posts] if any(k in t for k in bullish_kw))
        bear = sum(1 for t in titles[:max_posts] if any(k in t for k in bearish_kw))

        output += "### Recent Posts\n"
        for i, t in enumerate(titles[:max_posts]):
            short = t[:80] + ("..." if len(t) > 80 else "")
            rc = reads[i] if i < len(reads) else "?"
            output += f"- {short} | reads:{rc}\n"

        total = bull + bear
        if total > 0:
            output += f"\n**Sentiment**: {round(100*bull/total)}% bullish vs {round(100*bear/total)}% bearish\n"
        return output

    except Exception:
        return ""

def _fetch_guba_autocli(code: str) -> str:
    """Tier 2: Fallback scrape via Chrome session."""
    if not is_chrome_available():
        return ""

    guba_url = f"https://guba.eastmoney.com/list,{code}.html"
    selectors = {
        "posts": ".articlelist tr",
        "total_count": ".pager .sumpage",
    }
    result = fetch_via_chrome(guba_url, selectors)
    if not result or not result.get("posts"):
        return ""

    posts_text = result["posts"]
    total = result.get("total_count", "")
    lines = posts_text.split("|||")

    output = f"## 股吧情绪 — {code} (via Chrome)\n"
    if total:
        output += f"**总帖数**: {total}\n"
    output += f"**抓取帖数**: {min(len(lines), 20)}\n"
    output += "### 热帖\n"
    count = 0
    for line in lines:
        if count >= 20:
            break
        short = line.strip()[:200]
        if short and "标题" not in short:
            output += f"- {short}\n"
            count += 1
    return output + "\n"


def get_guba_sentiment(ticker: str) -> str:
    """Retrieve Guba forum sentiment for a Chinese A-share stock.

    Args:
        ticker: Ticker in yfinance (.SS/.SZ), tushare (.SH/.SZ), or plain 6-digit format.

    Returns:
        Formatted markdown string with forum sentiment, or empty string on total failure.
    """
    if not _is_cn_ticker(ticker):
        return f"# SKIP_VENDOR: Guba only supports CN A-share tickers, not '{ticker}'"

    config = get_config()
    max_posts = config.get("autocli", {}).get("guba_max_posts", 20)
    code = _ticker_to_code(ticker)

    # Tier 1: Public API
    result = _fetch_guba_api(code, max_posts)
    if result:
        return result

    # Tier 2: Autocli Chrome scraping
    result = safe_autocli_fetch(_fetch_guba_autocli, code)
    if result:
        return result

    # Tier 3: Unavailable
    return f"<unavailable — Guba fetch failed for {ticker}>"
