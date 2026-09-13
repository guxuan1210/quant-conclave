"""Experience extractor: detect patterns in resolved entries and generate experiences."""
from __future__ import annotations
import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

# Precompiled regexes for PM structured output fields
_RATING_RE = re.compile(r'\*\*Rating\*\*[:\s]*(\w+)')
_OUTLOOK_5D_RE = re.compile(r'\*\*5-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)')
_OUTLOOK_20D_RE = re.compile(r'\*\*20-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)')
_CONFIDENCE_RE = re.compile(r'\*\*Confidence\*\*[:\s]*(\w+)')


def _parse_decision_fields(text: str) -> dict:
    """Extract structured fields from PM decision markdown."""
    result = {}
    m = _RATING_RE.search(text)
    if m:
        result["rating"] = m.group(1)
    m = _OUTLOOK_5D_RE.search(text)
    if m:
        result["outlook_5d"] = m.group(1).strip()[:80]
    m = _OUTLOOK_20D_RE.search(text)
    if m:
        result["outlook_20d"] = m.group(1).strip()[:80]
    m = _CONFIDENCE_RE.search(text)
    if m:
        result["confidence"] = m.group(1).strip().lower()
    # Detect direction from outlook text
    result["5d_bullish"] = any(k in (result.get("outlook_5d","")).lower() for k in ["bullish", "up", "涨", "看涨", "买入"])
    result["20d_bearish"] = any(k in (result.get("outlook_20d","")).lower() for k in ["bearish", "down", "跌", "看跌", "卖出"])
    result["divergence"] = result.get("5d_bullish", False) and result.get("20d_bearish", False)
    return result


SECTOR_MAP = {}  # populated on demand

def _get_sector_for_ticker(ticker: str) -> str:
    """Simplified sector detection based on ticker pattern."""
    # This is a heuristic; full sector data would come from analysis state JSON
    ticker = ticker.upper()
    if ticker.startswith("688") or ticker.startswith("002"):
        return "tech"
    if ticker.startswith("600") or ticker.startswith("000"):
        return "traditional"
    return "other"

