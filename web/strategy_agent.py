"""Strategy Agent chat handler (SSE streaming)."""
from __future__ import annotations
import json, logging
from datetime import datetime
from typing import Generator, Optional, Annotated
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from web.results_store import get_chat_messages, save_chat_message
from quantconclave.strategy.manager import create_strategy, list_strategies, get_strategy, delete_strategy, search_strategies, add_version
from quantconclave.strategy.generator import generate_strategy_code, StrategyGenerationError
from quantconclave.backtest.templates import list_templates

logger = logging.getLogger(__name__)


def build_strategy_system_prompt(config: dict, lang: str = "Chinese") -> str:
    today = datetime.now()
    return (
        f"You are the QuantConclave Strategy Agent, specialized in creating, managing, "
        f"and refining trading strategies.\n\n"
        f"**Current date**: {today.strftime('%Y-%m-%d')}\n\n"
        f"## Capabilities\n"
        f"- list_templates: Show built-in strategy templates\n"
        f"- generate_strategy: Create a new strategy from natural language description\n"
        f"- save_strategy: Save a strategy to your library\n"
        f"- load_strategy: View a saved strategy\n"
        f"- delete_strategy: Remove a strategy\n"
        f"- search_strategies: Search your strategy library\n"
        f"- add_version: Save a new version of an existing strategy\n\n"
        f"## Workflow\n"
        f"1. Ask the user what kind of strategy they want\n"
        f"2. Offer templates OR generate from description\n"
        f"3. Let the user preview/confirm before saving\n"
        f"4. Save to strategy library with `save_strategy`\n\n"
        f"- Write in {lang}"
    )


def build_strategy_tools(config, llm):

    @tool
    def list_templates_tool() -> str:
        """List available strategy templates."""
        return "\n".join(f"- {t['name']} ({t['id']}): {t['description']}" for t in list_templates())

    @tool
    def generate_strategy(description: str) -> str:
        """Generate a backtrader strategy from natural language. Returns the code."""
        try:
            code = generate_strategy_code(description, config)
            return f"Strategy code generated:\n\n```python\n{code}\n```"
        except StrategyGenerationError as e:
            return f"Generation failed: {e}"

    @tool
    def save_strategy(name: str, description: str, code: str, tags: str = "") -> str:
        """Save a strategy to your library. Returns the strategy ID."""
        sid = create_strategy(name, description, "custom_llm", "", {}, code, tags)
        return f"Strategy saved with ID #{sid}"

    @tool
    def load_strategy(strategy_id: int) -> str:
        """Load a saved strategy by ID."""
        s = get_strategy(strategy_id)
        if not s:
            return "Strategy not found"
        return f"#{s['id']} {s['name']}: {s['description']}\n\n```python\n{s['code']}\n```"

    @tool
    def search_strategies_tool(query: str) -> str:
        """Search saved strategies by name or description."""
        results = search_strategies(query)
        if not results:
            return "No strategies found"
        return "\n".join(f"#{s['id']} {s['name']} ({s['type']})" for s in results)

    return [list_templates_tool, generate_strategy, save_strategy, load_strategy, search_strategies_tool]


def create_strategy_agent(config, lang=None):
    from quantconclave.llm_clients import create_llm_client, resolve_role_llm
    provider, model, _ = resolve_role_llm(config, "deep")
    client = create_llm_client(provider=provider, model=model, base_url=config.get("backend_url"), timeout=120)
    llm = client.get_llm()
    tools = build_strategy_tools(config, llm)
    llm_with_tools = llm.bind_tools(tools)
    system_prompt = build_strategy_system_prompt(config, lang or config.get("output_language", "Chinese"))
    return llm_with_tools, tools, system_prompt, llm


def _sse_event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_strategy_chat(thread_id, question, config, lang=None):
    try:
        llm_with_tools, tools, system_prompt, llm = create_strategy_agent(config, lang)
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
