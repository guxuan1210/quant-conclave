"""Unified 妙想(MX) API client for QuantConclave.

Wraps the MX fintech-skill endpoints (``mkapi2.dfcfs.com/finskillshub/api/claw``)
so new integrations share ONE code path for apikey loading, request posting,
error normalization, and structured table extraction — instead of a fourth
copy of the bare ``requests.post`` loop (previously duplicated in
``eastmoney_tools``, ``ai_pick_agent`` and ``app.py`` self-select).

Endpoints
---------
- ``.../query``          — natural language → data tables (行情/资金流向/大宗/财务)
- ``.../news-search``    — natural language → news / research / announcements
- ``.../self-select/get`` — the user's 东方财富 watchlist

Failure contract
----------------
Every public call raises :class:`MxError` on missing key / HTTP error /
non-JSON / API error-code. Callers catch ``MxError`` and degrade to the next
vendor or a cache, matching the ``"# SKIP_VENDOR"`` fallback convention used
by :mod:`quantconclave.dataflows.interface`.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_API_BASE = "https://mkapi2.dfcfs.com/finskillshub/api/claw"
DEFAULT_TIMEOUT = 30


class MxError(Exception):
    """Raised when the MX API is unavailable or returns an error code."""


def get_api_key() -> Optional[str]:
    """Return the MX_APIKEY env var, or None (callers must handle the absence)."""
    key = os.environ.get("MX_APIKEY", "").strip()
    return key or None


def _post(path: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """POST JSON to an MX endpoint and normalize errors into MxError."""
    import requests

    key = get_api_key()
    if not key:
        raise MxError("MX_APIKEY not set")
    url = f"{_API_BASE}/{path}"
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "apikey": key},
            json=payload,
            timeout=timeout,
        )
    except Exception as e:  # DNS / timeout / connection reset
        raise MxError(f"MX request to {path} failed: {e}") from e
    try:
        data = resp.json()
    except Exception:
        raise MxError(f"MX response not JSON (status {resp.status_code})") from None
    if resp.status_code != 200 or data.get("code", 0) != 0:
        raise MxError(
            f"MX API error ({data.get('code', '?')}): {data.get('msg', 'unknown')}"
        )
    return data


def query(tool_query: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run a natural-language data query against the MX 行情/数据 engine."""
    return _post("query", {"toolQuery": tool_query}, timeout=timeout)


