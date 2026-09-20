"""Regression tests for the parallel analyst runner's ToolNode invocation.

langgraph >= 1.0 requires a ``Runtime`` to be present in the config when a
``ToolNode`` is invoked outside a compiled graph (the parallel runner calls it
directly from a worker thread). These tests pin that path so a future langgraph
upgrade cannot silently break the non-anchor analysts again.
"""

from __future__ import annotations

import pytest

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from quantconclave.graph.parallel_analyst_runner import _tool_runtime_config


@tool
def _add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@pytest.mark.unit
def test_tool_runtime_config_shape():
    cfg = _tool_runtime_config()
    if cfg is None:
        pytest.skip("langgraph Runtime API unavailable (pre-1.0)")
    assert "configurable" in cfg


@pytest.mark.unit
def test_tool_node_invoke_with_runtime_config():
    node = ToolNode([_add], handle_tool_errors=True)
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "_add", "args": {"a": 2, "b": 3}, "id": "1", "type": "tool_call"},
        ],
    )
    out = node.invoke({"messages": [msg]}, config=_tool_runtime_config())
    assert str(out["messages"][-1].content) == "5"
