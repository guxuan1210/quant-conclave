# Native US Market Adapter Design

**Date:** 2026-09-17
**Status:** Approved for implementation planning
**Scope:** Native market adaptation and single-stock US equity deep analysis

## 1. Objective

Add first-class US equity analysis without replacing QuantConclave's existing dataflow or LangGraph architecture. The first release will support deep analysis of individual US-listed equities while preserving all existing A-share behavior and stored ticker formats.

The implementation must make market-specific evidence explicit. A US analysis must not invoke or describe northbound flow, Dragon-Tiger lists, A-share large-order flow, or other China-only evidence. It must distinguish real-time observations from daily, filing-date, and quarterly evidence.

US market-wide screening, sector rotation, AI Pick, and scheduled evaluation are explicitly deferred to a later release.

## 2. Design Principles

1. **Market-aware, not branch-driven.** Agents and providers consume a resolved instrument profile instead of adding more scattered `is_cn` checks.
2. **One factual snapshot per run.** Core metrics are collected once and shared across agents to prevent contradictory values in the same analysis.
3. **Point-in-time correctness.** Evidence is eligible only when it was publicly available on or before the analysis date.
4. **Provenance is part of the data.** Every evidence item records its provider, effective date, retrieval time, status, and freshness.
5. **Graceful but visible degradation.** Missing data must be reported as unavailable, never silently converted to zero or neutral.
6. **Backward compatibility.** Existing A-share workflows, persisted tickers, scheduling, history, and SPY-based US outcome resolution remain compatible.

## 3. Architecture

The existing LangGraph topology remains unchanged. Two market-aware stages run before graph propagation:

```text
User input (AAPL)
        |
        v
InstrumentResolver
        |
        v
InstrumentProfile
        |
        v
EvidenceBuilder
        |
        v
EvidencePack
        |
        v
Existing multi-agent LangGraph pipeline
```

### 3.1 InstrumentProfile

`InstrumentProfile` is the authoritative description of the analyzed instrument.

```python
InstrumentProfile(
    symbol="AAPL",
    asset_type="equity",
    market="US",
    exchange="NASDAQ",
    currency="USD",
    timezone="America/New_York",
    calendar="NASDAQ",
    benchmark="SPY",
    sector_taxonomy="GICS",
)
```

Required fields:

- canonical symbol;
- asset type;
- market and exchange;
- quote currency;
- market timezone and trading calendar;
- default benchmark;
- sector taxonomy;
- supported evidence capabilities.

The resolver preserves existing stored ticker values. It must correctly resolve ordinary tickers such as `AAPL` and `NVDA`, class-share formats such as `BRK.B`, and existing exchange-qualified non-US symbols without treating every non-CN symbol as a US equity.

### 3.2 EvidencePack

`EvidencePack` is an immutable per-run snapshot of shared facts. It contains:

- company identity;
- adjusted OHLCV and current quote snapshot;
- normalized financial statements and valuation snapshot;
- SEC filings and filing dates;
- insider transactions;
- institutional holdings;
- analyst recommendations and estimate revisions when available;
- news and sentiment inputs;
- market, sector, and benchmark context;
- data-quality and provenance metadata.

Each entry is represented as an `EvidenceItem` with these fields:

```python
EvidenceItem(
    kind="income_statement",
    source="sec_companyfacts",
    as_of="2026-06-27",
    period_end="2026-06-27",
    filed_at="2026-08-01",
    fetched_at="2026-09-17T10:00:00Z",
    freshness="filing",
    status="available",
    payload={...},
)
```

Valid statuses are `available`, `no_data`, `degraded`, `stale`, and `error`. `no_data` and `error` are distinct from a zero numeric value and from a neutral analytical signal.

## 4. Component Boundaries

```text
quantconclave/
|-- instruments/
|   |-- __init__.py
|   |-- models.py       # InstrumentProfile and market enums
|   `-- resolver.py     # ticker to market/exchange/profile
|-- evidence/
|   |-- __init__.py
|   |-- models.py       # EvidenceItem and EvidencePack
|   `-- builder.py      # collection, normalization, eligibility
`-- dataflows/
    |-- sec_edgar.py    # submissions, company facts, filings, Form 4
    `-- us_market.py    # US market and positioning context
```

### 4.1 InstrumentResolver

The resolver has one responsibility: turn a user-supplied symbol and optional asset hint into an `InstrumentProfile`. It may use deterministic symbol rules first and provider metadata second. If identity remains ambiguous, it returns a resolution error instead of guessing.

### 4.2 EvidenceBuilder

