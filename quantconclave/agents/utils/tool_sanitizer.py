"""Tool argument sanitizer — fixes LLM parameter format errors before Tushare calls.

Small local LLMs (Ollama) often pass wrong formats for ticker, dates, etc.
This module provides a single validation layer that normalizes arguments
regardless of which LLM generated them.
"""

import re
from datetime import datetime, timedelta


def sanitize_tool_args(tool_name: str, args: dict) -> dict:
    """Normalize tool call arguments for Tushare compatibility.

    Returns a modified copy of args with:
    - Ticker: bare codes auto-suffixed (.SH/.SZ)
    - Dates: "YYYY-MM-DD" → "YYYYMMDD", filled with 60-day range if missing
    - Proxies start_date/end_date from 'days' param if present
    """
    args = dict(args)  # shallow copy

    # ── Ticker normalization ──
    for key in ("ticker", "symbol", "ts_code"):
        if key in args and args[key]:
            t = str(args[key]).strip().upper()
            if not t:
                continue
            # Bare 6-digit → add suffix
            if t.isdigit() and len(t) == 6:
                if t.startswith(("6", "9")):
                    args[key] = f"{t}.SH"
                elif t.startswith(("0", "3")):
                    args[key] = f"{t}.SZ"
                elif t.startswith(("4", "8")):
                    args[key] = f"{t}.BJ"
            # .SS suffix → .SH
            elif t.endswith(".SS"):
                args[key] = t[:-3] + ".SH"
            elif t.endswith(".SHE"):
                args[key] = t[:-4] + ".SZ"
            elif t.endswith(".SZE"):
                args[key] = t[:-4] + ".SZ"

    # ── Date normalization ──
    today = datetime.now().strftime("%Y%m%d")
    sixty_ago = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

    for key in ("start_date", "end_date", "trade_date"):
        if key in args and args[key]:
            d = str(args[key]).strip()
            # "2026-07-02" → "20260702"
            d = d.replace("-", "").replace("/", "")
            if len(d) == 8 and d.isdigit():
                args[key] = d
            elif not d.isdigit():
                args[key] = today  # unparseable → use today

    # Fill missing date range
    if tool_name in ("get_money_flow", "get_margin_trading", "get_market_flow"):
        if "start_date" not in args or not args.get("start_date"):
            args["start_date"] = sixty_ago
        if "end_date" not in args or not args.get("end_date"):
            args["end_date"] = today

    # Convert 'days' param to start_date if present
    if "days" in args:
        try:
            ndays = int(args["days"])
            args["start_date"] = (datetime.now() - timedelta(days=ndays)).strftime("%Y%m%d")
            args["end_date"] = today
        except (ValueError, TypeError):
            pass
        del args["days"]

    return args
