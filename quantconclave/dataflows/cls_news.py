"""CLS (财联社) news vendor for Chinese financial market.

财联社 is China's fastest financial news terminal, heavily used by retail and
institutional investors. This vendor provides:

1. Ticker-specific news via CLS search (get_news)
2. Market-wide flash news / telegraph (get_global_news) — the defining CLS feature

No authentication required for public endpoints. Falls back gracefully via
``# SKIP_VENDOR`` when data is unavailable so the vendor chain continues.
"""

from typing import Optional
from datetime import datetime, timedelta
import hashlib
import json
import re
import time
import urllib.parse

from .config import get_config
from .proxy import fetch_json


def _is_cn_ticker(ticker: str) -> bool:
    """Check if a ticker is a Chinese A-share ticker."""
    ticker = ticker.strip().upper()
    if any(ticker.endswith(s) for s in (".SH", ".SZ", ".BJ", ".SS")):
        return True
    if ticker.isdigit() and len(ticker) == 6:
        return True
    return False


def _ticker_to_code(ticker: str) -> str:
    """Extract plain 6-digit code from any ticker format."""
    ticker = ticker.strip().upper()
    for suffix in (".SS", ".SH", ".SZ", ".BJ"):
        if ticker.endswith(suffix):
            return ticker.split(".")[0]
    return ticker


def _ticker_to_keyword(ticker: str) -> str:
    """Build a search-friendly keyword for the ticker.

    For CN tickers, appends the stock name hint so the CLS search API
    returns more relevant results.
    """
    code = _ticker_to_code(ticker)
    return code


