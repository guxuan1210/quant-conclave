"""Shared proxy-aware HTTP helpers for data fetching.

Configure via:
  - QUANTCONCLAVE_PROXY env var (e.g. ``http://127.0.0.1:7897``)
  - ``proxy`` key in config dict
  - Defaults to ``http://127.0.0.1:7897`` (Clash / V2Ray standard port)

Proxy is only applied per-request when ``use_proxy=True`` — callers for
sites that are NOT blocked (e.g. CLS, Eastmoney) should pass
``use_proxy=False`` to avoid unnecessary proxy routing.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import (
    ProxyHandler,
    Request,
    build_opener,
    urlopen,
)

logger = logging.getLogger(__name__)

_DEFAULT_PROXY = "http://127.0.0.1:7897"

# Cached proxy opener, recreated when the proxy URL changes.
_proxy_opener = None
_proxy_opener_url: str | None = None


def get_proxy_url(config: dict | None = None) -> str | None:
    """Resolve the proxy URL from config, env var, or default."""
    env_val = os.environ.get("QUANTCONCLAVE_PROXY", "").strip()
    if not env_val:
        env_val = os.environ.get("CAPITALRADAR_PROXY", "").strip()
    if env_val:
        return env_val
    if config:
        cfg_val = config.get("proxy", "")
        if cfg_val:
            return str(cfg_val)
    # Fall back to the global runtime config (set via set_config)
    try:
        from .config import get_config as _get_runtime_config
        runtime_val = _get_runtime_config().get("proxy", "")
        if runtime_val:
            return str(runtime_val)
    except Exception:
        pass
    return _DEFAULT_PROXY


def _get_proxy_opener(config: dict | None = None):
    """Return a cached proxy opener, recreating it if the URL changed."""
    global _proxy_opener, _proxy_opener_url
    proxy_url = get_proxy_url(config)
    if not proxy_url:
        return None
    if _proxy_opener is None or _proxy_opener_url != proxy_url:
        handler = ProxyHandler({"http": proxy_url, "https": proxy_url})
        _proxy_opener = build_opener(handler)
        _proxy_opener_url = proxy_url
        logger.info("Proxy opener created: %s", proxy_url)
    return _proxy_opener


def fetch_json(
    url: str,
    timeout: float = 10.0,
    config: dict | None = None,
    headers: dict | None = None,
    method: str = "GET",
    body: dict | None = None,
    use_proxy: bool = True,
) -> tuple[dict | list | None, str | None]:
    """Fetch JSON from a URL.

    When ``use_proxy=True`` (default), routes through the configured proxy.
    Set to False for domestic sites that are not blocked.

    Returns ``(parsed_data, error_message)`` — one of the two is always None.
    """
    try:
        req_headers = {"User-Agent": "quantconclave/0.2", "Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        data_bytes = None
        if body is not None:
            data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = Request(url, data=data_bytes, headers=req_headers, method=method)

        if use_proxy:
            opener = _get_proxy_opener(config)
            if opener is not None:
                with opener.open(req, timeout=timeout) as resp:
                    return json.loads(resp.read()), None

        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
