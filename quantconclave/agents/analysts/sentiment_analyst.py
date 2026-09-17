"""Sentiment analyst 鈥?multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news 鈥?which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines     鈥?Yahoo Finance (institutional framing)
  2. StockTwits messages 鈥?retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags
  3. Reddit posts        鈥?r/wallstreetbets, r/stocks, r/investing

The agent does not use tool-calling; the data is in the prompt from
turn 0. The LLM produces the sentiment report in a single invocation.

See: https://github.com/TauricResearch/TradingAgents/issues/557
"""

from datetime import datetime, timedelta
import concurrent.futures

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from quantconclave.agents.utils.agent_utils import (
    build_state_instrument_context,
    get_language_instruction,
    get_news,
    get_realtime_quote,
)
from quantconclave.agents.utils.web_search_tool import web_search
from quantconclave.dataflows.reddit import fetch_reddit_posts
from quantconclave.dataflows.stocktwits import fetch_stocktwits_messages
from quantconclave.dataflows.xueqiu_sentiment import get_xueqiu_sentiment
from quantconclave.dataflows.eastmoney_guba import get_guba_sentiment


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits + Reddit data, injects them into the
    prompt as structured blocks, and produces a sentiment report in a
    single LLM call.
    """

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_state_instrument_context(state)
        market = str((state.get("instrument_profile") or {}).get("market", "UNKNOWN")).upper()

        # Pre-fetch all sources IN PARALLEL using a ThreadPoolExecutor.
        # Each fetcher degrades gracefully and returns a string, so the LLM
        # always sees something -- either real data or a clear placeholder.
        # Parallel fetching reduces wall-clock time from sum(all) to max(all).
        FETCH_TIMEOUT = 25  # max seconds to wait for all data fetchers
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as _exec:
            _futs = {
                _exec.submit(get_news.func, ticker, start_date, end_date): 'news',
                _exec.submit(fetch_stocktwits_messages, ticker, 30): 'stocktwits',
                _exec.submit(fetch_reddit_posts, ticker): 'reddit',
                _exec.submit(_safe_fetch_realtime, ticker): 'realtime',
            }
            if market == "CN":
                _futs[_exec.submit(_safe_fetch_xueqiu, ticker)] = "xueqiu"
                _futs[_exec.submit(_safe_fetch_guba, ticker)] = "guba"
            _res = {}
            done, not_done = concurrent.futures.wait(
                _futs, timeout=FETCH_TIMEOUT,
                return_when=concurrent.futures.FIRST_EXCEPTION,
            )
            # Collect results from completed futures
            for _f in done:
                _k = _futs[_f]
                try:
                    _res[_k] = _f.result()
                except Exception as _e:
                    _res[_k] = f'<{_k} unavailable: {_e}>'
            # Cancel any still-pending futures so they don't hang the process
            for _f in not_done:
                _k = _futs[_f]
                _f.cancel()
                _res.setdefault(_k, f'<{_k} unavailable: timed out after {FETCH_TIMEOUT}s>')
        news_block = _res.get('news', '<news unavailable>')
        stocktwits_block = _res.get('stocktwits', '<stocktwits unavailable>')
        reddit_block = _res.get('reddit', '<reddit unavailable>')
        realtime_block = _res.get('realtime', '<realtime unavailable>')
        xueqiu_block = _res.get('xueqiu', 'NO_DATA')
        guba_block = _res.get('guba', 'NO_DATA')

        capital_flow_report = state.get("capital_flow_report", "")

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            realtime_block=realtime_block,
            xueqiu_block=xueqiu_block,
            guba_block=guba_block,
            capital_flow_report=capital_flow_report,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # No bind_tools 鈥?the data is already in the prompt; a single LLM
        # call produces the report directly.
        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node


def _safe_fetch_realtime(ticker: str) -> str:
    """Fetch real-time quote, degrading gracefully on failure."""
    try:
        return get_realtime_quote.func(ticker)
    except Exception:
        return f"<unavailable 鈥?real-time quote fetch failed for {ticker}>"


def _safe_fetch_xueqiu(ticker: str) -> str:
    """Fetch Xueqiu sentiment, degrading gracefully on failure."""
    try:
        return get_xueqiu_sentiment(ticker)
    except Exception:
        return f"<unavailable 鈥?Xueqiu fetch failed for {ticker}>"


def _safe_fetch_guba(ticker: str) -> str:
    """Fetch Guba forum sentiment, degrading gracefully on failure."""
    try:
        return get_guba_sentiment(ticker)
    except Exception:
        return f"<unavailable 鈥?Guba fetch failed for {ticker}>"


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    realtime_block: str = "",
    xueqiu_block: str = "",
    guba_block: str = "",
    capital_flow_report: str = "",
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on complementary data sources that have already been collected for you.

NO_DATA means evidence is unavailable or insufficient. Never convert NO_DATA into neutral sentiment; state the limitation and reduce confidence.

## YOUR MOST IMPORTANT REFERENCE: CAPITAL FLOW REPORT

Before you start, carefully read this Capital Flow report 鈥?it tells you what major capital is ACTUALLY doing with this stock:

<start_of_capital_flow>
{capital_flow_report[:4000] if capital_flow_report else 'Not yet available.'}
<end_of_capital_flow>

Use this as your truth anchor for sentiment analysis. Every sentiment signal must be cross-referenced against actual capital flows.
If retail sentiment is overwhelmingly bullish but capital is flowing OUT, this is a classic distribution trap (涓诲姏鍑鸿揣,鏁ｆ埛鎺ョ洏).
If retail sentiment is overwhelmingly bearish but capital is flowing IN, this is a classic accumulation play (涓诲姏鍚哥,鏁ｆ埛鍓茶倝).
Your job: determine whether the sentiment is GENUINE or MANUFACTURED by major capital to manipulate retail behavior.

## Data sources (pre-fetched, in this prompt)

### News headlines 鈥?Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages 鈥?retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts 鈥?r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

### Real-time Market Snapshot (pre-fetched)
Live price, bid/ask spread, session volume, day range, and market state (open/closed). Use this to anchor all sentiment analysis in current market reality.

<start_of_realtime>
{realtime_block}
<end_of_realtime>

### 闆悆绀惧尯 鈥?A鑲℃姇璧勮€呰璁猴紙棰勬姄鍙栵級
涓浗鏈€澶х殑鎶曡祫鑰呯ぞ鍖猴紝鐢ㄦ埛鍋忎笓涓?鍗婁笓涓氾紝姣忔潯甯栧瓙鍙惈澶氱┖鏍囪銆佺敤鎴疯璇佺骇鍒€備笌鑲″惂浜掍负瀵圭収銆?

<start_of_xueqiu>
{xueqiu_block}
<end_of_xueqiu>

### 涓滄柟璐㈠瘜鑲″惂 鈥?鏁ｆ埛璁哄潧锛堥鎶撳彇锛?
姣忓彧A鑲′笓灞炶鍧涳紝鏁ｆ埛鎯呯华鏈€鐩存帴鐨勭獥鍙ｃ€備笌闆悆浜掍负瀵圭収銆?

<start_of_guba>
{guba_block}
<end_of_guba>

## How to analyze this data (best practices)

1. **Check the Real-time Market Snapshot first.** Current price, market state, and session volume anchor your sentiment analysis in the present 鈥?not just the 7-day historical window. If the market is open, live price action may be reacting to breaking sentiment shifts. If the current price is moving against the prevailing sentiment direction, flag this as a divergence signal.

2. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; 鈮?0/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters 鈥?base rates on the actual message count, not percentages alone.

3. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal 鈥?it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

4. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context 鈥?the title alone often misleads.

5. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

6. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

7. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust 鈥?flag this caveat explicitly. If the sources are silent on a given subreddit, say so.

8. **Identify catalysts and risks** that emerge across sources 鈥?news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

9. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

10. **闆悆涓庤偂鍚т簰涓哄鐓?* 鈥?闆悆鐢ㄦ埛鍋忎笓涓?鍗婁笓涓氾紝鑲″惂鐢ㄦ埛鍋忔暎鎴枫€傞洩鐞冪湅澶?+ 鑲″惂鎭愭厡 = 鏈烘瀯鍦ㄩ洩鐞冨甫鑺傚锛屾暎鎴峰湪鑲″惂鍓茶倝 鈫?鍚哥淇″彿銆備袱鑰呬竴鑷存瀬绔湅澶?= 鎯呯华杩囩儹 鈫?鍑忎粨淇″彿銆傞洩鐞冭璁洪噺寮傚姩锛堢獊鐒跺ぇ骞呭鍔狅級= 鏈変簨浠跺偓鍖栵紝闇€缁撳悎鏂伴椈婧愰獙璇併€?

## Output

Produce a sentiment report covering, in order:

1. **Overall sentiment direction** 鈥?Bullish / Bearish / Neutral / Mixed 鈥?with a brief confidence note based on data quality and sample size.
2. **Source-by-source breakdown** 鈥?what each of news / StockTwits / Reddit is telling you, with specific evidence (cite message counts, ratios, notable posts).
3. **Divergences, alignments, and key narratives** across sources.
4. **Catalysts and risks** surfaced by the data.
5. **Markdown table** at the end summarizing key sentiment signals, their direction, source, and supporting evidence.

## Major Fund Movement Assessment (REQUIRED SECTION)

Before concluding, you MUST analyze whether major funds / institutions are manipulating retail sentiment. Institutions can flood social media with coordinated bullish narratives, pay influencers to promote stocks, or orchestrate fear campaigns 鈥?all while trading in the opposite direction. Key manipulation signals to detect:

- **Uniform sentiment extremism**: If StockTwits / Reddit show 鈮?0% bullish sentiment on a single day without a clear news catalyst, this may be a coordinated pump campaign
- **Sentiment-flow divergence**: CROSS-REFERENCE WITH THE CAPITAL FLOW REPORT ABOVE. If retail sentiment is overwhelmingly bullish but capital flow shows net OUTFLOW, this is the single strongest manipulation red flag 鈥?institutions are using sentiment to distribute
- **Bot-like posting patterns**: Identical or near-identical messages across accounts, sudden surges in low-quality posts, or coordinated hashtag campaigns
- **Narrative timing relative to price**: If a bullish narrative emerges AFTER a significant price run-up, institutions may be using the narrative to attract retail liquidity for their exit

At the end of your report, add:

---
## Major Fund Movement Assessment
**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]
**Evidence**: [Specific sentiment patterns that suggest genuine enthusiasm vs. manufactured hype; flag sentiment-flow divergences, uniformity extremes, suspicious timing]
**Capital Flow vs Sentiment Alignment**: [ALIGNED / DIVERGENT 鈥?are retail sentiment and major capital flowing in the same direction?]
**Major Capital's Most Likely Play**: [Are they pumping sentiment to distribute, or crushing sentiment to accumulate?]
**Retail Investor Action**: [Should retail follow the sentiment or counter it?]
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


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)

