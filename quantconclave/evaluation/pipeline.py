"""Evaluation orchestrator: weekly sampling + full-pipeline prediction + settlement,
and the monthly single-model ablation.

Persistence is done through ``web.eval_store`` and ``web.results_store`` via
lazy imports so the rest of the evaluation package stays web-independent.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import uuid

from .config import get_eval_config
from .hashes import input_snapshot_hash, model_config_hash
from .prices import nth_trading_day
from .sampling import (
    fetch_constituents,
    fetch_exclusion_sets,
    sample_weekly_stocks,
    week_key,
)
from .settlement import settle_entry, settle_horizon
from .single_model import run_single_llm_ablation

logger = logging.getLogger(__name__)

_CONF_RE = re.compile(r"\*\*Confidence\*\*[:\-：]\s*(\w+)", re.IGNORECASE)


def extract_confidence(text: str) -> str:
    """Pull the categorical confidence out of rendered PortfolioDecision markdown."""
    m = _CONF_RE.search(text or "")
    return m.group(1).strip().lower() if m else ""


def _reports_fingerprint(final_state: dict) -> str:
    keys = ("market_report", "sentiment_report", "news_report",
            "fundamentals_report", "capital_flow_report",
            "competitor_report", "partner_report")
    blob = "\n\n".join(str(final_state.get(k, "")) for k in keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _default_run(config, ticker, selection_date):
    """Run the full multi-agent pipeline and return (final_state, signal, stats)."""
    from cli.stats_handler import StatsCallbackHandler
    from quantconclave.graph.trading_graph import QuantConclaveGraph

    stats = StatsCallbackHandler()
    ta = QuantConclaveGraph(debug=False, config=config, callbacks=[stats])
    final_state, signal = ta.execute_graph(ticker, selection_date, asset_type="stock")
    return final_state, signal, stats.get_stats()


def run_full_analysis_with_retry(config, ticker, selection_date, run_fn=None, max_attempts=2):
    """Run a full analysis, retrying once on failure. Raises after exhausting attempts."""
    run = run_fn or _default_run
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return run(config, ticker, selection_date)
        except Exception as e:
            last_err = e
            logger.warning("full analysis attempt %d failed for %s: %s", attempt, ticker, e)
    raise last_err


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _last_open_day(config) -> str | None:
    try:
        from web.trade_cal import last_open_day
        d = last_open_day()
        if d:
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    except Exception:
        pass
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def _entry_grace_exceeded(entry_date: str, as_of_date: str, grace_days: int, config) -> bool:
    """Whether a still-missing entry price has outlasted its retry window."""
    if grace_days <= 0:
        return True
    try:
        from web.trade_cal import get_open_days
        from .sampling import trading_days_since
        open_days = get_open_days()
        return trading_days_since(entry_date, as_of_date, open_days) >= grace_days
    except Exception:
        return False


def settle_pending_cases(config=None, as_of_date=None):
    """Progressively settle every non-failed case whose prices are now available.

    Forward testing means the entry (next trading day) and 5/20/60-day exits are
    in the future at selection time, so this is the *deferred* settlement step
    that the daily scheduler calls. It is idempotent: already-settled horizons
    are skipped and ``eval_returns`` rows are ``INSERT OR REPLACE``.

    A case is marked ``settled`` once its full-pipeline 5-day horizon settles
    (the primary coverage/report horizon); the 20/60-day and single-model
    horizons continue to backfill on later runs.
    """
    from quantconclave.dataflows.config import get_config
    config = config or get_config()
    ecfg = get_eval_config(config)

    from web import eval_store

    as_of_date = as_of_date or _last_open_day(config)
    summary = {"settled": 0, "settled_horizons": 0, "failed": 0, "skipped": 0}

    horizons = ecfg["holdings_horizons"]
    cases = eval_store.list_eval_cases(config, limit=10000)
    for case in cases:
        if case["status"] == "failed":
            continue
        predictions = eval_store.get_eval_predictions(config, case_id=case["case_id"])
        if not predictions:
            continue

        # Only settle (prediction, horizon) pairs still missing a result.
        pending = []
        for pred in predictions:
            for h in horizons:
                existing = eval_store.get_eval_returns(
                    config, prediction_id=pred["prediction_id"], horizon=h)
                if existing and existing[0].get("net_return") is not None:
                    continue
                pending.append((pred, h))
        if not pending:
            continue

        entry = settle_entry(config, case["ticker"], case["selection_date"])
        entry_date, entry_price = entry["entry_date"], entry["entry_price"]

        if entry_date > as_of_date:
            summary["skipped"] += 1
            continue

        if entry_price is None:
            if _entry_grace_exceeded(entry_date, as_of_date, ecfg["entry_grace_days"], config):
                eval_store.mark_case_failed(config, case["case_id"],
                                            f"no entry price on {entry_date}")
                summary["failed"] += 1
            else:
                summary["skipped"] += 1
            continue

        full5 = False
        for pred, h in pending:
            exit_date = nth_trading_day(entry_date, h, config)
            if exit_date > as_of_date:
                summary["skipped"] += 1
                continue
            rec = settle_horizon(config, case["ticker"], entry_date, entry_price, h)
            if not rec["settled"]:
                summary["skipped"] += 1
                continue
            eval_store.save_eval_return(config, {
                "prediction_id": pred["prediction_id"],
                "case_id": case["case_id"],
                **rec,
            })
            summary["settled_horizons"] += 1
            if pred["variant"] == "full" and h == 5:
                full5 = True

        if full5:
            eval_store.mark_case_settled(config, case["case_id"])
            summary["settled"] += 1

    return summary


def run_weekly_evaluation(config=None, selection_date=None):
    """Sample this week's stocks and run the full pipeline (no settlement).

    Returns a summary dict. Idempotent per week: if the week already has cases,
    it is skipped so a retry never double-samples. Forward-testing settlement is
    deferred to :func:`settle_pending_cases` (daily scheduler), since the entry
    and 5/20/60-day exit prices are in the future at selection time.
    """
    from quantconclave.dataflows.config import get_config
    config = config or get_config()
    ecfg = get_eval_config(config)

    from web import eval_store

    selection_date = selection_date or _last_open_day(config)
    wk = week_key(selection_date)

    cases = eval_store.list_eval_cases(config, week_key=wk, source="weekly")
    resumed = bool(cases)
    expected_count = ecfg["csi300_count"] + ecfg["csi500_count"]

    # Freeze the whole deterministic slate before the first expensive analysis.
    # A retry reuses the stored slate and resumes only missing predictions.
    if len(cases) < expected_count:
        constituents = fetch_constituents(config, selection_date)
        st_codes, traded_codes, open_days = fetch_exclusion_sets(config, selection_date)
        current_tickers = {c["ticker"] for c in cases}
        recent = eval_store.recent_tickers(config, ecfg["dedup_weeks"]) - current_tickers
        sampled = sample_weekly_stocks(
            config, selection_date, constituents,
            recent_tickers=recent, st_codes=st_codes,
            traded_codes=traded_codes, open_days=open_days,
        )
        frozen_cases = []
        for stock in sampled:
            stable_id = hashlib.sha256(
                f"{wk}:{stock['code']}".encode("utf-8")
            ).hexdigest()[:16]
            frozen_cases.append({
                "case_id": stable_id,
                "week_key": wk,
                "ticker": stock["code"],
                "company_name": stock["name"],
                "index_source": stock["index_source"],
                "industry": stock["industry"],
                "selection_date": selection_date,
                "source": "weekly",
                "status": "pending",
            })
        eval_store.create_eval_cases(config, frozen_cases)
        cases = eval_store.list_eval_cases(config, week_key=wk, source="weekly")

    mcfg_hash = model_config_hash(config)
    summary = {"status": "ok", "week_key": wk, "selection_date": selection_date,
               "sampled": len(cases), "predictions": 0, "failed": 0,
               "skipped": 0, "resumed": resumed}

    for case in cases:
        if eval_store.get_full_prediction(config, case["case_id"]):
            summary["skipped"] += 1
            continue
        eval_store.mark_case_pending(config, case["case_id"])

        try:
            final_state, signal, stats = run_full_analysis_with_retry(
                config, case["ticker"], selection_date)
            rating = signal or "Hold"
            confidence = extract_confidence(final_state.get("final_trade_decision", ""))
            sms = None
            if rating in ("Buy", "Overweight"):
                try:
                    from quantconclave.sector_scan.smart_money_score import compute_smart_money_score
                    sms, _ = compute_smart_money_score(case["ticker"], config)
                except Exception:
                    sms = None

            snapshot_hash = input_snapshot_hash(
                case["ticker"], selection_date, _reports_fingerprint(final_state))
            snapshot_hash = eval_store.save_frozen_reports(
                config, case["case_id"], final_state, snapshot_hash
            )

            run_id = _persist_result_run(config, case["ticker"],
                                         case.get("company_name", ""), selection_date,
                                         rating, signal)

            prediction = {
                "prediction_id": uuid.uuid4().hex[:16],
                "case_id": case["case_id"],
                "variant": "full",
                "run_id": run_id,
                "rating": rating,
                "confidence": confidence,
                "smart_money_score": sms,
                "model_config_hash": mcfg_hash,
                "input_snapshot_hash": snapshot_hash,
                "llm_calls": stats.get("llm_calls", 0),
                "tool_calls": stats.get("tool_calls", 0),
                "tokens_in": stats.get("tokens_in", 0),
                "tokens_out": stats.get("tokens_out", 0),
                "estimated_cost": _estimate_run_cost(config, stats),
                "elapsed_ms": 0,
            }
            eval_store.create_eval_prediction(config, prediction)
            summary["predictions"] += 1
        except Exception as e:
            logger.exception("evaluation failed for %s", case["ticker"])
            eval_store.mark_case_failed(config, case["case_id"], str(e))
            summary["failed"] += 1

    return summary


def _persist_result_run(config, ticker, company_name, selection_date, rating, signal) -> str:
    """Save a result_runs row mirroring the full state the graph wrote to disk."""
    from quantconclave.dataflows.utils import safe_ticker_component
    from quantconclave.llm_clients.factory import resolve_role_llm
    from web.results_store import save_result

    deep_provider, _, _ = resolve_role_llm(config, "deep")
    quick_provider, _, _ = resolve_role_llm(config, "quick")
    json_path = f"{safe_ticker_component(ticker)}/QuantConclaveStrategy_logs/full_states_log_{selection_date}.json"
    return save_result(config, {
        "ticker": ticker,
        "date": selection_date,
        "company_name": company_name,
        "rating": rating,
        "signal": signal,
        "analysts": "capital_flow,market,social,news,fundamentals,competitor,partner",
        "provider": config.get("llm_provider", ""),
        "deep_model": config.get("deep_think_llm", ""),
        "quick_model": config.get("quick_think_llm", ""),
        "language": config.get("output_language", "Chinese"),
        "run_type": "evaluation",
        "scheduled_job_id": "",
        "json_path": json_path,
        "deep_provider": deep_provider,
        "quick_provider": quick_provider,
    })


def _estimate_run_cost(config, stats: dict) -> float:
    from quantconclave.llm_clients.factory import resolve_role_llm
    from .cost import estimate_cost
    _, model, _ = resolve_role_llm(config, "deep")
    return estimate_cost(stats.get("tokens_in", 0), stats.get("tokens_out", 0), model=model)


def select_ablation_cases(cases: list[dict], count: int, seed: int = 0) -> list[dict]:
    """Deterministically pick up to ``count`` cases from a month's weekly samples."""
    if not cases or count <= 0:
        return []
    rng = random.Random(seed)
    pool = sorted(cases, key=lambda c: (c.get("week_key", ""), c.get("ticker", "")))
    return rng.sample(pool, min(count, len(pool)))