def extract_experiences(config: dict, resolved_entries: list = None) -> list:
    """Detect patterns in resolved entries and generate experience proposals."""
    from quantconclave.advisory.experience_store import create_experience
    from quantconclave.agents.utils.memory import QuantConclaveMemoryLog
    
    if resolved_entries is None:
        ml = QuantConclaveMemoryLog(config)
        all_entries = ml.load_entries()
        resolved_entries = [e for e in all_entries if not e.get("pending")]
    
    if not resolved_entries:
        return []
    
    created = []
    
    # Pattern 1: sector bias - group by sector, check bearish accuracy
    sector_bearish = defaultdict(list)
    sector_all = defaultdict(list)
    for e in resolved_entries:
        sec = _get_sector_for_ticker(e.get("ticker", ""))
        sector_all[sec].append(e)
        text = (e.get("decision") or "").lower()
        if any(k in text for k in ["sell","underweight","bearish","回避","卖出","减持","看空","减配"]):
            sector_bearish[sec].append(e)
    
    for sec, entries in sector_bearish.items():
        if len(entries) >= 5:
            wrong = [e for e in entries if e.get("raw") is not None and e.get("raw", 0) >= 0]
            accuracy = (len(entries) - len(wrong)) / len(entries) * 100
            if accuracy < 50:
                eid = create_experience(
                    content=f"Sector (sector) bearish accuracy only {accuracy:.0f}%. Consider discounting flow signals for this sector.",
                    category="sector_bias",
                    lesson_abstract=f"sector_bias {sec}",
                )
                created.append({"id": eid, "sector": sec, "accuracy": accuracy})
    
    # Pattern 2: ticker streak - same ticker correct 3+ times
    ticker_entries = defaultdict(list)
    for e in resolved_entries:
        ticker_entries[e.get("ticker", "")].append(e)
    for ticker, entries in ticker_entries.items():
        if len(entries) >= 3:
            correct = [e for e in entries if e.get("raw") is not None and e.get("raw", 0) < 0]
            if len(correct) >= 3:
                eid = create_experience(
                    content=f"Ticker {ticker} has been correctly bearish {len(correct)} times in a row. Maintain high conviction.",
                    category="ticker_streak",
                    lesson_abstract=f"streak {ticker}",
                )
                created.append({"id": eid, "ticker": ticker, "streak": len(correct)})
    
    # Pattern 3: bearish bias
    bearish_entries = [e for e in resolved_entries if
        any(k in (e.get("decision") or "").lower() for k in
            ["sell","underweight","bearish","回避","卖出","减持","看空","减配"])]
    if len(resolved_entries) >= 20 and len(bearish_entries) / len(resolved_entries) > 0.8:
        eid = create_experience(
            content="Systematic bearish bias detected: over 80% of ratings are bearish. Consider enforcing reversal checks.",
            category="thesis_bias",
            lesson_abstract="thesis_bias",
        )
        created.append({"id": eid, "bearish_rate": len(bearish_entries) / len(resolved_entries) * 100})
    
    # Pattern 4: time-horizon divergence (5d bullish + 20d bearish)
    divergence = [e for e in resolved_entries
                  if _parse_decision_fields(e.get("decision", "")).get("divergence")]
    if len(divergence) >= 3:
        correct = [e for e in divergence if e.get("raw", 0) is not None and e.get("raw", 0) < 0]
        if correct:
            eid = create_experience(
                content=f"Time-horizon divergence detected {len(divergence)} times: 5d bullish + 20d bearish. "
                        f"In {len(correct)} cases the 20d bearish view was correct. "
                        f"Rule: when 5d and 20d ML predictions diverge, weight the 20d more heavily.",
                category="time_horizon",
                lesson_abstract="time_horizon_divergence",
            )
            created.append({"id": eid, "divergence_count": len(divergence), "correct_count": len(correct)})

    # Pattern 5: confidence calibration
    for level in ["high", "medium", "low"]:
        level_entries = [
            e for e in resolved_entries
            if _parse_decision_fields(e.get("decision", "")).get("confidence") == level
        ]
        if len(level_entries) >= 5:
            correct = sum(1 for e in level_entries if e.get("raw", 0) is not None and e.get("raw", 0) < 0)
            correct_rate = correct / len(level_entries) * 100
            if level == "high" and correct_rate < 65:
                eid = create_experience(
                    content=f"Overconfidence detected: 'high' confidence decisions only {correct_rate:.0f}% correct "
                            f"({correct}/{len(level_entries)}). Consider calibrating confidence down.",
                    category="confidence_calibration",
                    lesson_abstract=f"overconfidence_{level}",
                )
                created.append({"id": eid, "level": level, "correct_rate": correct_rate})
            elif level == "low" and correct_rate > 50:
                eid = create_experience(
                    content=f"Underconfidence detected: 'low' confidence decisions achieved {correct_rate:.0f}% correct "
                            f"({correct}/{len(level_entries)}). Consider raising confidence when pattern is clear.",
                    category="confidence_calibration",
                    lesson_abstract=f"underconfidence_{level}",
                )
                created.append({"id": eid, "level": level, "correct_rate": correct_rate})

    # Pattern 6: 5d rebound success rate
    bullish_5d = [e for e in resolved_entries
                  if _parse_decision_fields(e.get("decision", "")).get("5d_bullish")]
    if len(bullish_5d) >= 5:
        profitable = sum(1 for e in bullish_5d if e.get("raw", 0) is not None and e.get("raw", 0) > 0)
        profit_rate = profitable / len(bullish_5d) * 100
        eid = create_experience(
            content=f"5d bullish calls: {profit_rate:.0f}% profitable ({profitable}/{len(bullish_5d)}). "
                    f"Use this to calibrate the actual win rate of short-term bounce predictions.",
            category="short_term_accuracy",
            lesson_abstract=f"5d_rebound_rate_{profit_rate:.0f}",
        )
        created.append({"id": eid, "profit_rate": profit_rate, "total": len(bullish_5d)})

    # Pattern 7: rating-specific accuracy
    rating_entries = defaultdict(list)
    for e in resolved_entries:
        parsed = _parse_decision_fields(e.get("decision", ""))
        rating = parsed.get("rating", "")
        if rating:
            rating_entries[rating].append(e)
    for rating, entries in rating_entries.items():
        if len(entries) >= 4:
            correct_count = sum(1 for e in entries if e.get("raw", 0) is not None and e.get("raw", 0) < 0)
            accuracy = correct_count / len(entries) * 100
            if accuracy < 40:
                eid = create_experience(
                    content=f"Low {rating} accuracy: only {accuracy:.0f}% correct ({correct_count}/{len(entries)}). "
                            f"Consider reviewing the decision criteria for this rating level.",
                    category="rating_accuracy",
                    lesson_abstract=f"low_accuracy_{rating}",
                )
                created.append({"id": eid, "rating": rating, "accuracy": accuracy})

    return created
