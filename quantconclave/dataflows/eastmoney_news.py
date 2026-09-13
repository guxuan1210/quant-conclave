"""Eastmoney (东方财富) news vendor for Chinese A-share market.

Uses Eastmoney's search-api-web endpoint (same as akshare library) for
stock-specific news, and akshare's stock_info_global_em for market news.
No authentication required — all endpoints are free and publicly accessible.
"""

from typing import Optional
from datetime import datetime, timedelta
import json
import re
import time
import urllib.request
import urllib.parse

from .config import get_config


def _ticker_to_code(ticker: str) -> str:
    """Extract plain 6-digit code from any ticker format."""
    ticker = ticker.strip().upper()
    for suffix in (".SS", ".SH", ".SZ", ".BJ"):
        if ticker.endswith(suffix):
            return ticker.split(".")[0]
    return ticker


def _is_cn_ticker(ticker: str) -> bool:
    """Check if a ticker is a Chinese A-share ticker."""
    ticker = ticker.strip().upper()
    if any(ticker.endswith(s) for s in (".SH", ".SZ", ".BJ", ".SS")):
        return True
    if ticker.isdigit() and len(ticker) == 6:
        return True
    return False


def _eastmoney_search_request(keyword: str, page_size: int = 10) -> dict:
    """Call the Eastmoney search API (JSONP endpoint) and return parsed JSON."""
    inner_param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": min(page_size, 50),
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": "jQuery",
        "param": json.dumps(inner_param, ensure_ascii=False),
        "_": str(int(time.time() * 1000)),
    }
    url = "https://search-api-web.eastmoney.com/search/jsonp?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Referer": f"https://so.eastmoney.com/news/s?keyword={keyword}",
    })

    resp = urllib.request.urlopen(req, timeout=15)
    text = resp.read().decode("utf-8")

    # Strip JSONP wrapper: jQuery(...) or similar
    json_str = re.sub(r'^[^(]*\(', '', text)
    json_str = re.sub(r'\)\s*;?\s*$', '', json_str)
    return json.loads(json_str)