The builder coordinates independent provider calls, normalizes results, applies point-in-time eligibility, and returns one Evidence Pack. Provider-specific parsing stays in `dataflows`; the builder does not embed HTTP or vendor response logic.

The builder may fetch independent evidence concurrently, but the returned pack must be deterministic for a fixed set of provider responses. Required evidence is company identity and basic price history. All other evidence can degrade independently.

### 4.3 SEC EDGAR Adapter

The SEC adapter supplies:

- ticker/CIK identity mapping;
- submissions history;
- Company Facts XBRL data;
- relevant 10-K, 10-Q, and 8-K metadata and document links;
- Form 4 insider transactions.

It must:

- send a configured application name and contact email in the HTTP `User-Agent`;
- use bounded request rates, local caching, timeouts, and exponential backoff;
- distinguish fiscal period end from filing/publication date;
- exclude filings published after the analysis date;
- retain raw SEC concept names alongside normalized metrics;
- preserve accession numbers for traceability.

The first release does not require full natural-language parsing of every filing. It collects authoritative metadata, XBRL facts, and filing text needed by the existing agents.

### 4.4 US Market Adapter

The US market adapter provides market context suitable for US equities:

- SPY and QQQ benchmark behavior;
- current session state using the resolved US market calendar;
- relative performance and volume behavior;
- available institutional ownership and analyst recommendation data;
- insider and positioning summaries derived from eligible evidence.

Optional paid options-flow or consolidated order-flow providers are outside the first-release scope.

## 5. Provider Strategy

The existing `route_to_vendor` interface remains the system boundary. The first release uses:

| Evidence | Primary | Fallback |
|---|---|---|
| Adjusted daily OHLCV | existing yfinance adapter | existing vendor chain where supported |
| Quote/intraday | existing yfinance adapter | unavailable/degraded status |
| Company identity and filings | SEC EDGAR | cached SEC identity; ticker-only failure if unresolved |
| Financial statements | SEC Company Facts | existing yfinance fundamentals |
| Insider activity | SEC Form 4 | existing yfinance insider transactions |
| Institutional ownership | existing yfinance holders | unavailable/no-data status |
| Analyst recommendations | existing yfinance recommendations | unavailable/no-data status |
| News and sentiment | existing news, Reddit, and StockTwits adapters | existing provider fallback chain |

OpenBB can be added later as an optional provider behind the same interfaces. It is not a core dependency of this design.

## 6. Market-Specific Analytical Semantics

The public role name `Capital Flow Analyst` remains unchanged for compatibility. Its internal prompt and tool set are selected by `InstrumentProfile.market`.

### 6.1 A-share mode

Existing evidence remains in use:

- main-force order-size flow;
- northbound flow;
- margin financing and securities lending;
- Dragon-Tiger lists;
- block trades and Eastmoney real-time flow.

### 6.2 US mode

The analyst applies a `Positioning & Flow` framework:

- price, volume, VWAP, liquidity, and relative-strength behavior;
- SEC Form 4 insider activity;
- institutional ownership and 13F-derived changes when available;
- short-interest or short-sale-volume evidence only when a compatible provider is present;
- analyst estimate and recommendation changes;
- sector and benchmark-relative positioning.

Evidence must retain its true cadence. In particular:

- 13F is quarterly and delayed; it cannot support claims about current-day institutional buying;
- daily short-sale volume is not the same as short interest;
- missing insider, holdings, news, or short data does not imply a neutral signal;
- yfinance holder snapshots are supplementary evidence, not a real-time institutional-flow feed.

## 7. Agent Integration

`InstrumentProfile` and `EvidencePack` are added to the initial graph state. Existing agents receive a compact, bounded evidence summary in their prompts and can access the structured pack through helpers.

Changes by role:

- **Capital Flow Analyst:** selects A-share or US positioning prompt and tools. The US path contains no mandatory `get_money_flow`, HSGT, margin, or Dragon-Tiger instructions.
- **Fundamentals Analyst:** uses SEC and yfinance tools for US profiles and does not expose Eastmoney-only tools.
- **Market Analyst:** uses the resolved calendar, currency, benchmark, and shared price snapshot.
- **News and Sentiment Analysts:** preserve the distinction between no data and neutral sentiment.
- **Competitor and Partner Analysts:** use GICS-oriented US peer and supply-chain context when the market is US.
- **Research Manager, risk roles, and Portfolio Manager:** receive evidence freshness and must not give quarterly evidence the same weight as live or daily observations.

