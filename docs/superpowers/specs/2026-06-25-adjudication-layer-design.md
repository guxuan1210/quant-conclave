# Adjudication Layer -- Loop Engineering Design

## Core Concept: Dual Loops

Not a static rule engine. Two nested OODA loops:

=== Fast Loop (per analysis) ===
O: 7 Analyst reports complete
O: Adjudicator reads reports + current threshold params
D: Output flags based on rules (thresholds are tunable)
A: Flags injected into downstream prompts

=== Slow Loop (cross-analysis, outcome-driven) ===
O: Collect resolved outcomes from memory_log
O: Pattern detection (like the PM retrospective report)
D: Auto-adjust adjudicator threshold parameters
A: User reviews -> new thresholds take effect

## Four Rule Mappings

### Rule 1: Tech Flow Discount
Fast: PE > threshold(current) -> flag tech_flow_unreliable
Slow: Every 10 tracked tech stock outcomes: if still too bearish -> lower PE threshold; if missed rallies -> raise PE threshold

### Rule 2: Sector Rotation
Fast: Sector in {quadrant} -> flag conviction_modifier
Slow: Evaluate quadrant predictive value. If Improving bearish misses >50% -> strengthen modifier

### Rule 3: Consensus Reversal
Fast: bearish_count >= threshold -> flag force_bull_reversal
Slow: If reversal still wrong -> adjust threshold (5->4->3); if reversal execution weak -> improve prompt

### Rule 4: Conflict Resolution
Fast: Detect conflict -> force Hold
Slow: If Hold still wrong -> extend lookback; if Hold missed rally -> shorten lookback

## Feedback Path

memory_log (outcomes) -> Pattern Detector -> Threshold Adjuster -> User Review -> adjudication_state.json -> Fast Loop reads updated params

No analyst prompts are modified. All learning happens through parameter adjustment.
