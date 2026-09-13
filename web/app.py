# reload trigger v2
"""FastAPI application for the QuantConclave web dashboard."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from pydantic import BaseModel
import yfinance as yf
import requests as req

from quantconclave.default_config import DEFAULT_CONFIG
from quantconclave.llm_clients.model_catalog import MODEL_OPTIONS
from web.stream import StreamEmitter, _pending_interactions, _user_responses, _pending_chats, _chat_questions, _session_results
from web.results_store import init_db, get_result
from web.history_chat import stream_history_chat
from web.ai_pick_agent import stream_aipick_chat
from web.advisory_experience_ui import router as experience_router
from web.calibration_ui import router as calibration_router
from web.history_agent import router as history_agent_router
from web.skill_ui import router as skill_router
from web.routes.results import router as results_router, CreateThreadBody

logger = logging.getLogger(__name__)

_scheduler_manager = None

# Project root is two levels up from web/app.py — anchor .env loading here so
# the dashboard works regardless of the CWD the server was launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    """Load the project .env file (absolute path, CWD-independent)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env", override=False)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app_ref: FastAPI):
    global _scheduler_manager
    _load_env()
    init_db(DEFAULT_CONFIG)
    from quantconclave.workspace.store import migrate_legacy_db
    migrate_legacy_db(DEFAULT_CONFIG)
    from web.results_store import init_shortlist
    init_shortlist(DEFAULT_CONFIG)
    from web.results_store import init_chat_tables
    init_chat_tables(DEFAULT_CONFIG)
    from web.results_store import init_rrg_snapshots
    init_rrg_snapshots(DEFAULT_CONFIG)
    from web.watchlist_store import init_watchlist_store
    init_watchlist_store(DEFAULT_CONFIG)
    from web.position_store import init_position_store
    init_position_store(DEFAULT_CONFIG)
    from web.twopass_records import init_twopass_store
    init_twopass_store(DEFAULT_CONFIG)
    from web.stage3_records import init_stage3_store
    init_stage3_store(DEFAULT_CONFIG)
    from web.moneyflow_cache import init_moneyflow_cache
    init_moneyflow_cache(DEFAULT_CONFIG)
    from web.task_audit import init_task_audit_store
    init_task_audit_store(DEFAULT_CONFIG)
    from web.scheduler import SchedulerManager, init_scheduler_run_store
    init_scheduler_run_store(DEFAULT_CONFIG)
    _scheduler_manager = SchedulerManager(DEFAULT_CONFIG)
    _scheduler_manager.start()
    # Start the WeCom 智能机器人 long-connection when globally configured — the
    # connection must already be up when a scheduled run finishes, so it can
    # learn the user's chat target and push results. No-op when unconfigured.
    from web import wecom_bot
    wecom_bot.ensure_started()
    # Route the bot's inbound messages into the advisor agent (remote-control of
    # scheduled tasks via WeChat). The bridge applies its own security gates.
    from web import bot_advisor_bridge
    wecom_bot.set_message_handler(bot_advisor_bridge.handle_message)
    # Every real inbound single-chat message activates/refreshes that user in
    # the bridge's user registry (pre-registered placeholders auto-activate).
    wecom_bot.set_user_learned_handler(bot_advisor_bridge.on_user_learned)
    yield
    if _scheduler_manager:
        _scheduler_manager.shutdown()


app = FastAPI(title="QuantConclave Dashboard", lifespan=lifespan)
app.include_router(experience_router)
app.include_router(calibration_router)
app.include_router(history_agent_router)
app.include_router(skill_router)
app.include_router(results_router)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_web_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_web_dir / "static")), name="static")

_TEMPLATE_DIR = _web_dir / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))

_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="12" fill="#0a1628"/>
<path d="M14 42h36" stroke="#54aeff" stroke-width="4" stroke-linecap="round"/>
<path d="M18 38l10-10 8 8 12-16" fill="none" stroke="#dafbe1" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="48" cy="20" r="4" fill="#54aeff"/>
</svg>"""


# Provider display names for the UI
PROVIDER_LABELS = {
    "deepseek": "DeepSeek (Default)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "xai": "xAI Grok",
    "qwen": "Qwen (Intl)",
    "qwen-cn": "Qwen (China)",
    "glm": "GLM (Intl)",
    "glm-cn": "GLM (China)",
    "minimax": "MiniMax (Intl)",
    "minimax-cn": "MiniMax (China)",
    "ollama": "S1-11434",
    "openrouter": "OpenRouter",
    "azure": "Azure OpenAI",
}

@app.get("/")
def index():
    """Serve the dashboard page."""
    cfg = DEFAULT_CONFIG
    template = _jinja_env.get_template("index.html")
    html = template.render(
        llm_provider=cfg.get("llm_provider", ""),
        deep_model=cfg.get("deep_think_llm", ""),
        quick_model=cfg.get("quick_think_llm", ""),
        backend_url=cfg.get("backend_url") or "",
        language=cfg.get("output_language", "Chinese"),
        today=datetime.now().strftime("%Y-%m-%d"),
    )
    return Response(
        content=html,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Serve a small favicon to avoid browser 404 noise."""
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


@app.get("/api/search")
def search_stocks(q: str = Query(min_length=1, max_length=100)):
    """Search for stocks by ticker or company name (yfinance + tushare fallback)."""
    items = _search_yf_sdk(q)
    if not items:
        items = _search_yf_api(q)
    if not items:
        items = _search_tushare(q)
    return items


def _search_tushare(q: str) -> list[dict]:
    """Search A-share stocks via tushare stock_basic (proxy-safe fallback)."""
    _load_env()
    try:
        import tushare as ts
        token = os.environ.get("TUSHARE_TOKEN","")
        if not token: return []
        pro = ts.pro_api(token)
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry")
        if df is None or df.empty: return []
        qu = q.upper().replace(".SH","").replace(".SZ","")
        m = df[df["ts_code"].str.contains(qu, na=False) | df["name"].str.contains(q, na=False)]
        results = []
        for _, r in m.head(8).iterrows():
            code = r["ts_code"]
            mkt = "SH" if code.endswith(".SH") else ("SZ" if code.endswith(".SZ") else "")
            results.append({"symbol": code, "name": r["name"], "exchange": mkt})
        return results
    except Exception:
        return []


def _format_quotes(quotes):
    """Convert raw Yahoo Finance quote dicts to the search-result format."""
    items = []
    for r in (quotes or [])[:8]:
        symbol = r.get("symbol", "")
        if not symbol:
            continue
        items.append({
            "symbol": symbol,
            "name": r.get("shortname") or r.get("longname") or symbol,
            "exchange": r.get("exchange", ""),
            "type": r.get("quoteType", ""),
        })
    return items


def _search_yf_sdk(query: str):
    """Try yfinance SDK Search (query2 endpoint)."""
    try:
        results = yf.Search(query=query, news_count=0).quotes
    except Exception:
        logger.debug("yf.Search failed for q=%s", query)
        return []
    return _format_quotes(results)


def _search_yf_api(query: str):
    """Fallback: direct request to query1 endpoint (less rate-limited)."""
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = req.get(url, params={"q": query, "lang": "en-US"}, headers=headers, timeout=8)
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = data.get("quotes", [])
    except Exception:
        logger.debug("yf query1 fallback failed for q=%s", query)
        return []
    return _format_quotes(results)


@app.get("/api/config")
def get_config():
    """Return current LLM config for the UI."""
    cfg = DEFAULT_CONFIG
    # Ollama backend_url: prefer explicit config, then OLLAMA_BASE_URL env var
    backend_url = cfg.get("backend_url") or os.environ.get("OLLAMA_BASE_URL", "")
    return {
        "llm_provider": cfg.get("llm_provider", ""),
        "deep_think_provider": cfg.get("deep_think_provider") or "",
        "quick_think_provider": cfg.get("quick_think_provider") or "",
        "deep_think_llm": cfg.get("deep_think_llm", ""),
        "quick_think_llm": cfg.get("quick_think_llm", ""),
        "backend_url": backend_url,
        "proxy": cfg.get("proxy") or "",
        "output_language": cfg.get("output_language", "Chinese"),
        "max_debate_rounds": cfg.get("max_debate_rounds", 1),
        "max_risk_discuss_rounds": cfg.get("max_risk_discuss_rounds", 1),
    }


@app.get("/api/runtime")
def get_runtime():
    """Expose the Runtime Manifest (ports / service URLs) for diagnostics."""
    from quantconclave.runtime_manifest import as_dict
    return as_dict()


@app.get("/api/models")
def get_models():
    """Return available providers and their model options for the UI.

    Includes both the primary Ollama server (OLLAMA_BASE_URL / localhost)
    and the secondary Ollama server (OLLAMA2_BASE_URL / 172.20.86.254:11434)
    as separate provider keys: ``ollama`` and ``ollama2``.
    """
    # Probe the 4 Ollama boxes concurrently — each /api/tags can block up to
    # 10s, and sequential probing made every page load ~5s+ slow even when the
    # user isn't using Ollama at all. Results are cached per-host for 30s
    # inside _fetch_ollama_models_from, so only the first load after a cache
    # miss pays the network cost.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as _pool:
        _ollama_futures = {
            "ollama": _pool.submit(_fetch_ollama_models),
            "ollama2": _pool.submit(_fetch_ollama2_models),
            "ollama3": _pool.submit(_fetch_ollama3_models),
            "ollama4": _pool.submit(_fetch_ollama4_models),
        }
    _ollama_models = {k: f.result() for k, f in _ollama_futures.items()}

    providers = []
    for provider_key, label in PROVIDER_LABELS.items():
        provider_models = MODEL_OPTIONS.get(provider_key, {})
        quick = [{"label": m[0], "value": m[1]} for m in provider_models.get("quick", [])]
        deep = [{"label": m[0], "value": m[1]} for m in provider_models.get("deep", [])]

        if provider_key == "ollama":
            ollama_models = _ollama_models.get("ollama") or []
            if ollama_models:
                quick = [{"label": m, "value": m} for m in ollama_models]
                deep = [{"label": m, "value": m} for m in ollama_models]

        providers.append({
            "key": provider_key,
            "label": label,
            "is_default": provider_key == "deepseek",
            "quick_models": quick,
            "deep_models": deep,
        })

    # ── Secondary Ollama servers (pre-fetched concurrently above) ──
    ollama2_models = _ollama_models.get("ollama2") or []
    if ollama2_models:
        providers.append({"key":"ollama2","label":"S2-11434","is_default":False,
            "quick_models":[{"label":m,"value":m} for m in ollama2_models],
            "deep_models":[{"label":m,"value":m} for m in ollama2_models]})
    ollama3_models = _ollama_models.get("ollama3") or []
    if ollama3_models:
        providers.append({"key":"ollama3","label":"S1-11435","is_default":False,
            "quick_models":[{"label":m,"value":m} for m in ollama3_models],
            "deep_models":[{"label":m,"value":m} for m in ollama3_models]})
    ollama4_models = _ollama_models.get("ollama4") or []
    if ollama4_models:
        providers.append({"key":"ollama4","label":"S2-11435","is_default":False,
            "quick_models":[{"label":m,"value":m} for m in ollama4_models],
            "deep_models":[{"label":m,"value":m} for m in ollama4_models]})

    return {"providers": providers}


# Ollama model lists are ~static per box; cache each host's /api/tags result
# briefly so /api/models (which probes 4 hosts) is fast on repeat page loads.
# A down or slow host is cached as [] too, avoiding a repeat 10s timeout on
# every load while the box is unreachable.
_ollama_models_cache: dict[str, tuple[float, list[str]]] = {}
_OLLAMA_MODELS_CACHE_TTL = 30.0


def _fetch_ollama_models_from(host: str) -> list[str]:
    """Fetch tool-calling-capable models from an Ollama server at *host*.

    *host* should be a full URL like ``http://localhost:11434`` or
    ``http://172.20.86.254:11434`` (no trailing ``/v1``).
    """
    import requests as _req
    _TOOL_CAPABLE = {
        "qwen", "llama", "mistral", "mixtral", "command-r",
        "deepseek", "glm", "phi", "gemma", "yi", "dbrx",
        "hermes", "dolphin", "wizard", "openchat", "zephyr",
        "nous", "solar", "falcon",
    }
    _now = time.monotonic()
    _cached = _ollama_models_cache.get(host)
    if _cached and _now - _cached[0] < _OLLAMA_MODELS_CACHE_TTL:
        return _cached[1]
    try:
        resp = _req.get(f"{host.rstrip('/')}/api/tags", timeout=10)
        if resp.status_code != 200:
            result: list[str] = []
        else:
            data = resp.json()
            result = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if any(family in name.lower() for family in _TOOL_CAPABLE):
                    result.append(name)
    except Exception:
        result = []
    _ollama_models_cache[host] = (_now, result)
    return result


def _fetch_ollama_models() -> list[str]:
    """Fetch models from the primary Ollama server (OLLAMA_BASE_URL or localhost)."""
    ollama_host = (
        os.environ.get("OLLAMA_BASE_URL")
        or DEFAULT_CONFIG.get("backend_url")
        or "http://localhost:11434/v1"
    )
    base = ollama_host.rstrip("/v1").rstrip("/")
    return _fetch_ollama_models_from(base)


def _fetch_ollama2_models() -> list[str]:
    host = os.environ.get("OLLAMA2_BASE_URL", "http://172.20.86.254:11434")
    return _fetch_ollama_models_from(host.rstrip("/v1").rstrip("/"))

def _fetch_ollama3_models() -> list[str]:
    host = os.environ.get("OLLAMA3_BASE_URL", "http://172.20.86.180:11435")
    return _fetch_ollama_models_from(host.rstrip("/v1").rstrip("/"))

def _fetch_ollama4_models() -> list[str]:
    host = os.environ.get("OLLAMA4_BASE_URL", "http://172.20.86.254:11435")
    return _fetch_ollama_models_from(host.rstrip("/v1").rstrip("/"))


@app.get("/api/pick/rankings")
def pick_rankings():
    """Return top gainers, top net inflow, and multi-factor rankings for Pick Agent."""
    try:
        from quantconclave.sector_scan.top_gainers import get_top_gainers, get_top_net_inflow, get_multi_factor_ranking
        import concurrent.futures

        # Try Tushare first, fall back to yfinance
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f1 = pool.submit(get_top_gainers, 15)
            f2 = pool.submit(get_top_net_inflow, 15)
            f3 = pool.submit(get_multi_factor_ranking, 15)

        gainers = _format_ranking(f1.result()) or _yf_gainers_fallback(15)
        inflow = _format_ranking(f2.result()) or _yf_gainers_fallback(15, sort_by="volume")
        multi = _format_ranking(f3.result()) or _yf_gainers_fallback(15, sort_by="mixed")

        return {"gainers": gainers, "inflow": inflow, "multifactor": multi}
    except Exception:
        yf = _yf_gainers_fallback(15)
        return {"gainers": yf, "inflow": _yf_gainers_fallback(15, "volume"), "multifactor": yf}


def _yf_gainers_fallback(limit: int = 15, sort_by: str = "pct") -> list[dict]:
    """Get stock rankings via yfinance for common A-share tickers."""
    try:
        import yfinance as yf
        tickers = [
            "600519.SS", "000858.SZ", "601318.SS", "000333.SZ", "600036.SS",
            "601398.SS", "000651.SZ", "600900.SS", "600276.SS", "601166.SS",
            "600030.SS", "000001.SZ", "002415.SZ", "600887.SS", "601012.SS",
            "002594.SZ", "600809.SS", "000568.SZ", "600585.SS", "601088.SS",
            "000725.SZ", "600104.SS", "002475.SZ", "600690.SS", "601225.SS",
            "000063.SZ", "600048.SS", "601857.SS", "000002.SZ", "600406.SS",
            "002714.SZ", "601211.SS", "601628.SS", "002230.SZ", "688981.SS",
            "601728.SS", "600031.SS", "000100.SZ", "601601.SS", "600150.SS",
        ]
        data = []
        for t in tickers:
            try:
                stock = yf.Ticker(t)
                info = stock.info
                name = info.get("shortName") or info.get("longName") or t
                hist = stock.history(period="2d")
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
                    curr = float(hist["Close"].iloc[-1])
                    pct = (curr - prev) / prev * 100
                    vol = int(hist["Volume"].iloc[-1]) if len(hist) > 0 else 0
                    data.append({"ticker": t, "name": str(name), "pct_chg": round(pct, 2), "volume": vol})
            except Exception:
                pass
        if sort_by == "volume":
            data.sort(key=lambda x: x["volume"], reverse=True)
        elif sort_by == "mixed":
            data.sort(key=lambda x: abs(x["pct_chg"]) * (x["volume"] / 1e6), reverse=True)
        else:
            data.sort(key=lambda x: x["pct_chg"], reverse=True)
        return data[:limit]
    except Exception:
        return []


def _format_ranking(results) -> list[dict]:
    """Format ranking results (CSV string or list) to {ticker, name, ...} for the UI."""
    # If results is a CSV string, parse it
    if isinstance(results, str):
        import csv, io
        formatted = []
        lines = results.split("\n")
        csv_start = next((i for i, l in enumerate(lines) if "ts_code" in l.lower()), 0)
        if csv_start:
            reader = csv.DictReader(io.StringIO("\n".join(lines[csv_start:])))
            for row in reader:
                ticker = row.get("ts_code", row.get("ticker", "")).strip()
                if not ticker:
                    continue
                formatted.append({
                    "ticker": ticker,
                    "name": row.get("name", ticker),
                    "pct_chg": float(row.get("pct_chg", 0) or 0),
                    "net_amount": float(row.get("net_amount", row.get("main_force_net", 0)) or 0),
                    "score": float(row.get("score", 0) or 0),
                })
                if len(formatted) >= 15:
                    break
        return formatted
    # If results is a list
    formatted = []
    for r in (results or [])[:15]:
        if isinstance(r, dict):
            formatted.append({
                "ticker": r.get("ticker", r.get("ts_code", "")),
                "name": r.get("name", ""),
                "pct_chg": r.get("pct_chg", 0),
                "net_amount": r.get("net_amount", r.get("main_force_net", 0)),
                "score": r.get("score", 0),
            })
    return formatted


from quantconclave.dataflows.ohlcv import fetch_ohlcv as _fetch_ohlcv


@app.get("/api/history")
def history(
    ticker: str = Query(min_length=1),
    period: str = Query(default="6mo"),
):
    """Return OHLCV candle data. A-shares prefer the tushare chain; yfinance fallback."""
    df = _fetch_ohlcv(ticker, period)
    if df is None:
        return {"error": "No data found", "candles": []}
    df = df.reset_index()
    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time": row["Date"].strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
        })
    return {"candles": candles}


@app.get("/api/history/{ticker}/indicators")
def history_indicators(
    ticker: str,
    period: str = Query(default="3mo"),
):
    """Return OHLCV + computed technical indicators (MACD, RSI, MA) for chart rendering."""
    df = _fetch_ohlcv(ticker, period)
    if df is None:
        return {"error": "No data found", "candles": []}
    return _compute_indicators(df)


@app.get("/api/history/{ticker}/weekly")
def history_weekly(ticker: str):
    """Return weekly OHLCV candles with MA/MACD/RSI indicators."""
    df = _fetch_ohlcv(ticker, "max", interval="1wk")
    if df is None:
        return {"error": "No data found", "candles": []}
    return _compute_indicators(df)


@app.get("/api/history/{ticker}/monthly")
def history_monthly(ticker: str):
    """Return monthly OHLCV candles with MA/MACD/RSI indicators."""
    df = _fetch_ohlcv(ticker, "max", interval="1mo")
    if df is None:
        return {"error": "No data found", "candles": []}
    return _compute_indicators(df)


def _compute_indicators(data):
    """Compute MA/MACD/RSI from OHLCV dataframe."""
    import pandas as pd
    data = data.reset_index()
    if "Date" in data.columns:
        data["Date"] = data["Date"].dt.strftime("%Y-%m-%d")
    elif "Datetime" in data.columns:
        data["Date"] = data["Datetime"].dt.strftime("%Y-%m-%d")
    closes = data["Close"].astype(float)
    ma5 = closes.rolling(5).mean()
    ma20 = closes.rolling(20).mean()
    ema12 = closes.ewm(span=12).mean()
    ema26 = closes.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    macd_bar = 2 * (dif - dea)
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, 1)
    rsi = 100 - (100 / (1 + rs))
    candles = []
    for i, (_, row) in enumerate(data.iterrows()):
        open_val = float(row["Open"])
        high_val = float(row["High"])
        low_val = float(row["Low"])
        close_val = float(row["Close"])
        vol_val = float(row["Volume"])
        # Skip rows with NaN values
        if any(v != v for v in [open_val, high_val, low_val, close_val]):
            continue
        candles.append({
            "time": str(row.get("Date", "")),
            "open": round(open_val, 2),
            "high": round(high_val, 2),
            "low": round(low_val, 2),
            "close": round(close_val, 2),
            "volume": int(vol_val),
            "ma5": round(float(ma5.iloc[i]), 2) if not pd.isna(ma5.iloc[i]) else None,
            "ma20": round(float(ma20.iloc[i]), 2) if not pd.isna(ma20.iloc[i]) else None,
            "dif": round(float(dif.iloc[i]), 4) if not pd.isna(dif.iloc[i]) else None,
            "dea": round(float(dea.iloc[i]), 4) if not pd.isna(dea.iloc[i]) else None,
            "macd": round(float(macd_bar.iloc[i]), 4) if not pd.isna(macd_bar.iloc[i]) else None,
            "rsi": round(float(rsi.iloc[i]), 1) if not pd.isna(rsi.iloc[i]) else None,
        })
    return {"candles": candles}


