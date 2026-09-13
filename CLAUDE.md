# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity

**CapitalRadar** — a multi-agent LLM framework that detects institutional capital flows ("主力资金") for retail investors. The core narrative: deploy specialized AI agents to scan for smart-money footprints across capital flow data, news, sentiment, fundamentals, and technical indicators, then debate and synthesize findings into actionable trading intelligence.

Internal Python package: `capitalradar`.

## Commands

```bash
# Interactive CLI (primary interface — 8-step guided questionnaire)
python -m cli.main

# Quick programmatic run (single ticker, single date — edit main.py first)
python main.py

# Web dashboard — FastAPI + SSE streaming at http://127.0.0.1:8003 (main app) + http://127.0.0.1:8005 (K-line chart)
python run_web.py

# Test suite
pytest                                      # all tests
pytest -m unit                              # unit tests only
pytest -m "not integration"                 # skip tests needing external services
pytest tests/test_memory_log.py             # single test file
```

## Architecture

**Pipeline via LangGraph**: The core is a 5-stage `StateGraph` at `capitalradar/graph/trading_graph.py` (`CapitalRadarGraph`). Stages:

1. **Analyst Team** — 7 parallel or sequential analysts (Capital Flow comes first as anchor, then Market, Sentiment, News, Fundamentals, Competitor, Partner). Each calls tools in a loop until satisfied or hitting `max_analyst_tool_calls`, then produces a prose report. Selection is user-configurable; order is fixed.
2. **Research Debate** — Bull Researcher ↔ Bear Researcher debate back-and-forth, then Research Manager adjudicates with structured output (`ResearchPlan`).
3. **Trader** — Translates the research plan into a concrete transaction proposal (`TraderProposal`).
4. **Risk Debate** — Aggressive ↔ Conservative ↔ Neutral round-robin debate, then Portfolio Manager makes the final decision (`PortfolioDecision`).
5. **Output** — Full state written to disk as JSON + markdown under `~/.capitalradar/logs/<TICKER>/CapitalRadarStrategy_logs/`.

All stages share a single `AgentState` TypedDict (`capitalradar/agents/utils/agent_states.py`).

### Graph construction (`capitalradar/graph/`)

- **`trading_graph.py`** (`CapitalRadarGraph`) — Orchestrator: creates two LLM instances (deep-thinking for managers, quick-thinking for analysts/debaters), resolves pending memory-log entries before each run, handles `_fetch_returns` for outcome resolution, and coordinates graph invocation (stream for debug/CLI, invoke for web).
- **`setup.py`** (`GraphSetup.setup_graph`) — Builds the LangGraph `StateGraph`, adding analyst+clear+tool nodes, debate nodes, and conditional edges. The analyst concurrency limit controls how analysts fan out.
- **`conditional_logic.py`** — Per-analyst `should_continue_*` methods route between the LLM node and its `ToolNode`. Uses message-based tool-call counting (per-analyst since messages are cleared between analysts via `create_msg_delete()`). Also controls debate round limits (`max_debate_rounds`, `max_risk_discuss_rounds`).
- **`analyst_execution.py`** — Defines `AnalystNodeSpec` (key, agent_node, clear_node, tool_node, report_key), `build_analyst_execution_plan()`, and `AnalystWallTimeTracker` for per-analyst wall-clock timing display in CLI.
- **`propagation.py`** — Creates initial `AgentState` with empty debate states and provides graph invocation args (`recursion_limit`, stream_mode).
- **`reflection.py`** — Post-hoc LLM reflection on trade outcomes (deferred until price data becomes available via yfinance).
- **`signal_processing.py`** — Extracts the final Buy/Hold/Sell signal from Portfolio Manager prose.
- **`checkpointer.py`** — LangGraph SQLite checkpointing; thread IDs are `ticker+date` so same ticker+date resumes, different date starts fresh.

### Agent nodes (`capitalradar/agents/`)

Each agent is a **factory function** (e.g. `create_market_analyst(llm)`) returning a LangGraph node callable. Factory pattern allows binding different LLMs per agent (quick-thinking for analysts/debaters, deep-thinking for managers).

**Structured output** (`agents/schemas.py`): The three decision-making agents produce typed Pydantic instances — `ResearchPlan` (recommendation: Buy/Overweight/Hold/Underweight/Sell), `TraderProposal` (action: Buy/Hold/Sell), `PortfolioDecision` (rating + executive summary + investment thesis). Each has a `render_*()` helper that converts back to markdown for downstream consumption. The per-model capability table in `llm_clients/capabilities.py` drives structured-output dispatch (`json_schema`, `function_calling`, `json_mode`, or `none`).

