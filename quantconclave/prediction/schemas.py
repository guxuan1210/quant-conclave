"""Pydantic schemas for PredictionAgent outputs."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────


class PredictionHorizon(str, Enum):
    SHORT = "5d"
    MEDIUM = "20d"


class ConfidenceTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BehaviorPhase(str, Enum):
    ACCUMULATION = "accumulation"
    SHAKEOUT = "shakeout"
    MARKUP = "markup"
    DISTRIBUTION = "distribution"
    EXIT = "exit"
    UNKNOWN = "unknown"


class CrossValidationVerdict(str, Enum):
    ALIGNED = "aligned"
    CONFLICT = "conflict"
    BEHAVIOR_MISMATCH = "behavior_model_mismatch"
    PRICE_MODEL_MISSING = "price_model_missing"


# ── Tool outputs ──────────────────────────────────────────────────────


class PriceRangeOutput(BaseModel):
    """Tool A: predicted price confidence interval."""
    horizon: PredictionHorizon
    lower_bound: float
    upper_bound: float
    median: float
    current_price: float
    interval_width_pct: float
    confidence: ConfidenceTier
    model_available: bool = True
    model_version: str = ""


class DirectionOutput(BaseModel):
    """Tool B: calibrated up/down probability."""
    horizon: PredictionHorizon
    up_probability: float
    down_probability: float
    confidence: ConfidenceTier
    calibration_score: float = 0.0
    model_available: bool = True
    model_version: str = ""


class HistoricalAnalog(BaseModel):
    """A past trade with similar Smart Money Score patterns."""
    date: str
    ticker: str
    phase: str
    outcome: str
    raw_return: Optional[float] = None


class BehaviorOutput(BaseModel):
    """Tool C: institutional behavior phase + next-move prediction."""
    current_phase: BehaviorPhase
    phase_confidence: ConfidenceTier
    predicted_next_behavior: str
    behavior_rationale: str
    historical_analogs: list[HistoricalAnalog] = Field(default_factory=list)
    risk_scenario: str = ""
    model_available: bool = True


# ── Aggregate report ──────────────────────────────────────────────────


class PredictionReport(BaseModel):
    """Full prediction output from PredictionAgent.predict()."""
    ticker: str
    trade_date: str
    current_price: Optional[float] = None

    # Tool outputs
    price_short: Optional[PriceRangeOutput] = None
    price_medium: Optional[PriceRangeOutput] = None
    direction_short: Optional[DirectionOutput] = None
    direction_medium: Optional[DirectionOutput] = None
    behavior: Optional[BehaviorOutput] = None

    # Cross-validation
    cross_validation: CrossValidationVerdict = CrossValidationVerdict.ALIGNED
    cross_validation_detail: str = ""

    # LLM synthesis
    synthesis_summary: str = ""       # 2-3 paragraph Chinese summary
    synthesis_guidance: str = ""      # actionable advice for PM
    overall_confidence: ConfidenceTier = ConfidenceTier.LOW

    # Errors
    errors: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the prediction report as markdown for PM/advisory consumption."""
        lines = [
            f"# Prediction Report: {self.ticker}",
            f"**Analysis Date:** {self.trade_date}",
            "",
        ]
        if self.current_price:
            lines.append(f"**Current Price:** {self.current_price:.2f}")
            lines.append("")

        if self.errors:
            lines.append("## Errors")
            for e in self.errors:
                lines.append(f"- {e}")
            lines.append("")

        if self.direction_short or self.direction_medium:
            lines.append("## Direction Forecast")
            lines.append("")
            lines.append("| Horizon | Up Probability | Down Probability | Confidence | Historical Accuracy |")
            lines.append("|---------|---------------|------------------|------------|---------------------|")
            for d in [self.direction_short, self.direction_medium]:
                if d:
                    acc_str = f"{d.calibration_score:.0%}" if d.calibration_score > 0 else "N/A"
                    lines.append(
                        f"| {d.horizon.value} | {d.up_probability:.0%} | "
                        f"{d.down_probability:.0%} | {d.confidence.value} | {acc_str} |"
                    )
            lines.append("")

        if self.price_short or self.price_medium:
            lines.append("## Price Range Forecast")
            lines.append("")
            lines.append("| Horizon | Lower | Median | Upper | Width | Confidence |")
            lines.append("|---------|-------|--------|-------|-------|------------|")
            for p in [self.price_short, self.price_medium]:
                if p:
                    lines.append(
                        f"| {p.horizon.value} | {p.lower_bound:.2f} | "
                        f"{p.median:.2f} | {p.upper_bound:.2f} | "
                        f"{p.interval_width_pct:.1f}% | {p.confidence.value} |"
                    )
            lines.append("")

        if self.behavior:
            lines.append("## Institutional Behavior Analysis")
            lines.append("")
            lines.append(f"**Current Phase:** {self.behavior.current_phase.value}")
            lines.append(f"**Phase Confidence:** {self.behavior.phase_confidence.value}")
            lines.append(f"**Predicted Next Move:** {self.behavior.predicted_next_behavior}")
            lines.append(f"**Rationale:** {self.behavior.behavior_rationale}")
            if self.behavior.risk_scenario:
                lines.append(f"**Risk Scenario:** {self.behavior.risk_scenario}")
            lines.append("")

        lines.append(f"**Cross-Validation:** {self.cross_validation.value}")
        if self.cross_validation_detail:
            lines.append(f"  {self.cross_validation_detail}")
        lines.append(f"**Overall Confidence:** {self.overall_confidence.value}")
        lines.append("")

        if self.synthesis_summary:
            lines.append("## Synthesis")
            lines.append("")
            lines.append(self.synthesis_summary)
            lines.append("")

        if self.synthesis_guidance:
            lines.append("## Guidance for Portfolio Manager")
            lines.append("")
            lines.append(self.synthesis_guidance)
            lines.append("")

        return "\n".join(lines)
