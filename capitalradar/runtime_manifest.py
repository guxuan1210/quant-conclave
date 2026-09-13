"""Runtime Manifest — the single source of truth for runtime facts.

Ports, hosts, and service identities live here so the launcher
(``run_web.py``), the chart server (``chart_app.py``), the dashboard
config endpoint, and the README-consistency test all read the same
numbers instead of drifting apart.

Values are overridable via ``CAPITALRADAR_WEB_MAIN_PORT`` /
``CAPITALRADAR_WEB_CHART_PORT`` (also settable in ``.env`` — dotenv loads
it into ``os.environ`` at package import time).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

HOST = "127.0.0.1"

_MAIN_PORT_DEFAULT = 8003
_CHART_PORT_DEFAULT = 8005


@dataclass(frozen=True)
class Service:
    """One runtime service (host + port + human label)."""

    key: str
    name: str
    port: int
    description: str

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"


def _port_from_env(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _services() -> dict[str, Service]:
    return {
        "main": Service(
            key="main",
            name="CapitalRadar Dashboard",
            port=_port_from_env("CAPITALRADAR_WEB_MAIN_PORT", _MAIN_PORT_DEFAULT),
            description="主面板：深度分析、选股、预测、策略、回测、顾问、历史复盘、校准",
        ),
        "chart": Service(
            key="chart",
            name="CapitalRadar Chart",
            port=_port_from_env("CAPITALRADAR_WEB_CHART_PORT", _CHART_PORT_DEFAULT),
            description="K 线图：Lightweight Charts，含 MA/MACD/RSI 指标，日/周/月",
        ),
    }


def get_service(key: str) -> Service:
    return _services()[key]


def main_port() -> int:
    return get_service("main").port


def chart_port() -> int:
    return get_service("chart").port


def main_url() -> str:
    return get_service("main").url


def chart_url() -> str:
    return get_service("chart").url


def render_readme_table() -> str:
    """Render the README 'Web 仪表盘' service table straight from the manifest."""
    lines = ["| 地址 | 功能 |", "|------|------|"]
    for svc in _services().values():
        lines.append(f"| `{svc.url}` | {svc.description} |")
    return "\n".join(lines)


def as_dict() -> dict:
    """Serializable manifest for the dashboard config/runtime endpoint."""
    return {key: {"name": s.name, "url": s.url, "port": s.port,
                  "description": s.description}
            for key, s in _services().items()}
