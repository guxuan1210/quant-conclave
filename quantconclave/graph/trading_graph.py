# quantconclave/graph/trading_graph.py

import logging
import os
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from quantconclave.llm_clients import create_llm_client, resolve_role_llm

from quantconclave.agents import *
from quantconclave.default_config import DEFAULT_CONFIG
from quantconclave.agents.utils.memory import QuantConclaveMemoryLog
from quantconclave.dataflows.utils import safe_ticker_component
from quantconclave.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from quantconclave.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from quantconclave.agents.utils.agent_utils import (
    get_stock_data,
    get_macro_context,
    detect_ah_relationship,
    get_hk_stock_data,
    get_ah_premium,
    get_southbound_flow,
    get_broker_recommend,
    get_weekly_data,
    get_monthly_data,
    get_fund_holdings,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news,
    get_money_flow,
    get_hsgt_flow,
    get_market_flow,
    get_institutional_holders,
    get_major_holders,
    get_analyst_recommendations,
    get_margin_trading,
    get_intraday_data,
    get_realtime_quote,
)

from quantconclave.agents.utils.quant_tools import (
    get_stochastic, get_williams_r, get_roc,
    get_trendlines, get_chart_pattern,
    ADVANCED_INDICATOR_TOOLS,
)
from quantconclave.agents.utils.web_search_tool import web_search

