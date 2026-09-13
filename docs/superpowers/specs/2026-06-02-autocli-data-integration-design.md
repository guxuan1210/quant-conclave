# Autocli Data Integration — Design Spec

**Date**: 2026-06-02
**Status**: Approved
**Scope**: Integrate 3 new A-share data sources (Xueqiu, Eastmoney Guba, Sina Dragon-Tiger List) into CapitalRadar via autocli Chrome-session scraping, with public-API-first fallback chains. Enhance Sentiment Analyst and Capital Flow Analyst with richer CN-market data.

---

## 1. Goals

1. **Add 3 new A-share data sources** that existing vendors (yfinance, tushare, eastmoney news) don't cover:
   - Xueqiu (雪球) — professional/semi-professional investor community sentiment
   - Eastmoney Guba (东方财富股吧) — retail investor forum sentiment per stock
   - Sina Dragon-Tiger List (新浪龙虎榜) — institutional trading desk disclosure data

2. **Hybrid access pattern**: Public HTTP API first, autocli Chrome-session scraping as fallback. Graceful degradation — failures never block the analysis pipeline.

3. **Minimal analyst changes**: Sentiment Analyst pre-fetches Xueqiu + Guba blocks (following existing pattern for News/StockTwits/Reddit). Capital Flow Analyst gains `get_dragon_tiger_list` tool.

4. **Follow existing vendor patterns**: Each module exports functions compatible with `interface.py`'s `VENDOR_METHODS` routing.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   New autocli Data Layer                  │
├───────────────────┬──────────────────┬───────────────────┤
│ xueqiu_sentiment  │ eastmoney_guba   │ sina_dragon_tiger  │
│ .py               │ .py              │ .py                │
├───────────────────┼──────────────────┼───────────────────┤
│ 帖子情绪+组合追踪  │ 股吧论坛情绪       │ 龙虎榜机构席位       │
├───────────────────┴──────────────────┴───────────────────┤
│                   autocli_utils.py                        │
│         fetch_via_chrome() — Chrome CDP scraper           │
└──────────────────────────────────────────────────────────┘
         ↓                      ↓
┌──────────────────────────────────────────────────────────┐
│              interface.py VENDOR_METHODS                  │
│  get_xueqiu_sentiment / get_guba_sentiment /              │
│  get_dragon_tiger_list                                    │
└──────────────────────────────────────────────────────────┘
         ↓
┌──────────────────────────────────────────────────────────┐
│              Analysts (existing, modified)                │
│  Sentiment Analyst ← pre-fetches xueqiu + guba           │
│  Capital Flow Analyst ← get_dragon_tiger_list tool        │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Data Modules

### 3.1 `autocli_utils.py` — Shared Chrome Scraper

```python
def fetch_via_chrome(url: str, selectors_map: dict) -> dict:
    """Scrape page data via Chrome DevTools Protocol.

    Args:
        url: Target page URL.
        selectors_map: {"field_name": "css_selector"} mapping.

    Returns:
        dict with extracted field values. Empty string for failed selectors.
    """

def is_chrome_available(debug_port: int = 9222) -> bool:
    """Check if Chrome is running with remote debugging enabled."""

def safe_autocli_fetch(fetch_fn, *args, **kwargs) -> str:
    """Wrap autocli calls: never raise, return '<unavailable>' on failure."""
```

### 3.2 `xueqiu_sentiment.py` — Xueqiu Sentiment

**Public API layer:**
- `xueqiu.com/stock/search.json?code={symbol}` → get internal stock_id
- `xueqiu.com/statuses/search.json?symbol={stock_id}&page=1` → get post stream

Each post carries: text content, user verification badge, bullish/bearish label.

**Autocli fallback layer:**
- Chrome visits `xueqiu.com/S/{symbol}` stock page
- Scrapes: top 20 hot posts, follower count, trending badge

**Function:**
```python
def get_xueqiu_sentiment(ticker: str) -> str:
    """Return formatted markdown with Xueqiu sentiment data."""
```

**Output format:**
```markdown
## 雪球情绪数据 — {ticker}
**讨论热度**: [HIGH / MEDIUM / LOW]（帖子数: X, 关注者: Y）
**多空比**: X% 看多 / Y% 看空
### 热门讨论
1. **@user** [V认证]: 帖子内容摘要... [Bullish] [赞:N]
...
### 关注组合异动
- @知名用户 新增持仓/减仓 {ticker}...
```

### 3.3 `eastmoney_guba.py` — Guba Forum Sentiment

**Public API layer:**
- Eastmoney push API: `push2.eastmoney.com/api/qt/clist/get` with guba-specific parameters
- Guba HTML page parsing as fallback: `guba.eastmoney.com/list,{code},f_{page}.html`

