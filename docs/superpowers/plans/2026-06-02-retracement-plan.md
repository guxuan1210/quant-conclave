# Trend Retracement Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add daily-swing (3-15 day) retracement/rebound identification to CapitalRadar with 7-analyst cross-validation, 0-14 composite scoring, and a UI summary card.

**Architecture:** Each analyst gets a retracement-specific prompt section appended to their existing system message. The Market analyst is the primary detector; the other 6 validate. Scores (0-2 per dimension) are embedded in structured `---` delimited report sections and extracted via regex in `stream.py`. The frontend renders a `Retracement Signal` card from `key_metrics.retracement` in `showHistoryDetail()`.

**Tech Stack:** Python (LangGraph prompts, regex extraction), vanilla JS (DOM rendering), CSS (card styles)

---

### Task 1: Add retracement config block to default_config.py

**Files:**
- Modify: `capitalradar/default_config.py:76-84`

- [ ] **Step 1: Add the retracement config dict**

Insert after the `"output_language"` line (line 76), before the `"max_debate_rounds"` line (line 78):

```python
    "retracement": {
        "lookback_days": 30,
        "min_decline_pct": 5.0,
        "max_retrace_pct": 15.0,
        "min_duration_days": 3,
        "max_duration_days": 15,
        "volume_shrink_ratio": 0.7,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "rsi_neutral_low": 40,
        "rsi_neutral_high": 50,
        "support_ma_periods": [20, 50],
    },
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.default_config import DEFAULT_CONFIG; print(DEFAULT_CONFIG['retracement'])"
```

Expected: prints the retracement dict.

- [ ] **Step 3: Commit**

```bash
git add capitalradar/default_config.py
git commit -m "feat: add retracement config block with 11 tunable parameters

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Add retracement detection prompt to Market Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/market_analyst.py:73-93`

- [ ] **Step 1: Append retracement detection section to system_message**

The retracement section goes after the closing `"""` of the existing major-fund-movement section (after the `---\n"""` on line 92) and before `+ get_language_instruction()` on line 93. Replace lines 91-93:

Old (lines 91-93):
```python
---
"""
            + get_language_instruction()
```

New:
```python
---

=== RETRACEMENT / PULLBACK DETECTION (REQUIRED) ===

After your standard technical analysis, you MUST perform a retracement scan.
Use get_stock_data to get daily OHLCV, then get_indicators for RSI, MACD,
Bollinger Bands, and moving averages.

Check for TWO patterns:

1. REBOUND OPPORTUNITY (下跌反弹):
   - Has price declined >=5% from its 30-day high?
   - Is RSI(14) recovering from below 30?
   - Is MACD showing a golden cross or bullish divergence?
   - Has price touched the lower Bollinger Band and bounced?
   - Volume: did selling volume shrink, then bounce volume expand?

2. PULLBACK BUY (上涨回调):
   - Has price risen >=5% from its 30-day low, then retraced 5-15%?
   - Is price holding above 20 SMA or 50 SMA?
   - Has RSI cooled from overbought to 40-50 neutral zone?
   - Is MACD still above zero with histogram contraction?
   - Volume: did advance volume expand, retracement volume shrink?

In your report, add a section:
---
## Retracement Analysis
**Signal Detected**: [REBOUND / PULLBACK / NONE]
**Retracement Depth**: X.X%
**Duration**: X days
**Volume Pattern**: [HEALTHY / SUSPICIOUS / NEUTRAL]
**Key Support Level**: (20 SMA / 50 SMA value)
**Technical Score**: [0-2]
---
"""
            + get_language_instruction()
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.market_analyst import create_market_analyst; print('OK')"
```

Expected: "OK" (no import errors).

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/market_analyst.py
git commit -m "feat: add retracement/pullback detection prompt to market analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Add retracement capital flow validation prompt to Capital Flow Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/capital_flow_analyst.py:133-136`

- [ ] **Step 1: Append retracement validation section**

Insert before the `---\n"""` closing on line 134, after the "Recommended Action" line. Replace lines 133-136:

Old:
```python
**Recommended Action**: [How traders should interpret capital flow data to avoid being trapped]
---
"""
            + get_language_instruction(),
```

