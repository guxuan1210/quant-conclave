# PredictionAgent Design Spec

**Date:** 2026-06-24
**Status:** Draft
**Author:** CapitalRadar Team

## 1. Overview

A new `PredictionAgent` for CapitalRadar that forecasts short-term (1-5 trading days)
and medium-term (1-4 weeks) price direction, price range, and institutional behavior
patterns. The agent is designed to be called by the existing Advisor/Portfolio Manager
system as a tool, and also usable standalone.

### 1.1 Why this matters

CapitalRadar today is **100% backward-looking** — it detects what major capital
is CURRENTLY doing but makes no systematic forward projection. Adding prediction
fills the last missing piece of the decision chain:

```
Detect (Smart Money Score) → Analyze (Analyst Team) → Predict (NEW) → Decide (PM)
```

### 1.2 Key design principles

- **Independent module** — lives in `capitalradar/prediction/`, does not touch
  the existing graph pipeline. Called via a single entry point.
- **ML for numbers, LLM for reasoning** — gradient boosting for price/direction
  probabilities; LLM for behavior pattern recognition and synthesis.
- **Calibrated outputs** — every prediction includes a confidence score and
  a flag when tools disagree (cross-validation).
- **Data flywheel** — Memory Log entries with resolved outcomes become training
  samples over time.

## 2. Architecture

```
capitalradar/prediction/
├── __init__.py              # Public API: PredictionAgent class
├── agent.py                 # Orchestrator: runs A/B/C, synthesizes, returns report
├── schemas.py               # Pydantic models: PredictionReport, PriceTarget, etc.
├── price_predictor.py       # Tool A: XGBoost quantile regression
├── direction_predictor.py   # Tool B: XGBoost classifier (probability-calibrated)
├── behavior_predictor.py    # Tool C: LLM + pattern matching
├── feature_engine.py        # Shared feature engineering pipeline
├── model_registry.py        # Model loading, versioning, fallback
├── trainer.py               # Offline training / retraining script
└── data_pipeline.py         # Historical data collection for training
```

### 2.1 Component boundaries

| Component | Responsibility | Dependencies |
|-----------|---------------|--------------|
| `PredictionAgent` (agent.py) | Orchestrates A/B/C, runs cross-validation, builds final report | All three tools |
| `PricePredictor` | Outputs confidence interval for next N days | `feature_engine`, trained model |
| `DirectionPredictor` | Outputs up/down probability with calibration | `feature_engine`, trained model |
| `BehaviorPredictor` | Classifies current phase, predicts next move | LLM, Memory Log, Smart Money Score |
| `FeatureEngine` | Builds feature vectors from raw data | tushare, akshare, yfinance |
| `ModelRegistry` | Loads correct model version, handles missing models | Filesystem |
| `Trainer` | Offline batch training | `data_pipeline`, `feature_engine` |
| `DataPipeline` | Collects historical OHLCV + capital flow + fundamentals | Existing dataflows |

## 3. Tool A: Price Range Predictor

### 3.1 Model
- **XGBoost quantile regression** — two models per time horizon (5d, 20d):
  - `q10` model: predicts 10th percentile (lower bound)
  - `q90` model: predicts 90th percentile (upper bound)
  - `q50` model: predicts median (point estimate, for reference)
- Why XGBoost over LSTM: tabular features dominate; training is fast; no GPU needed;
  handles missing data natively; well-calibrated quantile loss.

### 3.2 Features (shared with Tool B)
- **Price features:** 5d/10d/20d return, volatility, max drawdown in window
- **Volume features:** volume ratio vs 20d avg, volume trend (rising/falling)
- **Capital flow features:** net inflow 5d/20d, big-order direction, margin trend,
  northbound flow direction (from Smart Money Score + capital flow data)
- **Technical features:** RSI(14), MACD histogram, price vs MA20/MA50 distance (%),
  Bollinger position, ATR(14)
- **Market context:** sector RPS percentile, RRG quadrant, market-wide northbound flow
- **Valuation:** PE percentile vs sector, PB percentile vs sector