def run_monthly_ablation(config=None, month_key=None):
    """Run the cheap single-model ablation on this month's weekly cases.

    Selects ``monthly_ablation_count`` cases deterministically (frozen before
    analysis) and runs one single-LLM call each against the frozen full-pipeline
    analyst reports. Settlement is deferred to :func:`settle_pending_cases`.
    """
    from quantconclave.dataflows.config import get_config
    config = config or get_config()
    ecfg = get_eval_config(config)

    from web import eval_store

    if month_key is None:
        from datetime import datetime
        month_key = datetime.now().strftime("%Y-%m")

    cases = eval_store.list_eval_cases(config, month_key=month_key, source="weekly")
    eligible = []
    for case in cases:
        if case.get("status") == "failed":
            continue
        if not eval_store.get_full_prediction(config, case["case_id"]):
            continue
        frozen = eval_store.load_frozen_reports(config, case["case_id"])
        if not any(frozen.values()):
            continue
        prepared = dict(case)
        prepared["_frozen_reports"] = frozen
        eligible.append(prepared)
    if len(eligible) > ecfg["max_monthly_cases"]:
        eligible = eligible[:ecfg["max_monthly_cases"]]
    selected = select_ablation_cases(
        eligible, ecfg["monthly_ablation_count"], seed=ecfg["seed"]
    )

    summary = {"status": "ok", "month_key": month_key, "selected": len(selected),
               "predictions": 0, "failed": 0, "skipped": 0}
    for case in selected:
        if eval_store.get_eval_predictions(
            config, case_id=case["case_id"], variant="single"
        ):
            summary["skipped"] += 1
            continue
        pred_id = hashlib.sha256(
            f"{case['case_id']}:single".encode("utf-8")
        ).hexdigest()[:16]
        try:
            frozen = case["_frozen_reports"]
            res = run_single_llm_ablation(config, case["ticker"], case["selection_date"], frozen)
            eval_store.create_eval_prediction(config, {
                "prediction_id": pred_id,
                "case_id": case["case_id"],
                "variant": "single",
                "run_id": None,
                "rating": res["rating"],
                "confidence": "",
                "smart_money_score": None,
                "model_config_hash": model_config_hash(config),
                "input_snapshot_hash": input_snapshot_hash(case["ticker"], case["selection_date"], res.get("analysis", "")),
                "llm_calls": 1,
                "tool_calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "estimated_cost": 0.0,
                "elapsed_ms": 0,
            })
            summary["predictions"] += 1
        except Exception as e:
            logger.exception("ablation failed for %s", case["ticker"])
            summary["failed"] += 1

    return summary
