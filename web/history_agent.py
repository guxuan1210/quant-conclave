"""History Agent SSE streaming endpoint.

Provides a FastAPI router for the History Agent — a chat interface that
analyzes selected historical analysis records and extracts experiences.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history-agent", tags=["history-agent"])

_thread_sessions: dict[str, dict] = {}  # thread_id -> session state


class HistoryAgentChatRequest(BaseModel):
    thread_id: str = ""
    question: str
    run_ids: list[str] = []


def _sse_event(name: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


def _search_analyses(config: dict, run_ids: list[str] = None, ticker: str = None, limit: int = 50):
    """Search analysis records from result_runs table."""
    from web.results_store import _get_conn
    conn = _get_conn(config)
    if run_ids:
        placeholders = ",".join("?" * len(run_ids))
        rows = conn.execute(
            f"SELECT * FROM result_runs WHERE run_id IN ({placeholders}) ORDER BY date DESC",
            run_ids,
        ).fetchall()
    elif ticker:
        rows = conn.execute(
            "SELECT * FROM result_runs WHERE ticker LIKE ? ORDER BY date DESC LIMIT ?",
            (f"%{ticker}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM result_runs ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_experience_history(config: dict) -> list[dict]:
    """Get existing experiences for dedup context."""
    from capitalradar.advisory.experience_store import list_experiences
    try:
        all_exp = list_experiences()
        return all_exp[:30]
    except Exception:
        return []


async def stream_history_agent_chat(
    config: dict,
    thread_id: str,
    question: str,
    run_ids: list[str] = None,
    lang: str = "Chinese",
):
    """SSE generator for History Agent chat."""
    from capitalradar.llm_clients import create_llm_client, resolve_role_llm
    from capitalradar.advisory.history_agent import (
        build_history_agent_prompt,
        save_experiences_from_response,
        build_history_agent_tools,
    )
    from capitalradar.default_config import DEFAULT_CONFIG
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

    cfg = config or DEFAULT_CONFIG

    yield _sse_event("ha-start", {"thread_id": thread_id, "run_ids": run_ids or []})

    # Load analysis states
    records = _search_analyses(cfg, run_ids=run_ids)
    if not records:
        # Fallback: load all recent records
        records = _search_analyses(cfg, limit=20)

    states = []
    from web.results_store import load_full_state
    for r in records:
        rid = r.get("run_id", "")
        if not rid:
            continue
        try:
            s = load_full_state(cfg, rid)
            if s:
                s["ticker"] = r.get("ticker", "")
                states.append(s)
        except Exception:
            pass

    if not states:
        yield _sse_event("ha-error", {"message": "No analysis records found for the selected run IDs."})
        return

    # Build system prompt
    exp_history = _get_experience_history(cfg)
    system_prompt = build_history_agent_prompt(
        states, cfg, lang=lang, question=question, experience_history=exp_history,
    )

    # Create LLM
    provider, deep_model, _ = resolve_role_llm(cfg, "deep")
    try:
        client = create_llm_client(
            provider=provider,
            model=deep_model,
            base_url=cfg.get("backend_url"),
            timeout=120,
        )
        llm = client.get_llm()
    except Exception as e:
        yield _sse_event("ha-error", {"message": f"LLM init error: {e}"})
        return

    tools = build_history_agent_tools(cfg)
    llm_with_tools = llm.bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=question)]

    # Agent loop
    tool_records = []
    max_iter = 15
    for iteration in range(max_iter):
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            yield _sse_event("ha-error", {"message": f"LLM error: {e}"})
            return

        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})
                yield _sse_event("ha-tool", {"tool_name": tool_name, "args": {k: str(v)[:200] for k, v in tool_args.items()}})
                func = tool_map.get(tool_name)
                result_str = str(func.invoke(tool_args))[:3000] if func else f"Tool {tool_name} not found"
                yield _sse_event("ha-tool-result", {"tool_name": tool_name, "snippet": result_str[:300]})
                tool_records.append({"name": tool_name, "result": result_str[:500]})
                messages.append(ToolMessage(content=result_str, tool_call_id=tc.get("id", "")))
        else:
            final_text = response.content if hasattr(response, "content") else str(response)

            # Save extracted experiences
            saved_exps = save_experiences_from_response(final_text, cfg, run_ids)

            yield _sse_event("ha-done", {
                "full_response": final_text,
                "experiences": saved_exps,
                "records_analyzed": len(states),
                "tool_calls": tool_records,
            })
            return

    yield _sse_event("ha-error", {"message": "Max iterations reached without final answer."})


@router.get("/stream")
async def stream_chat(
    thread_id: str = Query(...),
    question: str = Query(...),
    run_ids: str = Query(""),
):
    """SSE streaming endpoint for History Agent chat."""
    from capitalradar.default_config import DEFAULT_CONFIG as config
    lang = config.get("output_language", "Chinese")

    # Parse run_ids from comma-separated string
    ids = [r.strip() for r in run_ids.split(",") if r.strip()] if run_ids else []

    return StreamingResponse(
        stream_history_agent_chat(config, thread_id, question, ids, lang),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/search")
async def search_analyses(
    ticker: str = Query(""),
    limit: int = Query(50),
):
    """Search analysis records for selection."""
    from capitalradar.default_config import DEFAULT_CONFIG as config
    records = _search_analyses(config, ticker=ticker, limit=limit)
    return records


@router.get("/all")
async def list_analyses(limit: int = Query(50)):
    """List all analysis records."""
    from capitalradar.default_config import DEFAULT_CONFIG as config
    records = _search_analyses(config, limit=limit)
    return records
