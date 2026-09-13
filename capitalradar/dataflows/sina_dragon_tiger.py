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