New:
```python
**Recommended Action**: [How traders should interpret capital flow data to avoid being trapped]

=== RETRACEMENT CAPITAL FLOW VALIDATION ===

Cross-reference your capital flow findings against any retracement signal:

- If a REBOUND signal exists: is major capital ACCUMULATING (net inflow)?
  Rebound + inflow = GENUINE bottom. Rebound + outflow = dead cat bounce (诱多).

- If a PULLBACK signal exists: is major capital HOLDING or ADDING (not distributing)?
  Pullback + stable/increasing institutional position = GENUINE consolidation.
  Pullback + net outflow = distribution in progress (出货).

Add to your report:
---
## Retracement Capital Validation
**Capital Direction During Retracement**: [INFLOW / OUTFLOW / NEUTRAL]
**Northbound Stance**: [BUYING / SELLING / NEUTRAL]
**Margin Position Change**: [ADDING / REDUCING / STABLE]
**Retracement Authenticity**: [GENUINE / MANUFACTURED / UNCERTAIN]
**Capital Flow Score**: [0-2]
---
"""
            + get_language_instruction(),
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.capital_flow_analyst import create_capital_flow_analyst; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/capital_flow_analyst.py
git commit -m "feat: add retracement capital flow validation to capital flow analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Add retracement sentiment check prompt to Sentiment Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/sentiment_analyst.py:218-220`

The sentiment analyst uses `_build_system_message()` which returns an f-string. The retracement section goes before the closing `{get_language_instruction()}` in the returned string.

- [ ] **Step 1: Add retracement sentiment check section**

In `_build_system_message()`, replace line 220:

Old:
```python

{get_language_instruction()}"""
```

New:
```python

### Retracement Sentiment Check
- Rebound scenario: is retail sentiment PANICKED (>=70% bearish)? Panic at lows = contrarian buy signal.
- Pullback scenario: has retail euphoria cooled to neutral? Cooling from greed = healthy.
Add to your report:
---
## Retracement Sentiment Check
**Retail Mood During Retracement**: [PANIC / FEAR / NEUTRAL / GREED / EUPHORIA]
**Sentiment-Flow Alignment**: [ALIGNED / DIVERGENT]
**Sentiment Score**: [0-2]
---

{get_language_instruction()}"""
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.sentiment_analyst import create_sentiment_analyst; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/sentiment_analyst.py
git commit -m "feat: add retracement sentiment check prompt to sentiment analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Add retracement catalyst check prompt to News Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/news_analyst.py:64-73`

- [ ] **Step 1: Append retracement catalyst section before the closing `---\n"""`**

Replace lines 70-73:

Old:
```python
**Retail Investor Action**: [Should retail follow the news narrative or counter it?]
---
"""
            + get_language_instruction()
```

New:
```python
**Retail Investor Action**: [Should retail follow the news narrative or counter it?]

=== RETRACEMENT CATALYST CHECK ===
- Did the retracement have a concrete negative catalyst, or is it purely technical?
- Technical pullback without negative news = healthy, buyable dip.
- Retracement driven by material bad news (earnings miss, regulatory action) = avoid.
Add to your report:
---
## Retracement Catalyst Check
**Catalyst Type**: [TECHNICAL / NEWS-DRIVEN / MIXED]
**News Severity**: [NONE / MILD / MATERIAL]
**News Score**: [0-2]
---
"""
            + get_language_instruction()
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.news_analyst import create_news_analyst; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/news_analyst.py
git commit -m "feat: add retracement catalyst check prompt to news analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Add retracement valuation anchor prompt to Fundamentals Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/fundamentals_analyst.py:65-73`

- [ ] **Step 1: Append retracement valuation section**

Replace lines 71-73:

Old:
```python
**Retail Investor Action**: [Should retail trust the fundamental narrative or counter it?]
---
"""
            + get_language_instruction(),
```

