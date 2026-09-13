# QuantConclave README Internationalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a concise English GitHub landing page with a corrected, detailed Chinese companion README.

**Architecture:** `README.md` becomes the canonical international landing page and links to `README.zh-CN.md`. The Chinese file preserves deeper product documentation while both files derive commands, ports, package names, and compatibility statements from the current repository.

**Tech Stack:** GitHub-flavored Markdown, Python package metadata, FastAPI runtime entry points

---

### Task 1: Create the corrected Chinese companion

**Files:**
- Create: `README.zh-CN.md`
- Reference: `quantconclave/catalog.py`
- Reference: `quantconclave/runtime_manifest.py`

- [ ] **Step 1: Copy the existing Chinese content into `README.zh-CN.md` and add the language switch**

The first lines must be:

```markdown
[English](README.md) | **简体中文**

# 🛡️ QuantConclave — AI 多角色主力资金研究平台
```

- [ ] **Step 2: Correct terminology and implementation drift**

Replace the “8 个智能体” section with separate Capability and analysis-role tables. Describe Deep Analysis, AI Pick, Prediction, Strategy, Backtest, Advisory, History, and Calibration as capabilities; describe analysts, researchers, trader, risk debaters, and Portfolio Manager as roles/nodes. Replace active Backtrader claims with “vectorized backtest engine”, change the project-tree chart port to `8005`, and remove `.env` from the tracked project tree.

- [ ] **Step 3: Correct claims and compatibility language**

State that historical recommendations can be resolved and evaluated when sufficient market data is available. Do not state that every result is automatically backtested or that the system is profitable. Add a one-release compatibility note for the `capitalradar` CLI and `CAPITALRADAR_*` environment variables.

- [ ] **Step 4: Validate the Chinese document**

Run:

```powershell
rg -n "8007|backtrader 回测引擎|8 个 AI 智能体|每次分析的结果会被自动回测" README.zh-CN.md
```

Expected: no matches.

- [ ] **Step 5: Commit the Chinese companion**

```powershell
git add README.zh-CN.md
git commit -m "docs: add corrected Chinese README"
```

### Task 2: Replace the GitHub landing page with an English README

**Files:**
- Modify: `README.md`
- Reference: `pyproject.toml`
- Reference: `.env.example`

- [ ] **Step 1: Write the English header and positioning**

Open with the language switch, project name, a one-sentence description of an AI-assisted multi-role financial research system, and a prominent research-only disclaimer. Link the Chinese version with `[简体中文](README.zh-CN.md)`.

- [ ] **Step 2: Add the concise product narrative**

Use these sections in this order:

```markdown
## Why QuantConclave?
## Core Capabilities
## How the Research Pipeline Works
## Data and Reliability
## Human-Reviewed Learning Loop
## Quick Start
## Configuration
## Python API
## Project Layout
## Project Status and Limitations
## License
## Acknowledgements and Citation
```

Keep Capability, Role, and Node terminology distinct. Describe the vectorized engine and multi-provider fallback without asserting guaranteed data availability or investment returns.

- [ ] **Step 3: Add verified setup instructions**

Use the repository URL `https://github.com/guxuan1210/quant-conclave.git`, package import `quantconclave`, startup command `python run_web.py`, main UI port `8003`, and chart service port `8005`. List `DEEPSEEK_API_KEY` and `TUSHARE_TOKEN` as the primary example configuration and label optional integrations accurately.

- [ ] **Step 4: Document migration compatibility**

Explain that legacy `capitalradar` CLI/environment/data paths remain readable for one release and that new configuration should use `quantconclave` / `QUANTCONCLAVE_*` names.

- [ ] **Step 5: Commit the English landing page**

```powershell
git add README.md
git commit -m "docs: publish international QuantConclave README"
```

### Task 3: Verify both documents against the repository

**Files:**
- Verify: `README.md`
- Verify: `README.zh-CN.md`

- [ ] **Step 1: Verify stale names and claims are absent**

```powershell
rg -n "8007|from capitalradar|backtrader 回测引擎|backtrader backtest engine|github.com/.*/Capital" README.md README.zh-CN.md
```

Expected: no matches. Compatibility references to lowercase `capitalradar` are allowed only inside the migration note.

- [ ] **Step 2: Verify required facts are present**

```powershell
rg -n "README.zh-CN.md|8003|8005|quantconclave|Vector|vectorized|TradingAgents|LICENSE" README.md
rg -n "README.md|8003|8005|quantconclave|向量化|TradingAgents|LICENSE" README.zh-CN.md
```

Expected: every pattern has at least one relevant match.

- [ ] **Step 3: Verify documented Python imports**

```powershell
python -c "from quantconclave.graph.trading_graph import QuantConclaveGraph; from quantconclave.default_config import DEFAULT_CONFIG; print('README imports OK')"
```

Expected: `README imports OK` and exit code 0.

- [ ] **Step 4: Verify Markdown and repository cleanliness**

Run `git diff --check`, inspect both rendered Markdown files, and confirm `git status --short` contains only the intended README changes before the final commit.

- [ ] **Step 5: Commit any verification-only corrections**

If verification required corrections, commit only those files:

```powershell
git add README.md README.zh-CN.md
git commit -m "docs: correct README verification findings"
```
