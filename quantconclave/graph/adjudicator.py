"""Adjudication node. Applied after analysts, before debate. No prompt changes."""
from __future__ import annotations
import re, logging
from datetime import datetime, timedelta
logger = logging.getLogger(__name__)

BEARISH_KW = ["sell","underweight","bearish","回避","卖出","减持","看空","减配"]
BULLISH_KW = ["buy","overweight","bullish","买入","增持","看多","超配"]
TECH_SECTORS = ["半导体","芯片","AI","软件","新材料","电子"]

from quantconclave.catalog import ordered_roles
REPORT_FIELDS = [role.report_key for role in ordered_roles()]

def _count_bearish(state):
    c = 0
    for field in REPORT_FIELDS:
        text = (state.get(field) or "").lower()
        if any(k in text for k in BEARISH_KW):
            c += 1
    return c

def _extract_pe(text):
    m = re.search(r"pe[\s(ttm)]*[:：]?\s*([\d.]+)", text, re.I)
    return float(m.group(1)) if m else None

def _check_sector(text):
    t = text.lower()
    return any(s.lower() in t for s in TECH_SECTORS)

def _extract_rating(text):
    t = text.lower()
    if any(k in t for k in ["buy","买入","超配"]): return "Buy"
    if any(k in t for k in ["sell","卖出","减持"]): return "Sell"
    if any(k in t for k in ["overweight","增持"]): return "Overweight"
    if any(k in t for k in ["underweight","减配"]): return "Underweight"
    return "Hold"

def apply_tech_flow_discount(state, config):
    rule = config.get("adjudication_rules",{}).get("tech_flow_discount",{})
    if not rule.get("enabled",True): return
    pe = _extract_pe(state.get("fundamentals_report") or "")
    partner = state.get("partner_report") or ""
    market = state.get("market_report") or ""
    threshold = rule.get("pe_threshold",50)
    is_tech = False
    if pe and pe > threshold:
        is_tech = True
        state.setdefault("adjudication_notes",{})["pe"] = str(pe)
    if _check_sector(partner+market):
        is_tech = True
    if is_tech:
        state.setdefault("adjudication_flags",[]).append("tech_flow_unreliable")

def apply_sector_rotation(state, config):
    rule = config.get("adjudication_rules",{}).get("sector_rotation_check",{})
    if not rule.get("enabled",True): return
    data = state.get("sector_rotation_data")
    if data:
        q = data.get("quadrant","")
        d = str(data.get("days_in_quadrant",0))
        state.setdefault("adjudication_flags",[]).append("sector_momentum")
        state.setdefault("adjudication_notes",{})["sector_quadrant"] = q
        state["adjudication_notes"]["quadrant_days"] = d

def apply_consensus_reversal(state, config):
    rule = config.get("adjudication_rules",{}).get("consensus_reversal",{})
    if not rule.get("enabled",True): return
    threshold = rule.get("bearish_threshold",4)
    bearish_count = _count_bearish(state)
    if bearish_count >= threshold:
        state.setdefault("adjudication_flags",[]).append("force_bull_reversal")
        state.setdefault("adjudication_notes",{})["bearish_count"] = str(bearish_count)

def apply_conflict_resolution(state, config):
    rule = config.get("adjudication_rules",{}).get("conflict_resolution",{})
    if not rule.get("enabled",True): return
    notes = state.setdefault("adjudication_notes",{})
    flags = state.setdefault("adjudication_flags",[])
    rm = _extract_rating(state.get("investment_debate_state",{}).get("judge_decision") or "")
    trader = _extract_rating(state.get("trader_investment_plan") or "")
    pm = _extract_rating(state.get("final_trade_decision") or "")
    ratings = [r for r in [rm,trader,pm] if r in ("Buy","Sell")]
    if len(set(ratings)) > 1:
        flags.append("conflict_detected")
        notes["conflict_detail"] = "RM:{} Trader:{} PM:{}".format(rm,trader,pm)

def render_adjudication_notes(state):
    flags = state.get("adjudication_flags", [])
    notes = state.get("adjudication_notes", {})
    if not flags:
        return ""
    parts = []
    if "tech_flow_unreliable" in flags:
        pe = notes.get("pe", "?")
        parts.append("Adjudicator Note: Capital Flow Advisory for High-PE/Tech Stocks")
        parts.append("This stock has PE=" + pe + ". Capital flow data for high-PE tech stocks may be unreliable.")
        parts.append("Weight Capital Flow report at ~50%, increase weight on Market trend/breakout signals.")
    if "sector_momentum" in flags:
        q = notes.get("sector_quadrant", "unknown")
        d = notes.get("quadrant_days", "?")
        parts.append("Adjudicator Note: Sector Rotation Context")
        parts.append("This stock sector is in " + q + " quadrant (" + d + "d).")
        if q in ("improving", "leading"):
            parts.append("Reducing bearish conviction. Trend/momentum signals favored.")
    if "force_bull_reversal" in flags:
        count = notes.get("bearish_count", "?")
        parts.append("Adjudicator Note: Mandatory Bull Counter-Scenario")
        parts.append(count + "/7 analysts bearish. Guarding against consensus bias.")
        parts.append("Consider: what could drive a 10%+ rally? What might the bearish case be missing?")
    if "conflict_detected" in flags:
        detail = notes.get("conflict_detail", "?")
        parts.append("Adjudicator Note: Signal Conflict - Action Required")
        parts.append(detail)
        parts.append("FORCE-ADJUSTED: Final rating should be Hold/Watch until signals converge.")
    return "\n\n".join(parts)

def create_adjudicator_node(config):
    def adjudicator(state):
        state.setdefault("adjudication_flags",[])
        state.setdefault("adjudication_notes",{})
        apply_tech_flow_discount(state, config)
        apply_sector_rotation(state, config)
        apply_consensus_reversal(state, config)
        apply_conflict_resolution(state, config)
        return state
    return adjudicator
