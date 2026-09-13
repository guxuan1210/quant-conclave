"""SSE streaming adapter for CapitalRadar / CapitalRadarGraph.

Calls graph.stream() in up to three segments, pausing at configurable
human-in-the-loop checkpoints. At each active checkpoint the emitter
blocks on a threading.Event until the user submits a response via the
POST /api/respond/{session_id} endpoint.
"""

import json
import re
from datetime import datetime, timedelta
import time
import threading
import logging
from typing import Dict, Any, Generator, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError

from capitalradar.graph.trading_graph import CapitalRadarGraph
from capitalradar.default_config import DEFAULT_CONFIG
from capitalradar.agents.utils.rating import parse_rating

logger = logging.getLogger(__name__)

# Module-level session store shared with app.py
_pending_interactions: Dict[str, threading.Event] = {}
_user_responses: Dict[str, str] = {}
_session_results: Dict[str, Dict] = {}
_pending_chats: Dict[str, threading.Event] = {}
_chat_questions: Dict[str, str] = {}

# Pipeline stages in order
def _extract_next_analysis_date(final_decision: str, analysis_date: str) -> str:
    """Parse the PM's time_horizon from the final decision and compute next analysis date.

    Looks for **Time Horizon**: X-Y months patterns and adds the midpoint to analysis_date.
    Returns a date string (YYYY-MM-DD) or empty string if unparseable.
    """
    if not final_decision:
        return ""
    m = re.search(r'\*\*Time Horizon\*\*[:\s]*([^<\n]+)', final_decision, re.IGNORECASE)
    if not m:
        m = re.search(r'[Tt]ime\s*[Hh]orizon[:\s]*(\d+)\s*[-鈥搕o]+\s*(\d+)\s*(month|week|day|month)s?', final_decision)
    if not m:
        return ""

    horizon_text = m.group(1) if m.lastindex is None or m.lastindex < 2 else f"{m.group(1)}-{m.group(2)} {m.group(3)}"
    try:
        # Parse "3-6 months", "1-2 weeks", etc.
        nums = re.findall(r'(\d+)', horizon_text)
        if not nums:
            return ""
        if "week" in horizon_text.lower():
            days = int(nums[-1]) * 7
        elif "month" in horizon_text.lower():
            days = int(nums[-1]) * 30
        elif "day" in horizon_text.lower():
            days = int(nums[-1])
        else:
            days = int(nums[-1]) * 30  # default: interpret as months

        base = datetime.strptime(analysis_date, "%Y-%m-%d")
        next_date = base + timedelta(days=days)
        return next_date.strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return ""


STAGES = [
    ("analysts", "Analyst Team"),
    ("research", "Research Debate"),
    ("trader", "Trader"),
    ("risk", "Risk Debate (PM Verdict)"),
    ("portfolio", "Portfolio Manager"),
]

from capitalradar.catalog import ANALYST_ROLES
ANALYST_REPORT_KEYS = {key: role.report_key for key, role in ANALYST_ROLES.items()}
ANALYST_NAMES = {key: role.label for key, role in ANALYST_ROLES.items()}

_STAGE_COLORS = {
    "capital_flow": "#f0883e",
    "market": "#3fb950",
    "social": "#a371f7",
    "news": "#ffd700",
    "fundamentals": "#53d8fb",
    "competitor": "#ff6b6b",
    "partner": "#48dbfb",
}


def _elapsed_since(start: float) -> int:
    return int((time.time() - start) * 1000)


def _event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _clean_report(text: str) -> str:
    """Strip raw tool-call XML invocations from report text.

    Some LLMs (especially smaller Ollama models) emit raw tool-call XML
    blocks in their prose, which renders as garbled markdown in the UI.
    Strip ``...`` blocks and their content.
    """
    if not text:
        return text
    import re as _re
    # Remove <invoke ...>...</invoke> blocks (multi-line or single-line)
    text = _re.sub(r'<invoke[^>]*>.*?</invoke>', '', text, flags=_re.DOTALL)
    # Remove self-closing <invoke ... /> tags
    text = _re.sub(r'<invoke[^>]*?/>', '', text)
    # Remove stray tool-call JSON blocks: {"name": "...", "arguments": {...}}
    text = _re.sub(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}', '', text)
    # Collapse multiple blank lines
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def _extract_manipulation_risk(text: str) -> str:
    """Extract manipulation risk level from a report's Major Fund Movement Assessment section.

    Returns one of: 'HIGH', 'MEDIUM', 'LOW', or '' if not found.
    """
    if not text:
        return ""
    # Look for the risk level in the Manipulation Risk Level field
    # Matches patterns like: "**Manipulation Risk Level**: HIGH" or "Manipulation Risk Level: MEDIUM"
    try:
        m = re.search(
            r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*:\s*(HIGH|MEDIUM|LOW)',
            text, re.IGNORECASE
        )
        if m:
            return m.group(1).upper()
        # Fallback: look for standalone risk mentions near "Major Fund"
        if re.search(r'Major\s+Fund\s+Movement', text, re.IGNORECASE):
            if re.search(r'(?:risk|level).*HIGH', text, re.IGNORECASE):
                return "HIGH"
            if re.search(r'(?:risk|level).*MEDIUM', text, re.IGNORECASE):
                return "MEDIUM"
            if re.search(r'(?:risk|level).*LOW', text, re.IGNORECASE):
                return "LOW"
        return ""
    except (re.error, TypeError) as regex_err:
        logger.warning("Regex error in _extract_manipulation_risk: %s", regex_err)
        return ""


