"""Tool C: LLM-powered institutional behavior prediction.

Classifies the current lifecycle phase of major capital (accumulation,
shakeout, markup, distribution, exit) and predicts the most likely next
move by combining Smart Money Score data with historical pattern matching
from the Memory Log.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from quantconclave.agents.utils.memory import QuantConclaveMemoryLog
from quantconclave.prediction.schemas import (
    BehaviorOutput, BehaviorPhase, ConfidenceTier, HistoricalAnalog,
)

logger = logging.getLogger(__name__)

# ── LLM prompt template ─────────────────────────────────────────────

_BEHAVIOR_SYSTEM_PROMPT = """You are a veteran institutional trader specialized in
detecting smart-money lifecycle patterns in Chinese A-share markets.

Given the Smart Money Score gate results, capital flow data, and recent
analyst reports, you must:

1. Classify the CURRENT phase: accumulation / shakeout / markup / distribution / exit
2. Predict the most likely NEXT behavior pattern
3. Identify the key risk scenario

Follow the Wyckoff cycle model.  Chinese A-share institutional behavior
tends to follow a 4-stage cycle: 建仓 (accumulation) → 洗盘 (shakeout)
→ 拉升 (markup) → 出货 (distribution) → 退出 (exit).

Current ticker: {ticker}
Analysis date: {trade_date}

Smart Money Score (4-gate validation):
{smart_money_context}

Capital Flow Analyst Report:
{capital_flow_report}

Other Analyst Context:
{analyst_context}

Historical Analogs (past trades with similar patterns):
{historical_analogs}

Return a JSON object with EXACTLY these keys:
- current_phase: one of "accumulation" / "shakeout" / "markup" / "distribution" / "exit" / "unknown"
- phase_confidence: one of "high" / "medium" / "low"
- predicted_next_behavior: 2-3 sentences describing what major capital is most likely to do next
- behavior_rationale: 2-3 sentences explaining WHY (cite specific gate results or flow data)
- risk_scenario: the key tail risk investors should watch for