def news_search(query_text: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Search 妙想资讯 (news / research reports / announcements) by natural language."""
    return _post("news-search", {"query": query_text}, timeout=timeout)


def self_select_get(timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch the user's 东方财富 self-selected watchlist (structured list)."""
    return _post("self-select/get", {"query": "查询我的自选股"}, timeout=timeout)


# ── Structured extraction helpers ────────────────────────────────────────

def _walk(value: Any, path: str = "root", key_name: str = "dataTableDTOList"):
    """Yield every occurrence of ``key_name`` in the nested JSON tree."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key_name:
                yield (f"{path}.{k}", v)
            else:
                yield from _walk(v, f"{path}.{k}", key_name)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]", key_name)


def find_dto_list(data: dict) -> List[dict]:
    """Return the first ``dataTableDTOList`` found in the response (or [])."""
    for _path, dto_list in _walk(data):
        if isinstance(dto_list, list) and dto_list:
            return dto_list
    return []


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    s = str(value)
    return s if s else "-"


def _parse_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def parse_cn_amount(value: Any) -> Optional[float]:
    """Parse a CN currency amount, normalizing to 万元.

    Handles the units MX returns: ``7258万元`` → 7258.0, ``5.853亿元`` →
    58530.0, ``-7234万元`` → -7234.0, plain ``0.004`` → 0.004. Used by scoring
    engines that must compare fields across unit boundaries.
    """
    import re as _re

    s = str(value).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _re.search(r"([-+]?[\d.]+)\s*(万亿|亿|万|千)?", s)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2) or ""
    mult = {"万亿": 1e8, "亿": 1e4, "万": 1.0, "千": 0.1}.get(unit, 1.0)
    return num * mult


def render_data_tables(data: dict, max_blocks: int = 4, max_rows: int = 12) -> str:
    """Render MX data-table DTOs as readable markdown for LLM consumption.

    Handles both shapes the API returns:
    - **snapshot** DTOs (资金流/行情): one timestamp, indicator-coded keys →
      rendered as ``- 中文名: 值`` lines (nameMap decodes the codes).
    - **listing** DTOs (大宗交易): many rows, literal Chinese columns →
      rendered as a real markdown table with dates as the first column.
    """
    dto_list = find_dto_list(data)
    if not dto_list:
        return ""
    lines: List[str] = []
    for block in dto_list[:max_blocks]:
        if not isinstance(block, dict):
            continue
        title = block.get("title") or block.get("frontendTitle") or ""
        if title:
            lines.append(f"## {title}")
        tbl = block.get("table") or {}
        nm = block.get("nameMap") or {}
        if not tbl:
            continue
        head = tbl.get("headName") or []
        keys = [k for k in tbl.keys() if k != "headName"]
        if not keys:
            continue

        def _name(k: str) -> str:
            v = nm.get(k)
            return v if v and v != "?" else k

        if len(head) == 1 and len(keys) > 1:
            # Snapshot DTO: single timestamp → key/value lines.
            for k in keys:
                arr = tbl.get(k) or []
                lines.append(f"- {_name(k)}: {_cell(arr[0] if arr else None)}")
        else:
            # Listing / time-series DTO: real table.
            n_rows = len(head)
            for arr in (tbl.get(k) or [] for k in keys):
                if len(arr) > n_rows:
                    n_rows = len(arr)
            n_rows = min(n_rows, max_rows)
            lines.append("| 日期 | " + " | ".join(_name(k) for k in keys) + " |")
            lines.append("|" + "---|" * (len(keys) + 1))
            for i in range(n_rows):
                row = [_cell(head[i]) if i < len(head) else "-"]
                for k in keys:
                    arr = tbl.get(k) or []
                    row.append(_cell(arr[i]) if i < len(arr) else "-")
                lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    return "\n".join(lines).strip()


def _compact_json(data: dict) -> str:
    try:
        return json.dumps(data.get("data", data), ensure_ascii=False, indent=2)[:8000]
    except Exception:
        return json.dumps(data, ensure_ascii=False)[:8000]


def query_text(tool_query: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run an MX query and return readable text (tables if parseable).

    On failure returns an error string rather than raising, so @tool wrappers
    can pass the message straight back to the LLM.
    """
    try:
        data = query(tool_query, timeout=timeout)
    except MxError as e:
        return f"MX query failed: {e}"
    rendered = render_data_tables(data)
    return rendered if rendered else _compact_json(data)


def extract_snapshot_metrics(data: dict) -> Dict[str, Any]:
    """Collect single-timestamp DTOs as {中文指标名: 数值|原文}.

    Used by scoring engines (e.g. smart_money_score) that need real numbers,
    not rendered text. Callers look values up by substring match on the key,
    e.g. ``next((v for k, v in m.items() if "超大单净流入" in k), None)``.
    """
    out: Dict[str, Any] = {}
    for block in find_dto_list(data):
        if not isinstance(block, dict):
            continue
        tbl = block.get("table") or {}
        nm = block.get("nameMap") or {}
        head = tbl.get("headName") or []
        if len(head) != 1:
            continue  # only snapshots carry one-timestamp indicator values
        for k, arr in tbl.items():
            if k == "headName" or not isinstance(arr, list) or not arr:
                continue
            name = nm.get(k) or k
            if name == "?":
                continue
            val = arr[0]
            f = parse_cn_amount(val)
            out[name] = f if f is not None else val
    return out


def extract_listing_rows(data: dict, max_rows: int = 20) -> List[Dict[str, Any]]:
    """Extract multi-row listing DTOs (大宗交易 etc.) as list[dict] rows."""
    rows: List[Dict[str, Any]] = []
    for block in find_dto_list(data):
        if not isinstance(block, dict):
            continue
        tbl = block.get("table") or {}
        nm = block.get("nameMap") or {}
        head = tbl.get("headName") or []
        keys = [k for k in tbl.keys() if k != "headName"]
        if len(head) < 2:
            continue
        for i, _d in enumerate(head[:max_rows]):
            row: Dict[str, Any] = {"日期": str(_d) if _d else "-"}
            for k in keys:
                name = nm.get(k) or k
                if name == "?":
                    name = k
                arr = tbl.get(k) or []
                row[name] = arr[i] if i < len(arr) else None
            rows.append(row)
    return rows
