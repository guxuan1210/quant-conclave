"""Parallel analyst runner — runs all non-anchor analysts in a ThreadPool.
"""
from __future__ import annotations
import concurrent.futures
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from quantconclave.agents import (
    create_market_analyst, create_sentiment_analyst, create_news_analyst,
    create_fundamentals_analyst, create_competitor_analyst, create_partner_analyst,
)
from quantconclave.agents.utils.agent_utils import (
    get_stock_data, get_indicators, get_fundamentals, get_balance_sheet,
    get_cashflow, get_income_statement, get_news, get_insider_transactions,
    get_global_news, get_intraday_data, get_realtime_quote,
)
from quantconclave.agents.utils.quant_tools import (
    get_stochastic, get_williams_r, get_roc,
    get_trendlines, get_chart_pattern,
    ADVANCED_INDICATOR_TOOLS,
)
from quantconclave.agents.utils.web_search_tool import web_search
from quantconclave.dataflows.config import get_config
logger = logging.getLogger(__name__)
# Module-level thread-safe queue for progress events from parallel analyst threads
# Format: each event is {"analyst": str, "status": "in_progress"|"complete"|"timeout", "elapsed_ms": int}
import collections
_progress_queue: "collections.deque" = collections.deque()

def set_progress_callback():
    """Return a callback suitable for _run_analyst_loop's on_progress parameter."""
    def _on_progress(analyst_key, status, elapsed_ms):
        _progress_queue.append({
            "analyst": analyst_key,
            "status": status,
            "elapsed_ms": elapsed_ms,
        })
    return _on_progress

def drain_progress_events():
    """Drain and return all queued progress events (thread-safe)."""
    events = []
    while True:
        try:
            events.append(_progress_queue.popleft())
        except IndexError:
            break
    return events

ANALYST_TOOLS = {
    "market": [get_stock_data, get_indicators, get_realtime_quote, get_intraday_data,
               get_stochastic, get_williams_r, get_roc,
               get_trendlines, get_chart_pattern,
               *ADVANCED_INDICATOR_TOOLS, web_search],
    "social": [],
    "news": [get_news, get_global_news, get_insider_transactions, web_search],
    "fundamentals": [get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement, web_search],
    "competitor": [get_news, get_global_news, web_search],
    "partner": [get_news, get_global_news, web_search],
}
ANALYST_FACTORIES = {
    "market": create_market_analyst,
    "social": create_sentiment_analyst,
    "news": create_news_analyst,
    "fundamentals": create_fundamentals_analyst,
    "competitor": create_competitor_analyst,
    "partner": create_partner_analyst,
}
from quantconclave.catalog import ANALYST_ROLES
ANALYST_REPORT_KEYS = {
    key: role.report_key for key, role in ANALYST_ROLES.items() if key != "capital_flow"
}
# ── Localized analyst names for fallback error reports ──
_LOCALIZED = {
    "market": {"en": "Market Analyst", "zh": "市场分析师"},
    "social": {"en": "Sentiment Analyst", "zh": "情绪分析师"},
    "news": {"en": "News Analyst", "zh": "新闻分析师"},
    "fundamentals": {"en": "Fundamentals Analyst", "zh": "基本面分析师"},
    "competitor": {"en": "Competitor Analyst", "zh": "竞品分析师"},
    "partner": {"en": "Partner Analyst", "zh": "合作伙伴分析师"},
}
_ERROR_MSGS = {
    "node_failed": {"en": "Analysis node failed", "zh": "分析节点运行失败"},
    "analysis_failed": {"en": "Analysis failed", "zh": "分析过程出错"},
    "timed_out": {"en": "Analysis timed out after", "zh": "分析超时，等待了"},
    "seconds": {"en": "s", "zh": "秒"},
}

def _localized_error(analyst_key: str, msg_key: str, **kwargs) -> str:
    """Return a localized fallback report header + error message."""
    from quantconclave.dataflows.config import get_config
    lang = get_config().get("output_language", "Chinese")
    is_zh = lang.lower().startswith("zh") or "中文" in lang or "chinese" in lang.lower()
    locale = "zh" if is_zh else "en"
    name = _LOCALIZED.get(analyst_key, {}).get(locale, analyst_key.title())
    header = f"{name} 报告" if is_zh else f"{name} Report"
    msg = _ERROR_MSGS.get(msg_key, {}).get(locale, msg_key)
    detail = ""
    if "exc" in kwargs:
        detail = f" - {kwargs['exc']}"
    elif "timeout" in kwargs:
        unit = _ERROR_MSGS["seconds"].get(locale, "s")
        detail = f" {kwargs['timeout']}{unit}"
    return f"## {header}\n\n**{'错误' if is_zh else 'Error'}**: {msg}{detail}\n"