@app.get("/api/analyze")
def analyze(
    ticker: str = Query(min_length=1),
    date: str = Query(min_length=10, max_length=10),
    analysts: str = Query(default="capital_flow,market,social,news,fundamentals,competitor,partner"),
    provider: str = Query(default=""),
    deep_provider: str = Query(default=""),
    quick_provider: str = Query(default=""),
    deep_model: str = Query(default=""),
    quick_model: str = Query(default=""),
    backend_url: str = Query(default=""),
    proxy: str = Query(default=""),
    language: str = Query(default="English"),
    checkpoint_1: bool = Query(default=False),
    checkpoint_2: bool = Query(default=False),
    pm_chat: bool = Query(default=False),
):
    """Run the analysis pipeline and stream results via SSE."""
    analyst_list = [a.strip() for a in analysts.split(",") if a.strip()]

    session_id = uuid.uuid4().hex[:12]
    emitter = StreamEmitter(session_id=session_id)

    def event_stream():
        yield from emitter.stream_analysis(
            ticker=ticker,
            date=date,
            analysts=analyst_list,
            provider=provider,
            deep_provider=deep_provider,
            quick_provider=quick_provider,
            deep_model=deep_model,
            quick_model=quick_model,
            backend_url=backend_url,
            proxy=proxy,
            language=language,
            checkpoint_1=checkpoint_1,
            checkpoint_2=checkpoint_2,
            pm_chat=pm_chat,
        )
        _pending_interactions.pop(session_id, None)
        _user_responses.pop(session_id, None)
        _pending_chats.pop(session_id, None)
        _chat_questions.pop(session_id, None)
        _session_results.pop(session_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/ollama/models")
def get_ollama_models(base_url: str = Query(default="http://localhost:11434/v1")):
    """Fetch available models from a local Ollama instance."""
    import json
    import urllib.request
    import urllib.error

    # Parse base_url to get the Ollama host (strip /v1 suffix if present)
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    ollama_host = f"{parsed.scheme}://{parsed.netloc}"

    try:
        req = urllib.request.Request(
            f"{ollama_host}/api/tags",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        logger.exception("Failed to fetch Ollama models from %s", ollama_host)
        return {"models": [], "error": f"Could not connect to Ollama at {ollama_host}"}

    models = data.get("models", [])
    options = []
    for m in models:
        name = m.get("name", "")
        if not name:
            continue
        # Use the model name as both label and value
        options.append({
            "label": name,
            "value": name,
        })
    # Sort by name
    options.sort(key=lambda x: x["label"])
    return {"models": options, "host": ollama_host}


class InteractionResponse(BaseModel):
    answer: str


@app.post("/api/respond/{session_id}")
def respond_to_checkpoint(session_id: str, body: InteractionResponse):
    """Receive user response to a checkpoint interaction question."""
    event = _pending_interactions.get(session_id)
    if event is None:
        return {"error": "No pending interaction for this session", "acknowledged": False}
    _user_responses[session_id] = body.answer
    event.set()
    return {"acknowledged": True}


class ChatMessage(BaseModel):
    question: str


@app.post("/api/chat/{session_id}")
def chat_with_pm(session_id: str, body: ChatMessage):
    """Receive a chat question and trigger advisory PM response.

    For live-analysis sessions, creates an advisory thread linked to the
    session's analysis run and returns the stream URL.
    """
    from web.results_store import create_chat_thread, save_chat_message
    from web.stream import _session_results

    # Try to link to the analysis session's run_id
    session_data = _session_results.get(session_id, {})
    run_id = session_data.get("run_id", "")

    thread_id = create_chat_thread(DEFAULT_CONFIG, [run_id] if run_id else [],
                                   title=body.question[:40])
    save_chat_message(DEFAULT_CONFIG, thread_id, "user", body.question)

    # Also support legacy event-based flow
    event = _pending_chats.get(session_id)
    if event:
        _chat_questions[session_id] = body.question
        event.set()

    return {
        "acknowledged": True,
        "thread_id": thread_id,
        "stream_url": f"/api/chat/stream?thread_id={thread_id}&question={body.question}",
    }




class AdvisoryQuestion(BaseModel):
    thread_id: str | None = None
    question: str
    run_ids: list[str] | None = None

@app.post("/api/advisory/chat")
def advisory_chat(body: AdvisoryQuestion):
    """Unified advisory chat endpoint.

    Send a natural language question to the PM advisory agent.
    The PM can query past analyses, compare stocks, run fresh analysis,
    check memory log, and call live data tools autonomously.

    If thread_id is provided, conversation history is maintained.
    If run_ids are provided, those analyses are loaded as context.
    Otherwise, the PM discovers relevant analyses via tools.
    """
    from web.results_store import create_chat_thread, save_chat_message

    thread_id = body.thread_id
    # Always ensure thread exists
    if thread_id:
        try:
            save_chat_message(DEFAULT_CONFIG, thread_id, "user", body.question)
        except Exception:
            thread_id = None
    
    if not thread_id:
        run_ids = body.run_ids or []
        thread_id = create_chat_thread(DEFAULT_CONFIG, run_ids, title=body.question[:40])
        save_chat_message(DEFAULT_CONFIG, thread_id, "user", body.question)

    return {
        "thread_id": thread_id,
        "question": body.question,
        "stream_url": f"/api/chat/stream?thread_id={thread_id}&question={body.question}",
    }


class WechatPushSetting(BaseModel):
    enabled: bool


@app.get("/api/advisory/wechat_push")
def get_wechat_push_setting():
    """Whether completed web-advisor replies are forwarded to the WeCom bot's
    personal chat — the backing for the advisory-UI toggle."""
    from web.bot_advisor_bridge import get_web_advisor_push_enabled
    return {"enabled": get_web_advisor_push_enabled()}


@app.post("/api/advisory/wechat_push")
def set_wechat_push_setting(body: WechatPushSetting):
    """Turn the web-advisor→WeChat push switch on/off (persisted, default off)."""
    from web.bot_advisor_bridge import set_web_advisor_push_enabled
    set_web_advisor_push_enabled(body.enabled)
    return {"enabled": body.enabled}


class WecomBindingPayload(BaseModel):
    """Web-advisor↔WeCom channel binding + reverse mirror switch."""
    bound_user: str | None = None
    wechat_to_web: bool | None = None


@app.get("/api/advisory/wecom_binding")
def get_wecom_binding():
    """Web-advisor↔WeCom channel state: mirror-eligible registered users (with
    their names/activity in ``entries``) + which one is bound for cross-surface
    sharing + the WeChat→web mirror switch. When bound with the mirror on, also
    resolves the bound user's channel thread_id so the web panel can follow it
    live."""
    from web.bot_advisor_bridge import (get_web_advisor_binding,
                                        get_wecom_users,
                                        _resolve_thread)

    binding = get_web_advisor_binding()
    bound_user = binding.get("bound_user") or ""
    wechat_to_web = bool(binding.get("wechat_to_web", False))
    entries = [e for e in get_wecom_users()
               if e.get("active") and e.get("mirror")]
    users = sorted(e["userid"] for e in entries)
    thread_id = None
    if bound_user and wechat_to_web and bound_user in users:
        bot_id = DEFAULT_CONFIG.get("wecom_bot_id", "") or ""
        thread_id = _resolve_thread(bot_id, bound_user)
    return {
        "users": users,
        "entries": entries,
        "bound_user": bound_user,
        "wechat_to_web": wechat_to_web,
        "thread_id": thread_id,
    }


@app.post("/api/advisory/wecom_binding")
def set_wecom_binding(body: WecomBindingPayload):
    """Bind the web panel to one registered WeCom user and/or flip the
    WeChat→web mirror switch. Only active users with ``mirror`` enabled are
    bindable. Returns the resulting state (as GET)."""
    from web.bot_advisor_bridge import (set_web_advisor_binding,
                                        set_wechat_advisor_web_enabled,
                                        get_wecom_users)
    if body.bound_user is not None:
        bound = (body.bound_user or "").strip()
        if bound:
            eligible = {e["userid"] for e in get_wecom_users()
                        if e.get("active") and e.get("mirror")}
            if bound not in eligible:
                raise HTTPException(
                    400, "该用户尚未激活或未开启镜像，无法绑定到网页")
        set_web_advisor_binding(bound)
    if body.wechat_to_web is not None:
        set_wechat_advisor_web_enabled(body.wechat_to_web)
    return get_wecom_binding()


class WecomUserAdd(BaseModel):
    """Pre-register a WeCom user (name + userid) as a placeholder that
    auto-activates when that user first messages the bot."""
    userid: str
    name: str = ""


class WecomUserUpdate(BaseModel):
    """Update a registered WeCom user's display name / connectivity flags."""
    userid: str
    name: str | None = None
    advisor: bool | None = None
    push_reply: bool | None = None
    mirror: bool | None = None


@app.get("/api/advisory/wecom_users")
def list_wecom_users():
    """Every registered WeCom user with its per-user connectivity flags."""
    from web.bot_advisor_bridge import get_wecom_users
    return {"users": get_wecom_users()}


@app.post("/api/advisory/wecom_users")
def create_wecom_user(body: WecomUserAdd):
    """Pre-register a WeCom user (placeholder until they message the bot)."""
    from web.bot_advisor_bridge import add_wecom_user, get_wecom_users
    userid = (body.userid or "").strip()
    if not userid:
        raise HTTPException(400, "userid 不能为空")
    row = add_wecom_user(userid, body.name)
    return {"user": row, "users": get_wecom_users()}


@app.patch("/api/advisory/wecom_users")
def patch_wecom_user(body: WecomUserUpdate):
    """Update a registered WeCom user's name / advisor / push_reply / mirror."""
    from web.bot_advisor_bridge import update_wecom_user, get_wecom_users
    patch = {k: v for k, v in body.model_dump().items()
             if k != "userid" and v is not None}
    try:
        row = update_wecom_user(body.userid, patch)
    except KeyError:
        raise HTTPException(404, "该用户尚未登记")
    return {"user": row, "users": get_wecom_users()}


@app.delete("/api/advisory/wecom_users")
def delete_wecom_user(userid: str = Query(...)):
    """Remove a registered WeCom user's row (unmanaged — their next message
    re-activates a row). Also clears the web binding if it pointed at them."""
    from web.bot_advisor_bridge import (remove_wecom_user, get_wecom_users,
                                        get_web_advisor_binding,
                                        set_web_advisor_binding)
    try:
        remove_wecom_user(userid)
    except KeyError:
        raise HTTPException(404, "该用户尚未登记")
    if get_web_advisor_binding().get("bound_user") == userid:
        set_web_advisor_binding("")
    return {"users": get_wecom_users()}


@app.post("/api/aipick/chat")
def aipick_chat(body: AdvisoryQuestion):
    """Unified AI Pick chat endpoint. Same pattern as advisory chat."""
    from web.results_store import create_chat_thread, save_chat_message as _save

    thread_id = body.thread_id
    if thread_id:
        try:
            _save(DEFAULT_CONFIG, thread_id, "user", body.question)
        except Exception:
            thread_id = None

    if not thread_id:
        run_ids = body.run_ids or []
        thread_id = create_chat_thread(DEFAULT_CONFIG, run_ids, title=body.question[:40])
        _save(DEFAULT_CONFIG, thread_id, "user", body.question)

    return {
        "thread_id": thread_id,
        "question": body.question,
        "stream_url": f"/api/aipick/stream?thread_id={thread_id}&question={body.question}",
    }


@app.post("/api/prediction/chat")
def prediction_chat(body: AdvisoryQuestion):
    """Unified Prediction Agent chat endpoint. Same pattern as advisory/aipick."""
    from web.results_store import create_chat_thread, save_chat_message as _save

    thread_id = body.thread_id
    if thread_id:
        try:
            _save(DEFAULT_CONFIG, thread_id, "user", body.question)
        except Exception:
            thread_id = None

    if not thread_id:
        run_ids = body.run_ids or []
        thread_id = create_chat_thread(DEFAULT_CONFIG, run_ids, title=body.question[:40])
        _save(DEFAULT_CONFIG, thread_id, "user", body.question)

    return {
        "thread_id": thread_id,
        "question": body.question,
        "stream_url": f"/api/prediction/stream?thread_id={thread_id}&question={body.question}",
    }

@app.post("/api/backtest/chat")
def backtest_chat(body: AdvisoryQuestion):
    from web.results_store import create_chat_thread, save_chat_message as _save
    thread_id = body.thread_id
    if thread_id:
        try:
            _save(DEFAULT_CONFIG, thread_id, "user", body.question)
        except Exception:
            thread_id = None
    if not thread_id:
        run_ids = body.run_ids or []
        thread_id = create_chat_thread(DEFAULT_CONFIG, run_ids, title=body.question[:40])
        _save(DEFAULT_CONFIG, thread_id, "user", body.question)
    return {"thread_id": thread_id, "question": body.question,
            "stream_url": "/api/backtest/stream?thread_id=" + thread_id + "&question=" + body.question,
    }


@app.post("/api/strategy/chat")
def strategy_chat(body: AdvisoryQuestion):
    from web.results_store import create_chat_thread, save_chat_message as _save
    thread_id = body.thread_id
    if thread_id:
        try:
            _save(DEFAULT_CONFIG, thread_id, "user", body.question)
        except Exception:
            thread_id = None
    if not thread_id:
        run_ids = body.run_ids or []
        thread_id = create_chat_thread(DEFAULT_CONFIG, run_ids, title=body.question[:40])
        _save(DEFAULT_CONFIG, thread_id, "user", body.question)
    return {"thread_id": thread_id, "question": body.question,
            "stream_url": "/api/strategy/stream?thread_id=" + thread_id + "&question=" + body.question}

@app.post("/api/chat/threads")
def create_multi_chat_thread(body: CreateThreadBody):
    """Create a new chat thread for one or more analysis runs."""
    from web.results_store import create_chat_thread, get_result
    if not body.run_ids or len(body.run_ids) == 0:
        raise HTTPException(400, "run_ids is required")
    for rid in body.run_ids:
        if not get_result(DEFAULT_CONFIG, rid):
            raise HTTPException(404, f"Analysis run {rid} not found")
    thread_id = create_chat_thread(DEFAULT_CONFIG, body.run_ids)
    return {"thread_id": thread_id, "run_ids": body.run_ids}


@app.get("/api/chat/threads")
def list_all_chat_threads(run_id: str | None = None):
    """List chat threads, optionally filtered by run_id."""
    from web.results_store import list_chat_threads
    return list_chat_threads(DEFAULT_CONFIG, run_id=run_id)


@app.get("/api/chat/threads/{thread_id}/messages")
def get_chat_messages_new(thread_id: str):
    """Get all messages for a chat thread (no run_id required)."""
    from web.results_store import get_chat_messages as get_msgs
    return get_msgs(DEFAULT_CONFIG, thread_id)


@app.delete("/api/chat/threads/{thread_id}")
def delete_chat_thread_new(thread_id: str):
    """Delete a chat thread and all its messages (no run_id required)."""
    from web.results_store import delete_chat_thread
    ok = delete_chat_thread(DEFAULT_CONFIG, thread_id)
    if not ok:
        raise HTTPException(404, "Thread not found")
    return {"acknowledged": True}


@app.get("/api/chat/threads/{thread_id}/download")
def download_chat_thread_new(thread_id: str, format: str = Query(default="md")):
    """Download chat thread as markdown, JSON, PDF, or DOCX."""
    from fastapi.responses import Response
    from web.results_store import get_chat_messages as get_msgs, get_thread_run_ids, get_result
    import json as _json

    run_ids = get_thread_run_ids(DEFAULT_CONFIG, thread_id)
    ticker = "multi" if len(run_ids) > 1 else ""
    date = ""
    if run_ids:
        meta = get_result(DEFAULT_CONFIG, run_ids[0])
        if meta:
            ticker = meta.get("ticker", "unknown")
            date = meta.get("date", "")

    msgs = get_msgs(DEFAULT_CONFIG, thread_id)
    safe_date = date.replace("-", "") if date else datetime.now().strftime("%Y%m%d")
    safe_ticker = ticker or "chat"

    if format == "json":
        return Response(
            content=_json.dumps(msgs, ensure_ascii=False, indent=2, default=str),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=Chat_{safe_ticker}_{safe_date}.json"},
        )

    # Build MD content (reused for MD, PDF, DOCX)
    lines = [f"# QuantConclave Chat - {ticker} ({date})"]
    if len(run_ids) > 1:
        lines.append(f"*Multi-analysis thread covering {len(run_ids)} runs*")
    lines.append("")
    lines.append(f"Thread: {thread_id}")
    lines.append("")
    for m in msgs:
        role_label = "**User**" if m["role"] == "user" else "**Portfolio Manager**"
        lines.append(f"### {role_label}")
        lines.append("")
        lines.append(m["content"])
        lines.append("")
    md_content = "\n".join(lines)

    if format == "pdf":
        from web.chat_pdf import generate_chat_pdf
        pdf_bytes = generate_chat_pdf(md_content)
        return Response(content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=Chat_{safe_ticker}_{safe_date}.pdf"})

    if format == "docx":
        docx_bytes = _chat_md_to_docx(md_content)
        return Response(content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=Chat_{safe_ticker}_{safe_date}.docx"})

    # Default: Markdown
    return Response(content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=Chat_{safe_ticker}_{safe_date}.md"})


@app.get("/api/chat/stream")
def chat_stream(thread_id: str, question: str = Query(default="", description="User question for the advisory PM")):
    """SSE stream for advisory chat with the Portfolio Manager.

    The PM acts as the central advisory agent with access to:
    - Past analyses (query_past_analyses)
    - Live data tools (money flow, indicators, fundamentals, news)
    - Fresh analysis capability (run_fresh_analysis)
    - Cross-stock comparison (compare_tickers)
    - Memory log verification (query_memory_log)
    """
    import asyncio
    from web.history_chat import stream_advisory_chat

    if not question:
        # Fetch the latest user message from DB as the question
        from web.results_store import get_chat_messages
        msgs = get_chat_messages(DEFAULT_CONFIG, thread_id)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        question = user_msgs[-1]["content"] if user_msgs else ""

    async def event_generator():
        try:
            for event in stream_advisory_chat(thread_id, question, DEFAULT_CONFIG):
                yield event
        except Exception as e:
            logger.exception("Advisory chat stream error for thread %s", thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/prediction/stream")
def prediction_stream(thread_id: str, question: str = Query(default="")):
    """SSE stream for Prediction Agent."""
    if not question:
        from web.results_store import get_chat_messages as _gmsgs
        msgs = _gmsgs(DEFAULT_CONFIG, thread_id)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        question = user_msgs[-1]["content"] if user_msgs else ""

    async def event_generator():
        try:
            from web.prediction_chat import stream_prediction_chat
            for event in stream_prediction_chat(thread_id, question, DEFAULT_CONFIG):
                yield event
        except Exception as e:
            logger.exception("Prediction chat stream error for thread %s", thread_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _chat_md_to_docx(md_content: str) -> bytes:
    """Convert Markdown text to a simple .docx file in memory."""
    import re, zipfile, io as _io
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        doc_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        doc_xml += '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        for line in md_content.split("\n"):
            clean = re.sub(r'[<>&]', lambda m: {'<':'&lt;','>':'&gt;','&':'&amp;'}[m.group()], line.rstrip())
            if clean.startswith("# "):
                doc_xml += f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="36"/></w:rPr><w:t xml:space="preserve">{clean[2:]}</w:t></w:r></w:p>'
            elif clean.startswith("## "):
                doc_xml += f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="28"/></w:rPr><w:t xml:space="preserve">{clean[3:]}</w:t></w:r></w:p>'
            elif clean.startswith("### "):
                doc_xml += f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="24"/></w:rPr><w:t xml:space="preserve">{clean[4:]}</w:t></w:r></w:p>'
            elif clean.startswith("---"):
                doc_xml += '<w:p><w:r><w:t xml:space="preserve">────────────────────────</w:t></w:r></w:p>'
            elif clean.startswith("*") and clean.endswith("*"):
                doc_xml += f'<w:p><w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">{clean.strip("*")}</w:t></w:r></w:p>'
            elif not clean:
                doc_xml += '<w:p></w:p>'
            else:
                doc_xml += f'<w:p><w:r><w:t xml:space="preserve">{clean}</w:t></w:r></w:p>'
        doc_xml += '</w:body></w:document>'
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')
        zf.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    return buf.getvalue()



@app.get("/api/backtest/stream")
def backtest_stream(thread_id: str, question: str = Query(default="")):
    from web.backtest_agent import stream_backtest_chat
    event_generator = stream_backtest_chat(thread_id, question, DEFAULT_CONFIG)
    return StreamingResponse(event_generator, media_type="text/event-stream")

@app.get("/api/strategy/stream")
def strategy_stream(thread_id: str, question: str = Query(default="")):
    from web.strategy_agent import stream_strategy_chat
    event_generator = stream_strategy_chat(thread_id, question, DEFAULT_CONFIG)
    return StreamingResponse(event_generator, media_type="text/event-stream")

@app.get("/api/aipick/stream")
def aipick_stream(thread_id: str, question: str = Query(default="")):
    """SSE stream for AI Pick Agent chat."""
    from web.ai_pick_agent import stream_aipick_chat
    event_generator = stream_aipick_chat(thread_id, question, DEFAULT_CONFIG)
    return StreamingResponse(event_generator, media_type="text/event-stream")


class RunToolRequest(BaseModel):
    tool: str


@app.post("/api/aipick/run-tool")
def run_aipick_tool(body: RunToolRequest):
    """Directly invoke an AI Pick tool — bypasses LLM, returns full output."""
    _load_env()
    from web.ai_pick_agent import build_aipick_tools
    tools = build_aipick_tools(DEFAULT_CONFIG, None)
    tool_map = {t.name: t for t in tools}
    tool_fn = tool_map.get(body.tool)
    if not tool_fn:
        raise HTTPException(404, f"Tool not found: {body.tool}")

    try:
        result = tool_fn.invoke({})
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/download/{session_id}")
def download_report(session_id: str, format: str = Query(default="md")):
    """Generate and download a report (md or docx) of the analysis session."""
    from web.stream import generate_markdown, _session_results
    from web.docx_export import _get_persisted
    from fastapi.responses import Response
    import logging
    _log = logging.getLogger("web.app")

    # Try live stream data first, then persisted fallback
    data = _session_results.get(session_id) or _get_persisted(session_id) or {}
    ticker = data.get("ticker", "report")
    date_val = data.get("date", "")

    md = generate_markdown(session_id)
    safe_ticker = ticker.replace(".", "_")
    safe_date = date_val.replace("-", "") if date_val else datetime.now().strftime("%Y%m%d")

    if format == "pdf":
        from web.chat_pdf import generate_chat_pdf
        pdf_bytes = generate_chat_pdf(md)
        return Response(content=pdf_bytes, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=QuantConclave_{safe_ticker}_{safe_date}.pdf"})

    if format == "docx":
        from web.docx_export import generate_docx
        docx_bytes = generate_docx(session_id, _session_results)
        filename = f"QuantConclave_{safe_ticker}_{safe_date}.docx"
        return Response(content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    filename = f"QuantConclave_{safe_ticker}_{safe_date}.md"
    return Response(content=md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/results/{run_id}/download")
def download_result_file(run_id: str, format: str = Query(default="md")):
    """Download a saved result file (md, json, pdf) by run_id."""
    from fastapi.responses import FileResponse, Response
    from pathlib import Path
    meta = get_result(DEFAULT_CONFIG, run_id)
    if not meta:
        raise HTTPException(404, "Result not found")
    results_dir = DEFAULT_CONFIG.get("results_dir", "")
    ticker = meta.get("ticker", "unknown")
    date = meta.get("date", "")
    safe_ticker = ticker.replace(".", "_")

    # Use results_dir if set, otherwise fall back to home dir default
    if results_dir:
        base_dir = Path(results_dir)
    else:
        base_dir = Path.home() / ".quantconclave" / "logs"
    base = base_dir / f"{ticker.upper()}/QuantConclaveStrategy_logs"
    json_path = base / f"full_states_log_{date}.json"
    md_path = base / f"full_report_{date}.md"

    if format == "json":
        if not json_path.exists():
            raise HTTPException(404, "JSON file not found")
        return FileResponse(str(json_path), media_type="application/json",
                            filename=f"QuantConclave_{safe_ticker}_{date}.json")

    # Build MD content — use existing report file or generate from JSON
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
    elif json_path.exists():
        import json as _json
        state = _json.loads(json_path.read_text(encoding="utf-8"))
        md_content = _state_to_markdown(state, ticker, date)
    else:
        raise HTTPException(404, "Report file not found")

    if format == "pdf":
        from web.chat_pdf import generate_chat_pdf
        pdf_bytes = generate_chat_pdf(md_content)
        return Response(content=pdf_bytes, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=QuantConclave_{safe_ticker}_{date}.pdf"})

    # Default: MD
    return Response(content=md_content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=QuantConclave_{safe_ticker}_{date}.md"})


def _state_to_markdown(state: dict, ticker: str, date: str) -> str:
    """Build a markdown report from a stored analysis state dict."""
    lines = [f"# QuantConclave Analysis: {ticker} ({date})", "", "---", ""]
    from quantconclave.catalog import ordered_roles
    reports = [(role.report_key, role.label) for role in ordered_roles()]
    for key, label in reports:
        text = state.get(key, "")
        if text:
            lines.append(f"## {label}")
            lines.append("")
            lines.append(str(text)[:5000])
            lines.append("")
            lines.append("---")
            lines.append("")
    decision = state.get("final_trade_decision", "")
    if decision:
        lines.append("## Final Decision")
        lines.append("")
        lines.append(str(decision)[:5000])
    return "\n".join(lines)


# ---- Scheduled Tasks API ----

class ScheduledModelWorker(BaseModel):
    provider: str = ""
    model: str = ""


class ScheduledTaskCreate(BaseModel):
    name: str
    task_type: str = "deep"              # "deep" | "emwl_batch" | "idx_batch"
    cron_expression: str = "30 9 * * *"
    timezone: str = "Asia/Shanghai"
    # ── 微信推送（群机器人 webhook 或 智能机器人 botid+secret；留空则不推送）──
    webhook_url: str = ""
    bot_id: str = ""        # 企业智能机器人 botid（WebSocket 长连接通道）
    bot_secret: str = ""    # 企业智能机器人 secret
    push_user: str | list[str] = ""  # 智能机器人推送目标用户（可多选 userid 数组；空=最近活跃）
    # ── deep 专用（兼容旧字段）──
    ticker: str = ""
    analysts: list[str] = ["market", "news", "fundamentals"]
    use_current_date: bool = True
    date: str = ""
    provider: str = ""
    deep_provider: str = ""
    quick_provider: str = ""
    deep_model: str = ""
    quick_model: str = ""
    # ── emwl_batch / idx_batch 共用 ──
    workers: list[ScheduledModelWorker] = []
    two_pass_workers: list[ScheduledModelWorker] = []
    excluded_boards: list[str] = []
    change_pct_min: float | None = None
    # ── idx_batch 专用 ──
    indexes: list[str] = []
    full_refresh: bool = False
    # ── 自动二次分析 ──
    two_pass: bool = False
    two_pass_mode: str = "reanalyze"     # "reanalyze"=重新分析看多股 | "pack"=仅打包现有结论
    # ── 阶段③·顾问进一步分析（二次分析后的无头综合报告）──
    stage3: bool = False                 # 独立开关；仅当 two_pass 开启且存在看多集时生效
    stage3_provider: str = ""            # 阶段③独立模型 provider（空=用 config deep 模型）
    stage3_model: str = ""               # 阶段③独立模型
    language: str = "English"


class WebhookTestPayload(BaseModel):
    webhook_url: str
    name: str = ""


class BotTestPayload(BaseModel):
    bot_id: str
    bot_secret: str
    name: str = ""


# Shown in the task form instead of the real 智能机器人 secret — the actual
# secret lives in .env and never leaves the server. Empty/masked secrets
# resolve to the global .env secret when a push or test fires.
BOT_SECRET_MASK = "••••••"

# Webhook-URL query params that carry credentials (a WeCom group-bot webhook is
# ``.../send?key=XXXX``). Their VALUES are redacted anywhere a stored task_data
# is returned to the browser or persisted to the audit log.
_CRED_QS_KEYS = {"key", "token", "secret", "sign", "code", "appid"}


def _redact_task_data(task_data: dict) -> dict:
    """Copy task_data for audit/browser persistence with credentials scrubbed.

    bot_secret is masked; a webhook_url's credential query params (``key`` /
    ``token`` / ...) have their values redacted so a real secret never lands in
    the audit log. The returned copy is safe to render; the stored task_data is
    untouched (server-side push still reads the real secret)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    redacted = dict(task_data)
    if redacted.get("bot_secret"):
        redacted["bot_secret"] = BOT_SECRET_MASK
    url = redacted.get("webhook_url") or ""
    if url:
        parts = urlsplit(str(url))
        query = [
            (k, BOT_SECRET_MASK if k.lower() in _CRED_QS_KEYS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        redacted["webhook_url"] = urlunsplit(
            (parts.scheme, parts.netloc, parts.path,
             urlencode(query), parts.fragment))
    return redacted


class ScheduledTaskUpdate(BaseModel):
    enabled: bool | None = None


def _log_task_audit(action: str, job_id: str, name: str, ttype: str,
                    detail: dict | None = None) -> None:
    """Append an audit entry; a failure here must never break the mutation."""
    try:
        from web.task_audit import log_task_action
        log_task_action(DEFAULT_CONFIG, job_id, action, name, ttype, detail=detail)
    except Exception:
        logger.exception("Failed to record task audit action=%s job=%s", action, job_id)


def _task_type_label(ttype: str) -> str:
    return {"emwl_batch": "东方自选批量", "idx_batch": "指数选股批量",
            "deep": "深度分析"}.get(ttype, ttype or "深度分析")


def _get_deep_scheduled_result(config: dict, job_id: str) -> Optional[dict]:
    """Newest result_runs row written by a deep scheduled task (if any)."""
    from web.results_store import _get_conn
    conn = _get_conn(config)
    try:
        row = conn.execute(
            "SELECT * FROM result_runs WHERE scheduled_job_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        # Fresh store without a result_runs table — treat as "no legacy run".
        return None
    finally:
        conn.close()


def _build_scheduler_report_md(job_id: str, run_id: int | None = None) -> str | None:
    """Merged markdown report for one task run, or None if no such run exists
    (caller decides 404).

    ``run_id`` (a ``scheduled_run_log`` row id) selects a specific past run;
    without it the most recent run is used. Every batch/deep run stores a
    self-contained ``summary`` in ``scheduled_run_log``, so any single run's
    report can be rebuilt on demand.
    """
    task = _scheduler_manager.get_task(job_id)
    if not task:
        return None
    ttype = task.get("task_type", "deep")
    lines = ["# 定时任务运行报告", ""]
    lines.append(f"- **任务名称**: {task.get('name', '')}")
    lines.append(f"- **任务类型**: {_task_type_label(ttype)}")
    lines.append(f"- **触发器**: {task.get('trigger', '')}")
    lines.append(f"- **下次运行**: {task.get('next_run') or '—'}")
    lines.append("")

    if ttype in ("emwl_batch", "idx_batch"):
        if run_id is not None:
            from web.scheduler import get_scheduler_run
            last = get_scheduler_run(DEFAULT_CONFIG, run_id, expected_job_id=job_id)
        else:
            last = task.get("last_run")
        if not last:
            return None
        summary = last.get("summary") or {}
        lines.append("## 运行摘要")
        lines.append("")
        lines.append(f"- **运行时间**: {last.get('run_at', '')}")
        lines.append(f"- **股票池**: {summary.get('pool', 0)} "
                     f"| **过滤后**: {summary.get('filtered', 0)} "
                     f"| **已分析**: {summary.get('analyzed', 0)}")
        lines.append(f"- **看多**: {summary.get('bullish', 0)} "
                     f"| **看空**: {summary.get('bearish', 0)} "
                     f"| **观望**: {summary.get('watch', 0)}")
        lines.append("")

        codes = summary.get("codes") or []
        if codes:
            lines.append("## ① 逐只批量结论")
            lines.append("")
            lines.append("| 代码 | 名称 | 涨跌幅 | 结论 | 模型 |")
            lines.append("|------|------|--------|------|------|")
            for c in codes:
                chg = c.get("change_pct")
                chg_str = f"{chg:+.2f}%" if chg is not None else "—"
                lines.append(f"| {c.get('code', '')} | {c.get('name', '')} | "
                             f"{chg_str} | {c.get('verdict', '')} | "
                             f"{c.get('model_name', '')} |")
            lines.append("")

        tp_id = summary.get("twopass_record_id")
        if tp_id:
            from web.twopass_records import get_twopass_record
            tp = get_twopass_record(DEFAULT_CONFIG, tp_id)
            if tp:
                lines.append("## ② 二次分析")
                lines.append("")
                lines.append(f"- **标题**: {tp.get('title', '')}")
                lines.append(f"- **时间**: {tp.get('created_at', '')}")
                lines.append("")
                stocks = tp.get("stocks") or []
                if stocks:
                    lines.append("| 代码 | 名称 | 前次结论 | 本次结论 | 模型 |")
                    lines.append("|------|------|----------|----------|------|")
                    for s in stocks:
                        lines.append(f"| {s.get('code', '')} | {s.get('name', '')} | "
                                     f"{s.get('prevVerdict') or '—'} | "
                                     f"{s.get('newVerdict', '')} | "
                                     f"{s.get('newModel') or ''} |")
                    lines.append("")

        s3_id = summary.get("stage3_record_id")
        if s3_id:
            from web.stage3_records import get_stage3_record
            s3 = get_stage3_record(DEFAULT_CONFIG, s3_id)
            if s3:
                lines.append("## ③ 阶段三·顾问综合报告")
                lines.append("")
                lines.append(f"- **标题**: {s3.get('title', '')}")
                lines.append(f"- **时间**: {s3.get('created_at', '')}")
                lines.append("")
                if s3.get("report"):
                    lines.append(s3["report"])
                    lines.append("")
    else:
        if run_id is not None:
            from web.scheduler import get_scheduler_run
            last = get_scheduler_run(DEFAULT_CONFIG, run_id, expected_job_id=job_id)
        else:
            last = task.get("last_run")
        summary = (last or {}).get("summary") or {}
        if summary:
            # New-style: this run was recorded in scheduled_run_log.
            res = {
                "ticker": summary.get("ticker", ""),
                "company_name": summary.get("company_name", ""),
                "date": summary.get("date", ""),
                "rating": summary.get("rating", ""),
                "signal": summary.get("signal", ""),
                "analysts": summary.get("analysts", ""),
                "created_at": (last or {}).get("run_at", ""),
            }
        else:
            # Legacy deep run stored only in result_runs (pre-run-log).
            res = _get_deep_scheduled_result(DEFAULT_CONFIG, job_id)
        if not res:
            return None
        lines.append("## 运行结果")
        lines.append("")
        lines.append(f"- **股票**: {res.get('ticker', '')}（{res.get('company_name', '')}）")
        lines.append(f"- **分析日期**: {res.get('date', '')}")
        lines.append(f"- **评级**: {res.get('rating', '')}")
        lines.append(f"- **信号**: {res.get('signal', '')}")
        lines.append(f"- **分析师**: {res.get('analysts', '')}")
        lines.append(f"- **运行时间**: {res.get('created_at', '')}")
        lines.append("")

    return "\n".join(lines)


def _scheduler_report_filename(job_id: str, ext: str) -> str:
    """Sanitized download filename, e.g. Scheduled_abc123_2026-08-23.md."""
    task = _scheduler_manager.get_task(job_id)
    run_at = ""
    if task:
        ttype = task.get("task_type", "deep")
        if ttype in ("emwl_batch", "idx_batch") and task.get("last_run"):
            run_at = task["last_run"].get("run_at", "")
        else:
            res = _get_deep_scheduled_result(DEFAULT_CONFIG, job_id)
            run_at = res.get("created_at", "") if res else ""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{job_id}_{run_at}")
    return f"Scheduled_{safe}.{ext}"


@app.get("/api/scheduler/tasks/{job_id}/report")
def get_scheduled_task_report(job_id: str, format: str = Query(default="md"),
                              run_id: int | None = Query(default=None)):
    """Merged single-run report (md/json/docx) for one scheduled task.

    Batch tasks merge ① per-stock conclusions ② two-pass table ③ stage-3
    composite report; deep tasks render the saved run metadata. Pass ``run_id``
    (a ``scheduled_run_log`` row id) to fetch a specific past run instead of
    the most recent one.
    """
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    if not _scheduler_manager.get_task(job_id):
        raise HTTPException(404, "Task not found")
    # Normalize when called programmatically (Query default is a FieldInfo,
    # and `int|None` without a value would wrongly look like "run given").
    if not isinstance(run_id, int):
        try:
            run_id = int(run_id) if run_id else None
        except (TypeError, ValueError):
            run_id = None
    md = _build_scheduler_report_md(job_id, run_id=run_id)
    if md is None:
        raise HTTPException(404, "No run recorded yet")

    if format == "json":
        from web.scheduler import get_scheduler_run
        task = _scheduler_manager.get_task(job_id)
        ttype = task.get("task_type", "deep")
        if ttype in ("emwl_batch", "idx_batch"):
            if run_id is not None:
                payload = get_scheduler_run(DEFAULT_CONFIG, run_id,
                                            expected_job_id=job_id) or {}
            else:
                payload = task.get("last_run") or {}
        else:
            if run_id is not None:
                run = get_scheduler_run(DEFAULT_CONFIG, run_id,
                                        expected_job_id=job_id)
                payload = (run or {}).get("summary") or {}
            else:
                payload = _get_deep_scheduled_result(DEFAULT_CONFIG, job_id) or {}
        resp = Response(content=json.dumps(payload, ensure_ascii=False, indent=2),
                        media_type="application/json; charset=utf-8")
        resp.headers["Content-Disposition"] = (
            f"attachment; filename={_scheduler_report_filename(job_id, 'json')}")
        return resp

    if format == "docx":
        from web import docx_export
        body = ET.Element(f"{{{docx_export._WML}}}body")
        docx_export._md_block(body, md)
        doc_el = ET.Element(f"{{{docx_export._WML}}}document")
        doc_el.append(body)
        doc_xml = docx_export._XML + ET.tostring(doc_el, encoding="unicode")
        resp = Response(content=docx_export._build_zip(doc_xml),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        resp.headers["Content-Disposition"] = (
            f"attachment; filename={_scheduler_report_filename(job_id, 'docx')}")
        return resp

    # default: markdown
    resp = Response(content=md, media_type="text/markdown; charset=utf-8")
    resp.headers["Content-Disposition"] = (
        f"attachment; filename={_scheduler_report_filename(job_id, 'md')}")
    return resp


@app.post("/api/scheduler/tasks/{job_id}/push")
def push_scheduled_task_report(job_id: str,
                              run_id: int | None = Query(default=None)):
    """Manually re-push one task run's WeChat card (the export-row 「推送微信」
    button). Uses the exact same ``push_run_result`` as the automatic scheduled
    hook, so the card matches what the task pushes on its own. ``run_id``
    selects a past run; without it the latest run is pushed."""
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task = _scheduler_manager.get_task(job_id)
    if not task:
        raise HTTPException(404, "Task not found")
    # Full stored task_data (webhook_url / bot_id / bot_secret / ...) — the
    # get_task view only carries schedule metadata, not the delivery channel.
    task_data = _scheduler_manager.get_task_config(job_id) or task
    if not isinstance(run_id, int):
        try:
            run_id = int(run_id) if run_id else None
        except (TypeError, ValueError):
            run_id = None
    if run_id is not None:
        from web.scheduler import get_scheduler_run
        run = get_scheduler_run(DEFAULT_CONFIG, run_id, expected_job_id=job_id)
    else:
        run = task.get("last_run")
    ttype = (run or {}).get("task_type") or task_data.get("task_type", "deep")
    summary = (run or {}).get("summary") or {}
    if not summary and ttype == "deep":
        # Legacy deep run recorded only in result_runs (pre-run-log).
        summary = _get_deep_scheduled_result(DEFAULT_CONFIG, job_id) or {}
    if not summary:
        raise HTTPException(404, "该任务还没有运行记录")
    from web.wecom_push import push_run_result
    return push_run_result(task_data, summary, ttype)


@app.get("/api/scheduler/tasks/{job_id}/runs")
def list_scheduled_task_runs(job_id: str, limit: int = Query(default=50, ge=1, le=500)):
    """Run history for one task, newest first — each entry is a
    ``scheduled_run_log`` row (id, run_at, status, summary). Feed a row's ``id``
    back as ``?run_id=`` to the report endpoint to view that specific run."""
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    if not _scheduler_manager.get_task(job_id):
        raise HTTPException(404, "Task not found")
    try:  # normalize when called programmatically (Query default is a FieldInfo)
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    from web.scheduler import get_scheduler_runs
    return get_scheduler_runs(DEFAULT_CONFIG, job_id, limit=limit)


def _build_task_data(body: "ScheduledTaskCreate") -> dict:
    """Validate a task payload and build the stored task_data dict. Shared by
    create and edit so both paths enforce identical rules."""
    ttype = body.task_type or "deep"
    if ttype not in ("deep", "emwl_batch", "idx_batch"):
        raise HTTPException(400, f"Unsupported task_type: {ttype}")
    if ttype == "idx_batch":
        if not body.indexes:
            raise HTTPException(400, "indexes is required for idx_batch tasks")
        # Normalize legacy keys (e.g. an old "周期股100" config stored "period")
        # and reject anything outside the canonical set. An unknown key used to
        # be stored fine but then broke the edit form: no checkbox matched the
        # prefilled value, so saving was blocked by "请至少选择一个指数".
        indexes = [_resolve_index_key(i) for i in body.indexes]
        unknown = [i for i in indexes if i not in _INDEX_CONFIG]
        if unknown:
            raise HTTPException(
                400, f"Unknown index: {', '.join(unknown)}. Supported: "
                     f"{list(_INDEX_CONFIG.keys())}")
    if body.two_pass_mode not in ("reanalyze", "pack"):
        raise HTTPException(400, "two_pass_mode must be 'reanalyze' or 'pack'")
    task_data = body.model_dump()
    # The edit form sends the mask → collapse it to "" (keep global .env
    # secret) so a placeholder never gets stored as a real secret.
    if task_data.get("bot_secret") == BOT_SECRET_MASK:
        task_data["bot_secret"] = ""
    # push_user may arrive as a scalar (legacy single target) or a list
    # (multi-select UI). Always store a list; empty = 最近活跃 fallback.
    raw = task_data.get("push_user")
    if isinstance(raw, list):
        task_data["push_user"] = [str(u).strip() for u in raw if str(u).strip()]
    elif isinstance(raw, str) and raw.strip():
        task_data["push_user"] = [raw.strip()]
    else:
        task_data["push_user"] = []
    if ttype == "idx_batch":
        task_data["indexes"] = [_resolve_index_key(i) for i in task_data["indexes"]]
    task_data["workers"] = [w.model_dump() for w in (body.workers or [])[:8]]
    task_data["two_pass_workers"] = [w.model_dump() for w in (body.two_pass_workers or [])]
    task_data["date_str"] = task_data.pop("date", "")
    if not task_data["date_str"]:
        task_data["date_str"] = datetime.now().strftime("%Y-%m-%d")
    return task_data


@app.post("/api/scheduler/tasks")
def create_scheduled_task(body: ScheduledTaskCreate):
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task_data = _build_task_data(body)
    job_id = _scheduler_manager.add_task(task_data)
    _log_task_audit("create", job_id, task_data.get("name", ""),
                    task_data.get("task_type", ""),
                    detail=_redact_task_data(task_data))
    return {"job_id": job_id}


@app.get("/api/scheduler/tasks")
def list_scheduled_tasks():
    if not _scheduler_manager:
        return []
    return _scheduler_manager.list_tasks()


@app.get("/api/scheduler/tasks/{job_id}")
def get_scheduled_task(job_id: str):
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task = _scheduler_manager.get_task(job_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/api/scheduler/tasks/{job_id}/config")
def get_scheduled_task_config(job_id: str):
    """Full stored task_data for one task — used to prefill the edit form.

    Credentials never leave the server: bot_secret is returned masked (the edit
    form shows the mask; saving it collapses to the global .env secret). The
    stored per-task secret is only ever read server-side by the push path."""
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    cfg = _scheduler_manager.get_task_config(job_id)
    if not cfg:
        raise HTTPException(404, "Task not found")
    return _redact_task_data(cfg)


@app.put("/api/scheduler/tasks/{job_id}/config")
def update_scheduled_task_config(job_id: str, body: ScheduledTaskCreate):
    """Fully replace a task's schedule/params. job_id stays the same so run
    history and the report endpoint keep working."""
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task = _scheduler_manager.get_task(job_id)
    if not task:
        raise HTTPException(404, "Task not found")
    task_data = _build_task_data(body)
    if not _scheduler_manager.update_task(job_id, task_data):
        raise HTTPException(404, "Task not found")
    _log_task_audit("update", job_id, task_data.get("name", ""),
                    task_data.get("task_type", ""),
                    detail=_redact_task_data(task_data))
    return {"acknowledged": True}


@app.put("/api/scheduler/tasks/{job_id}")
def update_scheduled_task(job_id: str, body: ScheduledTaskUpdate):
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task = _scheduler_manager.get_task(job_id)
    if not task:
        raise HTTPException(404, "Task not found")
    action = None
    if body.enabled is True:
        ok = _scheduler_manager.resume_task(job_id)
        action = "resume"
    elif body.enabled is False:
        ok = _scheduler_manager.pause_task(job_id)
        action = "pause"
    else:
        ok = True
    if not ok:
        raise HTTPException(404, "Task not found")
    if action:
        _log_task_audit(action, job_id, task.get("name", ""),
                        task.get("task_type", ""), detail={"enabled": body.enabled})
    return {"acknowledged": True}


@app.delete("/api/scheduler/tasks/{job_id}")
def delete_scheduled_task(job_id: str):
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task = _scheduler_manager.get_task(job_id)
    if not task:
        raise HTTPException(404, "Task not found")
    ok = _scheduler_manager.delete_task(job_id)
    if not ok:
        raise HTTPException(404, "Task not found")
    _log_task_audit("delete", job_id, task.get("name", ""),
                    task.get("task_type", ""))
    return {"acknowledged": True}


@app.post("/api/scheduler/tasks/{job_id}/run")
def run_scheduled_task_now(job_id: str):
    """Trigger a scheduled task immediately (manual run) without changing its schedule."""
    if not _scheduler_manager:
        raise HTTPException(503, "Scheduler not available")
    task = _scheduler_manager.get_task(job_id)
    if not task:
        raise HTTPException(404, "Task not found")
    ok = _scheduler_manager.run_task(job_id)
    if not ok:
        raise HTTPException(404, "Task not found")
    _log_task_audit("run", job_id, task.get("name", ""), task.get("task_type", ""))
    return {"acknowledged": True}


@app.post("/api/scheduler/webhook/test")
def test_scheduler_webhook(body: WebhookTestPayload):
    """Push a test message to a WeCom group-bot webhook so the user can verify
    the URL works before relying on it for real run pushes."""
    from web.wecom_push import push_wecom
    content = (
        f"# ✅ 微信推送测试\n\n"
        f"**来自**: {body.name or 'QuantConclave'}\n\n"
        "如果你在微信里看到这条消息，说明定时任务的微信推送已配置成功。"
    )
    return push_wecom(body.webhook_url, content)


@app.post("/api/scheduler/bot/test")
def test_scheduler_bot(body: BotTestPayload):
    """Push a test markdown message through the WeCom 智能机器人 long-connection
    channel. Fails with a clear errmsg until the user has messaged the bot once
    (the server must learn the target chatid first).

    Empty or masked credentials fall back to the global .env secret — the form
    never carries the real secret, so the test button works with the mask
    auto-filled."""
    from web import wecom_bot
    bot_id = body.bot_id or DEFAULT_CONFIG.get("wecom_bot_id", "") or ""
    bot_secret = body.bot_secret
    if not bot_secret or bot_secret == BOT_SECRET_MASK:
        bot_secret = DEFAULT_CONFIG.get("wecom_bot_secret", "") or ""
    wecom_bot.ensure_started(bot_id, bot_secret)
    content = (
        f"# ✅ 微信推送测试\n\n"
        f"**来自**: {body.name or 'QuantConclave'}\n\n"
        "如果你在微信里看到这条消息，说明定时任务的微信推送已配置成功。"
    )
    return wecom_bot.push_markdown(content)


@app.get("/api/scheduler/bot/status")
def get_scheduler_bot_status():
    """Whether the bot is configured/connected and whether a push target (a
    chat the user already had with the bot) has been learned. Carries the
    global bot_id plus a masked placeholder for the secret — the real secret
    never leaves the server."""
    from web import wecom_bot
    status = wecom_bot.bot_status()
    status["bot_id"] = DEFAULT_CONFIG.get("wecom_bot_id", "") or ""
    sec = DEFAULT_CONFIG.get("wecom_bot_secret", "") or ""
    status["secret_configured"] = bool(sec)
    status["bot_secret"] = BOT_SECRET_MASK if sec else ""
    status["users"] = wecom_bot.registered_users()
    return status


@app.get("/api/scheduler/audit")
def list_task_audit_entries(limit: int = Query(default=200, ge=1, le=1000)):
    """Read-only: recent scheduled-task operation history (newest first)."""
    from web.task_audit import list_task_audit
    return list_task_audit(DEFAULT_CONFIG, limit=limit)


# ---- Shortlist API ----

class ShortlistItem(BaseModel):
    ticker: str
    name: str = ""
    source: str = ""
    score: float = 0
    price_at_add: float = 0


class ManualAddRequest(BaseModel):
    ticker: str


class ShortlistBatch(BaseModel):
    items: list[ShortlistItem]


def _fetch_price_for_ticker(ticker: str) -> float:
    """Fetch real-time price for a ticker. Returns 0 on failure."""
    try:
        from quantconclave.dataflows.tencent_realtime import _normalize_symbol
        import requests
        norm = _normalize_symbol(ticker)
        resp = requests.get(f"http://qt.gtimg.cn/q={norm}", timeout=5)
        resp.encoding = "gbk"
        if '="' in resp.text:
            fld = resp.text.split('="')[1].rstrip('";\n').split("~")
            if len(fld) > 3:
                return float(fld[3]) if fld[3] else 0
    except Exception:
        pass
    return 0


@app.get("/api/shortlist")
def get_shortlist():
    from web.results_store import list_shortlist
    return list_shortlist(DEFAULT_CONFIG)


@app.post("/api/shortlist")
def add_shortlist_item(body: ShortlistItem):
    from web.results_store import add_to_shortlist, list_shortlist
    price = body.price_at_add or _fetch_price_for_ticker(body.ticker)
    ok = add_to_shortlist(DEFAULT_CONFIG, body.ticker, body.name, body.source, body.score, price)
    if not ok:
        raise HTTPException(500, "Failed to add")
    return list_shortlist(DEFAULT_CONFIG)


@app.post("/api/shortlist/add-manual")
def add_shortlist_manual(body: ManualAddRequest):
    """Add a stock to shortlist by ticker — auto-resolve name + fetch real-time price."""
    from web.results_store import add_to_shortlist, list_shortlist
    from web.ticker_utils import resolve_company_name, normalize_ticker

    raw = body.ticker.strip()
    # Normalize
    norm = normalize_ticker(raw)
    if not norm:
        # Try bare code
        raw_upper = raw.upper()
        if raw_upper.isdigit() and len(raw_upper) == 6:
            norm = raw_upper + (".SH" if raw_upper[0] in "69" else ".SZ")
        else:
            raise HTTPException(400, f"无法解析股票代码: {raw}")

    # Resolve name
    ticker_norm, name = resolve_company_name(norm)
    if not name:
        name = norm

    # Fetch real-time price
    price = _fetch_price_for_ticker(ticker_norm or norm)

    ok = add_to_shortlist(DEFAULT_CONFIG, ticker_norm or norm, name, "manual", 0, price)
    if not ok:
        raise HTTPException(500, "添加失败")
    return list_shortlist(DEFAULT_CONFIG)


@app.get("/api/shortlist/validate/{ticker}")
def validate_shortlist_item(ticker: str):
    """Get validation data for a shortlist item: current price vs add price."""
    from web.results_store import list_shortlist
    items = list_shortlist(DEFAULT_CONFIG)
    item = next((i for i in items if i["ticker"].upper() == ticker.upper()), None)
    if not item:
        raise HTTPException(404, "Not in shortlist")

    current_price = _fetch_price_for_ticker(ticker)
    add_price = item.get("price_at_add", 0) or 0
    change_pct = ((current_price - add_price) / add_price * 100) if add_price > 0 else 0

    return {
        "ticker": item["ticker"],
        "name": item.get("name", ""),
        "added_at": item.get("added_at", ""),
        "price_at_add": add_price,
        "current_price": current_price,
        "change_pct": round(change_pct, 2),
        "source": item.get("source", ""),
    }


@app.post("/api/shortlist/batch")
def batch_add_shortlist(body: ShortlistBatch):
    from web.results_store import add_to_shortlist, list_shortlist
    for item in body.items:
        add_to_shortlist(DEFAULT_CONFIG, item.ticker, item.name, item.source, item.score)
    return list_shortlist(DEFAULT_CONFIG)


@app.delete("/api/shortlist/{ticker}")
def delete_shortlist_item(ticker: str):
    from web.results_store import remove_from_shortlist, list_shortlist
    ok = remove_from_shortlist(DEFAULT_CONFIG, ticker)
    if not ok:
        raise HTTPException(404, "Not found in shortlist")
    return list_shortlist(DEFAULT_CONFIG)


@app.delete("/api/shortlist")
def clear_shortlist_all():
    from web.results_store import clear_shortlist
    n = clear_shortlist(DEFAULT_CONFIG)
    return {"deleted": n}


# ---- Eastmoney Watchlist API ----

@app.get("/api/eastmoney/watchlist")
def get_eastmoney_watchlist():
    """Fetch the user's Eastmoney (东方财富) self-selected watchlist with real-time prices.

    Persists the synced list to results.db (incremental merge — local data is
    never deleted). On failure / missing key, falls back to the locally-cached
    list so the dashboard works without needing a successful sync every time.
    """
    _load_env()
    import requests as _req, os as _os
    from web.watchlist_store import (
        merge_watchlist_stocks, log_sync, get_watchlist_stocks,
    )

    key = _os.environ.get("MX_APIKEY", "")
    if not key:
        # Cache fallback — MUST NOT set top-level "error" (frontend aborts on it)
        cached = get_watchlist_stocks(DEFAULT_CONFIG)
        if cached:
            return {"count": len(cached), "stocks": cached, "from_cache": True,
                    "notice": "MX_APIKEY 未配置，返回本地缓存"}
        return {"error": "MX_APIKEY not set", "stocks": []}

    try:
        resp = _req.post(
            "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get",
            headers={"apikey": key, "Content-Type": "application/json"},
            json={"query": "查询我的自选股"}, timeout=15,
        )
        data = resp.json()
        all_results = data.get("data", {}).get("allResults", {})
        result = all_results.get("result", {})
        data_list = result.get("dataList", [])
        count = result.get("total", 0)

        # Parse structured dataList (139 entries)
        stocks = []
        for item in data_list:
            try:
                code = str(item.get("SECURITY_CODE", ""))
                name = str(item.get("SECURITY_SHORT_NAME", ""))
                market = str(item.get("MARKET_SHORT_NAME", ""))
                price = item.get("NEWEST_PRICE", 0) or 0
                chg = item.get("CHG", 0) or 0

                if code and market:
                    code = f"{code}.{market}" if not code.endswith(market) else code

                if code:
                    # Parse additional fields from the date-suffixed keys
                    def _f(pattern, default=0):
                        for k, v in item.items():
                            if pattern in k:
                                try: return float(str(v).replace(",",""))
                                except: pass
                        return default
                    stocks.append({
                        "code": code,
                        "name": name,
                        "price": float(str(price).replace(",","")),
                        "change_pct": float(str(chg).replace(",","")),
                        "turnover": _f("TURNOVER_RATE"),
                        "vol_ratio": _f("LIANGBI"),
                    })
            except (ValueError, IndexError):
                pass

        # Persist: incremental merge (INSERT new, UPDATE existing, never DELETE)
        stats = merge_watchlist_stocks(DEFAULT_CONFIG, stocks)
        log_sync(DEFAULT_CONFIG, total=count or len(stocks),
                 added=stats["added"], updated=stats["updated"], removed=0,
                 source="mxapi")

        # Attach analysis data (batch — 1 DB query instead of N)
        from web.watchlist_store import attach_analysis_to_stocks
        attach_analysis_to_stocks(DEFAULT_CONFIG, stocks)

        return {"count": count or len(stocks), "stocks": stocks,
                "synced_at": stats["synced_at"], "from_cache": False}
    except Exception as e:
        # Cache fallback on any sync failure (API down, network, parse error)
        cached = get_watchlist_stocks(DEFAULT_CONFIG)
        if cached:
            return {"count": len(cached), "stocks": cached, "from_cache": True,
                    "notice": f"实时同步失败: {e}，返回本地缓存"}
        return {"error": str(e), "stocks": []}


@app.get("/api/eastmoney/watchlist/detail/{code}")
def watchlist_stock_detail(
    code: str,
    provider: str = Query(default=""),
    model: str = Query(default=""),
    provider2: str = Query(default=""),
    model2: str = Query(default=""),
    no_llm: bool = False,
):
    """Detailed data for a watchlist stock — flow detail + real-time snapshot.

    Optional provider/model params override the LLM. When provider2/model2 are
    set, both models run server-side and results are returned under ``analysis``
    (model 1) and ``analysis2`` / ``verdict2`` / ``model_name2`` (model 2).
    """
    _load_env()
    import os as _os, csv, io

    # Programmatic callers (web/scheduled_batch.py, tests) may call this function
    # directly and omit the Query params — their FastAPI Query(default="")
    # defaults are FieldInfo objects, not "". Coerce to real strings so
    # create_llm_client never receives a FieldInfo repr as the provider name.
    def _q(v):
        return v if isinstance(v, str) else ""
    provider = _q(provider)
    model = _q(model)
    provider2 = _q(provider2)
    model2 = _q(model2)

    result = {"code": code}

    # ── 1. Real-time snapshot from Tencent ──
    try:
        from quantconclave.dataflows.tencent_realtime import get_tencent_realtime_quote, _normalize_symbol
        import requests as _req
        norm = _normalize_symbol(code)
        resp = _req.get(f"http://qt.gtimg.cn/q={norm}", timeout=5)
        resp.encoding = "gbk"
        fld = resp.text.split('="')[1].rstrip('";\n').split("~") if '="' in resp.text else []
        result["rt_price"] = fld[3] if len(fld) > 3 else "?"
        # Tencent qt.gtimg.cn: fld[31]=涨跌额(元), fld[32]=涨跌幅(%). 之前读写反了,
        # 提示词【实时行情】显示"涨跌3.26%"而实际是 +9.99% 涨停(600460 08-21)。
        result["rt_change"] = fld[31] if len(fld) > 31 else "?"  # 涨跌额(元)
        result["rt_change_pct"] = fld[32] if len(fld) > 32 else "?"  # 涨跌幅(%)
        result["rt_high"] = fld[33] if len(fld) > 33 else "?"
        result["rt_low"] = fld[34] if len(fld) > 34 else "?"
        result["rt_volume"] = fld[6] if len(fld) > 6 else "?"
        result["rt_amount"] = fld[57] if len(fld) > 57 else "?"  # 万元
        result["rt_turnover"] = fld[38] if len(fld) > 38 else "?"  # 换手率%
        result["rt_vol_ratio"] = fld[49] if len(fld) > 49 else "?"  # 量比
    except Exception:
        pass

    # ── 2. Money flow detail (10 days, cached locally) ──
    flow_detail = []
    try:
        from datetime import datetime, timedelta
        from web.moneyflow_cache import fetch_or_cache_moneyflow
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        raw = fetch_or_cache_moneyflow(DEFAULT_CONFIG, code, start, end)
        for line in raw.split("\n"):
            if "ts_code" in line and "trade_date" in line and "net_amount" in line:
                # Found header — parse all lines from here
                csv_text = line + "\n"
                # Take all non-empty lines after the header (filter out only blank lines)
                remaining = raw.split("\n")[raw.split("\n").index(line)+1:]
                data_lines = [l for l in remaining if l.strip() and ',' in l]
                csv_text = line + "\n" + "\n".join(data_lines)
                reader = csv.DictReader(io.StringIO(csv_text))
                for r in reader:
                    try:
                        d = r.get("trade_date", "").strip()
                        if not d: continue
                        # The cache stores 元 (get_money_flow ×1e4 normalizes tushare's
                        # 万元, and the user_version=2 migration rebuilt it in 元). The
                        # prompt + raw-flow panel display 万元, so convert ONCE here —
                        # the old code appended a bare "万" to the 元 values, a 1万倍
                        # inflation (002566: 主力合计 was rendered +19009100万 = 1900亿
                        # for a 27亿-cap stock; real 12日主力 = +1900.91万元).
                        _W = 1e4
                        _b_elg = float(r.get("buy_elg_amount", 0) or 0) / _W
                        _s_elg = float(r.get("sell_elg_amount", 0) or 0) / _W
                        _b_lg = float(r.get("buy_lg_amount", 0) or 0) / _W
                        _s_lg = float(r.get("sell_lg_amount", 0) or 0) / _W
                        _b_md = float(r.get("buy_md_amount", 0) or 0) / _W
                        _s_md = float(r.get("sell_md_amount", 0) or 0) / _W
                        _b_sm = float(r.get("buy_sm_amount", 0) or 0) / _W
                        _s_sm = float(r.get("sell_sm_amount", 0) or 0) / _W
                        # 主力 = 超大单+大单. tushare net_amount 语义未定义 (四档
                        # 恒和≈0, net_amount 与主力方向常相反 — 002495 11天里6天反向),
                        # 绝不把 net_amount 当净额/全口径引用 (002696 铁律#6).
                        flow_detail.append({
                            "date": d,
                            "net": round((_b_elg - _s_elg) + (_b_lg - _s_lg), 0),
                            "buy_elg": round(_b_elg, 0),
                            "sell_elg": round(_s_elg, 0),
                            "buy_lg": round(_b_lg, 0),
                            "sell_lg": round(_s_lg, 0),
                            "buy_md": round(_b_md, 0),
                            "sell_md": round(_s_md, 0),
                            "buy_sm": round(_b_sm, 0),
                            "sell_sm": round(_s_sm, 0),
                        })
                    except (ValueError, KeyError): pass
                break  # Only process the first matching header block
    except Exception:
        pass
    # Normalize to newest-first. The moneyflow cache reads back ASC
    # (oldest→newest); downstream code (dates_range, OHLCV window, flow_text)
    # assumes the most recent trading day is flow_detail[0].
    flow_detail.sort(key=lambda r: r["date"], reverse=True)
    result["flow_detail"] = flow_detail

    # ── 2b. Multi-horizon 主力 (当日/5/20/60日 + 构成) from the 90-day cache ──
    # fetch_or_cache_moneyflow above already back-filled ~90 calendar days of 元
    # rows into moneyflow_cache. Read the full window, convert 元→万元 ONCE,
    # and aggregate per horizon — no extra tushare calls. Computed OUTSIDE the
    # no_llm guard so the frontend raw-flow panel can render it too. Best-effort:
    # on any failure result stays {} and the prompt/panel simply omit the block.
    result["flow_multi_horizon"] = {}
    try:
        from datetime import datetime as _mh_dt, timedelta as _mh_td
        from web.moneyflow_cache import get_cached_moneyflow
        from quantconclave.dataflows.eastmoney_sector import aggregate_moneyflow_wan
        _mh_rows = get_cached_moneyflow(
            DEFAULT_CONFIG, code,
            (_mh_dt.now() - _mh_td(days=90)).strftime("%Y-%m-%d"),
            _mh_dt.now().strftime("%Y-%m-%d"),
        )
        if _mh_rows:
            # cache stores 元 (get_money_flow ×1e4 normalized tushare's 万元);
            # display is 万元 — convert ONCE here, same rule as flow_detail.
            _mh_wan = [{
                "trade_date": r["trade_date"],
                "buy_elg_amount": (float(r.get("buy_elg_amount") or 0) / 1e4),
                "sell_elg_amount": (float(r.get("sell_elg_amount") or 0) / 1e4),
                "buy_lg_amount": (float(r.get("buy_lg_amount") or 0) / 1e4),
                "sell_lg_amount": (float(r.get("sell_lg_amount") or 0) / 1e4),
                "buy_md_amount": (float(r.get("buy_md_amount") or 0) / 1e4),
                "sell_md_amount": (float(r.get("sell_md_amount") or 0) / 1e4),
                "buy_sm_amount": (float(r.get("buy_sm_amount") or 0) / 1e4),
                "sell_sm_amount": (float(r.get("sell_sm_amount") or 0) / 1e4),
            } for r in _mh_rows]
            _mh = aggregate_moneyflow_wan(_mh_wan)
            if _mh:
                result["flow_multi_horizon"] = _mh
                # Best-effort 流通市值 (万元) — the scale anchor that makes the
                # absolute 净额 interpretable (same +5000万: 强吸筹 for a 27亿
                # micro-cap, 噪音 for a 2000亿 mega-cap). Anchor on the newest
                # cached trade_date (a real trading day) so daily_basic hits.
                try:
                    from quantconclave.dataflows.eastmoney_sector import _get_pro as _mh_pro
                    _td = _mh_rows[-1]["trade_date"]
                    _db = _mh_pro().daily_basic(
                        ts_code=code, trade_date=_td, fields="ts_code,circ_mv"
                    )
                    if _db is not None and not _db.empty:
                        result["flow_multi_horizon"]["circ_mv_wan"] = float(
                            _db.iloc[0]["circ_mv"] or 0
                        )
                except Exception:
                    pass  # optional context — block still renders without it
    except Exception:
        result["flow_multi_horizon"] = {}

    # ── MX realtime 主力资金 (DDX/DDY/DDZ) + 暗盘/大宗 — best-effort, never blocks ──
    # tushare moneyflow is EOD/delayed; MX returns the SAME-day live 主力
    # snapshot with DDX/DDY/DDZ plus block-trade (大宗交易) detail. Both fetched
    # in parallel and stored on result so the LLM prompt AND the frontend
    # no_llm panel can render them. Degrades silently (empty strings) on any
    # failure — the analysis never blocks on these bonus signals.
    result["mx_ddx"] = ""
    result["mx_block_trades"] = ""
    try:
        from concurrent.futures import ThreadPoolExecutor
        from quantconclave.dataflows.mx_client import query_text as _mx_q

        def _mx_fetch(q: str) -> str:
            try:
                t = _mx_q(q, timeout=15)
                return t if t and "query failed" not in t else ""
            except Exception:
                return ""

        with ThreadPoolExecutor(max_workers=2) as _mx_ex:
            _f1 = _mx_ex.submit(_mx_fetch, f"{code} 主力资金流向")
            _f2 = _mx_ex.submit(_mx_fetch, f"{code} 大宗交易明细")
            result["mx_ddx"] = _f1.result()
            result["mx_block_trades"] = _f2.result()
    except Exception:
        pass

    # ── 3. LLM analysis (skip when no_llm=True or flow_detail empty) ──
    if flow_detail and not no_llm:
        elg_net = sum(r["buy_elg"] - r["sell_elg"] for r in flow_detail)
        lg_net = sum(r["buy_lg"] - r["sell_lg"] for r in flow_detail)
        net_all = elg_net + lg_net          # 主力合计 = 超大+大单 (与下面子行一致)
        pos_days = sum(1 for r in flow_detail if r["net"] > 0)
        def _fmt_y(d: str) -> str:
            """Normalize YYYYMMDD → YYYY-MM-DD (pass through other formats)."""
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else str(d)
        dates_range = f"{_fmt_y(flow_detail[-1]['date'])}~{_fmt_y(flow_detail[0]['date'])}" if flow_detail else "?"
        rt_info = f"现价{result.get('rt_price','?')} | 涨跌{result.get('rt_change_pct','?')}% | 量比{result.get('rt_vol_ratio','?')} | 换手{result.get('rt_turnover','?')}% | 最高{result.get('rt_high','?')} 最低{result.get('rt_low','?')} | 量{result.get('rt_volume','?')}手"
        flow_text = "\n".join(
            f"{_fmt_y(r['date'])} 主力净{r['net']:+.0f}万 超大净{r['buy_elg']-r['sell_elg']:+.0f}万 大单净{r['buy_lg']-r['sell_lg']:+.0f}万"
            for r in flow_detail
        )

        # ── Fetch supporting data (before prompt + LLM so model2 can reuse) ──
        ohlcv_text = ""
        try:
            from quantconclave.dataflows.interface import route_to_vendor
            from quantconclave.backtest.data import parse_ohlcv_csv
            # flow_detail dates are YYYYMMDD from tushare moneyflow, convert to YYYY-MM-DD.
            # min/max makes the window independent of list ordering.
            _sd = min(r["date"] for r in flow_detail)   # oldest
            _ed = max(r["date"] for r in flow_detail)   # newest
            start = f"{_sd[:4]}-{_sd[4:6]}-{_sd[6:8]}" if len(_sd) == 8 else _sd
            end = f"{_ed[:4]}-{_ed[4:6]}-{_ed[6:8]}" if len(_ed) == 8 else _ed
            # Fetch a few days earlier than the window so the first displayed
            # row's 涨幅 is prev-close based (like tushare pct_chg). The app
            # used to fall back to open→close for the first row, which inflates
            # the move on gap days (600460 08-06 showed +4.21%, real was +1.76%).
            _start_dt = datetime.strptime(start, "%Y-%m-%d")
            _start_early = (_start_dt - timedelta(days=7)).strftime("%Y-%m-%d")
            raw = route_to_vendor("get_stock_data", code, start_date=_start_early, end_date=end)
            df = parse_ohlcv_csv(str(raw))
            if df is not None and not df.empty:
                # Window-only view: the extra pre-window rows exist solely to seed
                # the first row's prev-close. Position/extremes/trend must use the
                # SAME window as the moneyflow, or the model sees a 17-day range
                # next to a 12-day moneyflow (000603 reported "近17日区间96%").
                _w = df[df["date"].astype(str).str[:10] >= start].reset_index(drop=True)
                lines = []
                prev_close = None
                _pre = df[df["date"].astype(str).str[:10] < start]
                if len(_pre):
                    prev_close = float(_pre.iloc[-1]["close"])  # day just before window
                for _, row in _w.iterrows():   # window rows, ascending by date
                    d = str(row.get("date", ""))[:10]
                    o = round(float(row.get("open", 0)), 2)
                    c = round(float(row.get("close", 0)), 2)
                    h = round(float(row.get("high", 0)), 2)
                    l = round(float(row.get("low", 0)), 2)
                    v = int(row.get("volume", 0))
                    # 涨幅 = 相对昨收的日收益率 (matches tushare pct_chg), not
                    # intraday open→close which mislabels daily direction.
                    if prev_close:
                        chg = round((c - prev_close) / prev_close * 100, 2)
                    else:
                        chg = round((c - o) / o * 100, 2) if o > 0 else 0
                    prev_close = c
                    # 换手% (tushare daily_basic / akshare TurnoverRate) appended when
                    # the vendor supplied it — the paths demand 换手<8% / 高换手>10%
                    # checks per day, so the table must carry it per row.
                    _t = float(row.get("turnover", 0) or 0)
                    _t_str = f" 换手{_t:.2f}%" if _t > 0 else ""
                    lines.append(f"{d} 开{o} 收{c} 高{h} 低{l} 量{v} 涨幅{chg:+.2f}%{_t_str}")
                # Newest-first to match flow_text (recent days read first).
                ohlcv_text = "\n".join(reversed(lines))
        except Exception as e:
            logger.warning("OHLCV fetch failed for %s (start=%s end=%s): %s", code, start, end, e)

        # ── Pre-computed extremes ──
        # qwen keeps getting "max/min day" wrong when asked to scan the table
        # itself (000506: 08-10+9.98% called "12日最大涨幅" though 08-07 was
        # +10.01%; 08-11-5.27% called "最大跌幅" though 08-13 was -7.92%).
        # Compute the extremes here and hand them to the model verbatim.
        extremes_text = ""
        if flow_detail and ohlcv_text:
            try:
                _f_rows = [{
                    "d": r["date"],
                    "elg": r["buy_elg"] - r["sell_elg"],
                    "lg": r["buy_lg"] - r["sell_lg"],
                } for r in flow_detail]
                _elg_max = max(_f_rows, key=lambda x: x["elg"])
                _elg_min = min(_f_rows, key=lambda x: x["elg"])
                _lg_max = max(_f_rows, key=lambda x: x["lg"])
                _lg_min = min(_f_rows, key=lambda x: x["lg"])
                # 主力(超大+大单)合并口径的单日极值 — models hand-compute a max
                # outflow day and skip an earlier one (002463 called 08-19 -94164
                # the 12日最大 but 08-10 was -110414). Same pre-compute treatment.
                _main_max = max(_f_rows, key=lambda x: x["elg"] + x["lg"])
                _main_min = min(_f_rows, key=lambda x: x["elg"] + x["lg"])
                _o = _w.sort_values("date").reset_index(drop=True)
                _closes = [float(x) for x in _o["close"]]
                # First-row 涨幅 uses the same prev-close seed as the displayed
                # OHLCV table so the extremes can never disagree with the table.
                _p0 = float(_pre.iloc[-1]["close"]) if len(_pre) else None
                _pcts = []
                for _i in range(len(_o)):
                    if _i == 0 and _p0:
                        _pcts.append((_closes[_i] / _p0 - 1) * 100)
                    elif _i == 0:
                        _op = float(_o.loc[_i, "open"]); _cl = float(_o.loc[_i, "close"])
                        _pcts.append((_cl / _op - 1) * 100 if _op else 0.0)
                    else:
                        _pcts.append((_closes[_i] / _closes[_i - 1] - 1) * 100)
                _pct_max_i = _pcts.index(max(_pcts))
                _pct_min_i = _pcts.index(min(_pcts))
                _vol_max_i = int(_o["volume"].astype(float).idxmax())
                _hi_px = float(_o["high"].astype(float).max())
                _hi_d = str(_o.loc[_o["high"].astype(float).idxmax(), "date"])[:10]
                _lo_px = float(_o["low"].astype(float).min())
                _lo_d = str(_o.loc[_o["low"].astype(float).idxmin(), "date"])[:10]
                _hi_dist = round((_hi_px - _closes[-1]) / _hi_px * 100, 1)
                _avg_v5 = float(_o["volume"].astype(float).tail(5).mean()) / 1e4  # 万手
                _d_ohlcv = lambda i: str(_o.loc[i, "date"])[:10]
                # 主力同向流入日 (超大单与大单同为净流入) — models under-count when
                # asked to scan the table (600988 called 08-18/19 the "唯一" healthy
                # same-direction run, but 08-10 & 08-14 also qualify). Pre-compute
                # the exact set and the longest consecutive streak, ascending order.
                _asc = sorted(_f_rows, key=lambda x: x["d"])
                _same_days = [r["d"] for r in _asc if r["elg"] > 0 and r["lg"] > 0]
                _longest = 0; _cur = 0; _cur_start = None; _best_start = None; _best_end = None
                for _r in _asc:
                    if _r["elg"] > 0 and _r["lg"] > 0:
                        if _cur == 0:
                            _cur_start = _r["d"]
                        _cur += 1
                        if _cur > _longest:
                            _longest = _cur; _best_start = _cur_start; _best_end = _r["d"]
                    else:
                        _cur = 0
                if _same_days:
                    _same_txt = "、".join(_fmt_y(_d) for _d in _same_days)
                    if _longest >= 2:
                        _same_txt += f"，最长连续 {_longest} 日({_fmt_y(_best_start)}~{_fmt_y(_best_end)})"
                else:
                    _same_txt = "无"
                # 主力净流出日数 — models hand-count and mis-pair the 主力 sum with a
                # different day count (000060: "主力 8/12天净流出" — actually 主力 is
                # 6/12). tushare net_amount (四档恒和≈0) 被当全口径时方向常与主力相反,
                # 全库只留主力一种口径 (002696 铁律#6).
                _n_neg_main = sum(1 for r in _asc if r["elg"] + r["lg"] < 0)
                _n_days = len(_asc)
                # 最长连续主力净流出段 — models hand-count a "连续流出" run and skip
                # an inflow day in the middle (603619 claimed 08-17~19 连续流出, but
                # 08-18 was 主力+4288 流入; the real run is only 2 days). Same 预计算
                # treatment as the inflow streak above.
                _neg_run = 0; _neg_best = 0; _neg_start = None; _neg_best_start = None; _neg_best_end = None
                for _r in _asc:
                    if _r["elg"] + _r["lg"] < 0:
                        if _neg_run == 0:
                            _neg_start = _r["d"]
                        _neg_run += 1
                        if _neg_run > _neg_best:
                            _neg_best = _neg_run; _neg_best_start = _neg_start; _neg_best_end = _r["d"]
                    else:
                        _neg_run = 0
                if _neg_best >= 2:
                    _neg_streak_txt = f" | 主力最长连续净流出{_neg_best}日({_fmt_y(_neg_best_start)}~{_fmt_y(_neg_best_end)})"
                else:
                    _neg_streak_txt = ""
                # 换手要点 — 路径框架要求逐日核对 换手<8% / 高换手>10%，把窗口内换手
                # 最高/最低 + 最新量价日分位预计算好交给模型（与上面价格/资金极值相同的
                # 防编造纪律）。0 表示该日 vendor 未提供换手（如 yfinance），统计时剔除。
                _turnover_txt = ""
                if len(_w):
                    # parse_ohlcv_csv guarantees a float "turnover" column
                    # (0 when the vendor didn't supply it); no pandas import here.
                    _tv = _w["turnover"].astype(float)
                    _t_now = float(_tv.iloc[-1]) if len(_tv) else 0.0
                    _t_has = (_tv > 0)
                    if _t_has.any():
                        _tmax_i = int(_tv[_t_has].idxmax())
                        _tmin_i = int(_tv[_t_has].idxmin())
                        _turnover_txt = (
                            f"  最高换手: {_d_ohlcv(_tmax_i)} {_tv[_tmax_i]:.2f}%"
                            f" | 最低换手: {_d_ohlcv(_tmin_i)} {_tv[_tmin_i]:.2f}%"
                            f" | 最新量价日{_d_ohlcv(len(_tv)-1)}换手 {_t_now:.2f}%"
                        )
                        if _t_now > 0:
                            _t_rank = round((_tv[_t_has] < _t_now).mean() * 100, 0)
                            _turnover_txt += f"（位于近{int(_t_has.sum())}日{_t_rank:.0f}%分位）\n"
                        else:
                            _turnover_txt += "（最新日换手数据缺失）\n"
                    else:
                        _turnover_txt = "  换手率数据缺失（窗口内无换手记录，路径B/E的换手条件无法逐日核对）\n"
                extremes_text = (
                    f"\n【极值参考】(程序按全表自动计算，引用'最大/最小/唯一/最高/最低/距高点/均量/平均/最大成交量/主力单日最大流入/主力单日最大流出/换手最高/换手最低/换手分位'时直接采用下列值并逐字一致，凡'最大/次大/唯一'类断言——含'量最大/最大量/量能最大/放量最大'等量能表述——必须与下列极值行日期一致，断言里出现下列行之外的日期即视为错误。'累计/合计/超X亿'类数字必须由【期间主力资金】直接引用或逐日相加复算，严禁凭印象估数，勿自行扫描重排或心算)\n"
                    f"  最大涨幅: {_d_ohlcv(_pct_max_i)} {max(_pcts):+.2f}% | 最大跌幅: {_d_ohlcv(_pct_min_i)} {min(_pcts):+.2f}%\n"
                    f"  区间最高价: {_hi_d} {_hi_px:.2f} (现价距其{_hi_dist:.1f}%) | 区间最低价: {_lo_d} {_lo_px:.2f}\n"
                    f"  近5日均量: {_avg_v5:.1f}万手 | 最大成交量: {_d_ohlcv(_vol_max_i)} {float(_o.loc[_vol_max_i, 'volume']) / 1e4:.1f}万手\n"
                    f"  超大单最大流入: {_fmt_y(_elg_max['d'])} {_elg_max['elg']:+.0f}万 | 最大流出: {_fmt_y(_elg_min['d'])} {_elg_min['elg']:+.0f}万\n"
                    f"  大单最大流入: {_fmt_y(_lg_max['d'])} {_lg_max['lg']:+.0f}万 | 最大流出: {_fmt_y(_lg_min['d'])} {_lg_min['lg']:+.0f}万\n"
                    f"  主力(超大+大单)单日最大流入: {_fmt_y(_main_max['d'])} {_main_max['elg']+_main_max['lg']:+.0f}万 | 最大流出: {_fmt_y(_main_min['d'])} {_main_min['elg']+_main_min['lg']:+.0f}万\n"
                    f"  主力同向流入日(超大单+大单同为净流入): {_same_txt}\n"
                    f"  主力净流出{_n_neg_main}/{_n_days}日{_neg_streak_txt}\n"
                    f"{_turnover_txt}"
                )
            except Exception:
                extremes_text = ""

        block_trade_text = ""
        if result.get("mx_block_trades"):
            # 妙想(MX) real-time 暗盘/大宗 first — real data with 折溢价率/席位.
            block_trade_text = f"\n【大宗交易暗盘】(妙想实时)\n{result['mx_block_trades']}\n"
        else:
            try:
                # akshare fallback (may be unreachable on some machines).
                from quantconclave.dataflows.block_trade_data import get_block_trade_detail
                bt = get_block_trade_detail(code, lookback_days=15)
                if bt and "共 0 笔" not in bt:
                    block_trade_text = f"\n【大宗交易暗盘】\n{bt}\n"
            except Exception:
                pass

        recent_history = []
        try:
            from web.watchlist_store import get_watchlist_analysis_history
            recent_history = get_watchlist_analysis_history(
                DEFAULT_CONFIG, code, limit=3,
            )
            recent_history = [r for r in recent_history if r.get("verdict")]
        except Exception:
            pass

        # ── Margin trading (融资融券) data — last 5 trading days ──
        margin_text = ""
        try:
            import tushare as ts; import os as _os
            from datetime import datetime as _dt, timedelta as _td
            token = _os.environ.get("TUSHARE_TOKEN","")
            if token:
                pro = ts.pro_api(token)
                end_d = _dt.now().strftime("%Y%m%d")
                start_d = (_dt.now() - _td(days=10)).strftime("%Y%m%d")
                mdf = pro.margin_detail(ts_code=code, start_date=start_d, end_date=end_d)
                if mdf is not None and not mdf.empty:
                    mdf = mdf.sort_values("trade_date", ascending=True).tail(5)
                    lines = []
                    for _, r in mdf.iterrows():
                        td = r["trade_date"]
                        rz_net = (float(r.get("rzmre",0)) - float(r.get("rzche",0))) / 1e4  # 万元
                        rq_sell = (float(r.get("rqmcl",0)) - float(r.get("rqchl",0))) / 1e4  # 万股, 当日融券净卖出
                        rq_bal = float(r.get("rqyl",0)) / 1e4  # 万股, 融券余量=做空头寸
                        rzye = float(r.get("rzye",0)) / 1e8  # 亿
                        lines.append(f"{td} 融资余额{rzye:.1f}亿 | 融资净买{rz_net:+.0f}万 | 融券净卖{rq_sell:+.2f}万股 余量{rq_bal:.1f}万股")
                    margin_text = f"\n【融资融券(5日)】{code}\n" + "\n".join(lines) + "\n"
        except Exception:
            pass

        # ── Trend assessment from OHLCV (full available range, up to 15 days) ──
        trend_note = ""
        if ohlcv_text and df is not None and not df.empty and len(_w) >= 3:
            try:
                closes = [float(row.get("close", 0)) for _, row in _w.iterrows()]
                n = len(closes)
                last = closes[-1]
                # Full range change
                chg_full = round((closes[-1] - closes[0]) / closes[0] * 100, 2) if n >= 2 else 0
                # Half-range (recent half)
                half = max(n // 2, 3)
                chg_half = round((closes[-1] - closes[-half]) / closes[-half] * 100, 2) if n >= half else chg_full
                # Short-term (last 5 or less)
                s5 = min(5, n)
                chg_short = round((closes[-1] - closes[-s5]) / closes[-s5] * 100, 2) if n >= 2 else 0
                # Moving averages
                ma5 = round(sum(closes[-5:]) / 5, 2) if n >= 5 else None
                ma10 = round(sum(closes[-10:]) / 10, 2) if n >= 10 else None
                above_ma = []
                if ma5 and last > ma5: above_ma.append("MA5")
                if ma10 and last > ma10: above_ma.append("MA10")
                ma_note = f"站上{'/'.join(above_ma)}" if above_ma else ("低于均线" if ma5 or ma10 else "")
                # Trend direction
                if chg_short > 2: trend_dir = "短期走强"
                elif chg_short < -2: trend_dir = "短期走弱"
                else: trend_dir = "横盘震荡"
                # Position within the available range — the same money-flow
                # direction means opposite things at the low vs the high end
                # (低位放量=吸筹, 高位放量=换手/派发). This is the anchor for
                # the expert four-stage mapping in the analysis points.
                lo = min(closes); hi = max(closes)
                if hi > lo:
                    pos_pct = round((last - lo) / (hi - lo) * 100, 0)
                    pos_band = "低位" if pos_pct < 35 else ("高位" if pos_pct > 70 else "中位")
                    # 距区间最高 uses the intraday HIGH with its date — a plain
                    # "距高点x%" was paired with the wrong price by qwen before
                    # (000603: it attached the max-CLOSE distance 1.4% to the
                    # intraday high 39.33; true distance was 6.9%).
                    _hp = float(_w["high"].astype(float).max())
                    _hd = str(_w.loc[_w["high"].astype(float).idxmax(), "date"])[:10]
                    dist_high = round((_hp - last) / _hp * 100, 1)
                    pos_txt = f" | 位置:{pos_band}(近{n}日区间{pos_pct:.0f}%) 距区间最高{_hp:.2f}({_hd}) {dist_high:.1f}%"
                else:
                    pos_txt = ""
                trend_note = f"\n【股价走势】近{n}日整体{chg_full:+.1f}% | 近半程{chg_half:+.1f}% | 近{s5}日{chg_short:+.1f}% | 现价{last:.2f}{' '+ma_note if ma_note else ''} | {trend_dir}{pos_txt}\n"
            except Exception:
                pass

        # ── Realtime date-stamp + window-lag guard ──
        # Tencent realtime reflects the latest trading day (as of now), but the
        # moneyflow/OHLCV window may end a day earlier when today's moneyflow
        # isn't published yet. Without an explicit date the model tends to treat
        # live 量比/换手 as belonging to the last OHLCV bar — seen on 000603
        # where 08-21 realtime values were reported as 08-20's.
        _rt_date_txt = ""
        _lag_note = ""
        if flow_detail:
            try:
                from datetime import datetime as _dt
                from web import trade_cal as _tc
                _rt_raw = _tc.last_open_day(_dt.now().strftime("%Y-%m-%d"))
                _newest_raw = flow_detail[0]["date"]  # flow_detail is newest-first
                if _rt_raw:
                    _rt_date_txt = f"(截至{_fmt_y(_rt_raw)})"
                    if _newest_raw and _rt_raw != _newest_raw:
                        _lag_note = (
                            f"\n⚠️ 日期错位提醒：实时行情为{_fmt_y(_rt_raw)}（最新），"
                            f"但资金流/量价数据截至{_fmt_y(_newest_raw)}。若现价与最后收盘相差较大，"
                            f"说明{_fmt_y(_newest_raw)}之后股价已变动——切勿把实时量比/换手当成{_fmt_y(_newest_raw)}当天的K线。\n"
                        )
            except Exception:
                pass

        # ── Multi-horizon 主力资金 summary (program pre-computed) ──
        # 与【极值参考】同纪律: 多周期数字由程序从 90 日缓存聚合, 模型逐字引用,
        # 禁止心算 60 日累计. 缓存数据不足(新上市/停牌)时整块留空, 优雅降级.
        multi_horizon_text = ""
        if result.get("flow_multi_horizon"):
            try:
                from quantconclave.dataflows.eastmoney_sector import format_moneyflow_multi_horizon
                multi_horizon_text = (
                    "【资金多周期】" + format_moneyflow_multi_horizon(result["flow_multi_horizon"]) + "\n"
                )
            except Exception:
                multi_horizon_text = ""

        # ── Build prompt ──
        vol_info = ""
        if not ohlcv_text:
            vol_info = "\n⚠️ 注意：未能获取量价数据，量价关系分析请基于资金流数据推断。\n"
        else:
            vol_info = f"\n【逐日量价】日期 开 收 高 低 量 涨幅 换手\n{ohlcv_text}\n"
        # MX realtime DDX section (from result["mx_ddx"], set outside the LLM guard)
        mx_ddx_text = ""
        if result.get("mx_ddx"):
            mx_ddx_text = f"\n【主力资金·实时DDX】(妙想实时，含DDX/DDY/DDZ)\n{result['mx_ddx']}\n"

        prompt = (
            f"分析{code}（数据日期：{dates_range}，共{len(flow_detail)}个交易日，来源：tushare主力资金流 + 妙想实时DDX + 量价数据 + 腾讯实时行情 + 大宗交易）：\n"
            f"【实时行情{_rt_date_txt}】现价{result.get('rt_price','?')} | 涨跌{result.get('rt_change_pct','?')}% | 量比{result.get('rt_vol_ratio','?')} | 换手{result.get('rt_turnover','?')}% | 最高{result.get('rt_high','?')} 最低{result.get('rt_low','?')} | 量{result.get('rt_volume','?')}手\n"
            f"{_lag_note}"
            f"{mx_ddx_text}"
            f"{trend_note}"
            f"{extremes_text}"
            f"{margin_text}"
            f"【期间主力资金】(主力=超大单+大单, 万元) 主力净额{net_all:+.0f}万, {pos_days}/{len(flow_detail)}天净流入\n"
            f"  超大单净额: {elg_net:+.0f}万 (机构级资金)\n"
            f"  大单净额:   {lg_net:+.0f}万 (游资级资金)\n"
            f"{multi_horizon_text}"
            f"【逐日资金流】(最新在最上，向下为较早) 日期 主力净 超大单净 大单净 (万元)\n{flow_text}"
            f"{vol_info}"
            f"{block_trade_text}\n"
            f"=== 分析要点（重点关注，近{len(flow_detail)}个交易日主力资金数据） ===\n"
            f"【路径判定框架】本批分析是短线选股。判定顺序：先尝试把股票匹配到看多路径A~D之一，全部不中再尝试看空路径E~F，仍不中才判观望（观望=哪条路径都不命中，须说明卡在哪个条件）。判定必须逐条核对路径条件并引用具体数字，禁止用'近期''较大'等模糊词代替。\n"
            f"【看多路径】（命中其一→看多）：\n"
            f"  路径A 低位吸筹启动：位置<40% + 近3日超大单由负转正或主力合计净流入 + 当日放量上涨(量比>1.3)。核心逻辑：机构低位建仓后首次放量启动。反证：放量但超大单仍净流出=诱多。\n"
            f"  路径B 放量突破新高：距区间最高<5% + 当日放量上涨 + 超大单净流入 + 换手<8%。核心逻辑：高位放量+资金进=突破主升（区别于派发）。反证：高位放量滞涨/超大单流出/换手>10%+量比>3=对倒派发。\n"
            f"  路径C 主力同向持续流入：主力同向流入日≥3 或最长连续段≥2 + 12日主力净额为正 + 近3日净流入递增。核心逻辑：超大+大单分歧收敛，吸筹确认。反证：近3日出现单日大流出打断。\n"
            f"  路径D 超跌修复拐点：位置<30% + 前期急跌 + 当日放量反弹 + 超大单转正 + 主力同步转正。核心逻辑：抛压衰竭后机构回补。反证：缩量反弹(量比<1)或主力仍流出=弱反弹。\n"
            f"【看空路径】（看多路径全不中后尝试，命中其一→看空）：\n"
            f"  路径E 高位放量派发：位置>70% + 放量(量比>1.5)但超大单净流出，或高换手(>10%)+量比>3。核心逻辑：高位出货。反证：虽高位但超大单净流入且换手正常=回落为主升而非派发。\n"
            f"  路径F 破位下跌：跌破MA5/MA10 + 放量下跌 + 主力净流出。核心逻辑：趋势破位。反证：缩量回踩不破关键均线=洗盘。\n"
            f"【观望=残差】A~F全不命中时观望，须说明卡在哪条路径的哪个条件。\n"
            f"【路径选择注意】\n"
            f"⚠️ 位置只决定路径选择，不单独定多空：高位放量流入归路径B的检验（换手是否异常、超大单是否流出），低位放量流入归路径A/D，'位置高'本身不是看空理由\n"
            f"⚠️ 近3日权重最高：路径判定时最后3天资金流向优先，越近越重要\n"
            f"⚠️ 背离是路径反证而非独立结论：价涨+超大单净流出可否定路径A/B/D；价跌+超大单净流入支持路径D的洗盘逻辑\n"
            f"⚠️ 持续性大于单日：单日流入可能造假，路径C必须有多日同向支撑，路径A/B允许单日启动\n"
            f"⚠️ 量价配合+换手×量比：涨放量+资金进=健康 | 涨缩量+资金出=乏力诱多 | 换手>10%+量比>3=对倒派发；换手>5%+量比2-3=温和放量启动\n"
            f"⚠️ 大单vs超大单：同向=共识强（支持路径C），反向=分歧大（削弱路径判定）\n"
            f"【数字核对（必须严格遵守）】\n"
            f"⚠️ 口径提醒：全库资金统一按'主力'=超大单+大单口径（不含中小单）。tushare 的 net_amount 字段语义未定义（四档净额恒和≈0，net_amount 与主力方向常相反——002495 曾把该列当'全口径净流入+2054万'引用而实际该日四档净额≈0、又当'主力-814万'引用而实际该日主力仅-396万），故【逐日资金流】主力净列、【期间主力资金】主力净额、【极值参考】主力净流出N/M日均按主力口径程序预计算，**不存在'全口径'数字可引用**，禁止编造含'全口径/总净额'的金额；凡引用'X/Y天净流出/净流入'的天数比例，一律采用【极值参考】的'主力净流出N/M日'（000060曾写主力-64185万'8/12天净流出'，实际主力仅6/12天为负）\n"
            f"⚠️ 多周期口径：凡引用'当日/5日/20日/60日净流入/净流出/累计'及'主力方向/态度'，一律直接采用【资金多周期】的程序预计算数值（含主力/超大单/大单三口径、净流入天数比例与占日均成交额%强度），逐字一致、禁止心算长周期累计（002566曾把12日主力+1900.91万报成+19009100万=1900亿，程序预计算就是防这类单位与心算错误）；【期间主力资金】的'主力净额'=近{len(flow_detail)}日主力净额，与【资金多周期】的N日周期口径不同，引用时须注明周期数\n"
            f"⚠️ 近N日连续交易日：凡引用'近N日/最近N个交易日'的累计或净额，N日**必须**是【逐日资金流】按最新在上的表格顶部往下连续数N行（即最后N个连续交易日），禁止自行挑选日期拼凑窗口（302132曾把08-26/08-27/09-01三天称'近3日超大单+10827万'，中间跨过08-28/08-31两个交易日——跳过两个走弱日(主力-1308/-272)后数字才好看；真实最后3日(08-28/08-31/09-01)超大单仅+3947万、主力+218万≈零）。若引用的日期集合非连续，即视为挑日、结论作废；单日日期可自选，但'近N日'类窗口必须连续\n"
            f"⚠️ 主力强度相对化：**绝对净额本身不能判断强弱**——同一+5000万，对27亿小盘可能是强吸筹，对2000亿大盘只是噪音。判断强度与态度必须结合【资金多周期】的'占日均成交额%'、流通市值与股价位置（低位吸筹/高位派发），禁止只写'主力净流入X万=看多'这类脱离规模的绝对结论；方向含义同样取决于位置\n"
            f"⚠️ 极值断言核对：凡引用'最大/最小/首次/唯一/连续N日'，一律直接采用【极值参考】中的程序计算结果并注明日期，禁止自行扫描全表重排，禁止用'近期'等模糊词代替范围；凡引用'最高价/最低价/距高点'，必须使用【极值参考】的'区间最高价/最低价'及其距离，禁止把【股价走势】的'距区间最高'配到别的价位（000603曾把1.4%误配到盘中最高39.33，实际该距离对应最高收盘37.12，距39.33达6.9%）；凡引用'均量/平均量/日均'一律采用【极值参考】的'近5日均量'，禁止心算（002812把近5日均量约169000手，实际200715手）；凡引用'唯一/仅有的主力同向流入'等关于'哪几天主力同向流入'的断言，一律采用【极值参考】的'主力同向流入日'及其最长连续段，禁止自行扫表只数到最显眼的一天（600988曾称08-18/19为'唯一'同向流入，实为四日08-10/14/18/19，另两日被漏数）；**单日超大/大单的流入流出方向必须逐行核对【期间主力资金】，禁止张冠李戴（600460曾把08-20超大-13262万当成08-21的超大，且把08-21超大+134002万写成-1.34亿流出，方向完全反了）；若某日方向与【极值参考】'最大流入/最大流出'矛盾，以【极值参考】为准并复核该行**；若发现需要额外极值，须明确给出其数值与日期\n"
            f"⚠️ 关键价位引用：支撑/压力位只能用【逐日量价】表中出现过的价格（开/收/高/低）并标注日期；'前期低点/高点'必须指向更早的交易日，禁止把当日低点/高点称为'前期'\n"
            f"⚠️ 日期顺序：【逐日资金流】【逐日量价】均为最新在上（08-21在最上）。引用多个日期时按时间先后描述（如08-19→08-20→08-21），或注明'按最新在前排列'，不得把倒序当正序引用\n"
            f"⚠️ 大宗交易暗盘：如有大宗交易数据，折价+机构买入=暗盘吸筹，溢价+机构卖出=暗盘出货\n"
            f"\n请从以下角度逐一分析（每点2-3句话，必须引用具体数据，重点放在最近3-5日）：\n"
            f"1.【位置与阶段】位置（低位/中位/高位）与资金流向，判断处于吸筹/洗盘/拉升/派发哪一阶段——决定优先走哪条路径\n"
            f"2.【路径匹配】逐条核对路径A~F的条件（位置、距区间最高、主力/超大单净额、同向流入日、放量量比换手），明确写出命中/未命中哪条路径及其依据（引用具体数字）\n"
            f"3.【量价关系】验证命中路径的量价条件（放量?换手?量比?）：是健康放量、对倒虚假还是缩量整理？\n"
            f"4.【背离检查】该路径有无反证背离（价涨钱出/价跌钱进）？哪天最明显？\n"
            f"5.【风险等级】命中路径的风险低/中/高？路径反证条件是否触发？\n"
            f"6.【综合结论】看多/看空/观望，注明命中路径（路径名），引用具体数字。\n"
            f"   ⚠️ 先独立判断，不要受历史结论方向影响；若本次结论与前次[模型名]结论不同，请明确注明并简要说明关键差异。\n"
            f"格式要求：第一行必须以'结论：看多/看空/观望'开头；第二行必须为'命中路径：'后接路径名（观望时写'观望-无明确路径'，与第一行结论一致）。明确标注数据日期范围。中文。"
        )
        if recent_history:
            hist_lines = "\n".join(
                f"- {r['analyzed_at'][:10]} | {r['verdict']} | 🤖 {r.get('model_name', '?')}"
                for r in recent_history
            )
            prompt = prompt.rstrip() + f"\n\n📋 历史分析记录（供参考，注意模型差异）：\n{hist_lines}\n"

        # ── Invoke LLM (model 1, optionally model 2) ──
        # Robust verdict + 命中路径 extraction. Models may wrap lines in markdown
        # bold (**结论：看多** / **命中路径：路径B**) or merge both onto one line —
        # normalize each line before matching. (000426 stored '**命中路径：路径B
        # 放量突破新高**' but setup_type fell back to '观望-无明确路径' because a
        # plain startswith() missed the leading asterisks.)
        def _extract_verdict_setup(_text: str):
            _v = ""
            _st = ""
            for _ln in _text.split("\n")[:4]:
                _s = _ln.strip().strip("*#").strip()
                if not _s:
                    continue
                if not _v and _s.startswith("结论："):
                    _v = "看多" if "看多" in _s else ("看空" if "看空" in _s else "观望")
                    _pos = _s.find("命中路径：")
                    if not _st and _pos >= 0:
                        _st = _s[_pos + len("命中路径："):].strip()
                elif not _st and _s.startswith("命中路径："):
                    _st = _s[len("命中路径："):].strip()
            return _v, _st

        try:
            from quantconclave.llm_clients import create_llm_client, resolve_role_llm
            import os as _os
            # Prefer caller-supplied params, fall back to per-role config
            _resolved = resolve_role_llm(DEFAULT_CONFIG, "deep")
            _provider = provider or _resolved[0]
            _model = model or _resolved[1]
            # Resolve base_url: pass None for Ollama so the client factory handles
            # OLLAMA_BASE_URL env var automatically (matching /api/ollama/models and
            # the Deep Analysis flow). For other providers, use config backend_url.
            _base_url = DEFAULT_CONFIG.get("backend_url") or None
            _extra_kwargs = {}
            if _provider in ("ollama", "ollama2", "ollama3", "ollama4"):
                _base_url = (
                    _os.environ.get({"ollama":"OLLAMA_BASE_URL","ollama2":"OLLAMA2_BASE_URL","ollama3":"OLLAMA3_BASE_URL","ollama4":"OLLAMA4_BASE_URL"}.get(_provider,"OLLAMA_BASE_URL"))
                    or DEFAULT_CONFIG.get("backend_url")
                    or None
                )
                _extra_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            client = create_llm_client(
                provider=_provider,
                model=_model,
                base_url=_base_url, timeout=300,
                **_extra_kwargs,
            )
            # Record the model name for result comparison
            result["model_name"] = _model or _provider
            llm = client.get_llm()

            # ── Structured output: enforce verdict/analysis consistency ──
            from quantconclave.agents.schemas import (
                WatchlistAnalysis, render_watchlist_analysis,
            )
            from quantconclave.agents.utils.structured import (
                bind_structured, invoke_structured_or_freetext,
            )
            structured_llm = bind_structured(llm, WatchlistAnalysis, "Watchlist")

            # Structured output enforces verdict/analysis consistency at the API level.
            # Falls back to free-text if the provider doesn't support structured output.
            analysis_text = invoke_structured_or_freetext(
                structured_llm, llm, prompt,
                render_watchlist_analysis, "Watchlist",
            )
            result["analysis"] = analysis_text
            # Extract verdict + setup_type (robust to bold/same-line markup; the
            # store's extract_verdict remains the fallback for weird prose).
            if analysis_text:
                _v, _st = _extract_verdict_setup(analysis_text)
                if _v:
                    result["verdict"] = _v
                    result["setup_type"] = _st or "观望-无明确路径"
        except Exception as e:
            logger.exception("LLM analysis failed for %s (provider=%s model=%s)", code, _provider, _model)
            result["analysis"] = "结论：观望（分析服务暂时不可用，请稍后重试）"

        # ── Model 2 (dual-model) — same prompt, different LLM ──
        _p2 = str(provider2) if provider2 else ""
        _m2 = str(model2) if model2 else ""
        if _p2 and _m2:
            try:
                _base_url2 = DEFAULT_CONFIG.get("backend_url") or None
                _extra2 = {}
                if _p2 in ("ollama", "ollama2"):
                    _base_url2 = (
                        _os.environ.get("OLLAMA2_BASE_URL" if _p2 == "ollama2" else "OLLAMA_BASE_URL")
                        or DEFAULT_CONFIG.get("backend_url") or None
                    )
                    _extra2["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
                client2 = create_llm_client(provider=_p2, model=_m2, base_url=_base_url2,
                                            timeout=300, **_extra2)
                result["model_name2"] = _m2 or _p2
                llm2 = client2.get_llm()
                structured_llm2 = bind_structured(llm2, WatchlistAnalysis, "Watchlist")
                analysis_text2 = invoke_structured_or_freetext(
                    structured_llm2, llm2, prompt,
                    render_watchlist_analysis, "Watchlist",
                )
                result["analysis2"] = analysis_text2
                if analysis_text2:
                    _v2, _st2 = _extract_verdict_setup(analysis_text2)
                    if _v2:
                        result["verdict2"] = _v2
                        result["setup_type2"] = _st2 or "观望-无明确路径"
            except Exception as e:
                logger.exception("Model2 analysis failed for %s (p=%s m=%s)", code, _p2, _m2)
                result["analysis2"] = "结论：观望（模型2分析暂时不可用）"

    elif not flow_detail:
        result["analysis"] = "结论：观望（暂无主力资金流数据，请确认tushare配置或该股票为新股/停牌）"

    # ── 4. Intraday minute data (Tencent) ──
    try:
        from quantconclave.dataflows.tencent_realtime import _normalize_symbol
        import requests as _req
        norm = _normalize_symbol(code)
        resp = _req.get(f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={norm},m5,,80", timeout=8)
        data = resp.json()
        bars = data.get("data", {}).get(norm, {}).get("m5", [])
        result["intraday"] = [{"t": b[0], "o": b[1], "c": b[2], "h": b[3], "l": b[4], "v": b[5]} for b in bars[-80:]]
    except Exception:
        result["intraday"] = []

    # Backward compat: build flow_data text from flow_detail
    fd = result.get("flow_detail", [])
    if fd:
        lines = ["date,net,buy_elg,sell_elg,buy_lg,sell_lg,buy_md,sell_md,buy_sm,sell_sm"]
        for r in fd:
            lines.append(f"{r['date']},{r['net']},{r['buy_elg']},{r['sell_elg']},{r['buy_lg']},{r['sell_lg']},{r['buy_md']},{r['sell_md']},{r['buy_sm']},{r['sell_sm']}")
        result["flow_data"] = "\n".join(lines)

    # ── 5. Persist analysis only when LLM actually ran ──
    # Always record which model was used (even if we didn't call it)
    if "model_name" not in result:
        _p = provider or DEFAULT_CONFIG.get("llm_provider", "deepseek")
        _m = model or DEFAULT_CONFIG.get("deep_think_llm", "")
        result["model_name"] = _m or _p

    if not no_llm and flow_detail:
        try:
            from web.watchlist_store import save_watchlist_analysis
            result["analysis_id"] = save_watchlist_analysis(DEFAULT_CONFIG, result)
            result["analyzed_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        except Exception as e:
            logger.warning("Failed to persist watchlist analysis for %s: %s", code, e)

    return result


class AddManualStockRequest(BaseModel):
    code: str
    name: str = ""
    push_to_eastmoney: bool = False


@app.post("/api/eastmoney/watchlist/add-manual")
def add_manual_stock(body: AddManualStockRequest):
    """Add a stock to the local watchlist DB, optionally push to Eastmoney MXAPI.

    Resolves company name if not provided, fetches real-time price, and merges
    into the local watchlist_stocks table. When push_to_eastmoney=True, also
    sends a natural-language command to the Eastmoney self-select/manage API.
    """
    _load_env()
    import os as _os, requests as _req
    from web.watchlist_store import merge_watchlist_stocks, get_watchlist_stocks
    from web.ticker_utils import resolve_company_name, normalize_ticker

    raw = body.code.strip()
    norm = normalize_ticker(raw)
    if not norm:
        raw_upper = raw.upper()
        if raw_upper.isdigit() and len(raw_upper) == 6:
            norm = raw_upper + (".SH" if raw_upper[0] in "69" else ".SZ")
        else:
            raise HTTPException(400, f"无法解析股票代码: {raw}")

    # Resolve name
    ticker_norm, resolved_name = resolve_company_name(norm)
    name = body.name.strip() or resolved_name or norm

    # Fetch real-time price (Tencent)
    price = _fetch_price_for_ticker(ticker_norm or norm)
    price_float = float(price) if price else 0.0

    # Persist locally
    stock = {"code": ticker_norm or norm, "name": name, "price": price_float,
             "change_pct": 0.0, "turnover": None, "vol_ratio": None}
    stats = merge_watchlist_stocks(DEFAULT_CONFIG, [stock], source="manual")

    # Optionally push to Eastmoney
    push_result = None
    if body.push_to_eastmoney:
        key = _os.environ.get("MX_APIKEY", "")
        if key:
            try:
                query = f"把{name}({ticker_norm or norm})加入自选"
                resp = _req.post(
                    "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/manage",
                    headers={"apikey": key, "Content-Type": "application/json"},
                    json={"query": query}, timeout=15,
                )
                push_result = {"ok": resp.status_code == 200,
                               "message": resp.json().get("message", "") if resp.ok else resp.text[:200]}
            except Exception as e:
                push_result = {"ok": False, "message": str(e)}
        else:
            push_result = {"ok": False, "message": "MX_APIKEY 未配置"}

    return {"acknowledged": True, "ticker": ticker_norm or norm, "name": name,
            "price": price_float, "push_result": push_result,
            "items": get_watchlist_stocks(DEFAULT_CONFIG)}


@app.post("/api/eastmoney/watchlist/sync-up")
def sync_watchlist_to_eastmoney():
    """Push locally-manual-added stocks up to Eastmoney self-select list.

    Compares local watchlist_stocks with the remote Eastmoney list and pushes
    any stocks that exist locally but not remotely.
    """
    _load_env()
    import os as _os, requests as _req
    from web.watchlist_store import get_watchlist_stocks

    key = _os.environ.get("MX_APIKEY", "")
    if not key:
        raise HTTPException(400, "MX_APIKEY 未配置，无法上传至东方财富")

    # Fetch remote list
    try:
        resp = _req.post(
            "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get",
            headers={"apikey": key, "Content-Type": "application/json"},
            json={"query": "查询我的自选股"}, timeout=15,
        )
        data = resp.json()
        data_list = data.get("data", {}).get("allResults", {}).get("result", {}).get("dataList", [])
    except Exception as e:
        logger.exception("Failed to fetch Eastmoney watchlist for sync-up")
        raise HTTPException(502, "获取东方财富自选列表失败，请稍后重试")

    remote_codes = set()
    for item in data_list:
        code = str(item.get("SECURITY_CODE", ""))
        market = str(item.get("MARKET_SHORT_NAME", ""))
        if code and market:
            remote_codes.add(f"{code}.{market}")

    local = get_watchlist_stocks(DEFAULT_CONFIG)
    to_push = [s for s in local if s["code"] not in remote_codes]

    results = []
    for s in to_push:
        try:
            query = f"把{s['name']}({s['code']})加入自选"
            r = _req.post(
                "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/manage",
                headers={"apikey": key, "Content-Type": "application/json"},
                json={"query": query}, timeout=15,
            )
            results.append({"code": s["code"], "name": s["name"],
                            "ok": r.status_code == 200,
                            "message": r.json().get("message", "") if r.ok else r.text[:100]})
        except Exception as e:
            results.append({"code": s["code"], "name": s["name"],
                            "ok": False, "message": str(e)})

    return {"total_to_push": len(to_push), "results": results}


@app.get("/api/eastmoney/watchlist/analyze/{code}")
def analyze_watchlist_stock(
    code: str,
    provider: str = Query(default=""),
    model: str = Query(default=""),
    provider2: str = Query(default=""),
    model2: str = Query(default=""),
):
    """Redirect to detail endpoint (backward compat), forwarding all model params."""
    return watchlist_stock_detail(code, provider=provider, model=model,
                                  provider2=provider2, model2=model2)


@app.post("/api/eastmoney/watchlist/twopass/download")
def download_twopass_report(body: list[dict], format: str = Query(default="docx")):
    """Generate and download a two-pass analysis report (DOCX/MD/CSV/JSON).

    POST body: JSON array of {code, name, price, change_pct, prevModel, prevVerdict, newModel, newVerdict, agree}
    """
    import json as _json
    from web.docx_export import generate_twopass_docx

    if not body:
        return Response(status_code=400, content="Empty request body")

    body_str = _json.dumps(body, ensure_ascii=False)

    if format == "docx":
        docx_bytes = generate_twopass_docx(body_str)
        resp = Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        resp.headers["Content-Disposition"] = "attachment; filename=twopass-analysis.docx"
        return resp
    elif format == "md":
        md = _build_twopass_md(body)
        resp = Response(content=md, media_type="text/markdown; charset=utf-8")
        resp.headers["Content-Disposition"] = "attachment; filename=twopass-analysis.md"
        return resp
    elif format == "csv":
        csv = _build_twopass_csv(body)
        resp = Response(content=csv, media_type="text/csv; charset=utf-8")
        resp.headers["Content-Disposition"] = "attachment; filename=twopass-analysis.csv"
        return resp
    elif format == "json":
        resp = Response(content=body_str, media_type="application/json; charset=utf-8")
        resp.headers["Content-Disposition"] = "attachment; filename=twopass-analysis.json"
        return resp
    else:
        return Response(status_code=400, content=f"Unsupported format: {format}")


# ── Two-pass (二次分析) records — saved per run, surfaced to Advisory Agent ──
class TwoPassStockItem(BaseModel):
    code: str = ""
    name: str = ""
    price: float = 0
    change_pct: float | None = None
    prevModel: str = ""
    prevVerdict: str = ""
    newModel: str = ""
    newVerdict: str = ""
    analysis: str = ""


class TwoPassRecordCreate(BaseModel):
    tab: str = "emwl"
    model_count: int = 1
    stocks: list[TwoPassStockItem] = []


@app.post("/api/twopass/records")
def create_twopass_record(body: TwoPassRecordCreate):
    """Save one two-pass analysis run as a record (frontend calls on completion)."""
    from web.twopass_records import save_twopass_record
    tab = body.tab or "emwl"
    if tab not in ("emwl", "idx"):
        raise HTTPException(400, f"Invalid tab: {tab} (must be 'emwl' or 'idx')")
    stocks = [s.model_dump() for s in body.stocks]
    rid = save_twopass_record(DEFAULT_CONFIG, tab,
                              max(1, body.model_count or 1), stocks)
    return {"id": rid}


@app.get("/api/twopass/records")
def list_twopass_records(limit: int = Query(default=100)):
    """List saved two-pass records (newest first)."""
    from web.twopass_records import list_twopass_records as _list
    return _list(DEFAULT_CONFIG, limit=limit)


@app.get("/api/twopass/records/{record_id}")
def get_twopass_record(record_id: int):
    """Get one two-pass record with its full stock list + LLM conclusions."""
    from web.twopass_records import get_twopass_record as _get
    rec = _get(DEFAULT_CONFIG, record_id)
    if rec is None:
        raise HTTPException(404, f"Record {record_id} not found")
    return rec


@app.delete("/api/twopass/records/{record_id}")
def delete_twopass_record(record_id: int):
    """Delete a saved two-pass record."""
    from web.twopass_records import delete_twopass_record as _del
    ok = _del(DEFAULT_CONFIG, record_id)
    if not ok:
        raise HTTPException(404, f"Record {record_id} not found")
    return {"ok": True}


# ── Stage-3 (阶段三·顾问综合报告) records — read-only, surfaced to Advisory panel ──
@app.get("/api/stage3/records")
def list_stage3_records(limit: int = Query(default=100)):
    """List saved stage-3 composite-report records (newest first)."""
    from web.stage3_records import list_stage3_records as _list
    return _list(DEFAULT_CONFIG, limit=limit)


@app.get("/api/stage3/records/{record_id}")
def get_stage3_record(record_id: int):
    """Get one stage-3 record with its report + input stock list."""
    from web.stage3_records import get_stage3_record as _get
    rec = _get(DEFAULT_CONFIG, record_id)
    if rec is None:
        raise HTTPException(404, f"Record {record_id} not found")
    return rec


def _build_twopass_md(rows: list) -> str:
    md = "# 🔬 二次分析报告\n\n"
    md += f"生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    bull = sum(1 for r in rows if r.get("newVerdict") == "看多")
    bear = sum(1 for r in rows if r.get("newVerdict") == "看空")
    md += f"**本次看多: {bull} 只 | 看空: {bear} 只 | 观望: {len(rows) - bull - bear} 只**\n\n"
    md += "| 代码 | 名称 | 最新价 | 涨跌幅 | 前次模型 | 前次结论 | 本次模型 | 本次结论 |\n"
    md += "|------|------|--------|--------|----------|----------|----------|----------|\n"
    for r in rows:
        name = r.get("name", "")
        price = f"{r['price']:.2f}" if r.get("price") else "-"
        chg = r.get("change_pct")
        chg_str = f"{chg:+.2f}%" if chg is not None else "-"
        md += f"| {r.get('code','?')} | {name} | {price} | {chg_str} | {r.get('prevModel','?')} | {r.get('prevVerdict','看多')} | {r.get('newModel','?')} | {r.get('newVerdict','?')} |\n"
    return md


def _build_twopass_csv(rows: list) -> str:
    csv = "代码,名称,最新价,涨跌幅,前次模型,前次结论,本次模型,本次结论\n"
    for r in rows:
        name = r.get("name", "")
        price = f"{r['price']:.2f}" if r.get("price") else "-"
        chg = r.get("change_pct")
        chg_str = f"{chg:+.2f}%" if chg is not None else "-"
        csv += f"{r.get('code','?')},{name},{price},{chg_str},{r.get('prevModel','?')},{r.get('prevVerdict','看多')},{r.get('newModel','?')},{r.get('newVerdict','?')}\n"
    return csv


@app.post("/api/eastmoney/watchlist/export")
def export_watchlist_batch(body: dict, format: str = Query(default="docx")):
    """Export last batch analysis results filtered by verdict, as DOCX/MD/CSV."""
    from web.watchlist_store import _get_conn
    from web.docx_export import _build_zip, _WML, _XML
    import xml.etree.ElementTree as ET
    ET.register_namespace("w",_WML)

    verdict_filter = body.get("verdict","看多")
    source_filter = body.get("source","")
    model_filter = body.get("model","")  # optional: filter by specific model
    change_threshold = body.get("change_threshold")  # optional: 仅分析涨幅 constraint (mirrors batch analysis)
    conn = _get_conn(DEFAULT_CONFIG)
    try:
        if source_filter and model_filter:
            rows = conn.execute("""SELECT wa.*, ws.name, ws.change_pct FROM watchlist_analysis wa
              JOIN watchlist_stocks ws ON wa.code=ws.code
              WHERE ws.source=? AND wa.model_name=? ORDER BY wa.analyzed_at DESC LIMIT 500""",
              (source_filter, model_filter)).fetchall()
        elif source_filter:
            rows = conn.execute("""SELECT wa.*, ws.name, ws.change_pct FROM watchlist_analysis wa
              JOIN watchlist_stocks ws ON wa.code=ws.code
              WHERE ws.source=? ORDER BY wa.analyzed_at DESC LIMIT 500""",(source_filter,)).fetchall()
        else:
            rows = conn.execute("""SELECT wa.*, ws.name, ws.change_pct FROM watchlist_analysis wa
              JOIN watchlist_stocks ws ON wa.code=ws.code
              ORDER BY wa.analyzed_at DESC LIMIT 500""").fetchall()
        parsed = [dict(r) for r in rows]
        latest_date = (parsed[0].get("analyzed_at","") or "")[:10] if parsed else ""
        # Dedupe: per stock, keep the MOST RECENT analysis (sorted DESC, first=latest)
        seen = {}
        for r in parsed:
            code = r.get("code","")
            if code not in seen: seen[code] = r
        filtered = [r for r in seen.values() if r.get("verdict","") == verdict_filter]
        # 仅分析涨幅 constraint: keep only stocks whose current change_pct > threshold
        if change_threshold is not None:
            try:
                thr = float(change_threshold)
            except (TypeError, ValueError):
                thr = None
            if thr is not None:
                filtered = [r for r in filtered if (r.get("change_pct") or 0) > thr]
    finally:
        conn.close()

    vlabel = {"看多":"bull","看空":"bear","观望":"hold"}.get(verdict_filter,"filtered")
    if format == "docx":
        body_el = ET.Element(f"{{{_WML}}}body")
        p=ET.SubElement(body_el,f"{{{_WML}}}p");r=ET.SubElement(p,f"{{{_WML}}}r");ET.SubElement(r,f"{{{_WML}}}t").text=f"Batch Analysis Export — {verdict_filter} ({len(filtered)} stocks)"
        lines=["| Code | Name | Verdict | Model | Time | Price |"]
        lines.append("|------|------|---------|-------|------|-------|")
        for r2 in filtered:
            lines.append(f"| {r2['code']} | {r2.get('name','')} | {r2['verdict']} | {r2.get('model_name','')} | {(r2.get('analyzed_at','') or '')[:19]} | {r2.get('rt_price','')} |")
        from web.docx_export import _build_table
        _build_table(body_el, lines)
        doc_el=ET.Element(f"{{{_WML}}}document");doc_el.append(body_el)
        docx=_build_zip(_XML+ET.tostring(doc_el,encoding="unicode"))
        return Response(content=docx,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition":f"attachment; filename=batch-{vlabel}-{latest_date}.docx"})
    elif format == "md":
        md=f"# Batch Analysis Export — {verdict_filter}\n\n{len(filtered)} stocks | {latest_date}\n\n| Code | Name | Verdict | Model | Price |\n|------|------|---------|-------|-------|\n"
        for r2 in filtered: md+=f"| {r2['code']} | {r2.get('name','')} | {r2['verdict']} | {r2.get('model_name','')} | {r2.get('rt_price','')} |\n"
        return Response(content=md,media_type="text/markdown",headers={"Content-Disposition":f"attachment; filename=batch-{vlabel}-{latest_date}.md"})
    else:
        csv="Code,Name,Verdict,Model,Time,Price\n"
        for r2 in filtered: csv+=f"{r2['code']},{r2.get('name','')},{r2['verdict']},{r2.get('model_name','')},{(r2.get('analyzed_at','') or '')[:19]},{r2.get('rt_price','')}\n"
        return Response(content=csv,media_type="text/csv",headers={"Content-Disposition":f"attachment; filename=batch-{vlabel}-{latest_date}.csv"})


@app.delete("/api/eastmoney/watchlist/analysis/{analysis_id}")
def delete_watchlist_analysis(analysis_id: int):
    """Delete a single analysis record by its ID."""
    from web.watchlist_store import _get_conn
    conn = _get_conn(DEFAULT_CONFIG)
    try:
        cur = conn.execute("DELETE FROM watchlist_analysis WHERE id=?", (analysis_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Analysis record not found")
    finally:
        conn.close()
    return {"acknowledged": True}


@app.get("/api/eastmoney/watchlist/returns/{code}")
def watchlist_returns(code: str):
    """Return per-analysis return validation for a stock (newest first).

    For each recorded analysis: buy price = the analysis-day close, sell price
    = the current real-time price, return_pct = (sell - buy) / buy * 100.
    """
    from web.watchlist_store import (
        get_watchlist_analysis_history, compute_analysis_return,
    )
    history = get_watchlist_analysis_history(DEFAULT_CONFIG, code, limit=50)
    results = []
    for row in history:
        ret = compute_analysis_return(
            DEFAULT_CONFIG, code, row["analyzed_at"],
            verdict=row.get("verdict", ""),
        )
        if ret:
            results.append({
                **ret,
                "code": code,
                "analysis_id": row["analysis_id"],
                "verdict": row.get("verdict", ""),
                "analysis": row.get("analysis", ""),
                "model_name": row.get("model_name", ""),
            })
    return {"code": code, "returns": results}


# ---- Index Constituent Stocks API ----


# Supported indices with tushare index_code and display name
_INDEX_CONFIG = {
    "csi300":   ("000300.SH", "沪深300"),
    "csi500":   ("000905.SH", "中证500"),
    "csi1000":  ("000852.SH", "中证1000"),
    "gem":      ("399006.SZ", "创业板指"),
    "star50":   ("000688.SH", "科创50"),
    "sse50":    ("000016.SH", "上证50"),
    "highdiv":  ("custom", "高股息100"),
    "shanghai": ("custom", "上证A股"),
    "cycle":    ("custom", "周期股100"),
    "microcap": ("custom", "万得微盘股868008"),
}

# Legacy index-key aliases: an older "周期股100" config stored "period"; the
# canonical key is "cycle". Kept so tasks created before alias normalization
# still prefill in the edit form and resolve at run time.
_INDEX_ALIASES = {"period": "cycle"}


def _resolve_index_key(index: str) -> str:
    """Map a legacy index key to its canonical ``_INDEX_CONFIG`` key (identity
    when already canonical or unknown)."""
    return _INDEX_ALIASES.get(index, index)


@app.get("/api/index/constituents")
def get_index_constituents(
    index: str = Query(default="csi300", description="Index key: csi300, csi500, csi1000, gem, star50, sse50, highdiv, shanghai, cycle, microcap"),
    full_refresh: bool = Query(default=False, description="True=re-fetch constituents from tushare; False=prices only"),
):
    """Fetch constituent stocks for a given index with real-time prices.

    Quick mode (full_refresh=False): reads cached codes+names from local DB,
    only refreshes real-time prices via Tencent. Fast — ~2-5s for any index.

    Full mode (full_refresh=True): fetches constituent list from tushare,
    resolves names, then enriches with prices. Slow — ~10s+ for large indices.
    """
    _load_env()
    import os as _os, requests as _req
    from web.watchlist_store import (
        merge_watchlist_stocks, log_sync, get_index_cached_codes,
    )

    # A stored task may still carry a legacy key (pre-alias fix) — resolve it
    # before lookup so those tasks keep working without an edit.
    original = index
    index = _resolve_index_key(index)
    cfg = _INDEX_CONFIG.get(index)
    if not cfg:
        return {"error": f"Unknown index: {original}. Supported: {list(_INDEX_CONFIG.keys())}", "stocks": []}
    index_code, index_name = cfg

    stocks_data = []
    is_cached = False

    # ── High-dividend stocks: custom logic (no tushare index_weight) ──
    if index == "highdiv":
        try:
            import tushare as ts; import os as _os
            from datetime import datetime as _dt
            token = _os.environ.get("TUSHARE_TOKEN","")
            if not token: return {"error":"TUSHARE_TOKEN not set","stocks":[]}
            pro=ts.pro_api(token);today=_dt.now().strftime("%Y%m%d")
            cal=pro.trade_cal(exchange="SSE",start_date="20260701",end_date=today)
            cal_open=cal[cal["is_open"]==1]
            if cal_open.empty: return {"error":"No recent trade dates","stocks":[]}
            trade_dates=sorted(cal_open["cal_date"].tolist(),reverse=True)
            df=None
            for td in trade_dates[:5]:
                df=pro.daily_basic(trade_date=td,fields="ts_code,dv_ratio,total_mv,pe,pb")
                if df is not None and len(df)>0: break
            if df is None or len(df)==0: return {"error":"No dividend data available","stocks":[]}
            df=df.dropna(subset=["dv_ratio"]);df=df[df["dv_ratio"]>0]
            df=df.sort_values("dv_ratio",ascending=False).head(100)
            codes=df["ts_code"].tolist()
            name_map={}
            for i in range(0,len(codes),100):
                try:
                    basic=pro.stock_basic(ts_code=",".join(codes[i:i+100]),fields="ts_code,name")
                    if basic is not None and not basic.empty:
                        for _,r in basic.iterrows(): name_map[r["ts_code"]]=r["name"]
                except: pass
            for c in codes: stocks_data.append({"code":c,"name":name_map.get(c,c)})
        except Exception as e:
            return {"error":f"High-dividend fetch failed: {e}","stocks":[]}

    elif index == "cycle":
        # ── Cyclical stocks (周期性行业) ──
        try:
            import tushare as ts; import os as _os
            token = _os.environ.get("TUSHARE_TOKEN","")
            if not token: return {"error":"TUSHARE_TOKEN not set","stocks":[]}
            pro=ts.pro_api(token)
            cycle_industries=["钢铁","煤炭开采","石油石化","有色金属","基础化工","建筑材料","航运","房地产","电力","化学原料","化学制品","化纤","橡胶","塑料","水泥","玻璃","造纸","航空","水运","铁路"]
            basic=pro.stock_basic(exchange="",list_status="L",fields="ts_code,name,industry")
            if basic is not None and not basic.empty:
                cycle=basic[basic["industry"].isin(cycle_industries)]
                cycle=cycle.head(100)
                for _,r in cycle.iterrows():
                    stocks_data.append({"code":r["ts_code"],"name":r["name"]})
        except Exception as e:
            return {"error":f"Cycle stocks fetch failed: {e}","stocks":[]}

    elif index == "shanghai":
        # ── Shanghai A-share stocks (all SSE main board) ──
        try:
            import tushare as ts; import os as _os
            token = _os.environ.get("TUSHARE_TOKEN","")
            if not token: return {"error":"TUSHARE_TOKEN not set","stocks":[]}
            pro=ts.pro_api(token)
            basic=pro.stock_basic(exchange="SSE",list_status="L",fields="ts_code,name")
            if basic is not None and not basic.empty:
                for _,r in basic.iterrows():
                    stocks_data.append({"code":r["ts_code"],"name":r["name"]})
        except Exception as e:
            return {"error":f"Shanghai A-share fetch failed: {e}","stocks":[]}

    elif index == "microcap":
        # ── 万得微盘股指数 868008.WI (月调): 市值最小的400只, 剔除ST/次新 ──
        # Wind 自营指数, tushare index_weight 无成分数据; 用东方财富复刻 Wind
        # 选样规则 (smallest-400 by total market cap, excl ST & <60 trade days).
        if not full_refresh:
            cached = get_index_cached_codes(DEFAULT_CONFIG, index)
            if cached:
                stocks_data = cached
                is_cached = True
        if not stocks_data:
            try:
                from quantconclave.dataflows.eastmoney_microcap import get_microcap_constituents
                stocks_data = get_microcap_constituents(400)
                full_refresh = True
            except Exception as e:
                logger.exception("EastMoney microcap fetch failed for %s", index)
                return {"error": f"微盘股成分获取失败: {e}", "stocks": []}

    elif full_refresh:
        # ── Full refresh: tushare constituent list + names ──
        try:
            import tushare as ts
            token = _os.environ.get("TUSHARE_TOKEN", "")
            if not token:
                return {"error": "TUSHARE_TOKEN not set", "stocks": []}
            pro = ts.pro_api(token)

            from datetime import datetime as _dt
            for m in range(3):
                month = (_dt.now().month - m) if (_dt.now().month - m) > 0 else 12
                year = _dt.now().year if m == 0 or _dt.now().month - m > 0 else _dt.now().year - 1
                end_date = f"{year}{month:02d}28"
                df = pro.index_weight(index_code=index_code, start_date="20260101",
                                      end_date=end_date)
                if df is not None and not df.empty:
                    latest_month = df["trade_date"].max()
                    df = df[df["trade_date"] == latest_month]
                    for _, row in df.iterrows():
                        code = row.get("con_code", "")
                        if code:
                            stocks_data.append({"code": code, "name": ""})
                    break

            if not stocks_data:
                return {"error": f"No constituent data found for {index_name}", "stocks": []}

            # Look up stock names
            codes = [s["code"] for s in stocks_data]
            name_map = {}
            for i in range(0, len(codes), 100):
                batch = codes[i:i+100]
                try:
                    basic = pro.stock_basic(ts_code=",".join(batch), fields="ts_code,name")
                    if basic is not None and not basic.empty:
                        for _, r in basic.iterrows():
                            name_map[r["ts_code"]] = r["name"]
                except Exception:
                    pass
            for s in stocks_data:
                s["name"] = name_map.get(s["code"], s["code"])
        except Exception as e:
            logger.exception("Tushare fetch failed for %s", index)
            return {"error": f"Failed to fetch constituent list: {e}", "stocks": []}

    else:
        # ── Quick refresh: read cached codes from DB ──
        cached = get_index_cached_codes(DEFAULT_CONFIG, index)
        if not cached:
            # No cache yet — fall back to full refresh
            logger.info("No cached codes for %s, falling back to full refresh", index)
            full_refresh = True
            try:
                import tushare as ts
                token = _os.environ.get("TUSHARE_TOKEN", "")
                if not token:
                    return {"error": "TUSHARE_TOKEN not set (no cache available)", "stocks": []}
                pro = ts.pro_api(token)

                from datetime import datetime as _dt
                for m in range(3):
                    month = (_dt.now().month - m) if (_dt.now().month - m) > 0 else 12
                    year = _dt.now().year if m == 0 or _dt.now().month - m > 0 else _dt.now().year - 1
                    end_date = f"{year}{month:02d}28"
                    df = pro.index_weight(index_code=index_code, start_date="20260101",
                                          end_date=end_date)
                    if df is not None and not df.empty:
                        latest_month = df["trade_date"].max()
                        df = df[df["trade_date"] == latest_month]
                        for _, row in df.iterrows():
                            code = row.get("con_code", "")
                            if code:
                                stocks_data.append({"code": code, "name": ""})
                        break

                if not stocks_data:
                    return {"error": f"No constituent data found for {index_name}", "stocks": []}

                codes = [s["code"] for s in stocks_data]
                name_map = {}
                for i in range(0, len(codes), 100):
                    batch = codes[i:i+100]
                    try:
                        basic = pro.stock_basic(ts_code=",".join(batch), fields="ts_code,name")
                        if basic is not None and not basic.empty:
                            for _, r in basic.iterrows():
                                name_map[r["ts_code"]] = r["name"]
                    except Exception:
                        pass
                for s in stocks_data:
                    s["name"] = name_map.get(s["code"], s["code"])
            except Exception as e:
                logger.exception("Tushare fallback fetch failed for %s", index)
                return {"error": f"Failed to fetch constituent list: {e}", "stocks": []}
        else:
            stocks_data = cached
            is_cached = True

    # ── Enrich with real-time prices (batched Tencent API — 50 codes per request) ──
    def _fetch_batch(items: list) -> list[dict]:
        """Fetch prices for a batch of stocks in one HTTP request to qt.gtimg.cn."""
        results = []
        try:
            from quantconclave.dataflows.tencent_realtime import _normalize_symbol
            norms = [_normalize_symbol(item["code"]) for item in items]
            resp = _req.get(f"http://qt.gtimg.cn/q={','.join(norms)}", timeout=10)
            resp.encoding = "gbk"
            # Parse each stock's line from the response
            for item in items:
                code = item["code"]
                name = item.get("name", "")
                price = 0.0; chg = 0.0; turnover = None; vol_ratio = None
                norm = _normalize_symbol(code)
                # Response lines look like: v_sh600519="1~茅台~600519~1820.50~..."
                prefix = f'v_{norm}='
                for line in resp.text.split("\n"):
                    if prefix in line and '="' in line:
                        fld = line.split('="')[1].rstrip('";\r\n').split("~")
                        if len(fld) > 3:
                            price = float(fld[3]) if fld[3] else 0
                            chg = float(fld[32]) if len(fld) > 32 and fld[32] else 0
                            turnover = float(fld[38]) if len(fld) > 38 and fld[38] else None
                            vol_ratio = float(fld[45]) if len(fld) > 45 and fld[45] else None
                        break
                results.append({"code": code, "name": name, "price": price,
                                "change_pct": chg, "turnover": turnover, "vol_ratio": vol_ratio})
        except Exception:
            # Fall back to empty data on batch failure
            for item in items:
                results.append({"code": item["code"], "name": item.get("name", ""),
                                "price": 0.0, "change_pct": 0.0, "turnover": None, "vol_ratio": None})
        return results

    # Split into batches of 50, fetch in parallel with 5 workers
    from concurrent.futures import ThreadPoolExecutor, as_completed
    batch_size = 50
    batches = [stocks_data[i:i+batch_size] for i in range(0, len(stocks_data), batch_size)]
    stocks = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_batch, b): b for b in batches}
        for future in as_completed(futures):
            stocks.extend(future.result())
    logger.info("Index %s: fetched %d prices in %d batches (cached=%s full_refresh=%s)",
              index, len(stocks), len(batches), is_cached, full_refresh)

    # Merge into DB (updates prices + source; full_refresh also updates names/codes)
    stats = merge_watchlist_stocks(DEFAULT_CONFIG, stocks, source=index)
    if full_refresh:
        log_sync(DEFAULT_CONFIG, total=len(stocks),
                 added=stats["added"], updated=stats["updated"], removed=0,
                 source=index)

    # Attach analysis data (batch — 1 DB query instead of N)
    from web.watchlist_store import attach_analysis_to_stocks
    attach_analysis_to_stocks(DEFAULT_CONFIG, stocks)

    mode = "quick" if (not full_refresh or is_cached) else "full"
    return {"count": len(stocks), "stocks": stocks, "index": index_name,
            "index_key": index, "mode": mode,
            "updated_at": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/index/csi300")
def get_csi300_constituents():
    """Backward compat: redirect to generic endpoint."""
    return get_index_constituents(index="csi300")
    _load_env()
    import os as _os, requests as _req
    from web.watchlist_store import (
        merge_watchlist_stocks, get_latest_watchlist_analysis,
        compute_analysis_return,
    )

    try:
        import tushare as ts
        token = _os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            return {"error": "TUSHARE_TOKEN not set", "stocks": []}
        pro = ts.pro_api(token)

        # Get latest index_weight data for CSI 300 (index_code=000300.SH)
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y%m%d")
        # Try recent months to find the latest constituent list
        stocks_data = []
        for m in range(3):
            month = (_dt.now().month - m) if (_dt.now().month - m) > 0 else 12
            year = _dt.now().year if m == 0 or _dt.now().month - m > 0 else _dt.now().year - 1
            end_date = f"{year}{month:02d}28"
            df = pro.index_weight(index_code="000300.SH", start_date="20260101",
                                  end_date=end_date)
            if df is not None and not df.empty:
                # Get the latest month's data
                latest_month = df["trade_date"].max()
                df = df[df["trade_date"] == latest_month]
                for _, row in df.iterrows():
                    code = row.get("con_code", "")
                    if code:
                        stocks_data.append({"code": code, "name": ""})  # name filled below
                break

        if not stocks_data:
            return {"error": "No CSI 300 constituent data found", "stocks": []}

        # Look up stock names (index_weight only has con_code, not name)
        codes = [s["code"] for s in stocks_data]
        name_map = {}
        for i in range(0, len(codes), 100):
            batch = codes[i:i+100]
            try:
                basic = pro.stock_basic(ts_code=",".join(batch), fields="ts_code,name")
                if basic is not None and not basic.empty:
                    for _, r in basic.iterrows():
                        name_map[r["ts_code"]] = r["name"]
            except Exception:
                pass
        for s in stocks_data:
            s["name"] = name_map.get(s["code"], s["code"])

        # Enrich with real-time prices
        stocks = []
        for item in stocks_data:
            code = item["code"]
            name = item["name"]
            price = 0.0
            chg = 0.0
            turnover = None
            vol_ratio = None
            # Fetch Tencent real-time quote
            try:
                from quantconclave.dataflows.tencent_realtime import _normalize_symbol
                norm = _normalize_symbol(code)
                resp = _req.get(f"http://qt.gtimg.cn/q={norm}", timeout=5)
                resp.encoding = "gbk"
                if '="' in resp.text:
                    fld = resp.text.split('="')[1].rstrip('";\n').split("~")
                    if len(fld) > 3:
                        price = float(fld[3]) if fld[3] else 0
                        chg = float(fld[32]) if len(fld) > 32 and fld[32] else 0
                        turnover = float(fld[38]) if len(fld) > 38 and fld[38] else None
                        vol_ratio = float(fld[45]) if len(fld) > 45 and fld[45] else None
            except Exception:
                pass
            stocks.append({
                "code": code, "name": name,
                "price": price, "change_pct": chg,
                "turnover": turnover, "vol_ratio": vol_ratio,
            })

        # Merge into watchlist DB (track what we've shown)
        stats = merge_watchlist_stocks(DEFAULT_CONFIG, stocks, source="csi300")
        from web.watchlist_store import log_sync
        log_sync(DEFAULT_CONFIG, total=len(stocks),
                 added=stats["added"], updated=stats["updated"], removed=0,
                 source="csi300")

        # Attach analysis verdict + return per stock
        for s in stocks:
            latest = get_latest_watchlist_analysis(DEFAULT_CONFIG, s["code"])
            s["verdict"] = latest["verdict"] if latest else ""
            ret = compute_analysis_return(
                DEFAULT_CONFIG, s["code"], latest["analyzed_at"],
                verdict=latest["verdict"] if latest else "",
            ) if latest and latest["analyzed_at"] else None
            s["return_pct"] = ret["return_pct"] if ret else None
            s["buy_price"] = ret["buy_price"] if ret else None
            s["sell_price"] = ret["sell_price"] if ret else None
            # Last 3 analyses
            from web.watchlist_store import get_watchlist_analysis_history
            history = get_watchlist_analysis_history(DEFAULT_CONFIG, s["code"], limit=3)
            recent = []
            for row in history[:3]:
                r = compute_analysis_return(
                    DEFAULT_CONFIG, s["code"], row["analyzed_at"],
                    verdict=row.get("verdict", ""),
                )
                recent.append({
                    "analyzed_at": row["analyzed_at"],
                    "verdict": row.get("verdict", ""),
                    "model_name": row.get("model_name", ""),
                    "return_pct": r["return_pct"] if r else None,
                    "buy_price": r["buy_price"] if r else None,
                    "sell_price": r["sell_price"] if r else None,
                })
            s["recent_analyses"] = recent

        return {"count": len(stocks), "stocks": stocks, "index": "CSI 300 (沪深300)",
                "updated_at": datetime.now().isoformat(timespec="seconds")}

    except Exception as e:
        logger.exception("CSI 300 fetch failed: %s", e)
        return {"error": "Failed to fetch CSI 300 data. Please try again later.", "stocks": []}


# ---- Position / Portfolio API ----


class AddPositionRequest(BaseModel):
    ticker: str
    name: str = ""
    shares: int = 0
    cost_price: float = 0
    buy_date: str = ""
    notes: str = ""


@app.get("/api/positions")
def get_positions():
    """Return all tracked positions with real-time P&L."""
    from web.position_store import list_positions
    positions = list_positions(DEFAULT_CONFIG, refresh_prices=True)
    # Summary
    total_cost = sum(p["cost_total"] for p in positions)
    total_value = sum(p["market_value"] for p in positions)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    return {
        "positions": positions,
        "summary": {
            "count": len(positions),
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
        },
    }


@app.post("/api/positions")
def add_position(body: AddPositionRequest):
    """Add or update a position (averages cost basis if ticker already exists)."""
    from web.position_store import add_position, list_positions, get_position
    from web.ticker_utils import resolve_company_name, normalize_ticker

    raw = body.ticker.strip()
    norm = normalize_ticker(raw)
    if not norm:
        raw_upper = raw.upper()
        if raw_upper.isdigit() and len(raw_upper) == 6:
            norm = raw_upper + (".SH" if raw_upper[0] in "69" else ".SZ")
        else:
            raise HTTPException(400, f"无法解析股票代码: {raw}")

    ticker_norm, resolved_name = resolve_company_name(norm)
    name = body.name.strip() or resolved_name or norm

    pos = add_position(
        DEFAULT_CONFIG,
        ticker=ticker_norm or norm,
        name=name,
        shares=body.shares,
        cost_price=body.cost_price,
        buy_date=body.buy_date,
        notes=body.notes,
    )
    return {"acknowledged": True, "position": pos}


@app.delete("/api/positions/{ticker}")
def delete_position(ticker: str):
    """Remove a position by ticker."""
    from web.position_store import remove_position
    ok = remove_position(DEFAULT_CONFIG, ticker)
    if not ok:
        raise HTTPException(404, "Position not found")
    return {"acknowledged": True}


@app.post("/api/positions/sync-from-eastmoney")
def sync_positions_from_eastmoney():
    """Try to fetch positions from Eastmoney MXAPI simulated portfolio.

    Uses the general claw/query endpoint with natural language, then falls back
    to other patterns. The self-select/get endpoint always returns watchlist data
    regardless of query text, so we prefer the general query router.
    """
    _load_env()
    import os as _os, requests as _req
    from web.position_store import add_position

    key = _os.environ.get("MX_APIKEY", "")
    if not key:
        raise HTTPException(400, "MX_APIKEY 未配置")

    # Helper: recursively search a dict/list for stock-like records
    def _find_stock_lists(obj, depth=0):
        """Recursively find lists that look like position/stock data."""
        if depth > 8:
            return []
        if isinstance(obj, list):
            # Check if this list contains stock-like dicts
            if obj and isinstance(obj[0], dict):
                sample = obj[0]
                stock_keys = {"SECURITY_CODE", "code", "ticker", "symbol",
                              "SECURITY_SHORT_NAME", "name", "HOLD_AMOUNT",
                              "shares", "amount", "position"}
                if any(k in sample for k in stock_keys):
                    return [obj]
            # Recurse into list items
            results = []
            for item in obj[:20]:  # limit recursion breadth
                results.extend(_find_stock_lists(item, depth + 1))
            return results
        elif isinstance(obj, dict):
            results = []
            for key, val in obj.items():
                results.extend(_find_stock_lists(val, depth + 1))
            return results
        return []

    endpoints = [
        # Pattern 1: general claw query (routes to correct skill by NL)
        ("https://mkapi2.dfcfs.com/finskillshub/api/claw/query",
         {"toolQuery": "查询我的模拟持仓，列出每只股票的代码、名称、持仓数量和成本价"}),
        # Pattern 2: broader query
        ("https://mkapi2.dfcfs.com/finskillshub/api/claw/query",
         {"toolQuery": "我的模拟组合持仓情况"}),
        # Pattern 3: self-select with position-related query (may return different format)
        ("https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get",
         {"query": "查询我的模拟持仓"}),
    ]

    last_error = None
    imported = 0
    details = []

    for url, body in endpoints:
        try:
            resp = _req.post(
                url,
                headers={"apikey": key, "Content-Type": "application/json"},
                json=body, timeout=20,
            )
            if resp.status_code != 200:
                details.append({"url": url, "status": resp.status_code})
                last_error = f"{url} returned {resp.status_code}: {resp.text[:200]}"
                continue

            data = resp.json()

            # Recursively find stock-like lists in the response
            candidates = _find_stock_lists(data)
            if not candidates:
                details.append({
                    "url": url,
                    "status": "no_stock_data",
                    "top_keys": list(data.keys()) if isinstance(data, dict) else str(type(data)),
                })
                continue

            # Try each candidate list to find position data
            for data_list in candidates:
                for item in data_list:
                    try:
                        code = str(item.get("SECURITY_CODE", item.get("code", item.get("ticker", item.get("symbol", "")))))
                        name = str(item.get("SECURITY_SHORT_NAME", item.get("name", item.get("stockName", ""))))
                        market = str(item.get("MARKET_SHORT_NAME", item.get("market", item.get("exchange", ""))))
                        if code and market and not code.endswith(market) and "." not in code:
                            code = f"{code}.{market}"

                        # Try multiple keys for shares/amount
                        shares = 0
                        for sk in ("HOLD_AMOUNT", "holdAmount", "shares", "amount", "volume", "position", "currentAmount"):
                            v = item.get(sk)
                            if v is not None:
                                try: shares = int(float(str(v).replace(",", ""))); break
                                except: pass

                        # Try multiple keys for cost price
                        cost = 0.0
                        for ck in ("COST_PRICE", "costPrice", "cost", "price", "avgCost", "holdPrice", "currentPrice"):
                            v = item.get(ck)
                            if v is not None:
                                try: cost = float(str(v).replace(",", "")); break
                                except: pass

                        if code and shares > 0:
                            add_position(DEFAULT_CONFIG, ticker=code, name=name,
                                         shares=shares, cost_price=cost,
                                         source="mxapi")
                            imported += 1
                            details.append({"code": code, "name": name, "shares": shares, "cost": cost})
                    except (ValueError, TypeError):
                        pass

                if imported > 0:
                    break  # Success from this candidate list

            if imported > 0:
                break  # Success from this endpoint

        except Exception as e:
            details.append({"url": url, "error": str(e)[:200]})
            last_error = str(e)
            continue

    if imported == 0:
        logger.error("Position sync failed after %d endpoints. Last error: %s. Details: %s",
                     len(endpoints), last_error, json.dumps(details, ensure_ascii=False))
        raise HTTPException(
            502,
            "无法从东方财富同步持仓数据，请稍后重试。请确认: 1) 妙想模拟组合已开通 2) 账户中有持仓。"
        )

    from web.position_store import list_positions
    return {
        "acknowledged": True,
        "imported": imported,
        "details": details,
        "positions": list_positions(DEFAULT_CONFIG, refresh_prices=True),
    }


# ---- Sector Scan API ----


@app.get("/api/sector/list")
def sector_list(fund_flow_days: str = Query(default="5d")):
    """Return all A-share industry sectors sorted by fund flow."""
    from quantconclave.sector_scan import get_sectors_sorted
    return get_sectors_sorted(fund_flow_days)


@app.get("/api/sector/scan/{sector_code}")
def sector_scan(sector_code: str, sector_name: str = Query(default=""),
                strategies: str = Query(default=""),
                cap_percent: int = Query(default=20),
                require_inflow: bool = Query(default=False)):
    """Scan a sector with configurable strategy groups."""
    from quantconclave.sector_scan import scan_sector
    import json as _json
    try:
        groups = _json.loads(strategies) if strategies else None
    except Exception:
        groups = None
    result = scan_sector(sector_code, sector_name, strategy_groups=groups,
                         cap_percent=cap_percent, require_positive_inflow=require_inflow)
    return result


@app.get("/api/sector/scan/{sector_code}/batch")
def sector_scan_batch(sector_code: str):
    """Alias for sector_scan — returns empty list instead of 404 when no candidates."""
    from quantconclave.sector_scan import scan_sector
    return scan_sector(sector_code)


# ---- Batch Preliminary Analysis API ----

from pydantic import BaseModel as PydanticBaseModel


class BatchAnalysisRequest(PydanticBaseModel):
    candidates: list[dict]
    provider: str = ""
    deep_model: str = ""
    quick_model: str = ""
    quick_provider: str = ""
    language: str = "Chinese"
    context: str = "default"


@app.post("/api/sector/batch-analyze")
def batch_analyze_candidates(body: BatchAnalysisRequest):
    """Run batch preliminary analysis on selected sector scan candidates (non-SSE)."""
    from quantconclave.sector_scan.batch_analysis import run_batch_analysis
    from quantconclave.llm_clients import create_llm_client, resolve_role_llm

    candidates = body.candidates
    if not candidates:
        raise HTTPException(400, "No candidates provided")

    cfg = DEFAULT_CONFIG
    provider = body.quick_provider or body.provider or resolve_role_llm(cfg, "quick")[0]
    quick_model = body.quick_model or resolve_role_llm(cfg, "quick")[1]

    try:
        client = create_llm_client(
            provider=provider,
            model=quick_model,
            base_url=cfg.get("backend_url"),
        )
        llm = client.get_llm()
    except Exception as e:
        raise HTTPException(500, f"Failed to create LLM client: {e}")

    result = run_batch_analysis(candidates, llm_client=client, context=body.context)
    if result.get("error"):
        raise HTTPException(500, result["error"])
    return result


@app.post("/api/sector/batch-analyze-sse")
def batch_analyze_candidates_sse(body: BatchAnalysisRequest):
    """SSE streaming version with per-stock progress events. Uses POST to support large payloads."""
    import json as _json
    from quantconclave.sector_scan.batch_analysis import run_batch_analysis_sse
    from quantconclave.llm_clients import create_llm_client, resolve_role_llm

    candidates = body.candidates
    language = body.language

    if not candidates:
        raise HTTPException(400, "No candidates provided")

    prov = body.quick_provider or body.provider or resolve_role_llm(DEFAULT_CONFIG, "quick")[0]
    qm = body.quick_model or resolve_role_llm(DEFAULT_CONFIG, "quick")[1]

    try:
        client = create_llm_client(provider=prov, model=qm, base_url=DEFAULT_CONFIG.get("backend_url"))
    except Exception as e:
        raise HTTPException(500, f"Failed to create LLM client: {e}")

    def event_stream():
        for evt in run_batch_analysis_sse(candidates, llm_client=client, language=language, context=body.context):
            data_str = _json.dumps(evt, ensure_ascii=False)
            # Ensure single-line: json.dumps already escapes \n as \\n in string values
            yield f"event: {evt['event']}\ndata: {data_str}\n\n"
        yield "event: stream-end\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ---- RRG Rotation Monitor API ----


@app.get("/api/rotation/rrg")
def rotation_rrg(lookback: int = Query(default=10), mode: str = Query(default="capital")):
    """Return RRG data for all A-share industries."""
    from quantconclave.sector_scan.rotation import get_rrg_data
    result = get_rrg_data(lookback=lookback, mode=mode)
    if result.get("error") and not result.get("industries"):
        # Return 200 with error field — frontend handles display
        return result
    return result


# ---- Smart Scan API ----


class SmartScanRequest(BaseModel):
    top_n: int = 5
    auto_analyze: bool = False
    use_rps: bool = True
    quadrant_filter: str = "all"
    provider: str = ""
    quick_model: str = ""
    quick_provider: str = ""
    language: str = "Chinese"



@app.get("/api/rotation/rrg/snapshot")
def get_rrg_snapshot(date: str | None = None):
    """Get RRG snapshot data, optionally for a specific date."""
    from web.results_store import get_rrg_snapshots
    if date:
        snapshots = get_rrg_snapshots(DEFAULT_CONFIG, date_str=date)
    else:
        snapshots = get_rrg_snapshots(DEFAULT_CONFIG)
    return {"snapshots": snapshots}


@app.get("/api/rotation/rrg/stats")
def get_rrg_stats(days: int = Query(default=20, ge=5, le=90)):
    """Get RRG transition statistics over the last N days."""
    from quantconclave.sector_scan.rrg_stats import compute_transition_stats
    return compute_transition_stats(DEFAULT_CONFIG, days)


@app.get("/api/rotation/rrg/history/{industry_name}")
def get_rrg_history(industry_name: str, days: int = Query(default=60, ge=10, le=365)):
    """Get RRG trajectory history for a specific industry."""
    from web.results_store import get_rrg_snapshots
    snapshots = get_rrg_snapshots(DEFAULT_CONFIG, industry_name=industry_name)
    # Filter to requested days
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    snapshots = [s for s in snapshots if s["date"] >= cutoff]
    snapshots.sort(key=lambda x: x["date"])
    return {"industry": industry_name, "history": snapshots}



@app.get("/api/sector/concept/list")
def list_concepts_api(keyword: str | None = None):
    """List THS concept sectors, optionally filtered by keyword."""
    from quantconclave.sector_scan.concept_scanner import list_concepts
    return list_concepts(keyword=keyword)


@app.get("/api/sector/concept/rank")
def rank_concepts_api():
    """Get top-ranked concept sectors by recent performance."""
    from quantconclave.sector_scan.concept_scanner import rank_concepts
    return rank_concepts()


@app.get("/api/sector/concept/scan/{concept_code}")
def scan_concept_api(concept_code: str, top_n: int = 10):
    """Smart-scan stocks within a concept sector."""
    from quantconclave.sector_scan.concept_scanner import scan_concept_stocks
    return scan_concept_stocks(concept_code, top_n=top_n)


@app.post("/api/smart-scan")
def smart_scan(body: SmartScanRequest):
    """Smart-scan Leading+Improving industries with dual-strategy scoring."""
    from quantconclave.sector_scan.rotation import get_rrg_data
    from quantconclave.sector_scan.smart_scanner import run_smart_scan

    # Get RRG data for Leading+Improving industries
    rrg = get_rrg_data(lookback=10, mode="capital")
    industries = []
    quadrants = {}
    for ind in (rrg.get("industries") or []):
        if body.quadrant_filter == "leading" and ind["quadrant"] != "leading":
            continue
        if body.quadrant_filter == "improving" and ind["quadrant"] != "improving":
            continue
        if ind["quadrant"] not in ("leading", "improving"):
            continue
        industries.append(ind["name"])
        quadrants[ind["name"]] = ind["quadrant"]

    if not industries:
        raise HTTPException(404, "No leading/improving industries found")

    result = run_smart_scan(industries, quadrants, top_n=body.top_n, use_rps=body.use_rps)

    # If auto-analyze, run batch LLM analysis on top results
    report = None
    if body.auto_analyze and result["results"]:
        from quantconclave.sector_scan.batch_analysis import run_batch_analysis
        from quantconclave.llm_clients import create_llm_client, resolve_role_llm

        cfg = DEFAULT_CONFIG
        provider = body.quick_provider or body.provider or resolve_role_llm(cfg, "quick")[0]
        quick_model = body.quick_model or resolve_role_llm(cfg, "quick")[1]

        try:
            client = create_llm_client(provider=provider, model=quick_model, base_url=cfg.get("backend_url"))
            llm_result = run_batch_analysis(result["results"][:30], llm_client=client, context="smart_scan")
            if llm_result.get("report"):
                report = llm_result["report"]
        except Exception as e:
            logger.warning("Auto-analyze failed: %s", e)

    return {
        **result,
        "report": report,
        "industries_scanned": len(industries),
    }


