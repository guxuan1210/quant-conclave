"""Single-LLM ablation for the monthly cheap-model comparison.

The user chose to reuse the existing Watchlist single-LLM path
(``WatchlistAnalysis`` → 看多/看空/观望) as the "cheap single model". One LLM
call reads the frozen full-pipeline analyst reports and produces a verdict,
which is mapped back to the 5-tier rating scale so the full pipeline and single
model can be compared on the same axis.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_VERDICT_TO_RATING = {"看多": "Buy", "看空": "Sell", "观望": "Hold"}


def map_verdict_to_rating(verdict: str) -> str:
    return _VERDICT_TO_RATING.get(verdict, "Hold")


def extract_verdict(text: str) -> str:
    """Pull 看多/看空/观望 out of a rendered WatchlistAnalysis markdown blob."""
    for line in (text or "").splitlines():
        if "看多" in line:
            return "看多"
        if "看空" in line:
            return "看空"
        if "观望" in line:
            return "观望"
    return "观望"


def build_ablation_prompt(ticker: str, selection_date: str, frozen_reports: dict | None) -> str:
    """Build a compact prompt feeding the frozen reports into a single verdict."""
    reports = frozen_reports or {}
    excerpt = []
    for key in ("capital_flow_report", "market_report", "sentiment_report",
                "news_report", "fundamentals_report", "competitor_report", "partner_report"):
        text = str(reports.get(key, "")).strip()
        if text:
            excerpt.append(f"## {key}\n{text[:1200]}")
    body = "\n\n".join(excerpt) if excerpt else "(无分析师报告)"

    return (
        f"你是一名 A 股短线资金面分析员。以下是对 {ticker}（数据截至 {selection_date}）"
        f"的多智能体分析师报告。请仅基于这些冻结报告，给出看多/看空/观望三选一的结论，"
        f"并按要求填写各项分析字段。\n\n{body}"
    )


def run_single_llm_ablation(
    config: dict | None,
    ticker: str,
    selection_date: str,
    frozen_reports: dict | None = None,
) -> dict:
    """Run one cheap single-LLM call and return ``{rating, verdict, analysis}``.

    Uses the quick-thinking model via the Watchlist structured-output path.
    Returns a ``Hold``-mapped result (观望) on any failure so a model outage is
    recorded as a neutral observation rather than aborting the ablation run.
    """
    from quantconclave.agents.schemas import WatchlistAnalysis, render_watchlist_analysis
    from quantconclave.agents.utils.structured import bind_structured, invoke_structured_or_freetext
    from quantconclave.llm_clients.factory import create_llm_client, resolve_role_llm

    try:
        provider, model, base_url = resolve_role_llm(config, "quick")
        client = create_llm_client(provider=provider, model=model, base_url=base_url, timeout=300)
        llm = client.get_llm()
        structured_llm = bind_structured(llm, WatchlistAnalysis, "Watchlist")
        prompt = build_ablation_prompt(ticker, selection_date, frozen_reports)
        text = invoke_structured_or_freetext(
            structured_llm, llm, prompt, render_watchlist_analysis, "Watchlist",
        )
        verdict = extract_verdict(text)
        return {"rating": map_verdict_to_rating(verdict), "verdict": verdict, "analysis": text}
    except Exception as e:
        logger.warning("single-LLM ablation failed for %s: %s", ticker, e)
        return {"rating": "Hold", "verdict": "观望", "analysis": f"(ablation failed: {e})"}