def _record_timing(analyst_key, t_start, on_progress):
    """Record wall-clock timing and notify progress callback. Returns wall_ms."""
    wall_ms = int((time.monotonic() - t_start) * 1000)
    if on_progress:
        try:
            on_progress(analyst_key, "complete", wall_ms)
        except Exception:
            pass
    return wall_ms


def _run_analyst_loop(analyst_key, llm, state_snapshot, max_tool_calls, on_progress=None):
    """Run one analyst's full tool-calling loop; returns (analyst_key, report, wall_ms)."""
    t_start = time.monotonic()
    if on_progress:
        try:
            on_progress(analyst_key, "in_progress", 0)
        except Exception:
            pass

    factory = ANALYST_FACTORIES[analyst_key]
    tools = ANALYST_TOOLS[analyst_key]
    report_key = ANALYST_REPORT_KEYS[analyst_key]
    analyst_node = factory(llm)
    messages = []
    if not tools:
        local_state = {**state_snapshot, "messages": messages}
        try:
            result = analyst_node(local_state)
        except Exception as e:
            logger.error("Parallel analyst %s node call failed: %s", analyst_key, e, exc_info=True)
            return analyst_key, _localized_error(analyst_key, "node_failed", exc=e), _record_timing(analyst_key, t_start, on_progress)
        report = result.get(report_key, "") or str(result.get("messages", [{}])[-1].content)
        return analyst_key, report, _record_timing(analyst_key, t_start, on_progress)

    tool_node = ToolNode(tools, handle_tool_errors=True)
    for round_num in range(max_tool_calls + 1):
        local_state = {**state_snapshot, "messages": messages}
        result = analyst_node(local_state)
        last_msg = result["messages"][-1]
        if not last_msg.tool_calls:
            report = result.get(report_key, "") or str(last_msg.content)
            logger.info("Parallel analyst %s finished in %d round(s)", analyst_key, round_num)
            return analyst_key, report, _record_timing(analyst_key, t_start, on_progress)
        messages.append(last_msg)
        tool_result = tool_node.invoke({"messages": [last_msg]})
        messages.extend(tool_result["messages"])

    logger.warning("Parallel analyst %s hit max_tool_calls=%d", analyst_key, max_tool_calls)
    force_state = {**state_snapshot, "messages": messages}
    force_result = analyst_node(force_state)
    report = force_result.get(report_key, "") or str(force_result.get("messages", [{}])[-1].content)
    return analyst_key, report, _record_timing(analyst_key, t_start, on_progress)

def create_parallel_analyst_runner(quick_thinking_llm, selected_analysts=None, on_progress=None):
    """Create the graph node that runs non-anchor analysts in parallel.
    
    Args:
        on_progress: Optional callback(analyst_key, status, elapsed_ms) called from threads.
    """
    if selected_analysts is None:
        selected_analysts = list(ANALYST_FACTORIES.keys())
    if on_progress is None:
        on_progress = set_progress_callback()
    analyst_keys = [k for k in selected_analysts if k in ANALYST_FACTORIES]
    max_calls = get_config().get("max_analyst_tool_calls", 6)
    def parallel_runner_node(state):
        snapshot = {
            "company_of_interest": state["company_of_interest"],
            "trade_date": state["trade_date"],
            "asset_type": state.get("asset_type", "stock"),
            "capital_flow_report": state.get("capital_flow_report", ""),
            "messages": [],
        }
        results = {}
        ANALYST_TIMEOUT = 180  # per-analyst timeout in seconds (Ollama needs more)
        PARALLEL_TIMEOUT = 900  # total timeout — local LLMs are slower
        max_workers = min(len(analyst_keys), 6)  # DeepSeek cloud handles high concurrency
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_run_analyst_loop, k, quick_thinking_llm, snapshot, max_calls, on_progress): k
                for k in analyst_keys
            }
            done, not_done = concurrent.futures.wait(
                future_map, timeout=PARALLEL_TIMEOUT,
                return_when=concurrent.futures.ALL_COMPLETED,
            )
            for future in done:
                key = future_map[future]
                try:
                    _, report, _ = future.result()
                    results[ANALYST_REPORT_KEYS[key]] = report
                except Exception as exc:
                    logger.error("Parallel analyst %s failed: %s", key, exc)
                    results[ANALYST_REPORT_KEYS[key]] = _localized_error(key, "analysis_failed", exc=exc)
            # Cancel any still-pending futures (hung analysts)
            for future in not_done:
                key = future_map[future]
                future.cancel()
                logger.warning("Parallel analyst %s timed out after %ds total, cancelled", key, PARALLEL_TIMEOUT)
                if on_progress:
                    try:
                        on_progress(key, "timeout", int(PARALLEL_TIMEOUT * 1000))
                    except Exception:
                        pass
                results[ANALYST_REPORT_KEYS[key]] = _localized_error(key, "timed_out", timeout=PARALLEL_TIMEOUT)
        results["messages"] = []
        return results
    return parallel_runner_node

__all__ = ["create_parallel_analyst_runner"]