Write in Chinese.
"""


class BehaviorPredictor:
    """Predict institutional behavior phase and next move using LLM.

    Combines Smart Money Score gate results, capital flow lifecycle
    stage, analyst reports, and historical pattern matching from the
    Memory Log into a structured LLM prompt.

    Usage::

        bp = BehaviorPredictor()
        result = bp.predict(ticker, date, context, config)
        # result → BehaviorOutput(current_phase=DISTRIBUTION, ...)
    """

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────────────

    def predict(
        self, ticker: str, trade_date: str, context: dict, config: dict,
    ) -> BehaviorOutput:
        """Run behavior prediction.

        Args:
            ticker: Stock ticker.
            trade_date: Analysis date.
            context: Dict with smart_money, capital_flow_report, analyst_reports.
            config: QuantConclave config dict (for LLM + memory log paths).

        Returns:
            BehaviorOutput with phase classification and next-move prediction.
        """
        # Historical pattern matching
        memory_log = QuantConclaveMemoryLog(config)
        sm = context.get("smart_money", {})
        current_stage = sm.get("stage", "")
        analogs = self._find_historical_analogs(
            ticker, memory_log, current_stage,
        )

        # Build prompt and call LLM
        prompt_text = self._build_llm_prompt(ticker, trade_date, context, analogs)
        try:
            llm_response = self._call_llm(prompt_text, config)
            parsed = self._parse_llm_response(llm_response)
        except Exception as e:
            logger.error("Behavior LLM call failed: %s", e)
            return BehaviorOutput(
                current_phase=BehaviorPhase.UNKNOWN,
                phase_confidence=ConfidenceTier.LOW,
                predicted_next_behavior="LLM call failed",
                behavior_rationale=str(e)[:100],
                model_available=True,
            )

        # Build historical analog Pydantic models
        analog_models = [
            HistoricalAnalog(
                date=a["date"], ticker=a["ticker"],
                phase=a.get("phase", ""), outcome=a.get("outcome", ""),
                raw_return=a.get("raw_return"),
            )
            for a in analogs
        ]

        return BehaviorOutput(
            current_phase=BehaviorPhase(parsed.get("current_phase", "unknown")),
            phase_confidence=ConfidenceTier(parsed.get("phase_confidence", "low")),
            predicted_next_behavior=parsed.get("predicted_next_behavior", ""),
            behavior_rationale=parsed.get("behavior_rationale", ""),
            historical_analogs=analog_models,
            risk_scenario=parsed.get("risk_scenario", ""),
            model_available=True,
        )

    # ── Internal: prompt building ──────────────────────────────────

    def _build_llm_prompt(
        self, ticker: str, trade_date: str, context: dict,
        analogs: list[dict] | None = None,
    ) -> str:
        """Build the LLM prompt with all available context."""
        sm = context.get("smart_money", {})
        gates = sm.get("gates", {})

        # Format Smart Money Score context compactly
        sm_lines = [
            f"Verdict: {sm.get('verdict', 'unknown')}",
            f"Stage: {sm.get('stage', 'unknown')}",
            f"Confidence: {sm.get('confidence', 'low')}",
            "",
            "Gate Results:",
        ]
        for name, g in gates.items():
            passed = "PASS" if g.get("passed") else "FAIL"
            sm_lines.append(f"  {name}: {passed} — {g.get('detail', '')}")
        sm_context = "\n".join(sm_lines)

        # Capital flow report (truncated)
        cap_report = context.get("capital_flow_report", "")[:4000]

        # Analyst context
        analyst_reports = context.get("analyst_reports", {})
        analyst_parts = []
        for key, report in analyst_reports.items():
            if report:
                analyst_parts.append(f"--- {key} ---\n{str(report)[:2000]}")
        analyst_context = "\n\n".join(analyst_parts) if analyst_parts else "No additional analyst context."

        # Historical analogs
        if analogs:
            analog_lines = ["| Date | Ticker | Phase | Outcome | Return |",
                            "|------|--------|-------|---------|--------|"]
            for a in analogs[:3]:
                ret_str = f"{a.get('raw_return', 0):+.1%}" if a.get("raw_return") is not None else "N/A"
                analog_lines.append(
                    f"| {a['date']} | {a['ticker']} | {a.get('phase', '')} | "
                    f"{a.get('outcome', '')[:50]} | {ret_str} |"
                )
            analog_text = "\n".join(analog_lines)
        else:
            analog_text = "No historical analogs available."

        return _BEHAVIOR_SYSTEM_PROMPT.format(
            ticker=ticker,
            trade_date=trade_date,
            smart_money_context=sm_context,
            capital_flow_report=cap_report,
            analyst_context=analyst_context,
            historical_analogs=analog_text,
        )

    # ── Internal: LLM call ─────────────────────────────────────────

    def _call_llm(self, prompt_text: str, config: dict) -> str:
        """Call the LLM with the behavior prediction prompt."""
        from quantconclave.llm_clients import create_llm_client, resolve_role_llm
        from quantconclave.dataflows.config import get_config as _get_cfg

        cfg = config or {}
        if not cfg.get("llm_provider"):
            cfg = _get_cfg()
        provider, model, _ = resolve_role_llm(cfg, "quick", model_default=cfg.get("deep_think_llm", ""))
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=cfg.get("backend_url"),
        )
        llm = client.get_llm()
        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": "Analyze the current institutional behavior phase and predict the next move."},
        ]
        response = llm.invoke(messages)
        return response.content if hasattr(response, "content") else str(response)

    # ── Internal: parsing ──────────────────────────────────────────

    @staticmethod
    def _parse_llm_response(response_text: str) -> dict:
        """Parse the LLM's JSON response, with robust fallback."""
        # Try to extract JSON from the response (may be wrapped in markdown)
        text = response_text.strip()
        if "```" in text:
            # Extract code block
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse behavior LLM response as JSON")
            return {
                "current_phase": "unknown",
                "phase_confidence": "low",
                "predicted_next_behavior": response_text[:200],
                "behavior_rationale": "",
                "risk_scenario": "",
            }
        return parsed

    # ── Internal: historical analogs ───────────────────────────────

    def _find_historical_analogs(
        self, ticker: str, memory_log: QuantConclaveMemoryLog,
        current_stage: str, max_analogs: int = 3,
    ) -> list[dict]:
        """Find past Memory Log entries with similar patterns."""
        try:
            entries = memory_log.load_entries()
        except Exception:
            return []

        # Prefer same-ticker entries, then cross-ticker
        same_ticker = [e for e in entries if e.get("ticker") == ticker and not e.get("pending")]
        cross_ticker = [e for e in entries if e.get("ticker") != ticker and not e.get("pending")]

        # Rank by: has outcome data, recency
        def sort_key(e):
            has_outcome = 1 if e.get("raw_return") is not None else 0
            return (has_outcome, e.get("date", ""))

        same_ticker.sort(key=sort_key, reverse=True)
        cross_ticker.sort(key=sort_key, reverse=True)

        combined = same_ticker[:max_analogs]
        if len(combined) < max_analogs:
            combined += cross_ticker[:max_analogs - len(combined)]

        analogs = []
        for e in combined:
            # Extract a short outcome description from reflection or decision
            outcome = ""
            reflection = e.get("reflection", "")
            if reflection:
                outcome = reflection[:100]
            else:
                decision = e.get("decision", "")
                if decision:
                    outcome = decision[:100]
            phase = current_stage or "unknown"
            analogs.append({
                "date": e.get("date", ""),
                "ticker": e.get("ticker", ""),
                "phase": phase,
                "outcome": outcome,
                "raw_return": e.get("raw_return"),
            })
        return analogs
