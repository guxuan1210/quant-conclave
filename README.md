**English** | [简体中文](README.zh-CN.md)

# 🛡️ QuantConclave

**Multi-role AI research for institutional capital flow, market evidence, debate, risk review, and portfolio decisions.**

QuantConclave organizes financial research as an auditable workflow rather than a single long model response. Specialized roles examine independent evidence, challenge one another, and hand a structured record to a Portfolio Manager. The project is optimized for Chinese A-share research while retaining multi-market and multi-provider foundations.

> [!IMPORTANT]
> QuantConclave is a research and educational project. Model output may be wrong, stale, or incomplete. Nothing produced by this software is investment, trading, legal, or financial advice, and historical evaluation does not establish future profitability.

## Why QuantConclave?

Market research is usually fragmented across price data, capital flow, filings, news, sentiment, and several models. QuantConclave brings those inputs into one traceable process:

- independent role reports instead of one undifferentiated answer;
- institutional-flow evidence as the first research anchor;
- explicit bull/bear and risk debates before a final decision;
- multi-provider data routing with fallbacks and cross-checks;
- saved runs, outcome resolution, and human-reviewed learning;
- a local web workspace for research, screening, prediction, strategy, and backtesting.

## Core Capabilities

QuantConclave uses three distinct domain terms:

- A **Capability** is a complete user-facing workflow.
- A **Role** is an LLM identity with a defined research responsibility.
- A **Node** is a LangGraph execution unit for a role, tool, or control step.

| Capability | What it does |
|---|---|
| Deep Analysis | Coordinates seven analyst roles, adjudication, bull/bear debate, trading, risk debate, and the final Portfolio Manager decision |
| AI Pick | Builds candidate lists from natural-language screens and institutional-flow signals |
| Prediction | Combines XGBoost models with LLM synthesis for direction, ranges, and institutional behavior |
| Strategy | Produces and manages testable strategy definitions from research intent |
| Backtest | Runs vectorized simulations with costs, equity curves, trades, and risk metrics |
| Advisory | Provides a conversational entry point to current data, saved research, and approved experience |
| History | Searches, compares, and reviews previous analyses and outcomes |
| Calibration | Resolves outcomes, detects recurring patterns, extracts candidate experience, and versions approved skills |

## How the Research Pipeline Works

```text
User or scheduled task
        │
        ▼
Capital Flow Analyst (anchor)
        │
        ├── Market Analyst
        ├── Sentiment Analyst
        ├── News Analyst
        ├── Fundamentals Analyst
        ├── Competitor Analyst
        └── Partner Analyst
        │
        ▼
Rule adjudication → Bull / Bear Debate → Research Manager
        │
        ▼
Trader → Aggressive / Conservative / Neutral Risk Debate
        │
        ▼
Portfolio Manager → Buy / Overweight / Hold / Underweight / Sell
```

Capital flow runs first to establish a trade-time and institutional-behavior anchor. Downstream roles retain their own reports, while the final decision records short- and medium-term outlooks, confidence, risks, and supporting evidence.

## Data and Reliability

The capital-flow pipeline checks four properties:

| Check | Purpose |
|---|---|
| Scale | Reject flows too small to be meaningful |
| Persist | Test whether direction is sustained |
| Align | Compare capital direction with price behavior |
| Cross | Seek confirmation from another market or flow source |

Configured providers may include Tushare, Eastmoney-related interfaces, AKShare, and yfinance. Routing and fallback logic improve resilience, but third-party data can still be delayed, incomplete, rate-limited, or unavailable.

### Native US equity analysis (first release)

Single-stock deep analysis accepts US symbols such as `AAPL`, `NVDA`, and `BRK.B`. The instrument resolver marks them as US equities in USD and uses `SPY` as the default comparison benchmark. This release covers the deep-analysis workflow only; US AI Pick, market-wide screening, universe management, and sector-rotation dashboards are not included.

For SEC EDGAR data, set an identifying User-Agent in `.env` (for example, `QUANTCONCLAVE_SEC_USER_AGENT=QuantConclave your-email@example.com`). SEC does not require an API key, but it does require a descriptive User-Agent. A key for the selected LLM provider is still required to run the analysis. SEC filings and company facts are the primary sources for US identity, filings, and financial facts; yfinance supplies price/quote data and optional fallback market fields.

Every evidence item carries an explicit status. `NO_DATA` means that the requested source returned no eligible observation as of the analysis date; it must not be interpreted as zero, neutral positioning, or evidence that an event did not occur. `DEGRADED` means an eligible fallback was used or a primary source was unavailable. US holder and insider evidence is not equivalent to A-share “main-force” or northbound-flow data, and 13F holdings are delayed quarterly disclosures rather than real-time positioning.

Provider latency, filing delays, revisions, and incomplete coverage remain possible. Verify material conclusions against the original filing or market source; all output remains research-only and is not investment advice.

When sufficient trading days and prices are available, saved recommendations can be resolved at 5-, 20-, and 60-day horizons for review and calibration. Resolution is an evaluation mechanism, not proof of alpha.

## Human-Reviewed Learning Loop

```text
Saved research
    │
    ├── Resolve: attach observable market outcomes
    ├── Meta-Eval: identify recurring performance patterns
    ├── Extract: create candidate lessons
    └── Skill Gen: compile versioned guidance
                              │
                              ▼
                       pending_review
                              │ human approval
                              ▼
                           active
```