### 3.3 Output
```python
{
    "horizon": "5d",
    "lower_bound": 58.50,       # q10
    "upper_bound": 64.20,       # q90
    "median": 61.00,            # q50
    "interval_width_pct": 9.3,  # (upper - lower) / current_price * 100
    "confidence": "medium",     # narrow=high, wide=medium, very_wide=low
}
```

### 3.4 Fallback
If no trained model exists for the ticker's market (CN vs US vs crypto),
return `"model_unavailable"` and let the LLM synthesis layer produce a
qualitative estimate from analyst reports.

## 4. Tool B: Direction Predictor

### 4.1 Model
- **XGBoost binary classifier** with `objective="binary:logistic"` and
  probability calibration via isotonic regression.
- Two models: `direction_5d` and `direction_20d`.
- Labels: `1` if close_{t+N} > close_t * 1.005, `0` otherwise (0.5% threshold
  to avoid noise).
- Output is a calibrated probability, NOT a raw XGBoost score.

### 4.2 Features
Same feature set as Tool A (shared `FeatureEngine`).

### 4.3 Output
```python
{
    "horizon": "5d",
    "up_probability": 0.38,     # calibrated P(up)
    "down_probability": 0.62,   # calibrated P(down)
    "confidence": "medium",     # based on probability margin from 0.5
    "calibration_score": 0.92,  # historical Brier score complement
}
```

### 4.4 Confidence tiers
- `high`: probability ≥ 0.70 in either direction
- `medium`: probability 0.55-0.69
- `low`: probability < 0.55 (model is uncertain — this is itself useful information)

## 5. Tool C: Behavior Predictor

### 5.1 Approach
LLM-powered pattern recognition. Unlike A/B which are numeric ML models, Tool C
is a prompt-engineered LLM call that:

1. **Receives** the current Smart Money Score gates, capital flow lifecycle stage,
   and the 5 most recent analyst reports.
2. **Searches** Memory Log for similar historical patterns (same ticker or same
   sector, similar stage, similar gate results).
3. **Classifies** the current phase: `accumulation | shakeout | markup | distribution | exit`
4. **Predicts** the most likely next behavior with a rationale.

### 5.2 Pattern matching (preprocessing, not ML)
Before the LLM call, a deterministic step:
- Query Memory Log for past decisions on the same ticker
- Filter to those where the Smart Money Score patterns were similar
  (same stage, same gate results)
- Format 3 most relevant historical analogs as a compact table for the LLM prompt

### 5.3 Output
```python
{
    "current_phase": "distribution",          # enum
    "phase_confidence": "high",               # based on gate agreement
    "predicted_next_behavior": "continue distributing, possibly accelerate on rebounds",
    "behavior_rationale": "超大单40日>90%净流出...",  # 2-3 sentence LLM output
    "historical_analogs": [
        {"date": "2026-04-15", "phase": "distribution",
         "outcome": "continued decline -12% over 10d"}
    ],
    "risk_scenario": "如果跌破60元支撑位，可能加速下跌至55元",  # tail risk
}
```

## 6. Synthesis Layer

### 6.1 Cross-validation rules
After A, B, C produce their outputs independently, the synthesis layer checks
for internal consistency:

| Condition | Action |
|-----------|--------|
| A and B agree on direction | High confidence, output directly |
| A and B disagree | Flag `cross_validation: "conflict"`, downgrade confidence |
| C contradicts A+B | Flag `cross_validation: "behavior_model_mismatch"`, explain |
| A returned `model_unavailable` | Use B + C only, flag `price_model_missing` |
| All three agree | `cross_validation: "aligned"`, highest confidence |

### 6.2 LLM Synthesis
A single LLM call takes the raw outputs of A, B, C and the cross-validation
verdict, and produces:

1. A **Chinese-language prediction summary** (2-3 paragraphs)
2. A **confidence statement** (HIGH / MEDIUM / LOW with rationale)
3. **Actionable guidance** for the Portfolio Manager (e.g., "建议等待方向预测概率突破 0.65 后再考虑入场")

## 7. Data Flow