New:
```python
**Retail Investor Action**: [Should retail trust the fundamental narrative or counter it?]

=== RETRACEMENT VALUATION ANCHOR ===
- After the retracement, is PE/PB below historical median? Below = value support.
- Is ROE stable? Declining ROE + price dip = value trap, not opportunity.
Add to your report:
---
## Retracement Valuation Anchor
**Valuation Zone**: [UNDERVALUED / FAIR / OVERVALUED]
**ROE Stability**: [STABLE / DECLINING / IMPROVING]
**Fundamentals Score**: [0-2]
---
"""
            + get_language_instruction(),
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.fundamentals_analyst import create_fundamentals_analyst; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/fundamentals_analyst.py
git commit -m "feat: add retracement valuation anchor prompt to fundamentals analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Add sector retracement context prompt to Competitor Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/competitor_analyst.py:63-69`

The competitor prompt is built in `_build_competitor_prompt()`. The retracement section goes before the closing `---\n"` on line 68.

- [ ] **Step 1: Append sector retracement context section**

Replace lines 66-69:

Old:
```python
        "**Retail Investor Action**: [Should retail follow or counter the competitor narrative? Specific advice]\n"
        "---\n"
        + " Make sure to append a Markdown table at the end summarizing key competitor events and their impact on target's capital flows."
        + get_language_instruction()
```

New:
```python
        "**Retail Investor Action**: [Should retail follow or counter the competitor narrative? Specific advice]\n"
        "\n"
        "=== SECTOR RETRACEMENT CONTEXT ===\n"
        "- Is the whole sector pulling back, or just this stock?\n"
        "- Sector-wide retracement = macro/rotation driven, higher confidence.\n"
        "- Individual stock weakness while sector is strong = company-specific problem.\n"
        "Add to your report:\n"
        "---\n"
        "## Sector Retracement Context\n"
        "**Sector Participation**: [BROAD / SELECTIVE / ISOLATED]\n"
        "**Competitor Score**: [0-2]\n"
        "---\n"
        + " Make sure to append a Markdown table at the end summarizing key competitor events and their impact on target's capital flows."
        + get_language_instruction()
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.competitor_analyst import create_competitor_analyst; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/competitor_analyst.py
git commit -m "feat: add sector retracement context prompt to competitor analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: Add supply chain retracement context prompt to Partner Analyst

**Files:**
- Modify: `capitalradar/agents/analysts/partner_analyst.py:70-74`

The partner prompt is built in `_build_partner_prompt()`. Same pattern as competitor.

- [ ] **Step 1: Append supply chain retracement context**

Replace lines 70-74:

Old:
```python
        "**Retail Investor Action**: [Should retail follow or counter the supply chain narrative? Specific advice]\n"
        "---\n"
        + " Make sure to append a Markdown table at the end summarizing key supply chain events and their impact on target's capital flows."
        + get_language_instruction()
```

New:
```python
        "**Retail Investor Action**: [Should retail follow or counter the supply chain narrative? Specific advice]\n"
        "\n"
        "=== SUPPLY CHAIN RETRACEMENT CONTEXT ===\n"
        "- Upstream cost increases or downstream demand weakness may explain the retracement.\n"
        "- Structural supply-chain issue = don't buy the dip.\n"
        "Add to your report:\n"
        "---\n"
        "## Supply Chain Retracement Context\n"
        "**Chain Health**: [STABLE / MIXED / DETERIORATING]\n"
        "**Partner Score**: [0-2]\n"
        "---\n"
        + " Make sure to append a Markdown table at the end summarizing key supply chain events and their impact on target's capital flows."
        + get_language_instruction()
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from capitalradar.agents.analysts.partner_analyst import create_partner_analyst; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add capitalradar/agents/analysts/partner_analyst.py
git commit -m "feat: add supply chain retracement context prompt to partner analyst

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: Add retracement key metrics extraction to stream.py

**Files:**
- Modify: `web/stream.py:695-749`

The `_extract_key_metrics()` function uses a `patterns_map` dict keyed by analyst. Each key has a list of `(regex, metric_name)` tuples. We add retracement extraction patterns to the existing keys.

- [ ] **Step 1: Add "retracement" entry to patterns_map**

After the `"partner"` block closing `],` on line 739, add a new `"retracement"` key with a nested dict of per-analyst patterns:

```python
            "retracement": {
                "signal_type": [
                    (r'\*\*Signal Detected\*\*[:\s]*\[?(REBOUND|PULLBACK|NONE)\]?', "signal_type"),
                ],
                "technical_score": [
                    (r'\*\*Technical Score\*\*[:\s]*\[?([012])\]?', "technical"),
                    (r'\*\*Capital Flow Score\*\*[:\s]*\[?([012])\]?', "capital_flow"),
                    (r'\*\*Sentiment Score\*\*[:\s]*\[?([012])\]?', "sentiment"),
                    (r'\*\*News Score\*\*[:\s]*\[?([012])\]?', "news"),
                    (r'\*\*Fundamentals Score\*\*[:\s]*\[?([012])\]?', "fundamentals"),
                    (r'\*\*Competitor Score\*\*[:\s]*\[?([012])\]?', "competitor"),
                    (r'\*\*Partner Score\*\*[:\s]*\[?([012])\]?', "partner"),
                ],
            },
```

Wait — this won't work with the current flat structure. The current code iterates `analyst_key` then `pattern, key` pairs. We need to integrate retracement patterns differently.

Instead, add the retracement extraction as a **separate pass** after the existing per-analyst extraction loop. The retracement scores are scattered across 7 different analyst reports; we need to run each pattern against the **full concatenated text** of all reports.

After line 748 (`metrics[key] = value`), add a new retracement extraction block:

```python
        # --- Retracement signal extraction ---
        # Retracement scores are scattered across all 7 analyst reports.
        # Scan the full concatenated report text for each pattern.
        all_text = ""
        for report_key in [
            "market_report", "capital_flow_report", "sentiment_report",
            "news_report", "fundamentals_report", "competitor_report", "partner_report",
        ]:
            r = state.get(report_key, "")
            if r:
                all_text += str(r) + "\n"

        retracement_scores = {}
        retrace_patterns = [
            (r'\*\*Signal Detected\*\*[:\s]*\[?(REBOUND|PULLBACK|NONE)\]?', "signal_type"),
            (r'\*\*Retracement Depth\*\*[:\s]*([\d.]+%)', "retracement_depth"),
            (r'\*\*Duration\*\*[:\s]*(\d+)\s*days', "retracement_duration"),
            (r'\*\*Volume Pattern\*\*[:\s]*\[?(HEALTHY|SUSPICIOUS|NEUTRAL)\]?', "volume_pattern"),
            (r'\*\*Key Support Level\*\*[:\s]*(.+?)(?:\n|$)', "support_level"),
            (r'\*\*Technical Score\*\*[:\s]*\[?([012])\]?', "technical"),
            (r'\*\*Capital Flow Score\*\*[:\s]*\[?([012])\]?', "capital_flow"),
            (r'\*\*Sentiment Score\*\*[:\s]*\[?([012])\]?', "sentiment"),
            (r'\*\*News Score\*\*[:\s]*\[?([012])\]?', "news"),
            (r'\*\*Fundamentals Score\*\*[:\s]*\[?([012])\]?', "fundamentals"),
            (r'\*\*Competitor Score\*\*[:\s]*\[?([012])\]?', "competitor"),
            (r'\*\*Partner Score\*\*[:\s]*\[?([012])\]?', "partner"),
            (r'\*\*Retracement Authenticity\*\*[:\s]*\[?(GENUINE|MANUFACTURED|UNCERTAIN)\]?', "authenticity"),
            (r'\*\*Sector Participation\*\*[:\s]*\[?(BROAD|SELECTIVE|ISOLATED)\]?', "sector_participation"),
            (r'\*\*Chain Health\*\*[:\s]*\[?(STABLE|MIXED|DETERIORATING)\]?', "chain_health"),
            (r'\*\*Valuation Zone\*\*[:\s]*\[?(UNDERVALUED|FAIR|OVERVALUED)\]?', "valuation_zone"),
            (r'\*\*Catalyst Type\*\*[:\s]*\[?(TECHNICAL|NEWS-DRIVEN|MIXED)\]?', "catalyst_type"),
            (r'\*\*Retail Mood During Retracement\*\*[:\s]*\[?(PANIC|FEAR|NEUTRAL|GREED|EUPHORIA)\]?', "retail_mood"),
        ]
        for pattern, key in retrace_patterns:
            m = re.search(pattern, all_text, re.IGNORECASE)
            if m:
                value = m.group(1).strip()
                if value and value not in ("N/A", "n/a", "-", "None", "null"):
                    retracement_scores[key] = value

        # Compute total retracement score (sum of 7 dimension scores)
        score_keys = ["technical", "capital_flow", "sentiment", "news", "fundamentals", "competitor", "partner"]
        total = 0
        count = 0
        for sk in score_keys:
            if sk in retracement_scores:
                try:
                    total += int(retracement_scores[sk])
                    count += 1
                except ValueError:
                    pass
        retracement_scores["total_score"] = str(total) + "/" + str(count * 2 if count > 0 else 14)
        if total >= 10:
            retracement_scores["tier"] = "STRONG"
        elif total >= 6:
            retracement_scores["tier"] = "MODERATE"
        else:
            retracement_scores["tier"] = "WEAK"

        if retracement_scores:
            metrics["retracement"] = retracement_scores
```