def _cls_http_request(url: str, method: str = "GET", body: Optional[dict] = None,
                      timeout: int = 15) -> dict:
    """Issue an HTTP request to CLS with proper browser-mimicking headers."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.cls.cn/",
        "Origin": "https://www.cls.cn",
    }
    data, err = fetch_json(url, timeout=float(timeout), headers=headers,
                           method=method, body=body, use_proxy=False)
    if err:
        raise RuntimeError(err)
    return data if isinstance(data, dict) else {}


def _cls_fetch_telegraphs(page_size: int = 20) -> list:
    """Fetch recent CLS telegraph (flash news) items.

    Uses the public roll-list endpoint that does not require signing.
    Returns a list of telegraph dicts.
    """
    try:
        url = (
            "https://www.cls.cn/nodeapi/updateTelegraphList?"
            + urllib.parse.urlencode({
                "app": "CailianpressWeb",
                "os": "web",
                "sv": "8.4.6",
                "category": "",
                "pageSize": str(min(page_size, 50)),
                "pageNum": "1",
            })
        )
        result = _cls_http_request(url, timeout=15)
    except Exception:
        # Try the alternative roll-list endpoint
        try:
            url = (
                "https://www.cls.cn/v1/roll/get_roll_list?"
                + urllib.parse.urlencode({
                    "app": "CailianpressWeb",
                    "os": "web",
                    "sv": "8.4.6",
                    "limit": str(min(page_size, 50)),
                    "page": "1",
                })
            )
            result = _cls_http_request(url, timeout=15)
        except Exception:
            return []

    if not isinstance(result, dict):
        return []

    # The API returns data under different keys depending on the endpoint
    items = (
        result.get("data", {}).get("roll_data", [])
        or result.get("data", {}).get("list", [])
        or result.get("data", [])
    )
    if isinstance(items, list):
        return items
    return []


def _cls_search_ticker(keyword: str, page_size: int = 10) -> list:
    """Search CLS for articles related to a ticker/stock code.

    Uses the CLS search suggestion + article list endpoints.
    Returns a list of article dicts.
    """
    try:
        url = (
            "https://www.cls.cn/v3/search/search?"
            + urllib.parse.urlencode({
                "keyword": keyword,
                "type": "article",
                "page": "1",
                "pageSize": str(min(page_size, 30)),
            })
        )
        result = _cls_http_request(url, timeout=15)
        if isinstance(result, dict):
            articles = (
                result.get("data", {}).get("articleList", [])
                or result.get("data", {}).get("list", [])
                or result.get("data", [])
            )
            if isinstance(articles, list):
                return articles
    except Exception:
        pass

    # Fallback: use the search API via POST
    try:
        url = "https://www.cls.cn/api/sw"
        body = {
            "app": "CailianpressWeb",
            "os": "web",
            "sv": "8.4.6",
            "method": "search",
            "params": {
                "keyword": keyword,
                "type": "all",
                "page": 1,
                "pageSize": min(page_size, 30),
            },
        }
        result = _cls_http_request(url, method="POST", body=body, timeout=15)
        if isinstance(result, dict):
            articles = (
                result.get("data", {}).get("articles", [])
                or result.get("data", {}).get("list", [])
                or result.get("data", [])
            )
            if isinstance(articles, list):
                return articles
    except Exception:
        pass

    return []


# ---------------------------------------------------------------------------
# Public vendor API
# ---------------------------------------------------------------------------

def get_news_cls(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """Retrieve news for a stock via CLS (财联社) search.

    Only activates for Chinese A-share tickers.

    Args:
        ticker: Ticker in yfinance (.SS/.SZ), tushare (.SH/.SZ), or plain 6-digit format.
        start_date: Start date in yyyy-mm-dd format.
        end_date: End date in yyyy-mm-dd format.

    Returns:
        Formatted markdown string with news articles.
    """
    if not _is_cn_ticker(ticker):
        return f"# SKIP_VENDOR: CLS only supports CN A-share tickers, not '{ticker}'"

    article_limit = get_config()["news_article_limit"]
    keyword = _ticker_to_keyword(ticker)

    try:
        articles = _cls_search_ticker(keyword, page_size=article_limit)
    except Exception as e:
        return f"# SKIP_VENDOR: CLS search failed for {ticker}: {e}"

    if not articles:
        return f"# SKIP_VENDOR: No CLS articles found for {ticker}"

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        return f"## {ticker} News (来源: 财联社), from {start_date} to {end_date}:\n\n"

    news_str = ""
    count = 0
    for a in articles:
        if not isinstance(a, dict):
            continue

        title = a.get("title") or a.get("article_title") or ""
        pub_time_str = a.get("ctime") or a.get("pub_time") or a.get("time") or ""

        # Date filter
        if pub_time_str:
            try:
                pub_dt = datetime.fromtimestamp(int(pub_time_str)) if pub_time_str.isdigit() else datetime.strptime(str(pub_time_str)[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                try:
                    pub_dt = datetime.strptime(str(pub_time_str)[:10], "%Y-%m-%d")
                except ValueError:
                    pub_dt = None
            if pub_dt and not (start_dt <= pub_dt <= end_dt):
                continue

        if not title:
            continue

        summary = a.get("brief") or a.get("summary") or a.get("content") or ""
        article_id = a.get("article_id") or a.get("id") or ""
        source = a.get("source") or a.get("media_name") or "财联社"

        news_str += f"### {title}"
        if source:
            news_str += f" (source: {source})"
        news_str += "\n"
        if summary:
            clean = re.sub(r'<[^>]+>', '', str(summary))
            if len(clean) > 500:
                clean = clean[:500] + "..."
            news_str += f"{clean}\n"
        if article_id:
            news_str += f"Link: https://www.cls.cn/detail/{article_id}\n"
        news_str += "\n"
        count += 1

    if count == 0:
        return f"No CLS news found for {ticker} ({keyword}) between {start_date} and {end_date}"

    return f"## {ticker} News (来源: 财联社), from {start_date} to {end_date}:\n\n{news_str}"


def get_global_news_cls(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Retrieve Chinese market flash news via CLS telegraph (财联社电报).

    The CLS telegraph is the defining feature of 财联社 — real-time
    breaking financial news flashes curated by editors. This is the
    primary news source that Chinese retail investors monitor daily.

    Falls back to keyword search if the telegraph endpoint is unavailable.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Days to look back (defaults to global_news_lookback_days in config).
        limit: Maximum articles (defaults to global_news_article_limit in config).

    Returns:
        Formatted markdown string with Chinese market flash news.
    """
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]

    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=look_back_days)
    except ValueError:
        return f"## 中国市场新闻 (来源: 财联社), date {curr_date}:\n\nInvalid date format.\n"

    # Strategy 1: Fetch CLS telegraph (flash news) list
    try:
        items = _cls_fetch_telegraphs(page_size=min(limit, 50))

        if items:
            news_str = ""
            count = 0
            for item in items:
                if count >= limit:
                    break
                if not isinstance(item, dict):
                    continue

                title = item.get("title") or item.get("content") or ""
                pub_time_str = item.get("ctime") or item.get("modified_time") or ""

                # Date filter
                if pub_time_str:
                    try:
                        if str(pub_time_str).isdigit():
                            pub_dt = datetime.fromtimestamp(int(pub_time_str))
                        else:
                            pub_dt = datetime.strptime(str(pub_time_str)[:19], "%Y-%m-%d %H:%M:%S")
                    except (ValueError, OSError):
                        pub_dt = None
                    if pub_dt and not (start_dt <= pub_dt <= curr_dt + timedelta(days=1)):
                        continue

                if not title:
                    continue

                news_str += f"- {title}\n"
                item_id = item.get("id") or item.get("article_id") or ""
                if item_id:
                    news_str += f"  Link: https://www.cls.cn/detail/{item_id}\n"
                count += 1

            if count > 0:
                start_date = start_dt.strftime("%Y-%m-%d")
                return f"## 中国市场电报新闻 (来源: 财联社), from {start_date} to {curr_date}:\n\n{news_str}"
    except Exception:
        pass

    # Strategy 2: Fall back to keyword search against CLS
    try:
        search_queries = config.get("cn_news_queries", config["global_news_queries"])
        all_articles = []
        seen_titles = set()

        for query in search_queries[:4]:  # limit queries to avoid rate issues
            try:
                articles = _cls_search_ticker(query, page_size=min(limit, 20))
                for a in articles:
                    if not isinstance(a, dict):
                        continue
                    title = a.get("title") or a.get("article_title") or ""
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_articles.append(a)
            except Exception:
                continue

            if len(all_articles) >= limit * 2:
                break

        if not all_articles:
            return f"# SKIP_VENDOR: No CLS market news available for {curr_date}"

        news_str = ""
        count = 0
        for a in all_articles:
            if count >= limit:
                break

            title = a.get("title") or a.get("article_title") or ""
            pub_time_str = a.get("ctime") or a.get("pub_time") or a.get("time") or ""

            if pub_time_str:
                try:
                    if str(pub_time_str).isdigit():
                        pub_dt = datetime.fromtimestamp(int(pub_time_str))
                    else:
                        pub_dt = datetime.strptime(str(pub_time_str)[:19], "%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pub_dt = None
                if pub_dt and not (start_dt <= pub_dt <= curr_dt + timedelta(days=1)):
                    continue

            if not title:
                continue

            summary = a.get("brief") or a.get("summary") or a.get("content") or ""
            article_id = a.get("article_id") or a.get("id") or ""
            source = a.get("source") or a.get("media_name") or "财联社"

            news_str += f"### {title}"
            if source:
                news_str += f" (source: {source})"
            news_str += "\n"
            if summary:
                clean = re.sub(r'<[^>]+>', '', str(summary))
                if len(clean) > 500:
                    clean = clean[:500] + "..."
                news_str += f"{clean}\n"
            if article_id:
                news_str += f"Link: https://www.cls.cn/detail/{article_id}\n"
            news_str += "\n"
            count += 1

        if count == 0:
            return f"# SKIP_VENDOR: No CLS market news for {curr_date} within lookback window"

        start_date = start_dt.strftime("%Y-%m-%d")
        return f"## 中国市场新闻 (来源: 财联社), from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        return f"# SKIP_VENDOR: CLS global news fetch failed: {e}"