**Autocli fallback layer:**
- Chrome visits `guba.eastmoney.com/list,{code}.html`
- Scrapes: post titles, view counts, comment counts, timestamps

**Function:**
```python
def get_guba_sentiment(ticker: str) -> str:
    """Return formatted markdown with Guba forum sentiment."""
```

**Output format:**
```markdown
## 股吧情绪 — {ticker}
**帖子量**: X 帖/日（正常 / 异常放量）
**情绪倾向**: 看多 X% / 看空 Y% / 中性 Z%
**热门关键词**: [涨], [跌], [利好], [利空], [主力], [韭菜]
### 热帖
1. 标题... | 阅读:N | 评论:N | 时间
...
```

### 3.4 `sina_dragon_tiger.py` — Dragon-Tiger List

**Public API layer:**
- Sina finance LHB JSON endpoint: `vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml`

**Autocli fallback layer:**
- Chrome visits Sina LHB page
- Scrapes: list date, trading desk names, buy/sell amounts, institution flags

**Function:**
```python
def get_dragon_tiger_list(ticker: str, trade_date: str) -> str:
    """Return formatted markdown with Dragon-Tiger List data for the most recent listing."""
```

**Output format:**
```markdown
## 龙虎榜数据 — {ticker}（最近上榜日: YYYY-MM-DD）
| 席位名称 | 类型 | 买入(万) | 卖出(万) | 净额(万) |
|----------|------|----------|----------|----------|
| 机构专用 | 机构 | 5,000 | 0 | +5,000 |
| 某游资席位 | 游资 | 2,000 | 3,500 | -1,500 |
**机构净流入**: +X 万 | **游资净流入**: -Y 万
**信号**: 机构买入主导 → 中长期看好 / 游资主导 → 短线波动
```

---

## 4. Analyst Integration

### 4.1 Sentiment Analyst

Pre-fetch pattern (matching existing News/StockTwits/Reddit):

```python
# In sentiment_analyst_node(), add to pre-fetch block:
xueqiu_block = _safe_fetch_xueqiu(ticker)
guba_block = _safe_fetch_guba(ticker)

# Pass to _build_system_message():
system_message = _build_system_message(
    ...
    xueqiu_block=xueqiu_block,
    guba_block=guba_block,
)
```

New data sources added to the prompt:

```markdown
### 雪球社区 — A股投资者讨论（预抓取）
中国最大的投资者社区，用户偏专业/半专业，每条帖子可含多空标记、用户认证级别。
<start_of_xueqiu>
{xueqiu_block}
<end_of_xueqiu>

### 东方财富股吧 — 散户论坛（预抓取）
每只A股专属论坛，散户情绪最直接的窗口。
<start_of_guba>
{guba_block}
<end_of_guba>
```

New best practice #10:

```
10. 雪球与股吧互为对照：雪球用户偏专业/半专业，股吧用户偏散户
    - 雪球看多 + 股吧恐慌 = 机构在雪球带节奏，散户在股吧割肉 → 吸筹信号
    - 两者一致极端看多 = 情绪过热 → 减仓信号
    - 雪球讨论量异动（突然大幅增加）= 有事件催化，需结合新闻源验证
```

### 4.2 Capital Flow Analyst

New tool `get_dragon_tiger_list` added to tools list:

```python
tools = [
    get_money_flow, get_hsgt_flow, get_market_flow,
    get_margin_trading, get_institutional_holders, get_major_holders,
    get_analyst_recommendations, get_insider_transactions,
    get_indicators, get_intraday_data, get_realtime_quote,
    get_dragon_tiger_list,  # NEW
]
```

Prompt addition in the "SUPPORTING DIMENSIONS" section:

```
10. 龙虎榜（Dragon-Tiger List）:
    - Use `get_dragon_tiger_list` to check if {ticker} appeared on the list recently.
    - 机构专用席位 (Institutional Desk) net buying = strong bullish confirmation.
    - 游资席位 (Hot-money Desk) net buying without institutional participation = short-term speculation, not accumulation.
    - Cross-reference: institutional desk buying + money flow net inflow = dual confirmation (高置信度).
    - Cross-reference: institutional desk selling + money flow net inflow = divergence (分歧) — the inflow may be retail or hot-money driven.
    - Cross-reference: institutional desk selling + money flow net outflow = dual bearish confirmation.
```

### 4.3 Other Analysts

No direct changes. Their existing cross-reference against the Capital Flow and Sentiment reports will pick up the new data indirectly.

---

## 5. Configuration

### 5.1 `default_config.py` additions

Add to `data_vendors`:
```python
"social_sentiment_data": "xueqiu,eastmoney_guba",
```

Modify `capital_flow_data`:
```python
"capital_flow_data": "tushare,sina",
```

### 5.2 New `autocli` config block