```
User/Advisor calls PredictionAgent.predict(ticker, date)
        │
        ▼
FeatureEngine.build_features(ticker, date)
        │  Fetches: OHLCV, capital flow, fundamentals, sector data
        │  Builds:  standardized feature vector (dict → DataFrame)
        ▼
   ┌────┴────┬────────────┐
   ▼         ▼            ▼
Tool A     Tool B       Tool C
Price      Direction    Behavior
(ML)       (ML)         (LLM + Memory)
   │         │            │
   └────┬────┴────────────┘
        ▼
Synthesis Layer (cross-validation → LLM synthesis)
        │
        ▼
PredictionReport (structured + markdown)
```

## 8. Training Pipeline

### 8.1 Initial training
- **Data source:** tushare daily + akshare for A-shares; yfinance for US
- **Training period:** 2019-01-01 to 2025-12-31 (6 years of daily data)
- **Target construction:** for each trading day t, compute:
  - `target_5d_direction`: 1 if close_{t+5} > close_t * 1.005 else 0
  - `target_20d_direction`: same for t+20
  - `target_5d_return`: (close_{t+5} - close_t) / close_t (for quantile model)
  - `target_20d_return`: (close_{t+20} - close_t) / close_t
- **Train/val/test split:** 2019-2023 train, 2024 val, 2025 test
- **Training frequency:** retrain monthly with new data, or when model drift detected

### 8.2 Model storage
```
~/.capitalradar/models/
├── price_5d_q10.json       # XGBoost JSON format
├── price_5d_q50.json
├── price_5d_q90.json
├── price_20d_q10.json
├── price_20d_q50.json
├── price_20d_q90.json
├── direction_5d.json
├── direction_20d.json
├── calibrator_5d.pkl       # isotonic calibration
├── calibrator_20d.pkl
└── model_metadata.json     # training date, feature list, metrics
```

### 8.3 Retraining trigger
- Monthly cron (via existing APScheduler in web/)
- Or manual: `python -m capitalradar.prediction.trainer`
- Model versioning: each training run produces a new version; old versions kept
  for rollback

## 9. Integration Points

### 9.1 Advisor tool
The existing advisory PM (`web/history_chat.py`, `web/ai_pick_agent.py`) gains
a new tool:
```python
def predict_stock(ticker: str, date: str) -> str:
    """Run the PredictionAgent and return a formatted prediction report."""
```

### 9.2 Portfolio Manager prompt injection
The PM's system prompt gains an optional section:
```
If a prediction report is available for {ticker} on {date}, reference it
in your final decision. If the prediction conflicts with your own analysis,
explain why you are overruling it.
```

### 9.3 Web UI
- New tab or card in the dashboard: "Prediction" — input ticker + date,
  see the formatted prediction report
- SSE streaming support (reuses existing `StreamEmitter` pattern)

## 10. Error Handling & Edge Cases

| Case | Handling |
|------|----------|
| No trained model exists | Tool A/B return `model_unavailable`; synthesis falls back to C + qualitative |
| Insufficient historical data | Feature engine returns partial features; model predicts with lower confidence |
| Ticker is crypto | Use yfinance features only (no capital flow); different trained model |
| Market is closed (weekend/holiday) | Use last close; flag `market_state: closed` |
| All three tools fail | Return error report with reason; do not fabricate predictions |

## 11. Success Metrics

- Direction accuracy: > 58% (baseline: 50% random)
- Brier score: < 0.22 (lower is better calibration)
- Price interval coverage: true price inside [q10, q90] > 80% of the time
- Behavior phase accuracy: validated against Memory Log resolved outcomes
- Latency: PredictionAgent.predict() < 3 seconds (not including LLM calls)

## 12. Non-Goals (v1)

- No real-time streaming prediction (batch only for now)
- No multi-asset portfolio-level prediction (single ticker only)
- No options/derivatives pricing
- No intraday prediction (daily bar minimum)
- No automated trading execution
- No crypto-specific behavior model (reuse stock model with reduced features)

## 13. Implementation Phases

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| Phase 1: Foundation | Week 1 | FeatureEngine + DataPipeline + trainer skeleton |
| Phase 2: Direction Model | Week 2 | Tool B complete with calibration, stored model |
| Phase 3: Price Model | Week 2-3 | Tool A complete with quantile models |
| Phase 4: Behavior + Synthesis | Week 3 | Tool C + synthesis LLM + cross-validation |
| Phase 5: Integration | Week 4 | Advisor tool, PM prompt injection, web UI card |