def _extract_final_manipulation_summary(text: str) -> str:
    """Extract the manipulation risk level from the final Portfolio Manager decision.

    Looks for the overall manipulation risk verdict.
    """
    if not text:
        return ""
    m = re.search(
        r'(?:overall\s+)?manipulation\s+risk\s+level\s*(?:is|:)?\s*(HIGH|MEDIUM|LOW)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).upper()
    return _extract_manipulation_risk(text)


class StreamEmitter:
    """Wraps a CapitalRadarGraph run and emits SSE events with optional human-in-the-loop checkpoints."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None):
        self.config = config or DEFAULT_CONFIG.copy()
        import uuid
        self.session_id = (session_id or "").strip() or str(uuid.uuid4())
        self._analyst_tool_traces: dict = {}     # {analyst_key: [trace_record, ...]}
        self._selected_analysts: list = []
        self._analysts_completed: set = set()
        self._analysts_total: int = 0
        self._prev_message_count: int = 0        # to detect new messages

    def stream_analysis(
        self,
        ticker: str,
        date: str,
        analysts: list[str],
        provider: str = "",
        deep_provider: str = "",
        quick_provider: str = "",
        deep_model: str = "",
        quick_model: str = "",
        backend_url: str = "",
        proxy: str = "",
        language: str = "English",
        checkpoint_1: bool = False,
        checkpoint_2: bool = False,
        pm_chat: bool = False,
    ) -> Generator[str, None, None]:
        pipeline_start = time.time()
        stage_times: Dict[str, float] = {}
        current_stage_idx = -1

        # Initialize session results accumulator for markdown download
        _session_results[self.session_id] = {
            "ticker": ticker,
            "date": date,
            "analyst_reports": {},
            "research_debate_rounds": [],
            "research_manager_decision": "",
            "trader_proposal": "",
            "risk_debate_rounds": [],
            "risk_judge_decision": "",
            "investment_plan": "",
            "final_decision": "",
            "rating": "Hold",
            "checkpoint_1_answer": "",
            "checkpoint_2_answer": "",
            "analyst_tool_traces": {},
            "key_metrics": {},
            "retracement": None,
        }

        cfg = self.config.copy()
        self._selected_analysts = analysts.split(",") if isinstance(analysts, str) else list(analysts)
        self._analysts_total = len(self._selected_analysts)
        self._analysts_completed = set()
        if provider:
            cfg["llm_provider"] = provider
        if deep_provider:
            cfg["deep_think_provider"] = deep_provider
        if quick_provider:
            cfg["quick_think_provider"] = quick_provider
        if deep_model:
            cfg["deep_think_llm"] = deep_model
        if quick_model:
            cfg["quick_think_llm"] = quick_model
        if backend_url:
            # backend_url is a single global value passed to every LLM client,
            # so it can only be applied when NO role uses a cloud provider with
            # its own fixed endpoint. Per-role overrides (already applied to cfg
            # above) win over the global llm_provider.
            def _effective_role_provider(prefix):
                return (cfg.get(f"{prefix}_provider") or cfg.get("llm_provider", "")).lower()
            providers_using_url = {
                _effective_role_provider("deep_think"),
                _effective_role_provider("quick_think"),
            }
            if providers_using_url <= {"ollama", "openrouter"}:
                cfg["backend_url"] = backend_url
        if proxy:
            cfg["proxy"] = proxy
        cfg["output_language"] = language

        yield _event("pipeline-start", {
            "ticker": ticker,
            "date": date,
            "analysts": analysts,
            "provider": cfg.get("llm_provider", ""),
            "deep_provider": cfg.get("deep_think_provider") or cfg.get("llm_provider", ""),
            "quick_provider": cfg.get("quick_think_provider") or cfg.get("llm_provider", ""),
            "deep_model": cfg.get("deep_think_llm", ""),
            "quick_model": cfg.get("quick_think_llm", ""),
            "session_id": self.session_id,
        })

        try:
            ta = CapitalRadarGraph(
                selected_analysts=analysts,
                debug=False,
                config=cfg,
                checkpointer=MemorySaver(),
            )
        except Exception as e:
            yield _event("pipeline-error", {"stage": "init", "message": str(e)})
            return

        thread_config = {"configurable": {"thread_id": self.session_id}}

        past_context = ta.memory_log.get_past_context(ticker)
        init_state = ta.propagator.create_initial_state(
            ticker, date, asset_type="stock", past_context=past_context,
            checkpoint_1_enabled=checkpoint_1,
            checkpoint_2_enabled=checkpoint_2,
        )

        # Build interrupt lists for each segment
        cp1_interrupts = ["Trader"] if checkpoint_1 else []
        cp2_interrupts = ["Portfolio Manager"] if checkpoint_2 else []

        # ---- SEGMENT 1: Start -> Research Manager, interrupt before Trader ----
        prev_state: Dict[str, Any] = {}
        try:
            stream_kwargs = {
                "stream_mode": "values",
                "config": {"recursion_limit": 350, **thread_config},
            }
            if cp1_interrupts:
                stream_kwargs["interrupt_before"] = cp1_interrupts

            for chunk in ta.graph.stream(init_state, **stream_kwargs):
                prev_state, current_stage_idx = yield from self._process_chunk(
                    chunk, prev_state, pipeline_start, stage_times, current_stage_idx
                )
        except GraphRecursionError:
            logger.exception("Graph recursion limit hit in segment 1 (analyst phase)")
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": "Analysis exceeded step limit during analyst phase. The LLM called too many tools. The per-analyst cap (max_analyst_tool_calls) will now force a report after 12 tool rounds.",
            })
            yield _event("pipeline-done", {"total_elapsed_ms": _elapsed_since(pipeline_start)})
            return
        except Exception as e:
            import traceback as _tb
            tb_lines = _tb.format_exc()
            logger.exception("Pipeline error in segment 1")
            logger.error("FULL TRACEBACK:\n%s", "".join(tb_lines))
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": str(e),
                "traceback": "".join(tb_lines[-3:]),  # last 3 lines for UI
            })
            yield _event("pipeline-done", {"total_elapsed_ms": _elapsed_since(pipeline_start)})
            return

        # ---- CHECKPOINT 1: after Research Manager ----
        if checkpoint_1:
            question = prev_state.get("checkpoint_1_question", "").strip()
            if not question:
                question = "Do you have any additional context or concerns about this investment plan before the Trader executes?"

            yield _event("interaction-required", {
                "checkpoint": 1,
                "stage": "research",
                "label": "Research Manager Complete",
                "question": question,
                "session_id": self.session_id,
            })

            answer = yield from self._wait_for_response()
            if answer is not None:
                ta.graph.update_state(thread_config, {"human_feedback": answer})
                if self.session_id in _session_results:
                    _session_results[self.session_id]["checkpoint_1_answer"] = answer
            current_stage_idx = max(current_stage_idx, 1)

        # ---- SEGMENT 2: Trader -> Risk Debate, interrupt before Portfolio Manager ----
        try:
            stream_kwargs = {
                "stream_mode": "values",
                "config": {"recursion_limit": 350, **thread_config},
            }
            if cp2_interrupts:
                stream_kwargs["interrupt_before"] = cp2_interrupts

            for chunk in ta.graph.stream(None, **stream_kwargs):
                prev_state, current_stage_idx = yield from self._process_chunk(
                    chunk, prev_state, pipeline_start, stage_times, current_stage_idx
                )
        except GraphRecursionError:
            logger.exception("Graph recursion limit hit in segment 2")
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": "Analysis exceeded step limit during trader/risk phase. Try reducing the number of analysts or increasing max_recur_limit.",
            })
            yield _event("pipeline-done", {"total_elapsed_ms": _elapsed_since(pipeline_start)})
            return
        except Exception as e:
            import traceback as _tb
            tb_lines = _tb.format_exc()
            logger.exception("Pipeline error in segment 2")
            logger.error("FULL TRACEBACK:\n%s", "".join(tb_lines))
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": str(e),
                "traceback": "".join(tb_lines[-3:]),
            })
            yield _event("pipeline-done", {"total_elapsed_ms": _elapsed_since(pipeline_start)})
            return

        # ---- CHECKPOINT 2: after Portfolio Manager ----
        if checkpoint_2:
            question = prev_state.get("checkpoint_2_question", "").strip()
            if not question:
                question = "Do you have any final adjustments or concerns before this decision is finalized?"

            yield _event("interaction-required", {
                "checkpoint": 2,
                "stage": "portfolio",
                "label": "Portfolio Manager Complete",
                "question": question,
                "session_id": self.session_id,
            })

            answer = yield from self._wait_for_response()
            if answer is not None:
                ta.graph.update_state(thread_config, {"human_feedback": answer})
                if self.session_id in _session_results:
                    _session_results[self.session_id]["checkpoint_2_answer"] = answer

        # ---- SEGMENT 3: Portfolio Manager -> END ----
        try:
            for chunk in ta.graph.stream(None, thread_config, stream_mode="values"):
                prev_state, current_stage_idx = yield from self._process_chunk(
                    chunk, prev_state, pipeline_start, stage_times, current_stage_idx
                )
        except GraphRecursionError:
            logger.exception("Graph recursion limit hit in segment 3")
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": "Analysis exceeded step limit during final phase. Try reducing the number of analysts or increasing max_recur_limit.",
            })
        except Exception as e:
            import traceback as _tb
            tb_lines = _tb.format_exc()
            logger.exception("Pipeline error in segment 3")
            logger.error("FULL TRACEBACK:\n%s", "".join(tb_lines))
            yield _event("pipeline-error", {
                "stage": "pipeline",
                "message": str(e),
                "traceback": "".join(tb_lines[-3:]),
            })

        total_elapsed = _elapsed_since(pipeline_start)

        # Generate markdown report and include it so the frontend can offer
        # a client-side Blob download (avoids server-side session storage).
        report_md = ""
        try:
            report_md = generate_markdown(self.session_id)
        except Exception:
            logger.exception("Failed to generate markdown for session %s", self.session_id)

        # Complete the final stage before pipeline-done
        yield _event("stage-complete", {
            "stage": "portfolio",
            "elapsed": _elapsed_since(stage_times.get("portfolio", pipeline_start)),
        })

        yield _event("pipeline-done", {
            "total_elapsed_ms": total_elapsed,
            "report_md": report_md,
        })

        # Persist result so it appears in History and survives restarts.
        try:
            from web.results_store import save_result
            from capitalradar.agents.utils.rating import parse_rating

            # Save full state JSON (reuses _log_state pattern)
            ticker_safe = ticker.upper()
            json_rel = f"{ticker_safe}/CapitalRadarStrategy_logs/full_states_log_{date}.json"
            md_rel = f"{ticker_safe}/CapitalRadarStrategy_logs/full_report_{date}.md"
            session_data = _session_results.get(self.session_id, {})
            results_dir = cfg.get("results_dir") or ""
            if results_dir:
                from pathlib import Path
                # Save JSON (full data, no truncation)
                full_json = Path(results_dir) / json_rel
                full_json.parent.mkdir(parents=True, exist_ok=True)
                state_for_log = {
                    k: str(v) if isinstance(v, str) else v
                    for k, v in prev_state.items()
                }
                state_for_log["analyst_tool_traces"] = dict(self._analyst_tool_traces)
                state_for_log["key_metrics"] = session_data.get("key_metrics", {})
                with open(full_json, "w", encoding="utf-8") as f:
                    json.dump(state_for_log, f, ensure_ascii=False, indent=2, default=str)
                # Save markdown report
                full_md = Path(results_dir) / md_rel
                report_text = report_md or ""
                if not report_text:
                    try:
                        report_text = generate_markdown(self.session_id)
                    except Exception:
                        report_text = str(prev_state.get("final_trade_decision", ""))
                with open(full_md, "w", encoding="utf-8") as f:
                    f.write(report_text)


            final_decision = prev_state.get("final_trade_decision", "")
            rating = parse_rating(final_decision)
            next_date = _extract_next_analysis_date(final_decision, date)
            from web.ticker_utils import resolve_company_name, normalize_ticker
            from capitalradar.llm_clients import resolve_role_llm
            ticker_norm, company_name = resolve_company_name(ticker)
            save_result(cfg, {
                "ticker": ticker_norm or ticker,
                "date": date,
                "company_name": company_name,
                "rating": rating,
                "signal": "",
                "analysts": ",".join(analysts),
                "provider": cfg.get("llm_provider", ""),
                "deep_model": cfg.get("deep_think_llm", ""),
                "quick_model": cfg.get("quick_think_llm", ""),
                "language": language,
                "total_elapsed_ms": total_elapsed,
                "run_type": "manual",
                "json_path": json_rel,
                "risk_level": session_data.get("final_manipulation_risk", ""),
                "next_analysis_date": next_date,
                "deep_provider": resolve_role_llm(cfg, "deep")[0],
                "quick_provider": resolve_role_llm(cfg, "quick")[0],
            })
        except Exception:
            logger.exception("Failed to persist result for session %s", self.session_id)

        # ---- PM Chat loop (post-analysis Q&A) ----
        if pm_chat:
            chat_context = {
                "ticker": ticker,
                "date": date,
                "final_decision": prev_state.get("final_trade_decision", ""),
                "capital_flow_report": prev_state.get("capital_flow_report", ""),
                "market_report": prev_state.get("market_report", ""),
                "sentiment_report": prev_state.get("sentiment_report", ""),
                "news_report": prev_state.get("news_report", ""),
                "fundamentals_report": prev_state.get("fundamentals_report", ""),
                "competitor_report": prev_state.get("competitor_report", ""),
                "partner_report": prev_state.get("partner_report", ""),
                "investment_plan": prev_state.get("investment_plan", ""),
                "trader_investment_plan": prev_state.get("trader_investment_plan", ""),
            }
            chat_history = []

            yield _event("chat-ready", {
                "message": "You can now ask the Portfolio Manager questions about this analysis.",
                "session_id": self.session_id,
            })

            while True:
                question = yield from self._wait_for_chat()
                if question is None:
                    yield _event("chat-timeout", {"message": "Chat session timed out (5 min inactivity)."})
                    break

                question_stripped = question.strip()
                if question_stripped.lower() in ("exit", "quit", "bye", "end"):
                    yield _event("chat-done", {"message": "Chat ended.", "full_response": ""})
                    break

                yield _event("chat-typing", {"message": "Portfolio Manager is thinking..."})

                response_text = ""
                try:
                    response_text = self._generate_pm_chat_response(
                        chat_context, chat_history, question_stripped, cfg
                    )
                except Exception:
                    logger.exception("PM chat response failed")
                    response_text = "I encountered an error. Please try again or rephrase your question."

                chat_history.append({"role": "user", "content": question_stripped})
                chat_history.append({"role": "assistant", "content": response_text})

                yield _event("chat-done", {
                    "full_response": response_text,
                    "session_id": self.session_id,
                })

        # Persist session data for post-stream DOCX download.
        # The in-memory _session_results is cleared when the SSE stream ends,
        # but the user may click the download button *after* that point.
        try:
            from web.docx_export import store_session
            session_data = _session_results.get(self.session_id)
            if session_data:
                store_session(self.session_id, session_data)
        except Exception as exc:
            logger.warning("store_session failed for %s: %s", self.session_id, exc)

    def _wait_for_response(self) -> Optional[str]:
        """Block on a threading.Event until user responds or timeout expires.
        Emits SSE keepalive comments every 15 seconds to maintain connection.
        Returns the answer string or None on timeout.
        """
        event = threading.Event()
        _pending_interactions[self.session_id] = event
        waited = 0
        timeout = 15
        max_wait = 300  # 5 minutes

        try:
            while not event.wait(timeout=timeout):
                waited += timeout
                if waited >= max_wait:
                    return None
                yield ": keepalive\n\n"
        finally:
            _pending_interactions.pop(self.session_id, None)

        answer = _user_responses.pop(self.session_id, None)
        return answer

    def _wait_for_chat(self) -> Optional[str]:
        """Block on a threading.Event until user sends a chat message or timeout.
        Yields SSE keepalive comments every 15 seconds.
        Returns the question string or None on timeout.
        """
        event = threading.Event()
        _pending_chats[self.session_id] = event
        waited = 0
        timeout = 15
        max_wait = 300  # 5 minutes

        try:
            while not event.wait(timeout=timeout):
                waited += timeout
                if waited >= max_wait:
                    return None
                yield ": keepalive\n\n"
        finally:
            _pending_chats.pop(self.session_id, None)

        question = _chat_questions.pop(self.session_id, None)
        return question

    def _generate_pm_chat_response(
        self, context: dict, history: list, question: str, cfg: dict,
    ) -> str:
        """Generate a Portfolio Manager chat response using the deep-thinking LLM
        with full analysis context and recent conversation history."""
        from capitalradar.llm_clients import create_llm_client, resolve_role_llm

        provider, model, _ = resolve_role_llm(cfg, "deep", model_default=cfg.get("quick_think_llm", ""))
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=cfg.get("backend_url"),
        )
        llm = client.get_llm()

        history_str = ""
        if history:
            for msg in history[-6:]:
                role = "User" if msg["role"] == "user" else "PM"
                history_str += f"**{role}**: {msg['content']}\n\n"

        lang = cfg.get("output_language", "Chinese")

        system_prompt = (
            f"You are the Portfolio Manager from the CapitalRadar trading analysis system. "
            f"The user has just received your final trading decision and wants to ask follow-up questions.\n\n"
            f"**Current Analysis Context:**\n"
            f"- Ticker: {context.get('ticker', 'N/A')}\n"
            f"- Analysis Date: {context.get('date', 'N/A')}\n\n"
            f"**Your Final Decision:**\n{context.get('final_decision', 'Not available')[:3000]}\n\n"
            f"**Investment Plan:**\n{context.get('investment_plan', 'Not available')[:1500]}\n\n"
            f"**Trader Proposal:**\n{context.get('trader_investment_plan', 'Not available')[:1500]}\n\n"
            f"**Capital Flow Report:**\n{context.get('capital_flow_report', 'Not available')[:2000]}\n\n"
            f"**Market Report:**\n{context.get('market_report', 'Not available')[:1500]}\n\n"
            f"**Fundamentals Report:**\n{context.get('fundamentals_report', 'Not available')[:1500]}\n\n"
            f"**News Report:**\n{context.get('news_report', 'Not available')[:1500]}\n\n"
        )
        if history_str:
            system_prompt += (
                f"**Previous Conversation:**\n{history_str}\n\n"
                f"---\n"
                f"Answer the user's question conversationally, referencing specific data "
                f"from the analysis reports when relevant. Be concise but thorough. "
                f"If the user asks about something not covered in the analysis, "
                f"acknowledge the limitation and offer what related insights you can. "
                f"Write in {lang}."
            )
        else:
            system_prompt += (
                f"This is the first question. Answer conversationally, referencing specific "
                f"data from the analysis reports when relevant. Be concise but thorough. "
                f"Write in {lang}."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]

        response = llm.invoke(messages)
        return response.content if hasattr(response, 'content') else str(response)

    def _capture_analyst_tools(self, analyst_key: str, chunk: Dict[str, Any]):
        """Extract tool call records from the messages list for an analyst."""
        messages = chunk.get("messages", [])
        if not messages:
            return

        tool_records = []
        seen_call_ids = set()

        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id and tc_id not in seen_call_ids:
                        seen_call_ids.add(tc_id)
                        tool_records.append({
                            "tool_call_id": tc_id,
                            "tool_name": tc.get("name", "unknown"),
                            "tool_args": tc.get("args", {}),
                            "result_snippet": "",
                        })

        tool_results = {}
        for msg in messages:
            if hasattr(msg, "tool_call_id"):
                tc_id = msg.tool_call_id
                content = str(msg.content) if hasattr(msg, "content") else str(msg)
                tool_results[tc_id] = content[:2000]

        for rec in tool_records:
            tc_id = rec["tool_call_id"]
            if tc_id in tool_results:
                rec["result_snippet"] = tool_results[tc_id]
            else:
                rec["result_snippet"] = "(result not captured)"
            rec.pop("tool_call_id", None)

        if analyst_key not in self._analyst_tool_traces:
            self._analyst_tool_traces[analyst_key] = []

        existing_names = {r.get("tool_name") for r in self._analyst_tool_traces[analyst_key]}
        for rec in tool_records:
            if rec["tool_name"] not in existing_names:
                self._analyst_tool_traces[analyst_key].append(rec)
                existing_names.add(rec["tool_name"])

        if self.session_id in _session_results:
            _session_results[self.session_id]["analyst_tool_traces"] = dict(
                self._analyst_tool_traces
            )

    def _extract_key_metrics(self, analyst_key, report_text):
        """Extract structured key metrics from an analyst's final report.
        Returns a dict of metric_name -> value, or empty dict if nothing matched."""
        if not report_text:
            return {}

        text = str(report_text)
        metrics = {}

        patterns_map = {
            "capital_flow": [
                (r'(?:\*\*)?涓诲姏鍑€娴佸叆(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "main_net_inflow"),
                (r'(?:\*\*)?鍖楀悜璧勯噾(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "hsgt_net_inflow"),
                (r'(?:\*\*)?铻嶈祫浣欓(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "margin_balance"),
                (r'(?:\*\*)?鏈烘瀯鎸佷粨姣斾緥(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "institutional_holding_pct"),
                (r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*[锛?]\s*(HIGH|MEDIUM|LOW)',
                 "manipulation_risk"),
                (r'(?:\*\*)?(?:Main\s+Net|Net\s+Main)\s+Inflow(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "main_net_inflow"),
                (r'(?:\*\*)?Northbound\s+Flow(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "hsgt_net_inflow"),
                (r'(?:\*\*)?Institutional\s+Holding\s*%?(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "institutional_holding_pct"),
            ],
            "market": [
                (r'(?:\*\*)?Current\s+Price(?:\*\*)?\s*[锛?]\s*([$]?.+?)(?:\n|$)',
                 "current_price"),
                (r'(?:\*\*)?褰撳墠浠锋牸(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "current_price"),
                (r'(?:\*\*)?MA5(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "ma5"),
                (r'(?:\*\*)?MA20(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "ma20"),
                (r'(?:\*\*)?MACD\s+Signal(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "macd_signal"),
                (r'(?:\*\*)?MACD\s*淇″彿(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "macd_signal"),
                (r'(?:\*\*)?RSI\s*\(?14\)?(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "rsi_14"),
            ],
            "social": [
                (r'(?:\*\*)?Sentiment\s+Score(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "sentiment_score"),
                (r'(?:\*\*)?鎯呯华璇勫垎(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "sentiment_score"),
                (r'(?:\*\*)?Positive.*?Negative(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "pos_neg_ratio"),
                (r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*[锛?]\s*(HIGH|MEDIUM|LOW)',
                 "manipulation_risk"),
            ],
            "news": [
                (r'(?:\*\*)?Article(?:s)?\s+Analyzed(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "article_count"),
                (r'(?:\*\*)?Key\s+Topics?(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "key_topics"),
                (r'(?:\*\*)?鍏抽敭涓婚(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "key_topics"),
                (r'(?:\*\*)?Sentiment\s+Bias(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "sentiment_bias"),
                (r'(?:\*\*)?Manipulation\s+Risk\s+Level(?:\*\*)?\s*[锛?]\s*(HIGH|MEDIUM|LOW)',
                 "manipulation_risk"),
            ],
            "fundamentals": [
                (r'(?:\*\*)?PE\s*Ratio(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "pe"),
                (r'(?:\*\*)?PB\s*Ratio(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "pb"),
                (r'(?:\*\*)?ROE(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "roe"),
                (r'(?:\*\*)?Revenue\s+Growth(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "revenue_growth"),
                (r'(?:\*\*)?EPS(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "eps"),
            ],
            "competitor": [
                (r'(?:\*\*)?Competitor(?:s)?\s+Analyzed(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "competitor_count"),
                (r'(?:\*\*)?绔炰簤瀵规墜(?:鏁皘鍒嗘瀽)(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "competitor_count"),
                (r'(?:\*\*)?Relative\s+Position(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "relative_position"),
                (r'(?:\*\*)?鐩稿鍦颁綅(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "relative_position"),
                (r'(?:\*\*)?Key\s+Competitor(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "key_competitor"),
                (r'(?:\*\*)?涓昏绔炰簤瀵规墜(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "key_competitor"),
            ],
            "partner": [
                (r'(?:\*\*)?Partner(?:s)?\s+Analyzed(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "partner_count"),
                (r'(?:\*\*)?鍚堜綔浼欎即(?:鏁皘鍒嗘瀽)(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "partner_count"),
                (r'(?:\*\*)?Supply\s+Chain\s+Risk(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "supply_chain_risk"),
                (r'(?:\*\*)?Key\s+Partner(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "key_partner"),
                (r'(?:\*\*)?涓昏鍚堜綔浼欎即(?:\*\*)?\s*[锛?]\s*(.+?)(?:\n|$)',
                 "key_partner"),
            ],
        }

        patterns = patterns_map.get(analyst_key, [])
        for pattern, key in patterns:
            try:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    value = m.group(1).strip()
                    if value and value not in ("N/A", "n/a", "-", "None", "null"):
                        metrics[key] = value
            except (re.error, TypeError) as regex_err:
                logger.warning("Regex error in _extract_key_metrics for %s pattern %s: %s", analyst_key, key, regex_err)

        return metrics

    def _extract_retracement_signal(self, chunk: Dict[str, Any]) -> dict | None:
        """Extract retracement signal from all 7 analyst reports in the chunk state."""
        report_keys = [
            "market_report", "capital_flow_report", "sentiment_report",
            "news_report", "fundamentals_report", "competitor_report", "partner_report",
        ]
        all_text = ""
        for report_key in report_keys:
            r = chunk.get(report_key, "")
            if r:
                all_text += str(r) + "\n"

        if not all_text.strip():
            return None

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
            try:
                m = re.search(pattern, all_text, re.IGNORECASE)
                if m:
                    value = m.group(1).strip()
                    if value and value not in ("N/A", "n/a", "-", "None", "null"):
                        retracement_scores[key] = value
            except (re.error, TypeError) as regex_err:
                logger.warning("Regex error in _extract_retracement_signal for %s: %s", key, regex_err)

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

        return retracement_scores if retracement_scores else None

    def _process_chunk(
        self,
        chunk: Dict[str, Any],
        prev_state: Dict[str, Any],
        pipeline_start: float,
        stage_times: Dict[str, float],
        current_stage_idx: int,
    ):
        """Process a single state chunk and yield SSE events for detected changes.
        Returns (new_prev_state, new_stage_idx).
        """
        # Detect stage transitions
        new_stage_idx = _detect_stage(chunk)
        if new_stage_idx > current_stage_idx:
            for si in range(current_stage_idx + 1, new_stage_idx + 1):
                stage_key, stage_label = STAGES[si]
                stage_times[stage_key] = time.time()
                yield _event("stage-start", {
                    "stage": stage_key,
                    "label": stage_label,
                    "elapsed": _elapsed_since(pipeline_start),
                })
                # When analysts stage starts, mark ALL selected analysts as in_progress
                if stage_key == "analysts":
                    for ak in self._selected_analysts:
                        yield _event("analyst-progress", {
                            "analyst": ak,
                            "status": "in_progress",
                            "elapsed_ms": _elapsed_since(pipeline_start),
                        })
            # Complete the previous stage (if any) on transition
            if current_stage_idx >= 0:
                prev_key, _prev_label = STAGES[current_stage_idx]
                yield _event("stage-complete", {
                    "stage": prev_key,
                    "elapsed": _elapsed_since(stage_times.get(prev_key, pipeline_start)),
                })
            current_stage_idx = new_stage_idx

        # Detect analyst reports
        for analyst_key, state_key in ANALYST_REPORT_KEYS.items():
            prev_val = prev_state.get(state_key, "")
            curr_val = chunk.get(state_key, "")
            if prev_val == "" and curr_val != "":
                elapsed = _elapsed_since(stage_times.get("analysts", pipeline_start))
                report_text = _clean_report(str(curr_val)[:50000])
                manipulation_risk = _extract_manipulation_risk(report_text)
                # Accumulate for markdown download
                if self.session_id in _session_results:
                    _session_results[self.session_id]["analyst_reports"][analyst_key] = report_text
                # Capture tool calls for this analyst
                self._capture_analyst_tools(analyst_key, chunk)
                # Extract key metrics from this analyst's report
                metrics = self._extract_key_metrics(analyst_key, report_text)
                if metrics and self.session_id in _session_results:
                    _session_results[self.session_id]["key_metrics"][analyst_key] = metrics
                yield _event("analyst-report", {
                    "analyst": analyst_key,
                    "name": ANALYST_NAMES[analyst_key],
                    "report": report_text,
                    "elapsed_ms": elapsed,
                    "color": _STAGE_COLORS.get(analyst_key, "#888"),
                    "manipulation_risk": manipulation_risk,
                    "metrics": metrics,
                })
                # Emit per-analyst progress event for the UI progress tracker
                yield _event("analyst-progress", {
                    "analyst": analyst_key,
                    "status": "complete",
                    "elapsed_ms": elapsed,
                })

                # Track completion count and emit analyst-stage fill progress
                self._analysts_completed.add(analyst_key)
                pct = int(len(self._analysts_completed) / max(self._analysts_total, 1) * 100)
                yield _event("stage-progress", {
                    "stage": "analysts",
                    "completed": len(self._analysts_completed),
                    "total": self._analysts_total,
                    "percent": pct,
                    "elapsed_ms": elapsed,
                })

                # Track completion count and emit analyst-stage fill progress
                self._analysts_completed.add(analyst_key)
                pct = int(len(self._analysts_completed) / max(self._analysts_total, 1) * 100)
                yield _event("stage-progress", {
                    "stage": "analysts",
                    "completed": len(self._analysts_completed),
                    "total": self._analysts_total,
                    "percent": pct,
                    "elapsed_ms": elapsed,
                })


        # Drain any progress events from the parallel analyst runner
        try:
            from capitalradar.graph.parallel_analyst_runner import drain_progress_events as _drain
            for pe in _drain():
                yield _event("analyst-progress", {
                    "analyst": pe["analyst"],
                    "status": pe["status"],
                    "elapsed_ms": pe.get("elapsed_ms", 0),
                })
        except Exception:
            pass

        # Extract retracement signal once all reports are available
        if self.session_id in _session_results and _session_results[self.session_id].get("retracement") is None:
            all_reports_present = all(
                chunk.get(k) for k in ANALYST_REPORT_KEYS.values()
            )
            if all_reports_present:
                retracement = self._extract_retracement_signal(chunk)
                if retracement:
                    _session_results[self.session_id]["retracement"] = retracement
                    _session_results[self.session_id]["key_metrics"]["retracement"] = retracement

        # Detect debate rounds (research)
        deb_state = chunk.get("investment_debate_state", {})
        prev_deb = prev_state.get("investment_debate_state", {})
        if isinstance(deb_state, dict):
            history = deb_state.get("history", "")
            prev_history = prev_deb.get("history", "") if isinstance(prev_deb, dict) else ""
            if len(history) > len(prev_history):
                new_content = history[len(prev_history):].strip()
                bull_len = len(deb_state.get("bull_history", ""))
                bear_len = len(deb_state.get("bear_history", ""))
                prev_bull_len = len(prev_deb.get("bull_history", "")) if isinstance(prev_deb, dict) else 0
                prev_bear_len = len(prev_deb.get("bear_history", "")) if isinstance(prev_deb, dict) else 0
                side = ""
                if bull_len > prev_bull_len:
                    side = "bull"
                elif bear_len > prev_bear_len:
                    side = "bear"
                round_num = deb_state.get("count", 0)
                # Accumulate for markdown download
                if self.session_id in _session_results:
                    _session_results[self.session_id]["research_debate_rounds"].append({
                        "side": side, "round": round_num, "content": new_content[:30000],
                    })
                yield _event("debate-round", {
                    "stage": "research",
                    "side": side,
                    "round": round_num,
                    "content": new_content[:30000],
                })

            judge = deb_state.get("judge_decision", "")
            prev_judge = prev_deb.get("judge_decision", "") if isinstance(prev_deb, dict) else ""
            if prev_judge == "" and judge != "":
                judge_text = str(judge)[:30000]
                if self.session_id in _session_results:
                    _session_results[self.session_id]["research_manager_decision"] = judge_text
                yield _event("debate-decision", {
                    "stage": "research",
                    "decision": judge_text,
                })

        # Detect trader proposal
        trader = chunk.get("trader_investment_plan")
        prev_trader = prev_state.get("trader_investment_plan")
        if prev_trader is None and trader is not None:
            prop_text = str(trader)[:30000]
            if self.session_id in _session_results:
                _session_results[self.session_id]["trader_proposal"] = prop_text
            yield _event("trader-proposal", {
                "proposal": prop_text,
            })

        # Detect risk debate
        risk_state = chunk.get("risk_debate_state", {})
        prev_risk = prev_state.get("risk_debate_state", {})
        if isinstance(risk_state, dict):
            risk_history = risk_state.get("history", "")
            prev_risk_history = prev_risk.get("history", "") if isinstance(prev_risk, dict) else ""
            if len(risk_history) > len(prev_risk_history):
                new_content = risk_history[len(prev_risk_history):].strip()
                agg_len = len(risk_state.get("aggressive_history", ""))
                con_len = len(risk_state.get("conservative_history", ""))
                prev_agg_len = len(prev_risk.get("aggressive_history", "")) if isinstance(prev_risk, dict) else 0
                prev_con_len = len(prev_risk.get("conservative_history", "")) if isinstance(prev_risk, dict) else 0
                side = ""
                if agg_len > prev_agg_len:
                    side = "aggressive"
                elif con_len > prev_con_len:
                    side = "conservative"
                else:
                    side = "neutral"
                # Accumulate for markdown download
                if self.session_id in _session_results:
                    _session_results[self.session_id]["risk_debate_rounds"].append({
                        "side": side, "round": risk_state.get("count", 0), "content": new_content[:30000],
                    })
                yield _event("debate-round", {
                    "stage": "risk",
                    "side": side,
                    "round": risk_state.get("count", 0),
                    "content": new_content[:30000],
                })

            risk_judge = risk_state.get("judge_decision", "")
            prev_risk_judge = prev_risk.get("judge_decision", "") if isinstance(prev_risk, dict) else ""
            if prev_risk_judge == "" and risk_judge != "":
                risk_text = str(risk_judge)[:30000]
                if self.session_id in _session_results:
                    _session_results[self.session_id]["risk_judge_decision"] = risk_text
                yield _event("debate-decision", {
                    "stage": "risk",
                    "decision": risk_text,
                })

        # Detect final decision (PM output — this is the real PM decision)
        final = chunk.get("final_trade_decision")
        prev_final = prev_state.get("final_trade_decision")
        if prev_final is None and final is not None:
            final_text = str(final)[:50000]
            rating = parse_rating(final_text)
            final_manipulation_risk = _extract_final_manipulation_summary(final_text)
            if self.session_id in _session_results:
                _session_results[self.session_id]["final_decision"] = final_text
                _session_results[self.session_id]["rating"] = rating
                _session_results[self.session_id]["final_manipulation_risk"] = final_manipulation_risk
            yield _event("final-decision", {
                "decision": final_text,
                "rating": rating,
                "manipulation_risk": final_manipulation_risk,
            })

        return chunk, current_stage_idx


def _detect_stage(state: Dict[str, Any]) -> int:
    """Return the index of the furthest-reached pipeline stage (0-4)."""
    has_analyst_report = any(
        state.get(k) for k in ANALYST_REPORT_KEYS.values()
    )
    if not has_analyst_report:
        return -1

    deb_state = state.get("investment_debate_state", {})
    has_debate_decision = bool(
        isinstance(deb_state, dict) and deb_state.get("judge_decision")
    )
    if not has_debate_decision:
        return 0  # analysts stage

    has_trader = state.get("trader_investment_plan") is not None
    if not has_trader:
        return 1  # research stage

    risk_state = state.get("risk_debate_state", {})
    # Transition from "trader" to "risk" when risk debate actually starts
    # (any history/rounds present), not when the final decision is made
    has_risk_started = bool(
        isinstance(risk_state, dict) and (
            risk_state.get("history") or
            risk_state.get("aggressive_history") or
            risk_state.get("conservative_history") or
            risk_state.get("neutral_history")
        )
    )
    if not has_risk_started:
        return 2  # trader stage (Trader completed, risk debate not yet started)

    has_risk_decision = bool(
        isinstance(risk_state, dict) and risk_state.get("judge_decision")
    )
    if not has_risk_decision:
        return 3  # risk stage (debate in progress)

    has_final = state.get("final_trade_decision") is not None
    if not has_final:
        return 3  # risk stage (still debating / PM working)

    return 4  # portfolio manager / complete


def generate_markdown(session_id: str) -> str:
    """Generate a downloadable markdown report from accumulated session results."""
    data = _session_results.get(session_id)
    if not data:
        # Fallback to persisted store (DOCX export uses this)
        try:
            from web.docx_export import _get_persisted
            data = _get_persisted(session_id)
        except Exception:
            pass
    if not data:
        return "# No results found for this session."

    lines = []
    a = lines.append

    a(f"# CapitalRadar Analysis Report")
    a("")
    a(f"**Ticker:** {data.get('ticker', 'N/A')}  ")
    a(f"**Date:** {data.get('date', 'N/A')}  ")
    a(f"**Rating:** {data.get('rating', 'Hold')}  ")
    a("")
    a("---")
    a("")

    # Analyst Reports
    reports = data.get("analyst_reports", {})
    if reports:
        a("## Analyst Reports")
        a("")
        from capitalradar.catalog import ANALYST_ORDER, ANALYST_ROLES
        analyst_titles = {key: ANALYST_ROLES[key].label for key in ANALYST_ORDER}
        for key in ANALYST_ORDER:
            report = reports.get(key, "")
            if report:
                a(f"### {analyst_titles.get(key, key)}")
                a("")
                a(report)
                a("")
                a("---")
                a("")

    # Key Metrics
    metric_labels = {
        "capital_flow": "Capital Flow",
        "market": "Market",
        "social": "Sentiment",
        "news": "News",
        "fundamentals": "Fundamentals",
        "competitor": "Competitor",
        "partner": "Partner",
    }
    key_metrics = data.get("key_metrics", {})
    if key_metrics:
        a("## Key Metrics")
        a("")
        for analyst_key in ["capital_flow", "market", "social", "news", "fundamentals", "competitor", "partner"]:
            kv = key_metrics.get(analyst_key, {})
            if kv:
                a(f"### {metric_labels.get(analyst_key, analyst_key)}")
                a("")
                a("| Metric | Value |")
                a("|--------|-------|")
                for k, v in kv.items():
                    a(f"| {k} | {v} |")
                a("")
        a("---")
        a("")

    # Analyst Tool Traces
    tool_traces = data.get("analyst_tool_traces", {})
    if tool_traces:
        a("## Analyst Tool Traces")
        a("")
        for analyst_key in ["capital_flow", "market", "social", "news", "fundamentals", "competitor", "partner"]:
            traces = tool_traces.get(analyst_key, [])
            if traces:
                a(f"### {metric_labels.get(analyst_key, analyst_key)} 鈥?{len(traces)} tool calls")
                a("")
                for t in traces:
                    a(f"- **{t.get('tool_name', 'unknown')}**")
                    args_str = ", ".join(f"{k}={v}" for k, v in t.get("tool_args", {}).items())
                    a(f"  Args: `{args_str[:500]}`")
                    result = str(t.get("result_snippet", ""))[:300]
                    if result:
                        a(f"  Result: {result}")
                a("")
        a("---")
        a("")

    # Research Debate
    a("## Research Debate")
    a("")
    research_rounds = data.get("research_debate_rounds", [])
    if research_rounds:
        side_names = {"bull": "Bull Researcher", "bear": "Bear Researcher"}
        last_round = 0
        for r in research_rounds:
            if r["round"] != last_round:
                if last_round > 0:
                    a("")
                a(f"### Round {r['round']}")
                a("")
                last_round = r["round"]
            a(f"**{side_names.get(r['side'], r['side'])}:** {r['content']}")
            a("")

    rm_decision = data.get("research_manager_decision", "")
    if rm_decision:
        a("### Research Manager Decision")
        a("")
        a(rm_decision)
    a("")
    a("---")
    a("")

    # Checkpoint 1
    cp1 = data.get("checkpoint_1_answer", "")
    if cp1:
        a("## Checkpoint 1 鈥?Human Feedback (after Research Manager)")
        a("")
        a(f"> {cp1}")
        a("")
        a("---")
        a("")

    # Trader
    trader = data.get("trader_proposal", "")
    if trader:
        a("## Trader Proposal")
        a("")
        a(trader)
        a("")
        a("---")
        a("")

    # Risk Debate
    risk_rounds = data.get("risk_debate_rounds", [])
    if risk_rounds:
        a("## Risk Management Debate")
        a("")
        side_names = {
            "aggressive": "Aggressive Risk Analyst",
            "conservative": "Conservative Risk Analyst",
            "neutral": "Neutral Risk Analyst",
        }
        last_round = 0
        for r in risk_rounds:
            if r["round"] != last_round:
                if last_round > 0:
                    a("")
                a(f"### Round {r['round']}")
                a("")
                last_round = r["round"]
            a(f"**{side_names.get(r['side'], r['side'])}:** {r['content']}")
            a("")

    risk_judge = data.get("risk_judge_decision", "")
    if risk_judge:
        a("### Risk Manager Decision")
        a("")
        a(risk_judge)
    a("")
    a("---")
    a("")

    # Checkpoint 2
    cp2 = data.get("checkpoint_2_answer", "")
    if cp2:
        a("## Checkpoint 2 鈥?Human Feedback (after Portfolio Manager)")
        a("")
        a(f"> {cp2}")
        a("")
        a("---")
        a("")

    # Investment Plan
    inv_plan = data.get("investment_plan", "")
    if inv_plan:
        a("## Portfolio Manager Investment Plan")
        a("")
        a(inv_plan)
        a("")
        a("---")
        a("")

    # Final Decision
    a("## Final Decision")
    a("")
    a(f"**Rating: {data.get('rating', 'Hold')}**")
    a("")
    final = data.get("final_decision", "")
    if final:
        a(final)
    a("")
    a("---")
    a("")
    a("*Report generated by CapitalRadar*")

    return "\n".join(lines)