from quantconclave.agents.utils.eastmoney_tools import (
    get_eastmoney_money_flow,
    get_eastmoney_quote,
    get_eastmoney_fundamentals,
    get_eastmoney_data,
    get_eastmoney_block_trades,
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class QuantConclaveGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["capital_flow", "market", "social", "news", "fundamentals", "competitor", "partner"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
        checkpointer=None,
        on_progress: callable = None,
    ):
        """Initialize the trading agents graph and components."""


        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with per-role provider-specific thinking config.
        # deep (managers/decision) and quick (analysts/utility) may use
        # different providers via deep_think_provider/quick_think_provider.
        deep_provider, deep_model, _ = resolve_role_llm(self.config, "deep")
        quick_provider, quick_model, _ = resolve_role_llm(self.config, "quick")

        common_kwargs = {
            "timeout": 300,            # LLM API timeout (sec) — reasoning models need more
            "max_tokens": 4096,        # reasoning models consume tokens for thinking blocks
            "max_completion_tokens": 4096,
        }
        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            common_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=deep_provider,
            model=deep_model,
            base_url=self.config.get("backend_url"),
            **{**common_kwargs, **self._get_provider_kwargs(deep_provider)},
        )
        quick_client = create_llm_client(
            provider=quick_provider,
            model=quick_model,
            base_url=self.config.get("backend_url"),
            **{**common_kwargs, **self._get_provider_kwargs(quick_provider)},
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        
        self.memory_log = QuantConclaveMemoryLog(self.config)

        # Determine current active skill version
        try:
            from quantconclave.graph.skill_generator import get_active_skill_version
            self.skill_version = get_active_skill_version()
        except Exception:
            self.skill_version = 0
        logger.info("Using skill version %d", self.skill_version)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
            max_analyst_tool_calls=self.config.get("max_analyst_tool_calls", 12),
            max_pm_tool_calls=5,
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            config=self.config,
            analyst_concurrency_limit=self.config.get("analyst_concurrency_limit", 1),
            on_progress=on_progress,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile(checkpointer=checkpointer)
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self, provider: str) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation.

        Called per role so deep and quick can carry different providers
        (e.g. deep=google → thinking_level, quick=deepseek → none).
        """
        kwargs = {}
        provider = (provider or "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # QuantAgent-fused tools: advanced indicators + trendlines + patterns
                    get_stochastic,
                    get_williams_r,
                    get_roc,
                    get_trendlines,
                    get_chart_pattern,
                    *ADVANCED_INDICATOR_TOOLS,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                    # 妙想(MX) real-time fundamentals + generic data engine
                    get_eastmoney_fundamentals,
                    get_eastmoney_data,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
            "capital_flow": ToolNode(
                [
                    # Capital flow & institutional money flow tools
                    get_money_flow,
                    get_hsgt_flow,
                    get_market_flow,
                    get_margin_trading,
                    get_institutional_holders,
                    get_major_holders,
                    get_analyst_recommendations,
                    get_insider_transactions,
                    get_indicators,
                    # Intraday / real-time data tools
                    get_intraday_data,
                    get_realtime_quote,
                    # 妙想(MX) real-time 主力资金 (DDX/DDY/DDZ) + 行情 + 暗盘/大宗
                    get_eastmoney_money_flow,
                    get_eastmoney_quote,
                    get_eastmoney_block_trades,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
            "competitor": ToolNode(
                [
                    get_news,
                    get_global_news,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
            "partner": ToolNode(
                [
                    get_news,
                    get_global_news,
                    web_search,
                ],
                handle_tool_errors=True,
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.
        """
        explicit = self.config.get("benchmark_ticker")
        if explicit:
            return explicit
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = ticker.upper()
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _prepare_market_context(self, ticker: str, trade_date: str, asset_type: str) -> tuple[dict, dict]:
        """Resolve an instrument profile and collect one shared evidence snapshot."""
        from quantconclave.instruments import resolve_instrument
        from quantconclave.evidence import build_evidence_pack
        from quantconclave.dataflows.sec_edgar import SecEdgarClient

        profile = resolve_instrument(ticker, asset_type=asset_type)
        sec_client = None
        user_agent = self.config.get("sec_user_agent", "")
        if user_agent:
            sec_client = SecEdgarClient(
                user_agent=user_agent,
                cache_dir=self.config.get("data_cache_dir", ""),
                request_interval_seconds=self.config.get("sec_request_interval_seconds", 0.12),
                timeout_seconds=self.config.get("sec_timeout_seconds", 10.0),
            )
        pack = build_evidence_pack(profile, str(trade_date), self.config, sec_client=sec_client)
        return profile.to_dict(), pack.to_dict()

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str = "SPY",
    ) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline (resolved by the
        caller via ``_resolve_benchmark``). Returns ``(raw_return, alpha_return,
        actual_holding_days)`` or ``(None, None, None)`` if price data is
        unavailable (too recent, delisted, or network error).
        """
        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
            bench = yf.Ticker(benchmark).history(start=trade_date, end=end_str)

            if len(stock) < 2 or len(bench) < 2:
                return None, None, None

            actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0])
                / bench["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(
                ticker, entry["date"], benchmark=benchmark,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
                benchmark_name=benchmark,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def propagate(self, ticker, trade_date, asset_type: str = "stock"):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.
        """
        self.ticker = ticker

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(ticker)

        # Recompile with a checkpointer if the user opted in.
        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], ticker
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                self.config["data_cache_dir"], ticker, str(trade_date)
            )
            if step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s", step, ticker, trade_date
                )
            else:
                logger.info("Starting fresh for %s on %s", ticker, trade_date)

        try:
            return self._run_graph(ticker, trade_date, asset_type=asset_type)
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def _run_graph(self, ticker, trade_date, asset_type: str = "stock"):
        """Execute the graph and write the resulting state to disk and memory log."""
        # Initialize state — inject memory log context for PM.
        past_context = self.memory_log.get_past_context(ticker)
        instrument_profile, evidence_pack = self._prepare_market_context(ticker, trade_date, asset_type)
        init_agent_state = self.propagator.create_initial_state(
            ticker, trade_date, asset_type=asset_type, past_context=past_context,
            instrument_profile=instrument_profile, evidence_pack=evidence_pack,
        )
        # Run ML prediction for PM context (fail gracefully)
        try:
            from quantconclave.prediction import PredictionAgent
            pa = PredictionAgent()
            report = pa.predict(ticker, trade_date, self.config)
            init_agent_state["prediction_report"] = report.to_markdown()
        except Exception as e:
            import traceback
            logger.warning(
                "PredictionAgent pre-run failed for %s/%s (PM will call predict_stock_price as fallback): %s\ntraceback:\n%s",
                ticker, trade_date, e, traceback.format_exc(),
            )
            init_agent_state["prediction_report"] = ""
        init_agent_state["skill_version"] = self.skill_version
        args = self.propagator.get_graph_args()

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(ticker, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        if self.debug:
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)
            # Streamed chunks are per-node deltas. Merge them so the returned
            # state matches what graph.invoke() yields in the non-debug path.
            final_state = {}
            for chunk in trace:
                final_state.update(chunk)
        else:
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        self.memory_log.store_decision(
            ticker=ticker,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
            skill_version=self.skill_version,
        )
        # Check if analysis queue triggers auto-loop
        pending = self.memory_log.get_pending_entries()
        threshold = self.config.get("adjudication_rules", {}).get("auto_loop_threshold", 10)
        if len(pending) >= threshold:
            from quantconclave.graph.loop_coordinator import run_full_loop
            try:
                run_full_loop(self.config, trigger="post_analysis")
            except Exception as loop_err:
                logger.warning("Auto-loop failed: %s", loop_err)

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], ticker, str(trade_date)
            )

        return final_state, self.process_signal(final_state["final_trade_decision"])


    def execute_graph(self, ticker, trade_date, asset_type: str = "stock"):
        """Convenience wrapper: full analysis pipeline. Returns (final_state, signal)."""
        return self.propagate(ticker, trade_date, asset_type=asset_type)

    def analyze_partial(self, ticker, trade_date,
                        aspects=None,
                        reuse_state: dict = None):
        """Run only selected analysts, skipping debate/trader/risk.

        For the Advisor to do quick/standard refreshes. Non-refreshed
        analyst reports are reused from reuse_state (last full analysis).

        aspects: ["capital_flow","market"] etc. Default: capital_flow only.
        """
        if aspects is None:
            aspects = ["capital_flow", "market"]

        self.ticker = ticker
        self._resolve_pending_entries(ticker)

        past_context = self.memory_log.get_past_context(ticker)
        instrument_profile, evidence_pack = self._prepare_market_context(ticker, trade_date, "stock")
        init_state = self.propagator.create_initial_state(
            ticker, trade_date, asset_type="stock", past_context=past_context,
            instrument_profile=instrument_profile, evidence_pack=evidence_pack,
        )

        # Inject reused reports from last analysis
        from quantconclave.catalog import ANALYST_ROLES
        report_map = {
            key: role.report_key for key, role in ANALYST_ROLES.items() if key != "capital_flow"
        }
        if reuse_state:
            for analyst_key, report_key in report_map.items():
                if analyst_key not in aspects:
                    old = reuse_state.get(report_key, "")
                    if old:
                        init_state[report_key] = "(REUSED)\n\n" + str(old)[:3000]

        # Run partial graph
        partial_workflow = self.graph_setup.setup_partial_graph(aspects)
        partial_graph = partial_workflow.compile()
        args = self.propagator.get_graph_args()
        final_state = partial_graph.invoke(init_state, **args)
        self.curr_state = final_state
        self._log_state(trade_date, final_state)
        return final_state

    def _load_last_state(self, ticker: str) -> dict | None:
        """Load the most recent full analysis state for a ticker."""
        import glob, json
        from pathlib import Path
        safe = ticker.replace(".", "_").upper()
        directory = Path(self.config["results_dir"]) / safe / "QuantConclaveStrategy_logs"
        if not directory.exists():
            return None
        files = sorted(glob.glob(str(directory / "full_states_log_*.json")), reverse=True)
        if not files:
            return None
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "capital_flow_report": final_state["capital_flow_report"],
            "competitor_report": final_state["competitor_report"],
            "partner_report": final_state["partner_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "QuantConclaveStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