- [ ] **Step 2: Verify syntax and logic**

```bash
python -c "from web.stream import _extract_key_metrics; print('OK')"
```

Note: if `_extract_key_metrics` is not directly importable (it may be a nested function), use:

```bash
python -c "import ast; ast.parse(open('web/stream.py').read()); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add web/stream.py
git commit -m "feat: add retracement score extraction to _extract_key_metrics

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10: Add retracement summary card rendering to app.js

**Files:**
- Modify: `web/static/app.js:1402-1407`

- [ ] **Step 1: Add retracement card rendering call in showHistoryDetail**

In `showHistoryDetail()`, after line 1407 (after the existing key metrics cards render), insert a retracement card render:

```javascript
      // Retracement signal card
      if (state && state.key_metrics && state.key_metrics.retracement) {
        html += '<div class="card-body" style="padding-top:0;">';
        html += renderRetracementCard(state.key_metrics.retracement);
        html += '</div>';
      }
```

- [ ] **Step 2: Write the renderRetracementCard function**

Add the function before `renderKeyMetricsCards` (around line 1594):

```javascript
function renderRetracementCard(retrace) {
  if (!retrace || !retrace.signal_type || retrace.signal_type === "NONE") {
    return '<div class="retracement-card" style="border-left-color:#888;"><div class="retracement-header">🔄 No Active Retracement Signal</div></div>';
  }

  var typeLabel = retrace.signal_type === "REBOUND" ? "REBOUND OPPORTUNITY (下跌反弹)" : "PULLBACK BUY (上涨回调)";
  var tierLabel = retrace.tier || "WEAK";
  var tierColor = tierLabel === "STRONG" ? "var(--green)" : tierLabel === "MODERATE" ? "#f0ad4e" : "var(--red)";

  var html = '<div class="retracement-card">';
  html += '<div class="retracement-header">🔄 Retracement Signal</div>';
  html += '<div class="retracement-type">Type: ' + esc(typeLabel) + '</div>';
  html += '<div class="retracement-total">Total Score: ' + esc(retrace.total_score || "0/14") + ' — <span style="color:' + tierColor + ';font-weight:700;">' + esc(tierLabel) + '</span></div>';

  // Score grid: 7 dimensions in 3 rows (3 + 3 + 1)
  var dims = [
    {key: "technical", label: "Technical"},
    {key: "capital_flow", label: "Cap.Flow"},
    {key: "sentiment", label: "Sentiment"},
    {key: "news", label: "News"},
    {key: "fundamentals", label: "Fundam."},
    {key: "competitor", label: "Competitor"},
    {key: "partner", label: "Partner"},
  ];

  html += '<div class="retracement-grid">';
  dims.forEach(function(d) {
    var score = retrace[d.key];
    var scoreNum = score ? parseInt(score, 10) : 0;
    var dotColor = scoreNum === 2 ? "var(--green)" : scoreNum === 1 ? "#f0ad4e" : "var(--red)";
    var dot = scoreNum === 2 ? "🟢" : scoreNum === 1 ? "🟡" : "🔴";
    html += '<div class="retracement-dim">';
    html += '<div class="retracement-dim-label">' + esc(d.label) + '</div>';
    html += '<div class="retracement-dim-score" style="color:' + dotColor + ';">' + dot + ' ' + esc(String(scoreNum)) + '/2</div>';
    html += '</div>';
  });
  html += '</div>';

  // Supporting details
  if (retrace.retracement_depth || retrace.retracement_duration || retrace.volume_pattern || retrace.support_level) {
    html += '<div class="retracement-details">';
    if (retrace.retracement_depth) html += '<span>Depth: ' + esc(retrace.retracement_depth) + '</span> ';
    if (retrace.retracement_duration) html += '<span>Duration: ' + esc(retrace.retracement_duration) + ' days</span> ';
    if (retrace.volume_pattern) html += '<span>Volume: ' + esc(retrace.volume_pattern) + '</span> ';
    if (retrace.support_level) html += '<span>Support: ' + esc(retrace.support_level) + '</span>';
    html += '</div>';
  }

  // Suggestion text based on tier
  var suggestion = "";
  if (tierLabel === "STRONG") {
    suggestion = "Strong buy-the-dip signal. Multiple dimensions confirm the retracement is a genuine opportunity.";
  } else if (tierLabel === "MODERATE") {
    suggestion = "Mixed signals — requires human judgment. Some dimensions confirm but others are neutral or conflicting.";
  } else {
    suggestion = "Weak or conflicting signals. Retracement may be a trap or trend break. Not recommended without further confirmation.";
  }
  html += '<div class="retracement-suggestion">' + esc(suggestion) + '</div>';

  html += '</div>';
  return html;
}
```

- [ ] **Step 3: Verify no JS syntax errors**

```bash
node --check web/static/app.js 2>&1 || echo "Node not available, check manually"
```

If node is not available, verify by reading the file and checking bracket matching visually.

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js
git commit -m "feat: add retracement summary card rendering to history detail view

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 11: Add retracement card CSS styles

**Files:**
- Modify: `web/static/style.css` (append after the `.metric-card` styles around line 1565)

- [ ] **Step 1: Append retracement card styles**

Add after the `.metric-card-body` block (after line ~1565):

```css