def get_news_eastmoney(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """Retrieve news for a Chinese A-share stock via Eastmoney search API.

    Args:
        ticker: Ticker in yfinance (.SS/.SZ), tushare (.SH/.SZ), or plain 6-digit format.
        start_date: Start date in yyyy-mm-dd format.
        end_date: End date in yyyy-mm-dd format.

    Returns:
        Formatted markdown string with news articles.
    """
    if not _is_cn_ticker(ticker):
        return f"# SKIP_VENDOR: Eastmoney only supports CN A-share tickers, not '{ticker}'"

    article_limit = get_config()["news_article_limit"]
    code = _ticker_to_code(ticker)

    try:
        data = _eastmoney_search_request(code, page_size=article_limit)

        articles = []
        if isinstance(data, dict):
            articles = data.get("result", {}).get("cmsArticleWebOld", [])

        if not articles:
            return f"No Eastmoney news found for {ticker} ({code}) between {start_date} and {end_date}"

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

        news_str = ""
        count = 0
        for a in articles:
            if not isinstance(a, dict):
                continue

            title = a.get("title", "")
            pub_date = a.get("date", "")
            source = a.get("mediaName", "")
            content = a.get("content", "")
            article_code = a.get("code", "")

            # Date filter
            if pub_date:
                try:
                    pub_dt = datetime.strptime(pub_date[:10], "%Y-%m-%d")
                    if not (start_dt <= pub_dt <= end_dt):
                        continue
                except ValueError:
                    pass

            if not title:
                continue

            news_str += f"### {title}"
            if source:
                news_str += f" (source: {source})"
            news_str += "\n"
            if content:
                clean = re.sub(r'<[^>]+>', '', str(content))
                if len(clean) > 500:
                    clean = clean[:500] + "..."
                news_str += f"{clean}\n"
            if article_code:
                news_str += f"Link: https://finance.eastmoney.com/a/{article_code}.html\n"
            news_str += "\n"
            count += 1

        if count == 0:
            return f"No Eastmoney news found for {ticker} ({code}) between {start_date} and {end_date}"

        return f"## {ticker} News (来源: 东方财富), from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching Eastmoney news for {ticker}: {str(e)}"


def get_global_news_eastmoney(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Retrieve Chinese market news using akshare's stock_info_global_em wrapper.

    Falls back to Eastmoney keyword search if akshare is unavailable.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Days to look back (defaults to global_news_lookback_days in config).
        limit: Maximum articles (defaults to global_news_article_limit in config).

    Returns:
        Formatted markdown string with Chinese market news.
    """
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]

    # Try akshare first (more comprehensive, has proper date handling)
    try:
        import akshare as ak
        df = ak.stock_info_global_em()

        if df is None or df.empty:
            raise ValueError("akshare returned empty dataframe")

        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=look_back_days)

        # Map column names (akshare uses Chinese column names)
        col_map = {}
        for c in df.columns:
            if c in ("标题", "title"):
                col_map["title"] = c
            elif c in ("摘要", "summary", "digest"):
                col_map["summary"] = c
            elif c in ("发布时间", "ctime", "showTime", "pub_date"):
                col_map["pub_time"] = c
            elif c in ("链接", "url", "link"):
                col_map["url"] = c

        news_str = ""
        count = 0
        for _, row in df.iterrows():
            if count >= limit:
                break

            title = str(row.get(col_map.get("title", ""), "") or "")
            summary = str(row.get(col_map.get("summary", ""), "") or "")
            pub_time = str(row.get(col_map.get("pub_time", ""), "") or "")
            url = str(row.get(col_map.get("url", ""), "") or "")

            if not title or title == "nan":
                continue

            # Date filter
            if pub_time and pub_time != "nan":
                try:
                    pub_dt = datetime.strptime(pub_time[:10], "%Y-%m-%d")
                    if pub_dt < start_dt or pub_dt > curr_dt + timedelta(days=1):
                        continue
                except ValueError:
                    pass

            news_str += f"### {title}\n"
            if summary and summary != "nan":
                clean = re.sub(r'<[^>]+>', '', summary)
                if len(clean) > 500:
                    clean = clean[:500] + "..."
                news_str += f"{clean}\n"
            if url and url != "nan":
                news_str += f"Link: {url}\n"
            news_str += "\n"
            count += 1

        if count == 0:
            return f"No Chinese market news found for {curr_date}"

        start_date = start_dt.strftime("%Y-%m-%d")
        return f"## 中国市场新闻 (来源: 东方财富), from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        # Fallback: use Eastmoney search API with cn_news_queries
        search_queries = config.get("cn_news_queries", config["global_news_queries"])
        all_articles = []
        seen_titles = set()

        for query in search_queries:
            try:
                data = _eastmoney_search_request(query, page_size=min(limit, 30))
                articles = data.get("result", {}).get("cmsArticleWebOld", [])
                for a in articles:
                    if not isinstance(a, dict):
                        continue
                    title = a.get("title", "")
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_articles.append(a)
            except Exception:
                continue

            if len(all_articles) >= limit * 2:
                break

        if not all_articles:
            return f"No Chinese market news found for {curr_date} (akshare unavailable: {e})"

        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=look_back_days)

        news_str = ""
        count = 0
        for a in all_articles:
            if count >= limit:
                break

            title = a.get("title", "")
            pub_date = a.get("date", "")
            source = a.get("mediaName", "")
            content = a.get("content", "")
            article_code = a.get("code", "")

            if pub_date:
                try:
                    pub_dt = datetime.strptime(pub_date[:10], "%Y-%m-%d")
                    if pub_dt < start_dt or pub_dt > curr_dt + timedelta(days=1):
                        continue
                except ValueError:
                    pass

            if not title:
                continue

            news_str += f"### {title}"
            if source:
                news_str += f" (source: {source})"
            news_str += "\n"
            if content:
                clean = re.sub(r'<[^>]+>', '', str(content))
                if len(clean) > 500:
                    clean = clean[:500] + "..."
                news_str += f"{clean}\n"
            if article_code:
                news_str += f"Link: https://finance.eastmoney.com/a/{article_code}.html\n"
            news_str += "\n"
            count += 1

        if count == 0:
            return f"No Chinese market news found for {curr_date}"

        start_date = start_dt.strftime("%Y-%m-%d")
        return f"## 中国市场新闻 (来源: 东方财富), from {start_date} to {curr_date}:\n\n{news_str}"
