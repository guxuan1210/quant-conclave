# QuantConclave README Internationalization Design

## Goal

Make the repository landing page suitable for an international GitHub audience while preserving a complete Chinese reference. Documentation must describe the current implementation accurately and must not imply verified investment performance.

## Deliverables

- Replace `README.md` with a concise English landing page.
- Add `README.zh-CN.md` as the detailed Chinese version.
- Add reciprocal language links at the top of both files.

## English README Structure

1. Project name, one-sentence positioning, and research-only disclaimer.
2. Key capabilities expressed as product capabilities, not as a fixed count of agents.
3. Compact architecture flow showing analysts, debates, trader, risk roles, and Portfolio Manager.
4. Data-source fallback overview and the human-reviewed learning loop.
5. Quick start using the actual repository URL, package name, runtime entry point, and ports.
6. Minimal environment-variable table and Python API example.
7. Project status, limitations, license, attribution, and citation.

The English page should be scannable and avoid reproducing the entire source-tree inventory or long internal implementation descriptions.

## Chinese README

The Chinese version may retain the existing detailed explanations, but it must:

- distinguish Capability, Role, and execution Node instead of calling everything an “agent”;
- describe the current vectorized backtest engine rather than Backtrader;
- use ports 8003 and 8005 consistently;
- use current `quantconclave` imports, CLI names, environment variables, and GitHub URLs;
- describe return resolution and learning as evaluation workflows, not as proof of profitability;
- exclude `.env` from the repository tree because it is local and secret-bearing.

## Accuracy and Safety Rules

- Treat `quantconclave/runtime_manifest.py`, `pyproject.toml`, `.env.example`, and executable entry points as sources of truth.
- Preserve the one-release `capitalradar` compatibility note without presenting the legacy name as the product brand.
- Do not add CI, coverage, performance, or release badges unless a corresponding public source exists.
- Do not add screenshots because the repository currently contains no maintained screenshot assets.
- Keep the existing TradingAgents attribution and repository license link.

## Acceptance Criteria

- Every documented command and import resolves against the current repository layout.
- No stale `8007` port or active-Backtrader claim remains in either README.
- The English README links to Chinese and the Chinese README links to English.
- References to the analysis pipeline match the Capability/Role/Node catalog terminology.
- Markdown links are valid, fenced examples are syntactically coherent, and both files contain a prominent non-investment-advice disclaimer.