```python
"autocli": {
    "enabled": True,
    "chrome_debug_port": 9222,
    "request_delay_ms": 1500,
    "max_retries": 2,
    "timeout_seconds": 20,
    "xueqiu_max_posts": 20,
    "guba_max_posts": 20,
    "dragon_tiger_recent_days": 10,
},
```

### 5.3 New tools registered in VENDOR_METHODS

In `interface.py`:

```python
"get_xueqiu_sentiment": {
    "xueqiu": get_xueqiu_sentiment,
    "eastmoney_guba": lambda ticker: (
        "# SKIP_VENDOR: Xueqiu sentiment not available via Guba vendor"
    ),
},
"get_guba_sentiment": {
    "eastmoney_guba": get_guba_sentiment,
    "xueqiu": lambda ticker: (
        "# SKIP_VENDOR: Guba sentiment not available via Xueqiu vendor"
    ),
},
"get_dragon_tiger_list": {
    "sina": get_dragon_tiger_list,
    "tushare": lambda ticker, trade_date: (
        "# SKIP_VENDOR: Dragon-Tiger List not available via tushare"
    ),
},
```

### 5.4 TOOLS_CATEGORIES additions

```python
"social_sentiment_data": {
    "description": "Retail investor social sentiment from CN platforms",
    "tools": [
        "get_xueqiu_sentiment",
        "get_guba_sentiment",
    ]
},
```

---

## 6. Error Handling & Degradation

Each module follows a 2-tier fallback:

```
Tier 1: Public HTTP API (urllib/requests)
    ↓ Timeout / Rate-limit / JSON structure change
Tier 2: Autocli Chrome scraping (via autocli_utils.py)
    ↓ Chrome not running / Login expired
Return "<unavailable>" placeholder — NEVER block the pipeline
```

All autocli calls wrapped in `_safe_fetch_*()` helpers (same pattern as `_safe_fetch_realtime()` in sentiment_analyst.py). Exceptions never propagate.

### `# SKIP_VENDOR:` sentinel

For non-CN tickers, modules return `# SKIP_VENDOR:` to signal the vendor router to try the next fallback. Same convention as existing `eastmoney_news.py`.

---

## 7. Files Changed

| File | Change |
|------|--------|
| `capitalradar/dataflows/autocli_utils.py` | **CREATE** — Chrome CDP scraping utilities |
| `capitalradar/dataflows/xueqiu_sentiment.py` | **CREATE** — Xueqiu sentiment data vendor |
| `capitalradar/dataflows/eastmoney_guba.py` | **CREATE** — Guba forum sentiment vendor |
| `capitalradar/dataflows/sina_dragon_tiger.py` | **CREATE** — Sina Dragon-Tiger List vendor |
| `capitalradar/dataflows/interface.py` | Modify — Register new vendors and categories |
| `capitalradar/agents/utils/agent_utils.py` | Modify — Register `get_dragon_tiger_list` as LangChain @tool |
| `capitalradar/agents/analysts/sentiment_analyst.py` | Modify — Pre-fetch Xueqiu + Guba |
| `capitalradar/agents/analysts/capital_flow_analyst.py` | Modify — Add dragon tiger tool + prompt |
| `capitalradar/default_config.py` | Modify — Add autocli config + new vendor entries |

No frontend changes.

---

## 8. What's NOT in Scope

- Real-time streaming from these platforms (polling-based only)
- User authentication management (assumes Chrome is already logged in)
- Non-CN stock coverage (Xueqiu/Guba/Dragon-Tiger are A-share specific)
- Historical sentiment backfill (current window only, matching existing sentiment pattern)
- autocli as a standalone CLI tool (it's purely a library within CapitalRadar)

---

## 9. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Chrome not running / no debug port | Skip autocli tier, return available public API data or "<unavailable>" |
| Non-CN ticker (e.g., AAPL) | Return `# SKIP_VENDOR:` immediately — no attempt to scrape |
| Platform HTML structure changes | Autocli returns partial data with "[parse warning]" note |
| Rate-limited by platform | Exponential backoff (1s, 2s, 4s), then degrade to next tier |
| Tushare Token not configured | Dragon tiger still works via sina vendor fallback |
| Both public API and autocli fail | Return "<unavailable — Xueqiu / Guba / Dragon Tiger fetch failed for {ticker}>" |

---

## 10. Testing Strategy

- **Unit**: Test each vendor module with mock HTTP responses (no Chrome dependency)
- **Integration**: Test fallback chain: public API → autocli → unavailable
- **Manual**: Run sentiment analysis on a CN ticker (e.g., 000001.SZ), verify Xueqiu and Guba blocks appear in the sentiment report
- **Manual**: Run capital flow analysis, verify dragon tiger data appears when stock is listed