Agents may request supplementary data, but they must not independently redefine core shared metrics. If supplementary data conflicts with the Evidence Pack, the report records the conflict and source dates rather than silently replacing the shared value.

## 8. Data Flow

For an `AAPL` run:

1. Normalize the ticker without changing its persisted form.
2. Resolve the US equity profile and company identity.
3. Fetch required identity and price evidence.
4. Fetch independent SEC, ownership, analyst, news, and sentiment evidence.
5. Normalize provider results into Evidence Items.
6. Filter every item by public availability at the requested analysis date.
7. Build a compact evidence summary plus the structured pack.
8. Add the profile and pack to LangGraph initial state.
9. Run the existing analyst, debate, trader, risk, and Portfolio Manager topology.
10. Persist the decision together with a provenance summary and retain existing outcome-resolution behavior against SPY.

## 9. Failure and Degradation Behavior

| Failure | Behavior |
|---|---|
| SEC temporarily unavailable | Use eligible cache; otherwise fall back to yfinance fundamentals and mark the pack `degraded` |
| Price history unavailable from every source | Stop the analysis with a clear required-evidence error |
| Company identity unresolved | Stop rather than guess a company |
| 13F, Form 4, news, or analyst data empty | Record `no_data`; do not generate a neutral conclusion from absence |
| One optional provider errors | Record provider error and continue with remaining evidence |
| Evidence published after analysis date | Exclude it and record the exclusion in diagnostics |
| Conflicting metrics | Preserve both source values and dates; use the configured authoritative source for the shared metric |

Provider errors shown to users must be concise. Detailed request and parsing diagnostics remain available in logs without exposing secrets or API credentials.

## 10. Persistence and Compatibility

The existing ticker remains the primary lookup key for history and scheduling. No database migration is required for the first release.

Persisted analysis metadata gains optional fields:

- `instrument_profile`;
- `evidence_as_of`;
- `evidence_sources`;
- `evidence_quality`.

Older records without these fields remain readable. Existing SPY-based US resolution and alpha calculation continue unchanged.

## 11. Testing Strategy

### 11.1 Unit tests

- `AAPL` and `NVDA` resolve to US equities with USD, US market timezone, and SPY benchmark.
- `BRK.B` remains intact and resolves without being treated as an exchange suffix.
- Existing A-share forms continue to resolve as CN instruments.
- Unsupported or ambiguous symbols return an explicit resolution error.
- Evidence statuses distinguish zero, `no_data`, `degraded`, and `error`.

### 11.2 Adapter tests

- Parse committed SEC response fixtures without network access.
- Map CIKs, filings, fiscal periods, and accession numbers correctly.
- Reject filings whose publication date is later than the analysis date.
- Verify configured SEC identity headers, cache use, bounded retries, and sanitized errors.

### 11.3 Agent tests

- US Capital Flow prompt and tool list contain no HSGT, Dragon-Tiger, A-share main-force, or mandatory `get_money_flow` instructions.
- US Fundamentals tools contain no Eastmoney-only tools.
- A-share prompt and tool behavior remain unchanged.
- `no_data` inputs are not summarized as neutral evidence.

### 11.4 Workflow tests

- Run a complete graph with a fixed Evidence Pack for AAPL, NVDA, and BRK.B.
- Assert that every agent sees identical core financial values and dates.
- Verify graceful SEC degradation with fixed yfinance fallbacks.
- Verify SPY benchmark resolution and compatibility with stored history.
- Run the existing A-share and general test suites to detect regressions.

Live SEC and market-provider tests are optional integration tests and are excluded from the default unit suite.

## 12. Acceptance Criteria

The first release is complete when:

1. AAPL, NVDA, and BRK.B can complete the single-stock deep-analysis workflow.
2. US reports contain no inapplicable A-share evidence or terminology.
3. Core US financial facts include source and effective/publication dates.
4. All agents consume the same shared values for core financial metrics.
5. 13F, insider, news, and other missing evidence is represented accurately as unavailable rather than neutral.
6. SEC failure produces a visible, non-blocking degradation when eligible fallback data exists.
7. SPY-relative outcome resolution remains functional.
8. Existing A-share workflows and tests do not regress.

## 13. Deferred Work

The following items require separate designs and implementation plans:

- US AI Pick and whole-market screening;
- S&P 500, Nasdaq-100, and other universe management;
- US sector rotation dashboards;
- US scheduled sampling and investment-effect evaluation;
- survivorship-bias-free historical constituent data;
- paid options-flow, dark-pool, or consolidated institutional-flow providers;
- broker connectivity and trade execution;
- OpenBB as an optional provider extension.
