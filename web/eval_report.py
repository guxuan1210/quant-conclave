"""Fixed-template evaluation reports + WeCom summaries (no LLM).

Reports recompute their numbers from the DB via
``quantconclave.evaluation.summary.compute_summary``, render a fixed markdown
card, archive it to ``eval_reports``, and push it through the configured WeCom
channel (webhook or 智能机器人). No LLM calls happen anywhere in this module.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_HORIZON_LABEL = {"week": "5日", "month": "20日", "quarter": "60日"}
_PERIOD_LABEL = {"week": "周报", "month": "月报", "quarter": "季报"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_pct(x, digits=2):
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def _fmt_num(x, digits=2):
    return "—" if x is None else f"{x:.{digits}f}"


def build_weekly_report(config: dict | None, week_key: str) -> dict:
    from quantconclave.evaluation.summary import compute_summary
    s = compute_summary(config, "week")
    week = next((w for w in s.get("weekly", []) if w["week_key"] == week_key), None)
    metrics = {
        "period": "week",
        "period_key": week_key,
        "horizon": 5,
        "portfolio_net_return": week["portfolio_net_return"] if week else None,
        "benchmark_return": week["benchmark_return"] if week else None,
        "excess_return": week["excess_return"] if week else None,
        "positions": week["positions"] if week else 0,
        "cum_excess_return": s.get("cum_excess_return"),
        "annualized_ir": s.get("annualized_ir"),
        "max_drawdown_delta": s.get("max_drawdown_delta"),
        "coverage_pct": s.get("coverage_pct"),
        "validity": s.get("validity", {}),
        "direction": s.get("direction", {}),
        "mature_weeks": s.get("mature_weeks"),
    }
    return {"metrics": metrics, "markdown": build_wecom_summary(metrics), "generated_at": _now()}


def build_monthly_report(config: dict | None, month_key: str) -> dict:
    from quantconclave.evaluation.summary import compute_summary
    s = compute_summary(config, "month")
    metrics = {
        "period": "month",
        "period_key": month_key,
        "horizon": 20,
        "cum_excess_return": s.get("cum_excess_return"),
        "annualized_ir": s.get("annualized_ir"),
        "max_drawdown_delta": s.get("max_drawdown_delta"),
        "coverage_pct": s.get("coverage_pct"),
        "validity": s.get("validity", {}),
        "direction": s.get("direction", {}),
        "ablation": s.get("ablation"),
    }
    return {"metrics": metrics, "markdown": build_wecom_summary(metrics), "generated_at": _now()}


def build_quarterly_report(config: dict | None, quarter_key: str) -> dict:
    from quantconclave.evaluation.summary import compute_summary
    s = compute_summary(config, "quarter")
    metrics = {
        "period": "quarter",
        "period_key": quarter_key,
        "horizon": 60,
        "cum_excess_return": s.get("cum_excess_return"),
        "annualized_ir": s.get("annualized_ir"),
        "max_drawdown_delta": s.get("max_drawdown_delta"),
        "coverage_pct": s.get("coverage_pct"),
        "validity": s.get("validity", {}),
        "direction": s.get("direction", {}),
    }
    return {"metrics": metrics, "markdown": build_wecom_summary(metrics), "generated_at": _now()}


def build_wecom_summary(metrics: dict) -> str:
    """Render a fixed markdown card from a metrics dict (≤3800 bytes)."""
    period = metrics.get("period", "week")
    label = _HORIZON_LABEL.get(period, "5日")
    title = _PERIOD_LABEL.get(period, "报告")
    lines = [f"# 📈 评测{title} · {label}", ""]
    lines.append(f"**周期**: {metrics.get('period_key', '')}")
    if metrics.get("portfolio_net_return") is not None:
        lines.append(f"**组合净收益**: {_fmt_pct(metrics.get('portfolio_net_return'))}")
        lines.append(f"**基准收益**: {_fmt_pct(metrics.get('benchmark_return'))}")
        lines.append(f"**超额收益**: {_fmt_pct(metrics.get('excess_return'))}")
    lines.append(f"**累计超额**: {_fmt_pct(metrics.get('cum_excess_return'))}")
    lines.append(f"**年化信息比率**: {_fmt_num(metrics.get('annualized_ir'))}")
    lines.append(f"**回撤差**: {_fmt_pct(metrics.get('max_drawdown_delta'))}")
    lines.append(f"**覆盖率**: {_fmt_pct(metrics.get('coverage_pct'), 1)}")
    direction = metrics.get("direction") or {}
    if direction.get("directional_total"):
        lines.append(f"**方向正确率**: {_fmt_pct(direction.get('accuracy'), 1)} "
                     f"({direction.get('correct')}/{direction.get('directional_total')})")
    validity = metrics.get("validity") or {}
    lines.append(f"**有效性**: {validity.get('status', '—')}")
    ablation = metrics.get("ablation")
    if ablation and ablation.get("mean_delta") is not None:
        lines.append(f"**消融增量(满-单)**: {_fmt_pct(ablation['mean_delta'])}")
    return "\n".join(lines)


def _push_markdown(config: dict | None, markdown: str) -> dict:
    from quantconclave.dataflows.config import get_config
    cfg = config or get_config()
    url = cfg.get("wecom_webhook_url", "")
    if url:
        from web.wecom_push import push_wecom
        res = push_wecom(url, markdown)
        return {"channel": "webhook", "ok": bool(res.get("ok")), "errmsg": res.get("errmsg") or ""}
    bot_id = cfg.get("wecom_bot_id", "")
    bot_secret = cfg.get("wecom_bot_secret", "")
    if bot_id and bot_secret:
        from web import wecom_bot
        wecom_bot.ensure_started(bot_id, bot_secret)
        res = wecom_bot.push_markdown(markdown)
        return {"channel": "bot", "ok": bool(res.get("ok")), "errmsg": res.get("errmsg") or ""}
    return {"channel": None, "ok": False, "errmsg": "未配置微信推送渠道"}


def push_eval_report(config: dict | None, period: str, period_key: str) -> dict:
    """Build, archive, and push a period report. Returns ``{report, push}``."""
    builders = {"week": build_weekly_report, "month": build_monthly_report,
                "quarter": build_quarterly_report}
    if period not in builders:
        raise ValueError(f"invalid period: {period}")
    report = builders[period](config, period_key)
    metrics = report["metrics"]
    validity_status = metrics.get("validity", {}).get("status")
    is_valid = {"effective": 1, "not_effective": 0}.get(validity_status)

    from web import eval_store
    eval_store.upsert_eval_report(config, {
        "period": period,
        "period_key": period_key,
        "generated_at": report["generated_at"],
        "summary": metrics,
        "report": {"markdown": report["markdown"]},
        "coverage_pct": metrics.get("coverage_pct"),
        "cum_excess_return": metrics.get("cum_excess_return"),
        "annualized_ir": metrics.get("annualized_ir"),
        "max_drawdown_delta": metrics.get("max_drawdown_delta"),
        "is_valid": is_valid,
    })
    push_res = _push_markdown(config, report["markdown"])
    return {"report": report, "push": push_res}
