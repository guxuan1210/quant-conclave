"""Cross-validate a ticker's moneyflow between two independent pipelines.

Primary  = EastMoney-derived 逐日: akshare ``stock_individual_fund_flow`` first,
           falling back to tushare ``moneyflow`` (doc_id=25, also EastMoney data)
           where akshare is unreachable (proxy-blocked on this machine).
Verifier = tushare ``moneyflow_dc`` — 同花顺's OWN 主力资金 aggregation, a
           genuinely independent vendor from EastMoney, so drift between the two
           is a real signal of data problems, not a round-off.

Both sources report YUAN amounts. Window sums (5/10/20 日) are rendered in
万元 for readability. A window is skipped (not judged) when its magnitude is
below the noise floor (~100 万元) so tiny flows never fake a "偏差".

Both sides measure the SAME quantity — 主力净流入 — so a drift between them is
a real cross-vendor signal, not a field-semantics artifact:
  Primary EastMoney side = 主力 = 超大单 + 大单 (net_elg_amount + net_lg_amount).
      Never its raw ``net_amount`` column (that is tushare ``net_mf_amount``,
      铁律#6: an ill-defined 净流入额 — on limit-up days it can report e.g.
      -7982万 while 超大单+大单 = +3628万, opposite to every vendor).
  Verifier 同花顺 side   = moneyflow_dc ``net_amount``, 同花顺's own 主力净流入 —
      empirically ≈ 东财主力 within a few % (001366 33 日逐日对比几乎全部同向),
      so comparing it against 东财主力 is apples-to-apples.

Exposed to advisors as the ``verify_moneyflow`` tool — a data-integrity
check, NOT a hard scoring gate (the SMS engine keeps scoring independently;
an advisor consults this when the score or its underlying data looks
suspicious).
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Noise floor in yuan: window nets below ~100 万元 are skipped, not judged.
NOISE_FLOOR_YUAN = 1e6

# Window labels → day counts (most-recent-N rows of the chronological series).
WINDOWS = (("5日", 5), ("10日", 10), ("20日", 20))


def _parse_moneyflow_csv(text: str) -> List[Dict]:
    """Parse a moneyflow vendor blob into chronological rows.

    Vendors return `#`-comment header lines followed by a CSV (header row +
    data). tushare sorts NEWEST-first, akshare chronological — normalize to
    chronological so the "last N rows" window always means the most recent N
    trading days.
    """
    if not text:
        return []
    lines = str(text).split("\n")
    header_idx = next(
        (i for i, l in enumerate(lines) if l and not l.startswith("#")),
        None,
    )
    if header_idx is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    rows = []
    for r in reader:
        try:
            net = _row_main_force_net(r)
            rows.append({
                "date": str(r.get("trade_date", "")).strip(),
                "net_amount": net,
            })
        except (ValueError, KeyError):
            continue
    rows.sort(key=lambda r: r["date"] or "")
    return rows


def _row_main_force_net(r: Dict) -> float:
    """Extract a day's 主力净流 (yuan) from one vendor CSV row.

    东财 tushare moneyflow (doc_id=25) carries BOTH the raw ``net_amount`` column
    (its ``net_mf_amount`` — 铁律#6, ill-defined: 002696/001366 涨停日可报 -5052万/
    -7982万 while 超大单+大单 = +3225万/+3628万) AND per-tier ``net_elg_amount`` /
    ``net_lg_amount``. The only usable field is 主力 = net_elg_amount + net_lg_amount,
    so prefer it whenever those columns exist. akshare 东财 and 同花顺 moneyflow_dc
    expose no ``net_*_amount`` columns and their ``net_amount`` IS already 主力 —
    those fall through to ``net_amount``/``net_mf_amount`` unchanged.
    """
    if "net_elg_amount" in r and "net_lg_amount" in r:
        try:
            return (float(r.get("net_elg_amount") or 0)
                    + float(r.get("net_lg_amount") or 0))
        except (TypeError, ValueError):
            pass
    return float(r.get("net_amount", r.get("net_mf_amount", 0)) or 0)


def _fetch_source(
    func, ticker: str
) -> Optional[List[Dict]]:
    """Call a moneyflow vendor function, parse rows, return None on unavailability."""
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        text = func(ticker=ticker, start_date=start, end_date=today)
    except Exception as e:
        logger.warning("verify_moneyflow fetch failed for %s: %s", ticker, e)
        return None
    if not isinstance(text, str) or text.startswith("# SKIP_VENDOR:"):
        return None
    rows = _parse_moneyflow_csv(text)
    return rows if len(rows) >= 5 else None


def verify_moneyflow(
    ticker: str,
    flow_rows: Optional[List[Dict]] = None,
    config: Optional[dict] = None,
) -> Dict:
    """Cross-verify a ticker's moneyflow between EastMoney-derived and 同花顺.

    Args:
        ticker: CN A-share symbol ("600030" or "600030.SH").
        flow_rows: optional rows the caller already holds (same schema as the
            SMS engine: {"net_amount": yuan, ...}); used as the primary side
            instead of a fresh fetch.
        config: app config dict (reads ``sms_crosscheck_max_ratio``).

    Returns:
        Structured dict (see module docstring), ready for ``_render_verify_moneyflow``.
    """
    config = config or {}
    max_ratio = float(config.get("sms_crosscheck_max_ratio", 10.0))

    if flow_rows:
        primary_rows = sorted(
            [r for r in flow_rows if r.get("net_amount") is not None],
            key=lambda r: r.get("date", "") or "",
        )
        primary_src = "SMS引擎数据"
    else:
        # Primary: akshare 东财逐日 first; where it is unreachable (proxy-blocked
        # on this machine), fall back to tushare moneyflow (still EastMoney data)
        # so the cross-check still runs against the independent 同花顺 side.
        from quantconclave.dataflows.akshare_data import get_money_flow_akshare
        primary_rows = _fetch_source(get_money_flow_akshare, ticker)
        if primary_rows:
            primary_src = "东财逐日(akshare)"
        else:
            from quantconclave.dataflows.tushare_data import get_money_flow as get_tushare_money_flow
            primary_rows = _fetch_source(get_tushare_money_flow, ticker)
            primary_src = "东财逐日(tushare)"

    # Verifier: 同花顺 (tushare moneyflow_dc) — genuinely independent of EastMoney.
    from quantconclave.dataflows.tushare_data import get_money_flow_dc as get_ths_money_flow
    verifier_rows = _fetch_source(get_ths_money_flow, ticker)
    verifier_src = "同花顺逐日(tushare moneyflow_dc)"

    windows = []
    deviations = []
    for label, n in WINDOWS:
        win = {"window": label, "primary_wan": None, "verifier_wan": None,
               "ratio": None, "direction": None, "note": ""}
        prim_net = sum(r["net_amount"] for r in primary_rows[-n:]) if primary_rows else None
        ver_net = sum(r["net_amount"] for r in verifier_rows[-n:]) if verifier_rows else None
        if prim_net is not None:
            win["primary_wan"] = round(prim_net / 1e4)
        if ver_net is not None:
            win["verifier_wan"] = round(ver_net / 1e4)
        if prim_net is None or ver_net is None:
            win["note"] = "缺一侧数据"
            windows.append(win)
            continue
        if abs(prim_net) < NOISE_FLOOR_YUAN or abs(ver_net) < NOISE_FLOOR_YUAN:
            win["note"] = "量级过小(<100万)，不判偏差"
            windows.append(win)
            continue
        larger = max(abs(prim_net), abs(ver_net))
        smaller = max(min(abs(prim_net), abs(ver_net)), 1e-6)
        ratio = larger / smaller
        win["ratio"] = round(ratio, 2)
        same_dir = (prim_net >= 0) == (ver_net >= 0)
        win["direction"] = "同向" if same_dir else "反向"
        if not same_dir:
            win["note"] = f"方向相反！主源{win['primary_wan']:+}万 vs 验证{win['verifier_wan']:+}万"
            deviations.append(win)
        elif ratio > max_ratio:
            win["note"] = f"偏差{ratio:.1f}倍 > 阈值{max_ratio:.0f}倍"
            deviations.append(win)
        windows.append(win)

    if not primary_rows and not verifier_rows:
        verdict = "数据缺失"
        note = f"{ticker}: 主源({primary_src})与验证源({verifier_src})均不可用"
    elif not primary_rows:
        verdict = "数据缺失"
        note = f"{ticker}: 主源({primary_src})不可用，无法交叉验证"
    elif not verifier_rows:
        verdict = "数据缺失"
        note = f"{ticker}: 验证源({verifier_src})不可用，无法交叉验证"
    elif deviations:
        verdict = "偏差"
        note = f"{ticker}: {len(deviations)}个窗口与同花顺交叉验证出现偏差"
    else:
        verdict = "一致"
        note = f"{ticker}: 主源({primary_src})与同花顺逐日在5/10/20日窗口一致"

    return {
        "ticker": ticker,
        "primary_source": primary_src,
        "verifier_source": verifier_src,
        "max_ratio": max_ratio,
        "windows": windows,
        "verdict": verdict,
        "note": note,
    }


def _render_verify_moneyflow(d: Dict) -> str:
    """Render the verify result as markdown for an LLM tool reply."""
    lines = [
        f"## 资金流交叉验证 · {d['ticker']}",
        f"- **结论**: {d['verdict']} — {d['note']}",
        f"- 主源: {d['primary_source']}（元） | 验证源: {d['verifier_source']}（元）"
        f" | 偏差阈值: {d['max_ratio']:g}倍",
        "",
        "| 窗口 | 主源净流(万元) | 验证净流(万元) | 偏差倍数 | 方向 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for w in d["windows"]:
        p = "N/A" if w["primary_wan"] is None else f"{w['primary_wan']:+,d}"
        v = "N/A" if w["verifier_wan"] is None else f"{w['verifier_wan']:+,d}"
        ratio = "—" if w["ratio"] is None else f"{w['ratio']:.1f}×"
        direction = w["direction"] or "—"
        lines.append(
            f"| {w['window']} | {p} | {v} | {ratio} | {direction} | {w['note']} |"
        )
    return "\n".join(lines)
