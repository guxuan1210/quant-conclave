"""Results History + History Chat HTTP adapter.

Deep module for the "History + Conversation" workflow: querying past analysis
runs and running multi-turn Portfolio Manager chat threads against them. The
heavy lifting lives in ``web.results_store`` (persistence) and
``web.history_chat`` (SSE generator); this module only maps HTTP requests onto
those callables.
"""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from capitalradar.default_config import DEFAULT_CONFIG
from web.results_store import (
    list_results,
    get_result,
    load_full_state,
    delete_result,
    get_comparison,
)
from web.history_chat import stream_history_chat

router = APIRouter(prefix="/api", tags=["results"])


class CreateThreadBody(BaseModel):
    run_ids: list[str] = []
    title: str | None = None


# ---- Results History ----

@router.get("/results")
def get_results(
    ticker: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    rating: str = Query(default=""),
    run_type: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    cfg = DEFAULT_CONFIG
    return list_results(
        cfg,
        ticker=ticker or None,
        date_from=date_from or None,
        date_to=date_to or None,
        rating=rating or None,
        run_type=run_type or None,
        limit=limit,
        offset=offset,
    )


@router.get("/results/tickers")
def get_tickers_list():
    """Return list of all tickers that have been analyzed."""
    from web.results_store import list_all_tickers
    return {"tickers": list_all_tickers(DEFAULT_CONFIG)}


@router.get("/results/{run_id}")
def get_result_meta(run_id: str):
    res = get_result(DEFAULT_CONFIG, run_id)
    if not res:
        raise HTTPException(404, "Result not found")
    return res


@router.get("/results/{run_id}/full")
def get_result_full(run_id: str):
    state = load_full_state(DEFAULT_CONFIG, run_id)
    if not state:
        raise HTTPException(404, "Full state not found")
    return state


@router.get("/results/compare")
def compare_results(ids: str = Query(default="")):
    run_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if len(run_ids) < 2:
        raise HTTPException(400, "Need at least 2 run IDs")
    if len(run_ids) > 3:
        raise HTTPException(400, "Maximum 3 runs for comparison")
    rows = get_comparison(DEFAULT_CONFIG, run_ids)
    if not rows:
        raise HTTPException(404, "No results found")
    return rows


@router.delete("/results/{run_id}")
def delete_result_entry(run_id: str):
    ok = delete_result(DEFAULT_CONFIG, run_id)
    if not ok:
        raise HTTPException(404, "Result not found")
    return {"acknowledged": True}


# ---- History Chat ----

@router.post("/results/{run_id}/chat/threads")
def create_chat_thread_endpoint(run_id: str, body: CreateThreadBody = CreateThreadBody()):
    """Create a new chat thread for a historical analysis."""
    from web.results_store import create_chat_thread, get_result
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")
    thread_id = create_chat_thread(DEFAULT_CONFIG, run_id)
    return {"thread_id": thread_id}


@router.get("/results/{run_id}/chat/threads")
def list_chat_threads_endpoint(run_id: str):
    """List all chat threads for a historical analysis."""
    from web.results_store import get_result, list_chat_threads
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")
    threads = list_chat_threads(DEFAULT_CONFIG, run_id)
    return threads


@router.delete("/results/{run_id}/chat/threads/{thread_id}")
def delete_chat_thread_endpoint(run_id: str, thread_id: str):
    """Delete a chat thread and all its messages."""
    from web.results_store import delete_chat_thread
    ok = delete_chat_thread(DEFAULT_CONFIG, thread_id)
    if not ok:
        raise HTTPException(404, "Thread not found")
    return {"acknowledged": True}


@router.get("/results/{run_id}/chat/threads/{thread_id}/messages")
def get_chat_messages_endpoint(run_id: str, thread_id: str):
    """Get all messages for a chat thread."""
    from web.results_store import get_chat_messages as get_msgs
    msgs = get_msgs(DEFAULT_CONFIG, thread_id)
    return msgs


@router.get("/results/{run_id}/chat/stream")
def history_chat_stream(
    run_id: str,
    thread_id: str = Query(min_length=1),
    question: str = Query(min_length=1),
):
    """SSE stream for a PM chat Q&A turn on a historical analysis."""
    from web.results_store import get_result
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")

    def event_stream():
        yield from stream_history_chat(thread_id, question, DEFAULT_CONFIG)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/results/{run_id}/chat/threads/{thread_id}/download")
def download_chat_thread(run_id: str, thread_id: str, format: str = Query(default="md")):
    """Download chat thread messages as markdown or JSON."""
    from fastapi.responses import Response
    from web.results_store import get_chat_messages as get_msgs, get_result
    import json as _json

    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Analysis run not found")

    msgs = get_msgs(DEFAULT_CONFIG, thread_id)
    ticker = meta.get("ticker", "unknown")
    date = meta.get("date", "")
    thread_title = ""
    for m in msgs:
        if m["role"] == "user":
            thread_title = m["content"][:40]
            break

    if format == "json":
        return Response(
            content=_json.dumps(msgs, ensure_ascii=False, indent=2, default=str),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="Chat_{ticker}_{date}_{thread_id}.json"'
            },
        )

    # Markdown format
    lines = []
    a = lines.append
    a(f"# Portfolio Manager Chat — {ticker} ({date})")
    a("")
    a(f"**Thread:** {thread_title or thread_id}")
    a("")
    a("---")
    a("")
    for m in msgs:
        role_label = "**You**" if m["role"] == "user" else "**Portfolio Manager**"
        a(role_label)
        a("")
        a(m["content"])
        a("")
        tool_calls = m.get("tool_calls", "")
        if tool_calls:
            try:
                tc_list = _json.loads(tool_calls) if isinstance(tool_calls, str) else tool_calls
                if tc_list:
                    a("<details>")
                    a("<summary>Tool calls</summary>")
                    a("")
                    for tc in tc_list:
                        a(f"- **{tc.get('tool_name', 'unknown')}**")
                        a(f"  Args: `{_json.dumps(tc.get('args', {}), ensure_ascii=False)[:300]}`")
                        result = str(tc.get("result_snippet", ""))[:300]
                        if result:
                            a(f"  Result: {result}")
                        a("")
                    a("</details>")
                    a("")
            except Exception:
                pass
        a("---")
        a("")
    a(f"*Chat exported from CapitalRadar*")

    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="Chat_{ticker}_{date}_{thread_id}.md"'
        },
    )
