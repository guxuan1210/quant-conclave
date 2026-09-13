"""Backtest Agent chat handler (SSE streaming)."""
from __future__ import annotations
import json, logging
from datetime import datetime
from typing import Generator, Optional, Annotated
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from web.results_store import get_chat_messages, save_chat_message
from quantconclave.backtest.templates import list_templates, get_template
from quantconclave.backtest.strategies import get_strategy_class, SIGNAL_MAP
from quantconclave.backtest.engine import run_backtest
from quantconclave.backtest.report import compute_performance
from quantconclave.strategy.generator import compile_signal, StrategyGenerationError

logger = logging.getLogger(__name__)


def build_backtest_system_prompt(config: dict, lang: str = "Chinese") -> str:
    today = datetime.now()
    tmpl_list = "\n".join(f"  - {t['name']} ({t['id']}): {t['description']}" for t in list_templates())
    return (
        f"You are the QuantConclave Backtesting Agent, specialized in running and "
        f"analyzing trading strategy backtests with a fast vectorized engine.\n\n"
        f"**Current date**: {today.strftime('%Y-%m-%d')}\n\n"
        f"## Available Templates\n{tmpl_list}\n\n"
        f"## Workflow\n"
        f"1. Ask the user which strategy, ticker, and date range\n"
        f"2. Call run_backtest_tool with the specified parameters\n"
        f"3. Explain the results: Sharpe, drawdown, win rate, trade log\n"
        f"4. Offer to tweak parameters and re-run, or compare with another strategy\n"
        f"## Rules\n- Always confirm parameters before running\n- Never guarantee future performance\n"
        f"- Write in {lang}"
    )


def build_backtest_tools(config: dict, llm):

    @tool
    def list_templates_tool() -> str:
        """List all available backtesting strategy templates."""
        return "\n".join(f"- {t['name']} ({t['id']}): {t['description']}" for t in list_templates())

    @tool
    def run_backtest_tool(
        ticker: Annotated[str, "Stock ticker, e.g. 601127.SH"],
        strategy_id: Annotated[str, "Template ID, e.g. ma_cross"] = "ma_cross",
        start_date: Annotated[str, "Start date YYYY-MM-DD"] = "",
        end_date: Annotated[str, "End date YYYY-MM-DD"] = "",
        strategy_code: Annotated[str, "Optional vectorized signal code defining custom_signal(df, **params) -> pd.Series"] = "",
        **parameters,
    ) -> str:
        """Run a backtest for a template strategy on a ticker.

        Pass template parameters as extra keyword arguments (e.g. fast_period=10,
        slow_period=30 for ma_cross; period/oversold/overbought for rsi).
        Alternatively pass strategy_code (a vectorized custom_signal function) to
        backtest a custom strategy.
        """
        if not start_date:
            start_date = "2025-01-01"
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        # langchain's @tool folds the keyword `parameters` itself into **parameters
        parameters.pop("parameters", None)
        try:
            if strategy_code:
                strategy = compile_signal(strategy_code)
                strategy_name = "custom"
            else:
                strategy = get_strategy_class(strategy_id)
                strategy_name = strategy_id
                # Fill template defaults for any parameters the LLM did not pass
                tmpl = get_template(strategy_id)
                if tmpl:
                    for p in tmpl.get("parameters", []):
                        key = p.get("key")
                        if key and key not in parameters:
                            parameters[key] = p.get("default")
            result = run_backtest(ticker, strategy, parameters, start_date, end_date)
            report = compute_performance(result)
            return f"Strategy: {strategy_name}\n{report}"
        except (ValueError, StrategyGenerationError) as e:
            return f"Backtest failed: {e}"

    return [list_templates_tool, run_backtest_tool]


def create_backtest_agent(config, lang=None):
    from quantconclave.llm_clients import create_llm_client, resolve_role_llm
    provider, model, _ = resolve_role_llm(config, "deep")
    client = create_llm_client(provider=provider, model=model, base_url=config.get("backend_url"), timeout=120)
    llm = client.get_llm()
    tools = build_backtest_tools(config, llm)
    llm_with_tools = llm.bind_tools(tools)
    system_prompt = build_backtest_system_prompt(config, lang or config.get("output_language", "Chinese"))
    return llm_with_tools, tools, system_prompt, llm


def _sse_event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_backtest_chat(thread_id, question, config, lang=None):
    try:
        llm_with_tools, tools, system_prompt, llm = create_backtest_agent(config, lang)
    except Exception as e:
        yield _sse_event("chat-error", {"message": str(e)})
        return
    save_chat_message(config, thread_id, "user", question)
    yield _sse_event("chat-stream-start", {"thread_id": thread_id})
    history_msgs = get_chat_messages(config, thread_id) or []
    messages = [SystemMessage(content=system_prompt)]
    for h in history_msgs:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))
    tool_map = {t.name: t for t in tools}
    for iteration in range(10):
        response = llm_with_tools.invoke(messages)
        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                func = tool_map.get(tc.get("name", ""))
                result_str = str(func.invoke(tc.get("args", {})))[:8000] if func else "Tool not found"
                yield _sse_event("chat-tool-result", {"tool_name": tc.get("name"), "result_snippet": result_str[:500]})
                messages.append(ToolMessage(content=result_str, tool_call_id=tc.get("id", "")))
        else:
            final_text = response.content if hasattr(response, "content") else str(response)
            save_chat_message(config, thread_id, "assistant", final_text)
            yield _sse_event("chat-done", {"full_response": final_text})
            return
    yield _sse_event("chat-error", {"message": "Max iterations reached"})