/* ---- Retracement Signal Card ---- */
.retracement-card {
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 12px;
  background: var(--bg);
}
.retracement-header {
  font-weight: 700;
  font-size: 14px;
  margin-bottom: 6px;
  color: var(--text);
}
.retracement-type {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 4px;
}
.retracement-total {
  font-size: 13px;
  color: var(--text);
  margin-bottom: 10px;
}
.retracement-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}
.retracement-dim {
  text-align: center;
  padding: 8px 4px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
}
.retracement-dim-label {
  font-size: 10px;
  color: var(--text-muted);
  margin-bottom: 4px;
}
.retracement-dim-score {
  font-size: 14px;
  font-weight: 700;
}
.retracement-details {
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}
.retracement-suggestion {
  font-size: 12px;
  color: var(--text);
  padding: 8px 10px;
  background: #f0f7ff;
  border-radius: 6px;
  line-height: 1.5;
}
```

- [ ] **Step 2: Verify CSS is valid**

```bash
python -c "print('CSS syntax check: manual review recommended')"
```

- [ ] **Step 3: Commit**

```bash
git add web/static/style.css
git commit -m "feat: add retracement summary card CSS styles

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - [x] Config block (Task 1)
   - [x] Market analyst — primary detector (Task 2)
   - [x] Capital flow analyst — validator (Task 3)
   - [x] Sentiment analyst (Task 4)
   - [x] News analyst (Task 5)
   - [x] Fundamentals analyst (Task 6)
   - [x] Competitor analyst (Task 7)
   - [x] Partner analyst (Task 8)
   - [x] Key metrics extraction (Task 9)
   - [x] UI summary card (Task 10)
   - [x] CSS styles (Task 11)

2. **No placeholders** — all steps contain concrete code

3. **Type consistency:**
   - `retracement` key in `metrics` dict matches between stream.py (Task 9) and app.js (Task 10)
   - Score keys (technical, capital_flow, sentiment, news, fundamentals, competitor, partner) match between extraction patterns and render function
   - `renderRetracementCard()` function name matches between call site and definition

4. **Not in scope (per spec section 7):**
   - No intraday retracement
   - No Fibonacci levels
   - No automated trading
   - No backtesting
