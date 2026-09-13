"""Reusable web search tool — available to all agents."""

from langchain_core.tools import tool
from typing import Annotated


@tool
def web_search(
    query: Annotated[str, "Search query (e.g. stock news, company announcement, industry policy)"],
    max_results: Annotated[int, "Max results (default 5)"] = 5,
) -> str:
    """Search the web for current news, announcements, policies, or events.

    Uses DuckDuckGo (free, no API key). Returns title, snippet, and URL
    for each result.

    Use this to get the latest information that may not be in your training
    data — company announcements, policy changes, market events, etc.
    """
    results = []
    try:
        from duckduckgo_search import DDGS
        for r in DDGS().text(query, max_results=max_results):
            results.append({
                "title": r.get("title", "")[:120],
                "url": r.get("href", "")[:200],
                "snippet": r.get("body", "")[:300],
            })
    except Exception:
        pass

    if not results:
        return "[Web search unavailable. Use your existing knowledge.]"

    lines = [f"# Web Search: {query}", ""]
    for i, r in enumerate(results[:max_results], 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['snippet']}")
        lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines)
