# Autocli Data Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate 3 new A-share data sources (Xueqiu sentiment, Eastmoney Guba forum, Sina Dragon-Tiger List) into CapitalRadar via hybrid public-API + Chrome-session scraping.

**Architecture:** Each new vendor module follows `eastmoney_news.py` pattern — functions accept `(ticker, ...)` args, return formatted markdown strings, use `# SKIP_VENDOR:` sentinel for unsupported tickers. Two-tier fallback: public HTTP API first, autocli Chrome scraping second, `<unavailable>` placeholder third. Registered in `interface.py` VENDOR_METHODS with vendor routing. Exposed to LLMs as LangChain `@tool` functions following `capital_flow_tools.py` pattern.

**Tech Stack:** Python stdlib (`urllib`, `json`, `re`, `time`), Chrome DevTools Protocol (optional), LangChain `@tool` decorator

---

### Task 1: Create autocli_utils.py — shared Chrome scraping utility

**Files:**
- Create: `capitalradar/dataflows/autocli_utils.py`

- [ ] **Step 1: Write the module**

```python
"""Shared Chrome DevTools Protocol scraping utilities for autocli data vendors.

All vendors use these helpers for the Tier-2 fallback: when public HTTP APIs
fail or return incomplete data, autocli reuses the user's Chrome login session
to scrape the target page.
"""

import json
import time
import urllib.request

from .config import get_config


def _get_autocli_config():
    return get_config().get("autocli", {})


def is_chrome_available(debug_port=None):
    """Check if Chrome is running with remote debugging enabled.

    Returns True if Chrome's DevTools HTTP endpoint responds on the given port.
    """
    if debug_port is None:
        debug_port = _get_autocli_config().get("chrome_debug_port", 9222)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{debug_port}/json/version",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read().decode("utf-8"))
        return "Browser" in data.get("Browser", "")
    except Exception:
        return False


def fetch_via_chrome(url, selectors_map, debug_port=None, timeout=None):
    """Scrape page data via Chrome DevTools Protocol.

    Uses Chrome's /json/page API to navigate and evaluate JavaScript
    selectors against the target page. Returns structured data.

    Args:
        url: Target page URL.
        selectors_map: {"field_name": "css_selector"} dict.
        debug_port: Chrome debug port (default from config).
        timeout: Request timeout in seconds (default from config).

    Returns:
        dict with extracted text values. Empty strings on failure.
    """
    config = _get_autocli_config()
    if debug_port is None:
        debug_port = config.get("chrome_debug_port", 9222)
    if timeout is None:
        timeout = config.get("timeout_seconds", 20)

    if not is_chrome_available(debug_port):
        return {}

    result = {}
    try:
        # Open a new tab and navigate
        open_url = (
            f"http://127.0.0.1:{debug_port}/json/new?{urllib.parse.urlencode({'url': url})}"
        )
        tab_req = urllib.request.Request(open_url, headers={"User-Agent": "Mozilla/5.0"})
        tab_resp = urllib.request.urlopen(tab_req, timeout=timeout)
        tab_data = json.loads(tab_resp.read().decode("utf-8"))
        ws_url = tab_data.get("webSocketDebuggerUrl", "")

        if not ws_url:
            return {}

        # Evaluate each selector via CDP Runtime.evaluate over HTTP
        # (Simplified: use CDP HTTP endpoint /json/send for evaluation)
        for field_name, css_selector in selectors_map.items():
            try:
                # Build JavaScript to query the selector
                js_expr = json.dumps(
                    f"Array.from(document.querySelectorAll({json.dumps(css_selector)})).map(el => el.textContent.trim()).join('|||')"
                )
                cdp_payload = json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": js_expr,
                        "returnByValue": True,
                    },
                })

                eval_req = urllib.request.Request(
                    f"http://127.0.0.1:{debug_port}/json/send?"
                    + urllib.parse.urlencode({"ws": ws_url}),
                    data=cdp_payload.encode("utf-8"),
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/json",
                    },
                )
                eval_resp = urllib.request.urlopen(eval_req, timeout=timeout)
                eval_data = json.loads(eval_resp.read().decode("utf-8"))

                value = eval_data.get("result", {}).get("result", {}).get("value", "")
                result[field_name] = value
            except Exception:
                result[field_name] = ""

        # Close the tab
        try:
            close_url = (
                f"http://127.0.0.1:{debug_port}/json/close/{tab_data.get('id', '')}"
            )
            close_req = urllib.request.Request(close_url, headers={"User-Agent": "Mozilla/5.0"})
            urllib.request.urlopen(close_req, timeout=5)
        except Exception:
            pass

    except Exception:
        pass

    return result


def safe_autocli_fetch(fetch_fn, *args, **kwargs):
    """Wrap any autocli fetch function: never raise, return empty string on failure.

    All data-vendor functions should use this wrapper for their autocli tier,
    so the analysis pipeline never breaks on a scraping failure.
    """
    try:
        return fetch_fn(*args, **kwargs)
    except Exception:
        return ""
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('capitalradar/dataflows/autocli_utils.py').read()); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/dataflows/autocli_utils.py
git commit -m "feat: add autocli_utils — shared Chrome CDP scraping helpers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Create xueqiu_sentiment.py — Xueqiu vendor module

**Files:**
- Create: `capitalradar/dataflows/xueqiu_sentiment.py`

- [ ] **Step 1: Write the module**

```python
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
        search_resp = urllib.request.urlopen(search_req, timeout=15)
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
        status_resp = urllib.request.urlopen(status_req, timeout=15)
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
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('capitalradar/dataflows/xueqiu_sentiment.py').read()); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/dataflows/xueqiu_sentiment.py
git commit -m "feat: add xueqiu_sentiment vendor — two-tier Xueqiu data scraping

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Create eastmoney_guba.py — Guba forum vendor module

