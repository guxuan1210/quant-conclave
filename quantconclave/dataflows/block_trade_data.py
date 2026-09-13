"""Block trade (大宗交易/暗盘) data via AKShare / Eastmoney.

Provides institutional dark-pool trading data: block trades executed off-exchange
with price, volume, discount/premium, and trading desk information.

Key signals:
- 折价大宗 + 机构专用席位买入 = 机构低位吸筹 (bullish accumulation)
- 溢价大宗 + 机构席位卖出 = 拉高出货 (bearish distribution)
- 连续多日同一营业部大宗买入 = 主力建仓信号
- 折价率超过-10% = 可能是股东减持或利益输送

Uses Eastmoney's RPT_DATA_BLOCKTRADE report via akshare.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Cache: (date_range, df) to avoid repeated API calls
_cache: tuple[str, object] | None = ("", None)


def _normalize_ticker(code: str) -> str:
    """Strip exchange suffix: 600519.SH -> 600519."""
    for sfx in (".SH", ".SZ", ".BJ", ".SS", ".NS", ".T", ".HK", ".L", ".TO", ".AX"):
        if code.upper().endswith(sfx):
            return code[: -len(sfx)]
    return code


def _fetch_all_block_trades(start_date: str, end_date: str):
    """Fetch all A-share block trades for a date range. Cached per range."""
    global _cache
    cache_key = f"{start_date}_{end_date}"
    if _cache and _cache[0] == cache_key:
        return _cache[1]

    try:
        import akshare as ak
        df = ak.stock_dzjy_mrmx(symbol="A股", start_date=start_date, end_date=end_date)
        _cache = (cache_key, df)
        return df
    except Exception as e:
        logger.warning("Block trade fetch failed: %s", e)
        return None


def _parse_pct(value) -> float:
    """Parse a discount/premium string like '-5.2%' / '0%' → float."""
    import re as _re
    if value is None:
        return 0.0
    m = _re.search(r"([-+]?[\d.]+)", str(value))
    return float(m.group(1)) if m else 0.0


def _mx_block_trade_report(ticker: str, rows, start_date: str, end_date: str) -> str:
    """Build the same 暗盘报告 from 妙想(MX) block-trade rows (list[dict]).

    MX rows carry 成交价(元), 折/溢价率, 成交量(股), 成交金额(元), 买入/卖出营业部
    (机构专用 seats included). Signals mirror the akshare path.
    """
    from quantconclave.dataflows.mx_client import parse_cn_amount

    lines = [
        f"# 大宗交易暗盘: {ticker} ({start_date} ~ {end_date})",
        f"# 共 {len(rows)} 笔 | 含买方/卖方营业部",
        "",
    ]
    amounts = []
    has_inst_buy = False
    has_inst_sell = False
    for r in rows:
        date = str(r.get("日期", "") or "")
        price = r.get("成交价(元)")
        premium = _parse_pct(r.get("折/溢价率"))
        vol = r.get("成交量(股)")
        amt = parse_cn_amount(r.get("成交金额(元)")) or 0.0
        buyer = str(r.get("买入营业部", "") or "")
        seller = str(r.get("卖出营业部", "") or "")
        amounts.append(amt)
        direction = "溢价" if premium > 0 else ("折价" if premium < 0 else "平价")
        inst_b = "机构" in buyer
        inst_s = "机构" in seller
        has_inst_buy = has_inst_buy or inst_b
        has_inst_sell = has_inst_sell or inst_s
        tag = ""
        if inst_b and not inst_s:
            tag = " 🟢机构买入"
        elif inst_s and not inst_b:
            tag = " 🔴机构卖出"
        elif inst_b and inst_s:
            tag = " ⚪机构对倒"
        lines.append(
            f"{date} | 成交{price} ({direction}{premium:+.1f}%) "
            f"| 量{vol} 额{amt:.0f}万元{tag}"
        )
        if buyer or seller:
            lines.append(f"  买: {buyer[:40]} | 卖: {seller[:40]}")

    if not amounts:
        return ""
    total_amt = sum(amounts)
    # Premium average weighted by amount (weighted premium), or simple mean fallback.
    avg_premium = sum(_parse_pct(r.get("折/溢价率")) * (parse_cn_amount(r.get("成交金额(元)")) or 0.0)
                      for r in rows) / total_amt if total_amt else 0.0

    signal = ""
    if avg_premium < -5 and has_inst_buy:
        signal = "🟢 折价大宗+机构买入 — 机构暗盘吸筹，关注后续走势"
    elif avg_premium > 3 and has_inst_sell:
        signal = "🔴 溢价大宗+机构卖出 — 机构暗盘出货，注意风险"
    elif avg_premium < -8:
        signal = "🔴 大幅折价大宗 — 警惕股东减持或利益输送"
    elif avg_premium > 5:
        signal = "🟡 大幅溢价大宗 — 可能存在市值管理或利益输送"
    elif has_inst_buy:
        signal = "🟢 机构席位参与买入 — 暗盘有主力关注"
    elif avg_premium < -3:
        signal = "🟡 持续折价 — 关注是否正常机构调仓"
    else:
        signal = "⚪ 大宗价格接近市价，无明显异常信号"

    lines.append("")
    lines.append(f"**暗盘信号**: {signal}")
    lines.append(f"**汇总**: {len(rows)}笔 | 均价差{avg_premium:+.1f}% | 总额{total_amt/1e4:.2f}亿")
    return "\n".join(lines)


def get_block_trade_detail(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_days: int = 30,
) -> str:
    """Get per-trade block trade (大宗交易/暗盘) detail for a stock.

    Each row is one individual block trade with: trade date, deal price,
    discount/premium %, volume, amount, buyer desk, seller desk.

    Tries 妙想(MX) first (real 东方财富 暗盘/大宗 data); falls back to akshare
    Eastmoney RPT_DATA_BLOCKTRADE when MX_APIKEY is missing or MX fails.

    Args:
        ticker: Stock code (e.g. 600519.SH or 000858.SZ)
        start_date: Start date YYYYMMDD (default: lookback_days ago)
        end_date: End date YYYYMMDD (default: today)
        lookback_days: Days to look back

    Returns:
        Formatted report or empty string if no block trades found.
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    # ── 妙想(MX) real 东方财富 暗盘/大宗 first ──
    try:
        from quantconclave.dataflows.mx_client import query_text, extract_listing_rows, query
        _raw = query(f"{ticker} 大宗交易明细")
        rows = extract_listing_rows(_raw, max_rows=30)
        if rows:
            report = _mx_block_trade_report(ticker, rows, start_date, end_date)
            if report:
                return report
    except Exception:
        pass  # fall through to akshare

    short_code = _normalize_ticker(ticker)

    df = _fetch_all_block_trades(start_date, end_date)
    if df is None or df.empty:
        return ""

    # Filter for this stock
    df_stock = df[df["证券代码"] == short_code]
    if df_stock.empty:
        return ""

    lines = [
        f"# 大宗交易暗盘: {ticker} ({start_date} ~ {end_date})",
        f"# 共 {len(df_stock)} 笔 | 含买方/卖方营业部",
        "",
    ]

    for _, row in df_stock.iterrows():
        date = row["交易日期"]
        close = float(row["收盘价"])
        price = float(row["成交价"])
        premium = float(row["折溢率"])
        vol_shou = int(row["成交量"])  # 手
        amt_yuan = float(row["成交额"])  # 元
        buyer = str(row.get("买方营业部", "") or "")
        seller = str(row.get("卖方营业部", "") or "")

        direction = "溢价" if premium > 0 else ("折价" if premium < 0 else "平价")
        inst_b = "机构" in buyer
        inst_s = "机构" in seller
        tag = ""
        if inst_b and not inst_s:
            tag = " 🟢机构买入"
        elif inst_s and not inst_b:
            tag = " 🔴机构卖出"
        elif inst_b and inst_s:
            tag = " ⚪机构对倒"

        lines.append(
            f"{date} | {close:.2f} → {price:.2f} ({direction}{premium:+.1f}%) "
            f"| {vol_shou}手 {amt_yuan/1e4:.0f}万元{tag}"
        )
        if buyer or seller:
            lines.append(f"  买: {buyer[:30]} | 卖: {seller[:30]}")

    # Summary
    premiums = df_stock["折溢率"]
    avg_premium = float(premiums.mean())
    total_amt = float(df_stock["成交额"].sum())
    has_inst_buy = any("机构" in str(b) for b in df_stock.get("买方营业部", [""]))
    has_inst_sell = any("机构" in str(s) for s in df_stock.get("卖方营业部", [""]))

    signal = ""
    if avg_premium < -5 and has_inst_buy:
        signal = "🟢 折价大宗+机构买入 — 机构暗盘吸筹，关注后续走势"
    elif avg_premium > 3 and has_inst_sell:
        signal = "🔴 溢价大宗+机构卖出 — 机构暗盘出货，注意风险"
    elif avg_premium < -8:
        signal = "🔴 大幅折价大宗 — 警惕股东减持或利益输送"
    elif avg_premium > 5:
        signal = "🟡 大幅溢价大宗 — 可能存在市值管理或利益输送"
    elif has_inst_buy:
        signal = "🟢 机构席位参与买入 — 暗盘有主力关注"
    elif avg_premium < -3:
        signal = "🟡 持续折价 — 关注是否正常机构调仓"
    else:
        signal = "⚪ 大宗价格接近市价，无明显异常信号"

    lines.append("")
    lines.append(f"**暗盘信号**: {signal}")
    lines.append(f"**汇总**: {len(df_stock)}笔 | 均价差{avg_premium:+.1f}% | 总额{total_amt/1e8:.2f}亿")

    return "\n".join(lines)
