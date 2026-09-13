"""Shared Chrome DevTools Protocol scraping utilities for autocli data vendors.

All vendors use these helpers for the Tier-2 fallback: when public HTTP APIs
fail or return incomplete data, autocli reuses the user's Chrome login session
to scrape the target page.
"""

import json
import time
import urllib.parse
import urllib.request

from .config import get_config


def _get_autocli_config():
    return get_config().get("autocli", {})


def is_chrome_available(debug_port=None):
    """Check if Chrome is running with remote debugging enabled.

    Returns True if Chrome's DevTools HTTP endpoint responds on the given port.
    """
    if debug_port is None:
        debug_port = _get_autocli_config().get("chrome_debug_port", 9222)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{debug_port}/json/version",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read().decode("utf-8"))
        return "Browser" in data.get("Browser", "")
    except Exception:
        return False


def fetch_via_chrome(url, selectors_map, debug_port=None, timeout=None):
    """Scrape page data via Chrome DevTools Protocol.

    Uses Chrome's /json/page API to navigate and evaluate JavaScript
    selectors against the target page. Returns structured data.

    Args:
        url: Target page URL.
        selectors_map: {"field_name": "css_selector"} dict.
        debug_port: Chrome debug port (default from config).
        timeout: Request timeout in seconds (default from config).

    Returns:
        dict with extracted text values. Empty strings on failure.
    """
    config = _get_autocli_config()
    if debug_port is None:
        debug_port = config.get("chrome_debug_port", 9222)
    if timeout is None:
        timeout = config.get("timeout_seconds", 20)

    if not is_chrome_available(debug_port):
        return {}

    result = {}
    try:
        # Open a new tab and navigate
        open_url = (
            f"http://127.0.0.1:{debug_port}/json/new?{urllib.parse.urlencode({'url': url})}"
        )
        tab_req = urllib.request.Request(open_url, headers={"User-Agent": "Mozilla/5.0"})
        tab_resp = urllib.request.urlopen(tab_req, timeout=timeout)
        tab_data = json.loads(tab_resp.read().decode("utf-8"))
        ws_url = tab_data.get("webSocketDebuggerUrl", "")

        if not ws_url:
            return {}

        # Evaluate each selector via CDP Runtime.evaluate over HTTP
        for field_name, css_selector in selectors_map.items():
            try:
                js_expr = json.dumps(
                    f"Array.from(document.querySelectorAll({json.dumps(css_selector)})).map(el => el.textContent.trim()).join('|||')"
                )
                cdp_payload = json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": js_expr,
                        "returnByValue": True,
                    },
                })

                eval_req = urllib.request.Request(
                    f"http://127.0.0.1:{debug_port}/json/send?"
                    + urllib.parse.urlencode({"ws": ws_url}),
                    data=cdp_payload.encode("utf-8"),
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/json",
                    },
                )
                eval_resp = urllib.request.urlopen(eval_req, timeout=timeout)
                eval_data = json.loads(eval_resp.read().decode("utf-8"))

                value = eval_data.get("result", {}).get("result", {}).get("value", "")
                result[field_name] = value
            except Exception:
                result[field_name] = ""

        # Close the tab
        try:
            close_url = (
                f"http://127.0.0.1:{debug_port}/json/close/{tab_data.get('id', '')}"
            )
            close_req = urllib.request.Request(close_url, headers={"User-Agent": "Mozilla/5.0"})
            urllib.request.urlopen(close_req, timeout=5)
        except Exception:
            pass

    except Exception:
        pass

    return result


def safe_autocli_fetch(fetch_fn, *args, **kwargs):
    """Wrap any autocli fetch function: never raise, return empty string on failure.

    All data-vendor functions should use this wrapper for their autocli tier,
    so the analysis pipeline never breaks on a scraping failure.
    """
    try:
        return fetch_fn(*args, **kwargs)
    except Exception:
        return ""
