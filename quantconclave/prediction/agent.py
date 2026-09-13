"""PredictionAgent orchestrator.

Coordinates Tool A (Price), Tool B (Direction), and Tool C (Behavior),
runs cross-validation, and produces a synthesized PredictionReport.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from quantconclave.dataflows.config import get_config as get_runtime_config
from quantconclave.prediction.feature_engine import FeatureEngine
from quantconclave.prediction.price_predictor import PricePredictor
from quantconclave.prediction.direction_predictor import DirectionPredictor
from quantconclave.prediction.behavior_predictor import BehaviorPredictor
from quantconclave.prediction.schemas import (
    PredictionReport, ConfidenceTier, CrossValidationVerdict,
)

logger = logging.getLogger(__name__)


class PredictionAgent:
    """Orchestrate ML + LLM prediction and return a structured report.

    Usage::

        agent = PredictionAgent()
        report = agent.predict("601127.SH", "2026-06-24", config)
        print(report.to_markdown())
    """

    def __init__(self, models_dir: str = ""):
        self._feature_engine = FeatureEngine()
        self._price = PricePredictor(models_dir)
        self._direction = DirectionPredictor(models_dir)
        self._behavior = BehaviorPredictor()

    # ── Public API ──────────────────────────────────────────────────

    def predict(
        self, ticker: str, trade_date: str, config: dict,
    ) -> PredictionReport:
        """Run full prediction pipeline.

        Args:
            ticker: Stock ticker (e.g. "601127.SH").
            trade_date: Analysis date in YYYY-MM-DD format.
            config: QuantConclave config dict.

        Returns:
            PredictionReport with all tool outputs + synthesis.
        """
        errors: list[str] = []
        current_price: float | None = None

        # ── Build features ──
        try:
            features = self._feature_engine.build_features(
                ticker, trade_date, config,
            )
        except Exception as e:
            logger.exception("FeatureEngine failed for %s", ticker)
            return PredictionReport(
                ticker=ticker, trade_date=trade_date,
                errors=[f"FeatureEngine failed: {e}"],
            )

        # Get current price from features context (last close)
        try:
            from quantconclave.dataflows.interface import route_to_vendor
            rt_raw = route_to_vendor("get_realtime_quote", symbol=ticker)
            import re
            m = re.search(r"Current Price:\s*([\d.]+)", str(rt_raw))
            if m:
                current_price = float(m.group(1))
        except Exception:
            current_price = None

        # ── Run Tool A: Price ──
        price_results = {}
        try:
            price_results = self._price.predict(
                features, ticker, trade_date, current_price,
            )
        except Exception as e:
            logger.exception("PricePredictor failed")
            errors.append(f"PricePredictor: {e}")

        # ── Run Tool B: Direction ──
        dir_results = {}
        try:
            dir_results = self._direction.predict(features, ticker, trade_date)
        except Exception as e:
            logger.exception("DirectionPredictor failed")
            errors.append(f"DirectionPredictor: {e}")

        # ── Run Tool C: Behavior ──
        behavior_output = None
        try:
            context = self._build_behavior_context(ticker, trade_date, config)
            behavior_output = self._behavior.predict(
                ticker, trade_date, context, config,
            )
        except Exception as e:
            logger.exception("BehaviorPredictor failed")
            errors.append(f"BehaviorPredictor: {e}")

        # ── Cross-validation ──
        dir_short = dir_results.get("short")
        price_short = price_results.get("short")

        price_direction_up = None
        if price_short and price_short.model_available and price_short.median > 0:
            price_direction_up = price_short.median > current_price if current_price else None

        dir_up = dir_short.up_probability > 0.50 if dir_short and dir_short.model_available else None

        cv = self._cross_validate(
            price_up=price_direction_up,
            dir_up=dir_up,
            behavior_phase=behavior_output.current_phase.value if behavior_output else None,
            price_avail=price_short.model_available if price_short else False,
            dir_avail=dir_short.model_available if dir_short else False,
            beh_avail=behavior_output is not None and behavior_output.model_available,
        )

        # ── LLM synthesis ──
        synthesis_summary = ""
        synthesis_guidance = ""
        try:
            synthesis = self._llm_synthesis(
                ticker, trade_date, current_price,
                price_results, dir_results, behavior_output, cv, config,
            )
            synthesis_summary = synthesis.get("summary", "")
            synthesis_guidance = synthesis.get("guidance", "")
        except Exception as e:
            logger.exception("LLM synthesis failed")
            errors.append(f"Synthesis: {e}")

        # ── Overall confidence ──
        overall = self._overall_confidence(
            dir_short, cv["verdict"], errors,
        )

        return PredictionReport(
            ticker=ticker,
            trade_date=trade_date,
            current_price=current_price,
            price_short=price_results.get("short"),
            price_medium=price_results.get("medium"),
            direction_short=dir_results.get("short"),
            direction_medium=dir_results.get("medium"),
            behavior=behavior_output,
            cross_validation=cv["verdict"],
            cross_validation_detail=cv["detail"],
            synthesis_summary=synthesis_summary,
            synthesis_guidance=synthesis_guidance,
            overall_confidence=overall,
            errors=errors,
        )

    # ── Cross-validation ───────────────────────────────────────────

    @staticmethod
    def _cross_validate(
        price_up: bool | None,
        dir_up: bool | None,
        behavior_phase: str | None,
        price_avail: bool,
        dir_avail: bool,
        beh_avail: bool,
    ) -> dict:
        """Check consistency across the three tools."""
        if not price_avail:
            return {
                "verdict": CrossValidationVerdict.PRICE_MODEL_MISSING,
                "detail": "Price model unavailable; using direction + behavior only.",
            }

        if price_up is not None and dir_up is not None:
            if price_up == dir_up:
                return {
                    "verdict": CrossValidationVerdict.ALIGNED,
                    "detail": f"Price and direction agree: {'bullish' if price_up else 'bearish'}.",
                }
            else:
                return {
                    "verdict": CrossValidationVerdict.CONFLICT,
                    "detail": (
                        f"Price model says {'up' if price_up else 'down'}, "
                        f"direction model says {'up' if dir_up else 'down'}. "
                        "Treat with caution."
                    ),
                }

        if dir_up is None and price_up is None:
            return {
                "verdict": CrossValidationVerdict.CONFLICT,
                "detail": "Both models returned no directional signal.",
            }

        return {
            "verdict": CrossValidationVerdict.ALIGNED,
            "detail": "Single-model direction (other unavailable).",
        }

    # ── Behavior context builder ───────────────────────────────────

    @staticmethod
    def _build_behavior_context(
        ticker: str, trade_date: str, config: dict,
    ) -> dict:
        """Gather Smart Money Score + analyst reports for Tool C."""
        context: dict = {"smart_money": {}, "capital_flow_report": "",
                          "analyst_reports": {}}
        try:
            from quantconclave.sector_scan.smart_money_score import detect_smart_money
            sm_result = detect_smart_money(ticker, config)
            context["smart_money"] = sm_result
        except Exception:
            pass
        return context

    # ── LLM synthesis ──────────────────────────────────────────────

    @staticmethod
    def _llm_synthesis(
        ticker: str, trade_date: str, current_price: float | None,
        price_results: dict, dir_results: dict,
        behavior_output, cv: dict, config: dict,
    ) -> dict[str, str]:
        """Generate a Chinese-language synthesis of all prediction outputs."""
        from quantconclave.llm_clients import create_llm_client, resolve_role_llm

        # Build a compact summary of tool outputs for the LLM
        parts = [f"Ticker: {ticker}", f"Date: {trade_date}"]
        if current_price:
            parts.append(f"Current Price: {current_price:.2f}")

        for label, d in [("Short-term (5d)", dir_results.get("short")),
                          ("Medium-term (20d)", dir_results.get("medium"))]:
            if d and d.model_available:
                parts.append(
                    f"{label} Direction: {d.up_probability:.0%} up / "
                    f"{d.down_probability:.0%} down (confidence: {d.confidence.value})"
                )

        for label, p in [("Short-term (5d)", price_results.get("short")),
                          ("Medium-term (20d)", price_results.get("medium"))]:
            if p and p.model_available:
                parts.append(
                    f"{label} Price Range: {p.lower_bound:.2f} – "
                    f"{p.upper_bound:.2f} (median: {p.median:.2f})"
                )

        if behavior_output and behavior_output.model_available:
            parts.append(
                f"Behavior Phase: {behavior_output.current_phase.value} "
                f"({behavior_output.phase_confidence.value} confidence)"
            )
            parts.append(f"Predicted Next Move: {behavior_output.predicted_next_behavior}")

        parts.append(f"Cross-Validation: {cv['verdict'].value} — {cv['detail']}")

        prompt = (
            "You are a senior investment advisor synthesizing three independent "
            "prediction models for a Chinese A-share stock.  Write in Chinese.\n\n"
            + "\n".join(parts)
            + "\n\nReturn a JSON with:\n"
            "- summary: 2-3 paragraph synthesis of what all models are saying, "
            "noting convergence or divergence\n"
            "- guidance: 1-2 sentences of actionable guidance for the Portfolio Manager"
        )

        try:
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
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Synthesize the prediction outputs."},
            ]
            response = llm.invoke(messages)
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            return {"summary": f"Synthesis unavailable: {e}", "guidance": ""}

        # Parse JSON
        try:
            import re
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end + 1])
                return {
                    "summary": parsed.get("summary", text[:500]),
                    "guidance": parsed.get("guidance", ""),
                }
        except json.JSONDecodeError:
            pass
        return {"summary": text[:500], "guidance": ""}

    # ── Confidence aggregation ─────────────────────────────────────

    @staticmethod
    def _overall_confidence(
        dir_short, cv_verdict: CrossValidationVerdict, errors: list[str],
    ) -> ConfidenceTier:
        """Compute overall confidence from cross-validation and tool outputs."""
        if errors:
            return ConfidenceTier.LOW
        if cv_verdict == CrossValidationVerdict.CONFLICT:
            return ConfidenceTier.LOW
        if cv_verdict == CrossValidationVerdict.PRICE_MODEL_MISSING:
            return ConfidenceTier.LOW
        if dir_short and dir_short.model_available:
            margin = abs(dir_short.up_probability - 0.50)
            if margin >= 0.20:
                return ConfidenceTier.HIGH
            if margin >= 0.05:
                return ConfidenceTier.MEDIUM
        return ConfidenceTier.LOW
