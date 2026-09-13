from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from capitalradar.agents.utils.agent_utils import (
    build_instrument_context,
    get_indicators,
    get_intraday_data,
    get_language_instruction,
    get_realtime_quote,
    get_stock_data,
)
from capitalradar.agents.utils.quant_tools import (
    get_stochastic, get_williams_r, get_roc,
    get_trendlines, get_chart_pattern,
    ADVANCED_INDICATOR_TOOLS,
)
from capitalradar.agents.utils.web_search_tool import web_search
from capitalradar.dataflows.config import get_config


def create_market_analyst(llm):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = build_instrument_context(
            state["company_of_interest"], asset_type
        )

        capital_flow_report = state.get("capital_flow_report", "")

        tools = [
            get_stock_data,
            get_indicators,
            get_realtime_quote,
            get_intraday_data,
            get_stochastic,
            get_williams_r,
            get_roc,
            get_trendlines,
            get_chart_pattern,
            *ADVANCED_INDICATOR_TOOLS,
            web_search,
        ]

        system_message = (
            "=== YOUR MOST IMPORTANT REFERENCE: CAPITAL FLOW REPORT ===\n"
            "Before you start, carefully read this Capital Flow report — it tells you what major capital is ACTUALLY doing with this stock:\n\n"
            f"--- CAPITAL FLOW REPORT START ---\n"
            f"{capital_flow_report[:4000] if capital_flow_report else 'Not yet available.'}\n"
            f"--- CAPITAL FLOW REPORT END ---\n\n"
            "Use this as your truth anchor for technical analysis. Every technical signal must be cross-referenced against actual capital flows.\n"
            "If technicals look bullish but capital is flowing OUT, the patterns are likely a trap (manufactured breakout).\n"
            "If technicals look bearish but capital is flowing IN, the patterns may be a shakeout before accumulation.\n"
            "Your job: determine whether the technical patterns are GENUINE or MANUFACTURED by major capital.\n\n"
            "=== REAL-TIME MARKET CONTEXT (CHECK FIRST) ===\n"
            "Before diving into historical indicators, you MUST check current market conditions:\n"
            "- Call `get_realtime_quote` FIRST to get the live market pulse: current price, bid/ask spread, session volume, and whether the market is open/closed. This gives you the most up-to-date price and volume context.\n"
            "- Use `get_intraday_data` (5m or 15m intervals recommended) to detect intraday patterns that daily candles miss: volume spikes near day-high/low, VWAP deviations, and potential intraday accumulation/distribution.\n"
            "- If the market is currently open: intraday data shows LIVE order flow — look for volume-price divergences in real time.\n"
            "- If the market is closed: intraday data from the most recent session still provides finer-grained signals than daily candles alone.\n"
            "- Cross-reference the real-time/intraday picture against the Capital Flow report: does current price action align with or diverge from the capital flow direction?\n\n"
+ """You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

- Select indicators that provide diverse and complementary information. Avoid redundancy (e.g., do not select both rsi and stochrsi). Also briefly explain why they are suitable for the given market context. When you tool call, please use the exact name of the indicators provided above as they are defined parameters, otherwise your call will fail. Please make sure to call get_stock_data first to retrieve the CSV that is needed to generate indicators. Then use get_indicators with the specific indicator names. Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."""
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + """

⚠️ CRITICAL — Major Fund Movement Assessment:

Before concluding your report, you MUST analyze whether major funds / institutions could be MANIPULATING the technical patterns you are observing. Institutions with large capital can manufacture false technical signals to trap retail investors: they can push prices through key levels to trigger false breakouts, paint the tape to create artificial volume patterns, engineer moving-average crossovers by timing large orders, or drive RSI extremes to trigger algorithmic reactions. Volume-price divergence (rising price + declining volume) is the strongest manipulation signal — it means institutional distribution is in progress while retail chases the breakout.

**CROSS-REFERENCE WITH CAPITAL FLOW REPORT**: Compare your technical findings against the Capital Flow report above. If technicals show a bullish breakout but the Capital Flow report shows net institutional OUTFLOW, you MUST flag this as a likely manufactured breakout (诱多陷阱). If technicals show bearish breakdown but capital flow shows net INFLOW, flag as likely shakeout (诱空陷阱).

At the end of your report, add this REQUIRED section:

---
## Major Fund Movement Assessment
**Manipulation Risk Level**: [HIGH / MEDIUM / LOW]
**Evidence**: [Specific technical patterns that appear genuine vs. manufactured; flag volume-price divergences, unnatural breakouts, suspicious timing. Explicitly state whether technical patterns align with or diverge from the Capital Flow report]
**Capital Flow vs Technicals Alignment**: [ALIGNED / DIVERGENT — explain why]
**Major Capital's Most Likely Play**: [Are they using technical patterns to attract retail before distribution, or scare retail before accumulation?]
**Retail Investor Action**: [Should retail follow or counter the technical signals?]
---

=== RETRACEMENT / PULLBACK DETECTION (REQUIRED) ===

After your standard technical analysis, you MUST perform a retracement scan.
Use get_stock_data to get daily OHLCV, then get_indicators for RSI, MACD,
Bollinger Bands, and moving averages.

**Advanced tools (call at least 2-3):**
- get_stochastic(symbol) — %K/%D oscillator for overbought/oversold detection.
  More sensitive than RSI for short-term turning points.
- get_williams_r(symbol) — Williams %R for momentum extremes.
- get_roc(symbol) — Rate of Change to confirm trend momentum direction.
- get_trendlines(symbol) — Mathematical support/resistance fitting.
  Returns precise channel slope, width%, and trend bias. Use these
  levels (not eyeballed MA lines) for entry/exit recommendations.
- get_chart_pattern(symbol) — K-line candlestick pattern identification
  (head-and-shoulders, double top/bottom, triangles, engulfing, doji).
- get_cci(symbol) — Commodity Channel Index; > +100 overbought, < -100 oversold.
- get_atr(symbol) — Average True Range for volatility / stop-loss sizing.
- get_trend_score(symbol) — Regression trend strength (slope × R²).
- get_rsrs(symbol) — Support/resistance relative strength for timing.
- get_er(symbol) — Kaufman Efficiency Ratio (trend vs choppy).
- get_ma_series(symbol) — Fast/slow MA cross signals (golden/dead cross).
- get_supertrend(symbol) — ATR-based trend band; sign flips mark reversals.
- get_volatility_regime(symbol) — short/long vol ratio (>1.5 high risk).

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
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node