**Memory (`agents/utils/memory.py`)**: `CapitalRadarMemoryLog` — append-only markdown decision log. Stores decisions immediately with `[pending]` tags; resolves outcomes later via `batch_update_with_outcomes()` using atomic temp-file writes. Past same-ticker decisions (up to 5) and cross-ticker lessons (up to 3) are injected into the Portfolio Manager prompt as `past_context`. Supports optional rotation via `memory_log_max_entries`.

### LLM clients (`capitalradar/llm_clients/`)

Factory pattern: `create_llm_client(provider, model, base_url, **kwargs)` → `BaseLLMClient`. All OpenAI-compatible providers (openai, deepseek, xai, qwen, glm, minimax, ollama, openrouter, azure) share `OpenAIClient` → `NormalizedChatOpenAI`. Anthropic and Google have dedicated subclasses.

**Provider quirks are isolated declaratively** in `capabilities.py` — the `ModelCapabilities` dataclass records per-model `supports_tool_choice`, `preferred_structured_method`, `requires_reasoning_content_roundtrip`, `requires_reasoning_split`. Provider subclasses consult `get_capabilities(model_name)` instead of hardcoding model-name `if` ladders.

Adding a new provider requires: capability entry in `capabilities.py`, API key env var in `api_key_env.py`, model options in `model_catalog.py`, and routing in `factory.py` (most are just added to `_OPENAI_COMPATIBLE` tuple).

### Data vendors (`capitalradar/dataflows/`)

`interface.py` routes tool calls through a **multi-vendor fallback chain**. Vendors are configured per **category** (`data_vendors` config) or per **tool** (`tool_vendors` config). Example: `"fundamental_data": "tushare,yfinance"` tries tushare first, falls back to yfinance. Vendors signal unavailability by returning `"# SKIP_VENDOR:"` strings or raising; the router catches both and advances to the next vendor.

Supported vendors: yfinance, akshare, tushare, eastmoney, cls, xueqiu, eastmoney_guba, sina. Chinese A-share tickers are auto-detected by `_is_cn_ticker()` and domestic vendors are preferred.

`dataflows/config.py` provides `get_config()` and `set_config()` with one-level-deep merge for nested dict keys. In new dataflow code, use `get_config()` rather than importing `DEFAULT_CONFIG` directly.

### Configuration (`capitalradar/default_config.py`)

`DEFAULT_CONFIG` is the single source of truth — a dict with env-var overrides via `CAPITALRADAR_*` variables. `_ENV_OVERRIDES` maps env var names to config keys; `_apply_env_overrides` coerces types based on existing default values. The `.env` file is loaded at `capitalradar/__init__.py` import time via `dotenv(find_dotenv(usecwd=True))` with `override=False`.

### Web dashboard (`web/`)

FastAPI app with SSE streaming (`web/stream.py`), SQLite-backed results store (`web/results_store.py`), APScheduler for periodic analyses (`web/scheduler.py`), post-analysis chat with the Portfolio Manager (`web/history_chat.py`), sector scanning API, RRG rotation monitoring, and batch LLM analysis of sector scan candidates.

### Sector scan (`capitalradar/sector_scan/`)

A-share industry sector scanning pipeline: `rps.py` (relative strength ranking), `rotation.py` (RRG rotation monitor), `smart_scanner.py` (Leading+Improving industry detection with dual-strategy scoring), `batch_analysis.py` (batch LLM preliminary analysis of scan candidates with SSE streaming support).

## Key design rules

- The `.env` file is loaded at `capitalradar/__init__.py` import time via `dotenv` with `usecwd=True`. `load_dotenv` uses `override=False` — existing env vars take precedence.
- All API key env var names are centralized in `capitalradar/llm_clients/api_key_env.py`.
- New LLM providers need: a capability entry in `capabilities.py`, a base URL in `openai_client.py` (if OpenAI-compatible), an API key env var in `api_key_env.py`, model options in `model_catalog.py`, and (for OpenAI-compatible) just adding the name to `_OPENAI_COMPATIBLE` in `factory.py`.
- `_ENV_OVERRIDES` in `default_config.py` maps `CAPITALRADAR_*` env vars to config keys. Add a row there to expose a new key for env-based override.
- Ticker symbols with exchange suffixes (`.SH`, `.SZ`, `.NS`, `.T`, `.HK`, `.L`, `.TO`, `.AX`) are auto-mapped to regional benchmarks for alpha calculation via `benchmark_map` in config. Use `_resolve_benchmark()` not hardcoded `SPY`.
- The `output_language` config only affects analyst prose output — internal agent debate always stays in English for reasoning quality.
- Analyst report fields in `AgentState` are independent — each analyst writes its own report key. Messages are cleared between analysts via `create_msg_delete()`.
- Capital Flow analyst always runs first (anchor position); remaining analysts follow in fixed order.
- Asset type (`"stock"` vs `"crypto"`) is auto-detected from ticker by the CLI, or passed explicitly by programmatic callers.
