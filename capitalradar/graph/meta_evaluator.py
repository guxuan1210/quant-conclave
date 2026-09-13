import os, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from capitalradar.agents.utils.memory import CapitalRadarMemoryLog

_STATE_FILENAME = "adjudication_state.json"

def _get_path(config):
    return str(Path(config.get("data_cache_dir", "")) / _STATE_FILENAME)

def load_state(config):
    p = _get_path(config)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"rules": {}, "proposals": [], "stats": {}, "last_evaluation": "", "history": []}

def save_state(config, state):
    p = _get_path(config)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def _is_bearish(text):
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in ["sell","underweight","bearish","回避","卖出","减持","看空","减配"])

def run_meta_evaluation(config):
    state = _load(config)
    today = datetime.now().strftime("%Y-%m-%d")
    state["last_evaluation"] = today
    cr = config.get("adjudication_rules", {})
    state["rules"] = {k: v for k, v in cr.items() if isinstance(v, dict)}

    ml = CapitalRadarMemoryLog(config)
    resolved = [e for e in ml.load_entries() if not e.get("pending")]
    
    if not resolved:
        state["stats"] = {"total": 0, "last_run": today}
        save_state(config, state)
        return []

    total = len(resolved)
    bearish = [e for e in resolved if _is_bearish(e.get("decision"))]
    bearish_ok = [e for e in bearish if e.get("raw") is not None and e.get("raw", 0) < 0]
    bearish_bad = [e for e in bearish if e.get("raw") is not None and e.get("raw", 0) >= 0]
    acc = len(bearish_ok) / max(len(bearish), 1) * 100
    rate = len(bearish) / max(total, 1) * 100

    state["stats"] = {
        "total": total,
        "bearish": len(bearish),
        "bearish_ok": len(bearish_ok),
        "bearish_bad": len(bearish_bad),
        "accuracy": round(acc, 1),
        "bearish_rate": round(rate, 1),
    }

    proposals = []

    # Consensus threshold tuning
    rev = cr.get("consensus_reversal", {})
    if rev.get("enabled", True) and len(bearish) >= 5:
        cur = rev.get("bearish_threshold", 4)
        if acc < 60:
            new_val = max(3, cur - 1)
            if new_val < cur:
                proposals.append({
                    "rule": "consensus_reversal",
                    "field": "bearish_threshold",
                    "from": cur, "to": new_val,
                    "reason": "Bearish accuracy {:.0f}%. Lower threshold to {}.".format(acc, new_val),
                    "status": "pending",
                })

    # Conflict lookback tuning
    conf = cr.get("conflict_resolution", {})
    if conf.get("enabled", True) and len(bearish_bad) >= 3:
        cur = conf.get("lookback_days", 7)
        new_val = min(14, cur + 3)
        proposals.append({
            "rule": "conflict_resolution",
            "field": "lookback_days",
            "from": cur, "to": new_val,
            "reason": "{} bearish misses. Increase lookback to {}.".format(len(bearish_bad), new_val),
            "status": "pending",
        })

    state["proposals"] = proposals
    if proposals:
        state.setdefault("history", []).append({
            "date": today,
            "stats": dict(state["stats"]),
            "proposals": list(proposals),
        })
        state["history"] = state["history"][-20:]

    save_state(config, state)
    return proposals

def get_calibration_report(config):
    """Return a human-readable calibration report using adjudication_state."""
    state = _load(config)
    props = state.get("proposals", [])
    stats = state.get("stats", {})
    lines = []
    lines.append("### System Calibration Report")
    lines.append("")
    if stats.get("total", 0) == 0:
        lines.append("No resolved entries yet. Run more analyses and let outcomes track.")
        return "\n".join(lines)
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append("| Resolved Entries | {}".format(stats.get("total", 0)))
    lines.append("| Bearish % | {:.0f}%".format(stats.get("bearish_rate", 0)))
    lines.append("| Bearish Accuracy | {:.0f}%".format(stats.get("accuracy", 0)))
    if props:
        lines.append("")
        lines.append("### Pending Adjustments")
        for p in props:
            lines.append("- **{}** {}: {} -> {} ({})".format(
                p.get("rule","?"), p.get("field","?"),
                p.get("from","?"), p.get("to","?"),
                p.get("reason","?")))
        lines.append("")
        lines.append("Use `/api/advisory/calibration/approve` to review.")
    else:
        lines.append("")
        lines.append("No adjustments needed at this time.")
    return "\n".join(lines)

def approve_proposal(config, rule_name, field_name):
    """Approve a specific proposal and update the config in memory."""
    state = _load(config)
    for p in state.get("proposals", []):
        if p.get("rule") == rule_name and p.get("field") == field_name:
            p["status"] = "approved"
            # Update the in-memory config (not persisted to disk)
            rule_parent = config.get("adjudication_rules", {}).get(rule_name, {})
            if rule_parent:
                rule_parent[field_name] = p["to"]
            p["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_state(config, state)
    return state["proposals"]