**Files:**
- Create: `capitalradar/dataflows/eastmoney_guba.py`

- [ ] **Step 1: Write the module**

```python
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
    """Tier 1: Fetch forum posts from Eastmoney push API."""
    try:
        # Eastmoney push API for guba hot posts
        params = {
            "cb": "jQuery",
            "pn": "1",
            "pz": str(min(max_posts, 50)),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": f"m:90+t:3+i:{code}",
            "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f14,f15,f16",
            "_": str(int(time.time() * 1000)),
        }
        url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Referer": f"https://guba.eastmoney.com/list,{code}.html",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        text = resp.read().decode("utf-8")

        # Strip JSONP wrapper
        json_str = re.sub(r"^[^(]*\(", "", text)
        json_str = re.sub(r"\)\s*;?\s*$", "", json_str)
        data = json.loads(json_str)

        items = data.get("data", {}).get("diff", [])
        if not items:
            return ""

        output = f"## 股吧情绪 — {code}\n"
        output += f"**帖子量**: {len(items)} 帖\n"

        bullish_keywords = ["涨", "利好", "涨停", "翻倍", "起飞", "看好", "抄底", "买入", "牛"]
        bearish_keywords = ["跌", "利空", "跌停", "腰斩", "暴跌", "出货", "割肉", "卖出", "熊"]
        bullish_count = 0
        bearish_count = 0
        keywords_found = set()

        output += "### 热帖\n"
        for item in items[:max_posts]:
            title = item.get("f14", "") or item.get("f2", "")
            read_count = item.get("f3", 0) or 0
            comment_count = item.get("f4", 0) or 0
            pub_time = item.get("f5", "") or ""

            if not title:
                continue

            # Simple keyword-based sentiment
            title_lower = str(title).lower()
            for kw in bullish_keywords:
                if kw in title:
                    keywords_found.add(kw)
            for kw in bearish_keywords:
                if kw in title:
                    keywords_found.add(kw)

            if any(kw in title for kw in bullish_keywords):
                bullish_count += 1
            elif any(kw in title for kw in bearish_keywords):
                bearish_count += 1

            short_title = str(title)[:100]
            output += f"- {short_title} | 阅读:{read_count} | 评论:{comment_count}"
            if pub_time:
                output += f" | {pub_time}"
            output += "\n"

        total = bullish_count + bearish_count
        if total > 0:
            bull_pct = round(100 * bullish_count / total)
            bear_pct = round(100 * bearish_count / total)
            output += f"\n**情绪倾向**: 看多 {bull_pct}% / 看空 {bear_pct}%\n"
        if keywords_found:
            output += f"**热门关键词**: {', '.join(sorted(keywords_found))}\n"

        return output + "\n"

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
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('capitalradar/dataflows/eastmoney_guba.py').read()); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/dataflows/eastmoney_guba.py
git commit -m "feat: add eastmoney_guba vendor — two-tier Guba forum scraping

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Create sina_dragon_tiger.py — Dragon-Tiger List vendor

**Files:**
- Create: `capitalradar/dataflows/sina_dragon_tiger.py`

- [ ] **Step 1: Write the module**

```python
"""Sina Dragon-Tiger List (新浪龙虎榜) vendor.

Provides institutional trading desk disclosure data for Chinese A-share stocks.
The Dragon-Tiger List (龙虎榜) reveals which trading desks (营业部) bought or sold
stocks that made the daily price-move threshold, distinguishing institutional
desks from hot-money (游资) desks.

Two-tier fallback:
  Tier 1: Sina finance public JSON/HTML endpoint
  Tier 2: Autocli Chrome scraping
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .autocli_utils import fetch_via_chrome, is_chrome_available, safe_autocli_fetch
from .config import get_config


def _ticker_to_code(ticker: str) -> str:
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


def _is_institutional_desk(name: str) -> bool:
    """Heuristic: check if a trading desk name indicates institutional origin."""
    institutional_keywords = [
        "机构专用", "机构", "基金", "社保", "保险", "QFII",
        "沪股通", "深股通", "沪深港通", "北向",
        "中信证券", "华泰证券", "国泰君安", "中金公司",
        "中信建投", "招商证券", "海通证券", "广发证券",
    ]
    name_lower = name.lower()
    for kw in institutional_keywords:
        if kw in name_lower:
            return True
    return False


def _fetch_dragon_tiger_api(code: str, recent_days: int) -> str:
    """Tier 1: Fetch from Sina Dragon-Tiger List public endpoint."""
    try:
        # Sina finance LHB page — try JSON endpoint first
        url = (
            "https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/"
            f"index.phtml?symbol={code}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("gbk", errors="replace")

        # Parse HTML table rows for dragon tiger entries
        # Pattern: table row with trading desk name, buy amount, sell amount
        rows = re.findall(
            r"<tr[^>]*>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?<td[^>]*>(.*?)</td>.*?</tr>",
            html,
            re.DOTALL,
        )

        if not rows:
            return ""

        output = f"## 龙虎榜数据 — {code}\n"
        output += "| 席位名称 | 类型 | 买入(万) | 卖出(万) | 净额(万) |\n"
        output += "|----------|------|----------|----------|----------|\n"

        inst_buy_total = 0
        inst_sell_total = 0
        hot_buy_total = 0
        hot_sell_total = 0

        for row in rows[:30]:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row[0] + row[1] + row[2], re.DOTALL)
            if len(cells) >= 3:
                desk_name = re.sub(r"<[^>]+>", "", cells[0]).strip()
                buy_str = re.sub(r"<[^>]+>", "", cells[1]).strip()
                sell_str = re.sub(r"<[^>]+>", "", cells[2]).strip()
                try:
                    buy_amt = float(buy_str.replace(",", "").replace("万", ""))
                except ValueError:
                    buy_amt = 0
                try:
                    sell_amt = float(sell_str.replace(",", "").replace("万", ""))
                except ValueError:
                    sell_amt = 0

                net = buy_amt - sell_amt
                desk_type = "机构" if _is_institutional_desk(desk_name) else "游资"

                if desk_type == "机构":
                    inst_buy_total += buy_amt
                    inst_sell_total += sell_amt
                else:
                    hot_buy_total += buy_amt
                    hot_sell_total += sell_amt

                output += f"| {desk_name} | {desk_type} | {buy_amt:,.0f} | {sell_amt:,.0f} | {net:+,.0f} |\n"

        inst_net = inst_buy_total - inst_sell_total
        hot_net = hot_buy_total - hot_sell_total
        output += f"\n**机构净流入**: {inst_net:+,.0f} 万 | **游资净流入**: {hot_net:+,.0f} 万\n"

        if inst_net > 0 and hot_net > 0:
            output += "**信号**: 机构与游资共同买入 → 强烈看多\n"
        elif inst_net > 0:
            output += "**信号**: 机构买入主导 → 中长期看好\n"
        elif hot_net > 0 and inst_net < 0:
            output += "**信号**: 游资主导 + 机构卖出 → 短线炒作，注意风险\n"
        else:
            output += "**信号**: 机构与游资共同卖出 → 看空\n"

        return output + "\n"

    except Exception:
        return ""


def _fetch_dragon_tiger_autocli(code: str) -> str:
    """Tier 2: Fallback scrape via Chrome session."""
    if not is_chrome_available():
        return ""

    url = f"https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml?symbol={code}"
    selectors = {
        "rows": ".lhb_table tr",
        "date": ".lhb_date",
    }
    result = fetch_via_chrome(url, selectors)
    if not result or not result.get("rows"):
        return ""

    rows_text = result["rows"]
    list_date = result.get("date", "unknown")
    lines = rows_text.split("|||")

    output = f"## 龙虎榜数据 — {code} (via Chrome)\n"
    output += f"**上榜日期**: {list_date}\n"
    output += "| 席位 | 买入 | 卖出 |\n"
    output += "|------|------|------|\n"
    count = 0
    for line in lines:
        if count >= 15:
            break
        parts = [p.strip() for p in line.split("|")[:3]]
        if len(parts) >= 2:
            output += f"| {parts[0]} | {parts[1] if len(parts) > 1 else '-'} | {parts[2] if len(parts) > 2 else '-'} |\n"
            count += 1
    return output + "\n"


def get_dragon_tiger_list(ticker: str, trade_date: str) -> str:
    """Retrieve Dragon-Tiger List data for a Chinese A-share stock.

    Args:
        ticker: Ticker in yfinance (.SS/.SZ), tushare (.SH/.SZ), or plain 6-digit format.
        trade_date: Current analysis date in YYYY-MM-DD format.

    Returns:
        Formatted markdown table with trading desk data, or empty string on total failure.
    """
    if not _is_cn_ticker(ticker):
        return f"# SKIP_VENDOR: Dragon-Tiger List only available for CN A-share tickers, not '{ticker}'"

    config = get_config()
    recent_days = config.get("autocli", {}).get("dragon_tiger_recent_days", 10)
    code = _ticker_to_code(ticker)

    # Tier 1: Public API
    result = _fetch_dragon_tiger_api(code, recent_days)
    if result:
        return result

    # Tier 2: Autocli Chrome scraping
    result = safe_autocli_fetch(_fetch_dragon_tiger_autocli, code)
    if result:
        return result

    # Tier 3: Not listed or unavailable
    return f"# {ticker} 近期未上龙虎榜，或数据不可用\n"
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('capitalradar/dataflows/sina_dragon_tiger.py').read()); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/dataflows/sina_dragon_tiger.py
git commit -m "feat: add sina_dragon_tiger vendor — Dragon-Tiger List scraping

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Register new vendors in interface.py

**Files:**
- Modify: `capitalradar/dataflows/interface.py`

- [ ] **Step 1: Add imports for new vendors**

At the top of the file, after the existing `cls_news` import (line 20), add:

```python
from .xueqiu_sentiment import get_xueqiu_sentiment
from .eastmoney_guba import get_guba_sentiment
from .sina_dragon_tiger import get_dragon_tiger_list
```

- [ ] **Step 2: Add new category to TOOLS_CATEGORIES**

After the `"capital_flow_data"` category block (after line 87), add:

```python
    "social_sentiment_data": {
        "description": "Retail investor social sentiment from CN platforms",
        "tools": [
            "get_xueqiu_sentiment",
            "get_guba_sentiment",
        ]
    },
```

- [ ] **Step 3: Add new methods to VENDOR_METHODS**

After the `"get_realtime_quote"` block (after line 211), add:

```python
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
        "sina": get_dragon_tiger_list,
        "tushare": lambda ticker, trade_date: (
            "# SKIP_VENDOR: Dragon-Tiger List not available via tushare"
        ),
    },
```

- [ ] **Step 4: Add new vendors to VENDOR_LIST**

Replace line 90-96:
```python
VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
    "tushare",
    "eastmoney",
    "cls",
]
```

With:
```python
VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
    "tushare",
    "eastmoney",
    "cls",
    "xueqiu",
    "eastmoney_guba",
    "sina",
]
```

- [ ] **Step 5: Verify imports and syntax**

```bash
python -c "from capitalradar.dataflows.interface import VENDOR_METHODS, TOOLS_CATEGORIES; print('OK'); print(list(VENDOR_METHODS.keys()))"
```

- [ ] **Step 6: Commit**

```bash
git add capitalradar/dataflows/interface.py
git commit -m "feat: register xueqiu, guba, and dragon-tiger vendors in interface

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Register LangChain @tools for new data sources

**Files:**
- Modify: `capitalradar/agents/utils/capital_flow_tools.py` — add `get_dragon_tiger_list`
- Modify: `capitalradar/agents/utils/agent_utils.py` — export `get_xueqiu_sentiment`, `get_guba_sentiment`

- [ ] **Step 1: Add get_dragon_tiger_list to capital_flow_tools.py**

After the `get_analyst_recommendations` function (after line 165), add:

```python

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
```

- [ ] **Step 2: Add sentiment tool imports to agent_utils.py**

At the top of agent_utils.py, after the existing imports (after line 33), add:

```python
from capitalradar.agents.utils.social_sentiment_tools import (
    get_xueqiu_sentiment,
    get_guba_sentiment,
)
```

- [ ] **Step 3: Create social_sentiment_tools.py**

Create new file `capitalradar/agents/utils/social_sentiment_tools.py`:

```python
"""LangChain tool wrappers for social sentiment data from CN platforms.

