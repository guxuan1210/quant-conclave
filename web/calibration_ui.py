"""FastAPI routes for calibration panel."""
from fastapi import APIRouter
from pydantic import BaseModel
from quantconclave.graph.loop_coordinator import run_full_loop
from quantconclave.graph.meta_evaluator import approve_proposal

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


def _get_conn(config: dict):
    import sqlite3
    from pathlib import Path
    results_dir = config.get("results_dir", "")
    db_path = str(Path(results_dir) / "results.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/runs")
def list_runs(limit: int = 20):
    from quantconclave.default_config import DEFAULT_CONFIG as config
    conn = _get_conn(config)
    rows = conn.execute(
        "SELECT * FROM calibration_runs ORDER BY run_date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/runs/latest")
def latest_run():
    from quantconclave.default_config import DEFAULT_CONFIG as config
    conn = _get_conn(config)
    row = conn.execute(
        "SELECT * FROM calibration_runs ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


@router.get("/runs/{rid}")
def get_run(rid: int):
    from quantconclave.default_config import DEFAULT_CONFIG as config
    conn = _get_conn(config)
    row = conn.execute("SELECT * FROM calibration_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return dict(row) if row else {"error": "not found"}


@router.post("/run")
def trigger_run():
    from quantconclave.default_config import DEFAULT_CONFIG as config
    result = run_full_loop(config, trigger="manual")
    return result


class ProposalApproval(BaseModel):
    rule_name: str
    field_name: str


@router.post("/proposal/approve")
def approve_proposal_endpoint(body: ProposalApproval):
    from quantconclave.default_config import DEFAULT_CONFIG as config
    approve_proposal(config, body.rule_name, body.field_name)
    return {"status": "approved", "rule": body.rule_name, "field": body.field_name}
