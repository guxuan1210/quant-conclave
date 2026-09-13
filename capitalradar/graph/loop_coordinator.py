"""Loop coordinator: orchestrates all 4 phases of the automation loop."""
from __future__ import annotations
import json, logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

def save_calibration_record(config: dict, trigger: str, resolve_result: dict,
                             meta_result: list, experience_result: list) -> int:
    """Save a calibration run record to calibration_runs table."""
    try:
        conn = _get_cal_conn(config)
        cur = conn.execute(
            "INSERT INTO calibration_runs (run_date, trigger, total_pending, resolved_count, "
            "stats, proposals, experience_summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(),
                trigger,
                resolve_result.get("total_pending", 0),
                resolve_result.get("resolved", 0),
                json.dumps({"meta_proposals": len(meta_result), "experiences": len(experience_result)}),
                json.dumps(meta_result),
                json.dumps([{"id": e.get("id"), "category": e.get("category", "")} for e in experience_result]),
                "[]",
            )
        )
        conn.commit()
        record_id = cur.lastrowid
        conn.close()
        return record_id
    except Exception as e:
        logger.error("Failed to save calibration record: %s", e)
        return 0

def _get_cal_conn(config: dict):
    import sqlite3
    from pathlib import Path
    results_dir = config.get("results_dir", "")
    db_path = str(Path(results_dir) / "results.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def run_full_loop(config: dict, trigger: str = "manual") -> dict:
    """Run all 4 phases of the automation loop. Returns summary."""
    from capitalradar.graph.resolver import resolve_all_pending
    from capitalradar.graph.meta_evaluator import run_meta_evaluation
    from capitalradar.graph.experience_extractor import extract_experiences
    
    logger.info("Starting full loop (trigger: %s)", trigger)
    
    # Phase 1: Resolve pending
    resolve_result = resolve_all_pending(config)
    logger.info("Phase 1 done: %d resolved", resolve_result.get("resolved", 0))
    
    # Phase 2: Meta-evaluation
    try:
        meta_result = run_meta_evaluation(config)
    except Exception as e:
        logger.error("Meta-evaluation failed: %s", e)
        meta_result = []
    
    # Phase 3: Extract experiences
    try:
        experience_result = extract_experiences(config)
    except Exception as e:
        logger.error("Experience extraction failed: %s", e)
        experience_result = []
    
    # Phase 4: Save record. Skill versions are generated only after user review
    # and explicit confirmation via the Skill Control panel.
    skill_result = {"version": 0}
    record_id = save_calibration_record(config, trigger, resolve_result, meta_result, experience_result)

    return {
        "trigger": trigger,
        "record_id": record_id,
        "resolved": resolve_result.get("resolved", 0),
        "total_pending": resolve_result.get("total_pending", 0),
        "proposals": len(meta_result),
        "experiences": len(experience_result),
        "skill_version": skill_result.get("version", 0),
    }