These @tool-decorated functions expose Xueqiu and Guba sentiment data
as callable tools that the Sentiment Analyst LLM can invoke.
Each delegates to route_to_vendor() for vendor routing.
"""

from langchain_core.tools import tool
from typing import Annotated
from capitalradar.dataflows.interface import route_to_vendor


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
    the stock-specific forum. Guba users are predominantly retail investors —
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
```

- [ ] **Step 4: Verify imports**

```bash
python -c "from capitalradar.agents.utils.capital_flow_tools import get_dragon_tiger_list; from capitalradar.agents.utils.social_sentiment_tools import get_xueqiu_sentiment, get_guba_sentiment; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add capitalradar/agents/utils/capital_flow_tools.py capitalradar/agents/utils/social_sentiment_tools.py capitalradar/agents/utils/agent_utils.py
git commit -m "feat: register autocli tools as LangChain @tool functions

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Add Xueqiu + Guba pre-fetch to Sentiment Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/sentiment_analyst.py`

- [ ] **Step 1: Add imports for new fetchers**

In the imports section (after line 32), add:

```python
from capitalradar.dataflows.xueqiu_sentiment import get_xueqiu_sentiment
from capitalradar.dataflows.eastmoney_guba import get_guba_sentiment
```

- [ ] **Step 2: Add _safe_fetch helpers**

After the existing `_safe_fetch_realtime` function (after line 111), add:

```python
def _safe_fetch_xueqiu(ticker: str) -> str:
    """Fetch Xueqiu sentiment, degrading gracefully on failure."""
    try:
        return get_xueqiu_sentiment(ticker)
    except Exception:
        return f"<unavailable — Xueqiu fetch failed for {ticker}>"


def _safe_fetch_guba(ticker: str) -> str:
    """Fetch Guba forum sentiment, degrading gracefully on failure."""
    try:
        return get_guba_sentiment(ticker)
    except Exception:
        return f"<unavailable — Guba fetch failed for {ticker}>"
```

- [ ] **Step 3: Add pre-fetch calls in sentiment_analyst_node**

In the pre-fetch block (after `realtime_block = _safe_fetch_realtime(ticker)` on line 59), add:

```python
        xueqiu_block = _safe_fetch_xueqiu(ticker)
        guba_block = _safe_fetch_guba(ticker)
```

- [ ] **Step 4: Pass new blocks to _build_system_message**

In the call to `_build_system_message()` (lines 63-72), add the two new parameters:

```python
        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            realtime_block=realtime_block,
            xueqiu_block=xueqiu_block,
            guba_block=guba_block,
            capital_flow_report=capital_flow_report,
        )
```

- [ ] **Step 5: Update _build_system_message signature and body**

Update the function signature (around line 113-123) to accept the new params:

```python
def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    realtime_block: str = "",
    xueqiu_block: str = "",
    guba_block: str = "",
    capital_flow_report: str = "",
) -> str:
```

In the returned f-string, add the new data source blocks after the Reddit block (`<end_of_reddit>`) and after the Real-time Market Snapshot block (`<end_of_realtime>`), before the "## How to analyze this data" section. Find the `<end_of_realtime>` line and add after it:

```python

### 雪球社区 — A股投资者讨论（预抓取）
中国最大的投资者社区，用户偏专业/半专业，每条帖子可含多空标记、用户认证级别。与股吧互为对照。

<start_of_xueqiu>
{xueqiu_block}
<end_of_xueqiu>

### 东方财富股吧 — 散户论坛（预抓取）
每只A股专属论坛，散户情绪最直接的窗口。与雪球互为对照。

<start_of_guba>
{guba_block}
<end_of_guba>
```

Then in the "How to analyze" best practices section, add a new item 10 (after item 9 on line ~189):

```
10. **雪球与股吧互为对照** — 雪球用户偏专业/半专业，股吧用户偏散户。雪球看多 + 股吧恐慌 = 机构在雪球带节奏，散户在股吧割肉 → 吸筹信号。两者一致极端看多 = 情绪过热 → 减仓信号。雪球讨论量异动（突然大幅增加）= 有事件催化，需结合新闻源验证。
```

- [ ] **Step 6: Verify syntax**

```bash
python -c "import ast; ast.parse(open('capitalradar/agents/analysts/sentiment_analyst.py', encoding='utf-8').read()); print('OK')"
```

- [ ] **Step 7: Commit**

```bash
git add capitalradar/agents/analysts/sentiment_analyst.py
git commit -m "feat: add Xueqiu + Guba pre-fetch to sentiment analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: Add dragon tiger tool to Capital Flow Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/capital_flow_analyst.py`

- [ ] **Step 1: Add import for get_dragon_tiger_list**

In the imports (after line 16), add:

```python
    get_dragon_tiger_list,
```

Make sure it's imported from the same `capitalradar.agents.utils.agent_utils` module. Check the existing import block at the top of the file (lines 2-16) — `get_dragon_tiger_list` needs to be added there. If it's not exported from agent_utils yet (it's in capital_flow_tools.py), import it directly:

```python
from capitalradar.agents.utils.capital_flow_tools import (
    get_money_flow,
    get_hsgt_flow,
    get_market_flow,
    get_margin_trading,
    get_institutional_holders,
    get_major_holders,
    get_analyst_recommendations,
    get_dragon_tiger_list,
)
```

Note: The current import is from `capitalradar.agents.utils.agent_utils` (line 2-16). Check if those are re-exported. Looking at agent_utils.py, it imports from `capital_flow_tools` and re-exports them. So we need to add `get_dragon_tiger_list` to the import-from line in agent_utils.py AND to the capital_flow_analyst.py import. 

Since the capital flow analyst imports via agent_utils, update the import in agent_utils.py lines 21-29 to include `get_dragon_tiger_list`:

```python
from capitalradar.agents.utils.capital_flow_tools import (
    get_money_flow,
    get_hsgt_flow,
    get_market_flow,
    get_margin_trading,
    get_institutional_holders,
    get_major_holders,
    get_analyst_recommendations,
    get_dragon_tiger_list,
)
```

And update the capital_flow_analyst.py import (around line 2-16) to include `get_dragon_tiger_list`.

- [ ] **Step 2: Add tool to tools list**

In the `tools` list (after line 37, after `get_realtime_quote`), add:

```python
            get_dragon_tiger_list,
```

- [ ] **Step 3: Add Dragon Tiger prompt section**

In the system_message, after the "SUPPORTING DIMENSIONS" item 9 (intraday data) and before the "REPORT STRUCTURE" section, add item 10. Find the line after intraday data section (around line 110, after the line `"     activity not visible on daily candles.\n\n"`) and before `"=== REPORT STRUCTURE ===\n"`:

Insert:
```python
            "10. 龙虎榜 (Dragon-Tiger List):\n"
            "   - Use `get_dragon_tiger_list` to check if the stock appeared on the Dragon-Tiger List recently.\n"
            "   - 机构专用席位 (Institutional Desk) net buying = strong bullish confirmation signal.\n"
            "   - 游资席位 (Hot-money Desk) net buying WITHOUT institutional participation = short-term speculation, NOT accumulation.\n"
            "   - Cross-reference: institutional desk buying + money flow net inflow = dual bullish confirmation (高置信度).\n"
            "   - Cross-reference: institutional desk selling + money flow net inflow = divergence (分歧) — the inflow may be retail or hot-money driven.\n"
            "   - Cross-reference: institutional desk selling + money flow net outflow = dual bearish confirmation.\n"
            "   - If the stock has NOT appeared on the Dragon-Tiger List recently, note this and rely on other capital flow signals.\n\n"
```

- [ ] **Step 4: Verify syntax**

```bash
python -c "import ast; ast.parse(open('capitalradar/agents/analysts/capital_flow_analyst.py', encoding='utf-8').read()); print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add capitalradar/agents/analysts/capital_flow_analyst.py capitalradar/agents/utils/agent_utils.py
git commit -m "feat: add dragon tiger list tool to capital flow analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: Add autocli config to default_config.py

**Files:**
- Modify: `capitalradar/default_config.py`

Read the file first to find the exact location.

- [ ] **Step 1: Add autocli config block**

After the `"retracement"` block (added in the previous feature), add:

```python
    "autocli": {
        "enabled": True,
        "chrome_debug_port": 9222,
        "request_delay_ms": 1500,
        "max_retries": 2,
        "timeout_seconds": 20,
        "xueqiu_max_posts": 20,
        "guba_max_posts": 20,
        "dragon_tiger_recent_days": 10,
    },
```

- [ ] **Step 2: Update data_vendors for new categories**

In the `"data_vendors"` block, add:

```python
        "social_sentiment_data": "xueqiu,eastmoney_guba",
```

The `capital_flow_data` entry remains `"tushare"` for primary (tushare is the first vendor for money flow), and `sina` is the fallback for dragon tiger via the VENDOR_METHODS registration. No change needed to `capital_flow_data` since `get_dragon_tiger_list` uses `sina` as its primary vendor.

- [ ] **Step 3: Verify config loads**

```bash
python -c "from capitalradar.default_config import DEFAULT_CONFIG; print('autocli:', list(DEFAULT_CONFIG['autocli'].keys())); print('social_sentiment:', DEFAULT_CONFIG['data_vendors']['social_sentiment_data'])"
```

- [ ] **Step 4: Commit**

```bash
git add capitalradar/default_config.py
git commit -m "feat: add autocli config block and social sentiment vendor entries

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - [x] autocli_utils.py (Task 1)
   - [x] xueqiu_sentiment.py (Task 2)
   - [x] eastmoney_guba.py (Task 3)
   - [x] sina_dragon_tiger.py (Task 4)
   - [x] interface.py registration (Task 5)
   - [x] LangChain @tool registration (Task 6)
   - [x] Sentiment analyst pre-fetch + prompt (Task 7)
   - [x] Capital flow analyst tool + prompt (Task 8)
   - [x] Config additions (Task 9)

2. **Placeholder scan:** No TBDs, no TODOs — all steps contain complete code.

3. **Type consistency:**
   - All vendor functions return `str` (markdown formatted)
   - All vendor functions for sentiment take `(ticker)` → `str`
   - `get_dragon_tiger_list` takes `(ticker, trade_date)` → `str`
   - `# SKIP_VENDOR:` sentinel format matches existing eastmoney_news.py convention
   - `_safe_fetch_*` helpers follow `_safe_fetch_realtime` pattern
   - Tool names match between interface.py registration and @tool definitions
