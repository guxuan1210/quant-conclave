# Trend Retracement Detection — Design Spec

**Date**: 2026-06-02
**Status**: Approved
**Scope**: Add daily-swing (3-15 day) retracement/rebound identification to the CapitalRadar analysis pipeline, with multi-agent cross-validation and a scoring system.

---

## 1. Goals

1. **Detect two types of retracement signals** on the daily timeframe:
   - **Rebound opportunity** (反弹机会): After a downtrend, price bounces off oversold levels — potential reversal entry.
   - **Pullback buy** (回调买入): Within an uptrend, price retraces to moving-average support — trend-continuation entry.

2. **Multi-agent cross-validation**: Technical signals must be confirmed by capital flow, sentiment, news, fundamentals, competitor, and supply-chain dimensions. No single dimension drives the signal.

3. **Structured scoring**: 0-14 point composite score across 7 dimensions, with clear signal strength tiers.

4. **Visible in reports and UI**: Each analyst adds retracement-related analysis to their report; a summary card appears in the History detail view.

---

## 2. Detection Methodology

### 2.1 Rebound Opportunity (下跌反弹)

Price has declined meaningfully and shows signs of bottoming:

| Condition | Threshold |
|-----------|-----------|
| Price decline from recent high | ≥ 5% within 30 trading days |
| Decline duration | ≥ 3 consecutive days of lower closes |
| RSI(14) | Previously < 30 (oversold), now rising |
| MACD | Golden cross below zero line, or bullish divergence |
| Bollinger Bands | Price touched or breached lower band, now bouncing |
| Volume pattern | Declining volume during fall + increasing volume on bounce |

### 2.2 Pullback Buy (上涨回调)

Price is in an uptrend and retraces to support:

| Condition | Threshold |
|-----------|-----------|
| Price rise from recent low | ≥ 5% within 30 trading days |
| Retracement depth | 5%-15% from peak |
| Retracement duration | 3-15 trading days |
| MA support | Price retraces to 20 SMA or 50 SMA without breaking below |
| RSI(14) | Retreated from > 70 to 40-50 neutral zone |
| MACD | Above zero line; histogram contraction without bearish cross |
| Volume pattern | Rising volume during advance + declining volume during retracement |

### 2.3 Configurable Parameters

Stored in `default_config.py` under a `retracement` key:

```python
"retracement": {
    "lookback_days": 30,
    "min_decline_pct": 5.0,       # minimum decline to qualify as retracement
    "max_retrace_pct": 15.0,      # max retracement depth (beyond this = trend break)
    "min_duration_days": 3,
    "max_duration_days": 15,
    "volume_shrink_ratio": 0.7,   # retracement volume / prior trend volume
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "rsi_neutral_low": 40,
    "rsi_neutral_high": 50,
    "support_ma_periods": [20, 50],
}
```

---

## 3. Multi-Agent Integration

### 3.1 Market Analyst (Primary detector)

**New prompt sections added to existing system message:**

```
=== RETRACEMENT / PULLBACK DETECTION (REQUIRED) ===

After your standard technical analysis, you MUST perform a retracement scan.
Use get_stock_data to get daily OHLCV, then get_indicators for RSI, MACD,
Bollinger Bands, and moving averages.

Check for TWO patterns:

1. REBOUND OPPORTUNITY (下跌反弹):
   - Has price declined ≥5% from its 30-day high?
   - Is RSI(14) recovering from below 30?
   - Is MACD showing a golden cross or bullish divergence?
   - Has price touched the lower Bollinger Band and bounced?
   - Volume: did selling volume shrink, then bounce volume expand?

2. PULLBACK BUY (上涨回调):
   - Has price risen ≥5% from its 30-day low, then retraced 5-15%?
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
**Key Support Level**: ¥X.XX (20 SMA / 50 SMA)
**Technical Score**: [0-2]
---
```

### 3.2 Capital Flow Analyst (Validator)

**New prompt section:**

```
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
```

### 3.3 Sentiment Analyst

The sentiment analyst already pre-fetches data. Add to prompt:

```
### Retracement Sentiment Check
- Rebound scenario: is retail sentiment PANICKED (≥70% bearish)? Panic at lows = contrarian buy signal.
- Pullback scenario: has retail euphoria cooled to neutral? Cooling from greed = healthy.
Add to report:
---
## Retracement Sentiment Check
**Retail Mood**: [PANIC / FEAR / NEUTRAL / GREED / EUPHORIA]
**Sentiment-Flow Alignment**: [ALIGNED / DIVERGENT]
**Sentiment Score**: [0-2]
---
```

### 3.4 News Analyst

Add to prompt:

```
### Retracement Catalyst Check
- Did the retracement have a concrete negative catalyst, or is it purely technical?
- Technical pullback without negative news = healthy, buyable dip.
- Retracement driven by material bad news (earnings miss, regulatory action) = avoid.
Add to report:
---
## Retracement Catalyst Check
**Catalyst Type**: [TECHNICAL / NEWS-DRIVEN / MIXED]
**News Severity**: [NONE / MILD / MATERIAL]
**News Score**: [0-2]
---
```

### 3.5 Fundamentals Analyst

Add to prompt:

```
### Retracement Valuation Anchor
- After the retracement, is PE/PB below historical median? Below = value support.
- Is ROE stable? Declining ROE + price dip = value trap, not opportunity.
Add to report:
---
## Retracement Valuation Anchor
**Valuation Zone**: [UNDERVALUED / FAIR / OVERVALUED]
**Fundamentals Score**: [0-2]
---
```

### 3.6 Competitor Analyst

Add to prompt:

```
### Sector Retracement Context
- Is the whole sector pulling back, or just this stock?
- Sector-wide retracement = macro/rotation driven, higher confidence.
- Individual stock weakness while sector is strong = company-specific problem.
Add to report:
---
## Sector Retracement Context
**Sector Participation**: [BROAD / SELECTIVE / ISOLATED]
**Competitor Score**: [0-2]
---
```

### 3.7 Partner Analyst

Add to prompt:

```
### Supply Chain Retracement Context
- Upstream cost increases or downstream demand weakness may explain the retracement.
- Structural supply-chain issue = don't buy the dip.
Add to report:
---
## Supply Chain Retracement Context
**Chain Health**: [STABLE / MIXED / DETERIORATING]
**Partner Score**: [0-2]
---
```

---

## 4. Scoring System

| Dimension | Analyst | 2 (Strong) | 1 (Weak) | 0 (None) |
|-----------|---------|-----------|----------|----------|
| Technical | Market | Depth 5-15%, RSI recovery, MACD cross | Partial match | Structure broken |
| Capital Flow | Capital Flow | Net inflow + northbound buying | Neutral | Net outflow |
| Sentiment | Sentiment | Retail panic/fear | Neutral | Euphoria/greed |
| News | News | No bad news / positive catalyst | Neutral | Material negative |
| Fundamentals | Fundamentals | Undervalued, ROE stable | Fair value | Overvalued / declining |
| Competitor | Competitor | Broad sector pullback | Selective | Isolated weakness |
| Partner | Partner | Supply chain stable | Mixed | Deteriorating |

**Tiers:**
- **≥10**: Strong retracement signal — high confidence
- **6-9**: Moderate signal — needs human judgment
- **<6**: Weak signal — not recommended

---

## 5. UI Output

### 5.1 Analyst Reports

Each analyst adds their retracement section (shown above) to their existing report. The regex-based `_extract_key_metrics()` in `stream.py` gains new patterns to extract retracement scores.

### 5.2 Retracement Summary Card

In `showHistoryDetail()`, a new card appears alongside the existing key metrics cards:

```
┌─────────────────────────────────────────┐
│ 🔄 Retracement Signal                   │
│ Type: PULLBACK BUY                      │
│ Total Score: 11/14 — STRONG             │
│ ┌──────────┬──────────┬──────────┐      │
│ │Technical │Cap.Flow  │Sentiment │      │
│ │  2/2 🟢  │  2/2 🟢  │  1/2 🟡  │      │
│ ├──────────┼──────────┼──────────┤      │
│ │News      │Fundam.   │Competitor│      │
│ │  2/2 🟢  │  1/2 🟡  │  1/2 🟡  │      │
│ ├──────────┼──────────┼──────────┤      │
│ │Partner   │          │          │      │
│ │  2/2 🟢  │          │          │      │
│ └──────────┴──────────┴──────────┘      │
│ Suggestion: Strong buy-the-dip signal.  │
│ Price retracing to 20 SMA with inflow.  │
└─────────────────────────────────────────┘
```

### 5.3 Key Metrics Extraction

New regex patterns in `_extract_key_metrics()` for the `market` analyst:

```python
"signal_type": r"\*\*Signal Detected\*\*[:\s]*\[?(REBOUND|PULLBACK|NONE)\]?",
"retracement_depth": r"\*\*Retracement Depth\*\*[:\s]*([\d.]+%)",
"retracement_duration": r"\*\*Duration\*\*[:\s]*(\d+)\s*days",
"technical_score": r"\*\*Technical Score\*\*[:\s]*\[?([012])\]?",
```

And per-analyst score patterns for aggregation.

---

## 6. Files Changed

| File | Change |
|------|--------|
| `capitalradar/default_config.py` | Add `retracement` config block |
| `capitalradar/agents/analysts/market_analyst.py` | Add retracement detection prompt + scoring output |
| `capitalradar/agents/analysts/capital_flow_analyst.py` | Add retracement capital validation prompt |
| `capitalradar/agents/analysts/sentiment_analyst.py` | Add retracement sentiment check prompt |
| `capitalradar/agents/analysts/news_analyst.py` | Add retracement catalyst check prompt |
| `capitalradar/agents/analysts/fundamentals_analyst.py` | Add retracement valuation anchor prompt |
| `capitalradar/agents/analysts/competitor_analyst.py` | Add sector retracement context prompt |
| `capitalradar/agents/analysts/partner_analyst.py` | Add supply chain retracement context prompt |
| `web/stream.py` | Add retracement score extraction in `_extract_key_metrics()` |
| `web/static/app.js` | Add retracement summary card rendering |
| `web/static/style.css` | Add retracement card styles |

---

## 7. What's NOT in Scope

- Intraday (sub-daily) retracement detection — daily swing only
- Fibonacci retracement levels — no dedicated tool; MA-based support/resistance used instead
- Automated trading execution — signals are advisory only
- Backtesting the retracement signals against historical data

---

## 8. Edge Cases

| Scenario | Behavior |
|----------|----------|
| No retracement detected | Market analyst reports "NONE", score card shows "No active retracement signal" |
| Retracement exceeds 15% | Flag as potential trend break, not a normal pullback — score 0 for technical |
| Low-volume stock (daily vol < ¥10M) | Flag data quality warning, scores may be less reliable |
| Missing analyst (e.g., no competitor configured) | That dimension scores 0, total is out of remaining dimensions |

---

## 9. Testing Strategy

- **Unit**: Test retracement detection logic with known price patterns (mock OHLCV data)
- **Integration**: Test each analyst's retracement section appears when signal is present
- **Manual**: Run analysis on a stock in a known pullback (e.g., after a 10% dip), verify all 7 dimensions populate
