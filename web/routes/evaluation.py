"""HTTP adapter for the investment-effect evaluation subsystem.

Exposes the three evaluation endpoints from the plan:
- GET /api/evaluation/summary?period=week|month|quarter
- GET /api/evaluation/cases
- POST /api/evaluation/run?kind=weekly|monthly

The heavy lifting lives in ``quantconclave.evaluation`` (core) and
``web.eval_store`` (persistence); this module only maps HTTP onto those callables.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from quantconclave.default_config import DEFAULT_CONFIG

router = APIRouter(prefix="/api", tags=["evaluation"])


@router.get("/evaluation/summary")
def get_evaluation_summary(period: str = Query(default="week")):
    from quantconclave.evaluation.summary import compute_summary
    if period not in ("week", "month", "quarter"):
        raise HTTPException(400, f"invalid period: {period}")
    return compute_summary(DEFAULT_CONFIG, period)


@router.get("/evaluation/cases")
def get_evaluation_cases(
    week_key: str = Query(default=""),
    ticker: str = Query(default=""),
    status: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    from web import eval_store
    cases = eval_store.list_eval_cases(
        DEFAULT_CONFIG,
        week_key=week_key or None,
        ticker=ticker or None,
        status=status or None,
        limit=limit,
        offset=offset,
    )
    for case in cases:
        predictions = eval_store.get_eval_predictions(DEFAULT_CONFIG, case_id=case["case_id"])
        for p in predictions:
            p["returns"] = eval_store.get_eval_returns(DEFAULT_CONFIG, prediction_id=p["prediction_id"])
        case["predictions"] = predictions
    return cases


@router.post("/evaluation/run")
def run_evaluation(kind: str = Query(default="weekly")):
    from quantconclave.evaluation.pipeline import run_monthly_ablation, run_weekly_evaluation
    if kind == "weekly":
        return run_weekly_evaluation(DEFAULT_CONFIG)
    if kind == "monthly":
        return run_monthly_ablation(DEFAULT_CONFIG)
    raise HTTPException(400, f"invalid kind: {kind} (expected weekly|monthly)")


@router.post("/evaluation/settle")
def settle_evaluation():
    from quantconclave.evaluation.pipeline import settle_pending_cases
    return settle_pending_cases(DEFAULT_CONFIG)


@router.get("/evaluation/report")
def get_evaluation_report(period: str = Query(default="week"), key: str = Query(default="")):
    from web import eval_store
    from web import eval_report
    if period not in ("week", "month", "quarter"):
        raise HTTPException(400, f"invalid period: {period}")
    if key:
        archived = eval_store.get_eval_report(DEFAULT_CONFIG, period, key)
        if archived:
            return archived
    builders = {"week": eval_report.build_weekly_report,
                "month": eval_report.build_monthly_report,
                "quarter": eval_report.build_quarterly_report}
    return builders[period](DEFAULT_CONFIG, key)