Extracted experience is not activated automatically. A reviewer must approve it before it enters future research context, and active experience can be revoked or rolled back.

## Quick Start

Requirements: Python 3.10 or newer and credentials for at least one supported LLM provider.

```bash
git clone https://github.com/guxuan1210/quant-conclave.git
cd quant-conclave

# Install with uv
uv pip install -e .

# Or install with pip
pip install -e .

cp .env.example .env
# Add the API key for your selected LLM provider.
# TUSHARE_TOKEN is recommended for A-share capital-flow research.
# For US SEC evidence, set QUANTCONCLAVE_SEC_USER_AGENT to an app/contact identity.

python run_web.py
```

On Windows PowerShell, create the local environment file with:

```powershell
Copy-Item .env.example .env
python run_web.py
```

The launcher starts two local services:

| URL | Service |
|---|---|
| `http://127.0.0.1:8003` | Main research workspace |
| `http://127.0.0.1:8005` | Candlestick chart service |

Both defaults come from `quantconclave/runtime_manifest.py` and can be overridden through environment variables.

## Configuration

| Variable | Purpose | Required when |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek provider | DeepSeek is selected |
| `OPENAI_API_KEY` | OpenAI provider | OpenAI is selected |
| `ANTHROPIC_API_KEY` | Anthropic provider | Anthropic is selected |
| `GOOGLE_API_KEY` | Google provider | Google is selected |
| `TUSHARE_TOKEN` | A-share flow, financial, and index data | Recommended for A-share workflows |
| `QUANTCONCLAVE_SEC_USER_AGENT` | Identifies SEC EDGAR requests (`App name contact@example.com`) | Required for SEC-backed US evidence |
| `QUANTCONCLAVE_SEC_REQUEST_INTERVAL_SECONDS` | Minimum delay between SEC requests | Optional; keep a courteous rate |
| `QUANTCONCLAVE_SEC_TIMEOUT_SECONDS` | SEC request timeout | Optional |
| `HTTP_PROXY` / `HTTPS_PROXY` | Proxy for selected overseas sources | Optional |
| `NO_PROXY` | Direct routing for domestic sources | Recommended when using a proxy |

Runtime settings use the `QUANTCONCLAVE_*` prefix:

```dotenv
QUANTCONCLAVE_LLM_PROVIDER=deepseek
QUANTCONCLAVE_DEEP_THINK_LLM=deepseek-v4-pro
QUANTCONCLAVE_QUICK_THINK_LLM=deepseek-v4-flash
QUANTCONCLAVE_OUTPUT_LANGUAGE=English
```

See [.env.example](.env.example) for the maintained configuration template. The client layer also supports xAI, DashScope, Zhipu GLM, MiniMax, OpenRouter, Azure-compatible endpoints, and Ollama; actual model support depends on provider APIs and local configuration.

## Python API

```python
from quantconclave.default_config import DEFAULT_CONFIG
from quantconclave.graph.trading_graph import QuantConclaveGraph

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-v4-pro"
config["quick_think_llm"] = "deepseek-v4-flash"

graph = QuantConclaveGraph(debug=True, config=config)
state, decision = graph.propagate("600519.SH", "2026-06-30")
print(decision)
```

## Project Layout

```text
quant-conclave/
├── quantconclave/
│   ├── agents/          # Analyst, research, trading, risk, and manager roles
│   ├── graph/           # LangGraph orchestration, adjudication, and learning
│   ├── dataflows/       # Multi-provider data routing
│   ├── prediction/      # ML + LLM prediction
│   ├── backtest/        # Vectorized backtest compatibility layer
│   ├── quant/           # Vectorized calculations, costs, and metrics
│   ├── sector_scan/     # Sector scanning and candidate selection
│   ├── advisory/        # Experience and calibration tools
│   └── workspace/       # Unified persistence entry point
├── web/                 # FastAPI application and browser UI
├── tests/               # Unit, integration, and smoke tests
├── chart_app.py         # Chart service (default port 8005)
├── run_web.py           # Local dual-service launcher
└── pyproject.toml       # Python package metadata
```

## Migration Compatibility

For one release, QuantConclave retains compatibility with its pre-rename entry points:

- the `capitalradar` CLI remains an alias of the `quantconclave` command;
- `CAPITALRADAR_*` variables are read when the corresponding `QUANTCONCLAVE_*` variable is not set;
- the migration layer can discover legacy data locations and move supported data into the QuantConclave workspace.

New deployments should use the `quantconclave` package and command together with `QUANTCONCLAVE_*` settings.

## Project Status and Limitations

- Current package version: `0.2.5`.
- The backtest engine is vectorized; selected adapters preserve the legacy result shape for callers.
- Live and historical data quality depends on third-party providers and user entitlements.
- LLM output is non-deterministic and should be checked against source data and public disclosures.
- Backtests and calibration remain exposed to data bias, transaction-cost assumptions, and overfitting.
- The project does not connect to a broker or execute trades automatically.

## License

Licensed under the [Apache License 2.0](LICENSE).

## Acknowledgements and Citation

QuantConclave is derived from the [TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent financial trading research framework. It extends that foundation with institutional-flow analysis, multi-provider routing, prediction, screening, historical experience, and vectorized backtesting workflows.

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
  title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
  author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
  year={2025},
  eprint={2412.20138},
  archivePrefix={arXiv},
  primaryClass={q-fin.TR},
  url={https://arxiv.org/abs/2412.20138}
}
```
