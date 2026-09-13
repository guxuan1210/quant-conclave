"""RRG transition statistics — track how industries move through quadrants over time."""

from __future__ import annotations
import json
import logging
import math
import os
import sqlite3
from collections import defaultdict
from statistics import median

logger = logging.getLogger(__name__)


def _get_db_path(cfg: dict) -> str:
    """Get path to results database (mirrors web.results_store._get_db_path)."""
    results_dir = cfg.get("results_dir", "")
    return os.path.join(results_dir, "results.db")


def _distribution(lst: list[float]) -> dict:
    """Compute min/median/max/p25/p75/p90 for a list of numbers."""
    if not lst:
        return {"count": 0, "min": 0, "max": 0, "median": 0, "avg": 0, "p25": 0, "p75": 0, "p90": 0}
    s = sorted(lst)
    n = len(s)

    def _pctile(p):
        k = (n - 1) * p / 100.0
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return s[int(k)]
        return round(s[f] * (c - k) + s[c] * (k - f), 4)

    return {
        "count": n, "min": round(s[0], 1), "max": round(s[-1], 1),
        "median": round(median(s), 1), "avg": round(sum(s) / n, 1),
        "p25": round(_pctile(25), 1), "p75": round(_pctile(75), 1),
        "p90": round(_pctile(90), 1),
    }


def _try_parse_tail(raw: str | None) -> list[dict]:
    """Safely parse tail_json string, return [] on failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _compute_velocity(tail_entries: list[dict]) -> tuple[float, float, str]:
    """Compute speed and acceleration from tail trajectory in RRG (rs_ratio, rs_momentum) space.

    Returns (avg_speed, acceleration, direction) where:
      - avg_speed: mean Euclidean distance between consecutive tail points
      - acceleration: change in speed (latest - prior), + = accelerating
      - direction: "toward_leading" / "away_from_leading" / "stable" based on rs_ratio delta
    """
    if len(tail_entries) < 2:
        return (0.0, 0.0, "stable")
    speeds = []
    for i in range(1, len(tail_entries)):
        dr = tail_entries[i].get("rs_ratio", 0) - tail_entries[i - 1].get("rs_ratio", 0)
        dm = tail_entries[i].get("rs_momentum", 0) - tail_entries[i - 1].get("rs_momentum", 0)
        speeds.append(math.sqrt(dr * dr + dm * dm))
    avg_speed = round(sum(speeds) / len(speeds), 4)
    if len(speeds) >= 2:
        acceleration = round(speeds[-1] - speeds[-2], 4)
    else:
        acceleration = 0.0
    # Direction: use rs_ratio change over the last 3 entries (or fewer)
    recent = tail_entries[-min(3, len(tail_entries)):]
    ratio_delta = recent[-1].get("rs_ratio", 0) - recent[0].get("rs_ratio", 0)
    if ratio_delta > 0.01:
        direction = "toward_leading"
    elif ratio_delta < -0.01:
        direction = "away_from_leading"
    else:
        direction = "stable"
    return (avg_speed, acceleration, direction)


def compute_transition_stats(cfg: dict, lookback_days: int = 60) -> dict:
    """Analyze RRG snapshot history to compute rotation statistics.

    Returns dict with:
    - avg_days_improving_to_leading, avg_days_leading_to_weakening,
      avg_days_weakening_to_lagging, avg_days_lagging_to_improving
    - current_streaks: {industry_name: {quadrant, days_in_quadrant}}
    - top_longest_leading: [(name, days)]
    - top_longest_improving: [(name, days)]
    - quadrant_flow: [{date, leading, improving, weakening, lagging}]
    - latest_date, snapshots_count
    """
    db_path = _get_db_path(cfg)
    if not os.path.exists(db_path):
        return {"error": "No RRG snapshot data yet", "latest_date": ""}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Limit data window to the most recent N unique trading dates
    distinct_dates = conn.execute(
        "SELECT DISTINCT date FROM rrg_snapshots ORDER BY date DESC LIMIT ?",
        (lookback_days,)
    ).fetchall()

    if not distinct_dates:
        conn.close()
        return {"error": "No RRG snapshot data", "latest_date": ""}

    cutoff_date = distinct_dates[-1][0]  # oldest date in the window

    rows = conn.execute(
        "SELECT date, industry_name, quadrant FROM rrg_snapshots "
        "WHERE date >= ? ORDER BY date, industry_name",
        (cutoff_date,)
    ).fetchall()

    if not rows:
        conn.close()
        return {"error": "No RRG snapshot data", "latest_date": ""}

    # Organize by industry over time
    industry_history: dict[str, list[tuple[str, str]]] = defaultdict(list)
    all_dates = set()
    for r in rows:
        industry_history[r["industry_name"]].append((r["date"], r["quadrant"]))
        all_dates.add(r["date"])

    dates_sorted = sorted(all_dates)

    # Current streaks
    current_streaks = {}
    for name, history in industry_history.items():
        history.sort(key=lambda x: x[0])
        if not history:
            continue
        latest_quadrant = history[-1][1]
        days = 0
        for date, quad in reversed(history):
            if quad == latest_quadrant:
                days += 1
            else:
                break
        current_streaks[name] = {"quadrant": latest_quadrant, "days_in_quadrant": days}

    # Transition durations
    transition_durations: dict[str, list[int]] = {
        "improving_to_leading": [],
        "leading_to_weakening": [],
        "weakening_to_lagging": [],
        "lagging_to_improving": [],
    }

    for name, history in industry_history.items():
        history.sort(key=lambda x: x[0])
        current_quad = history[0][1]
        current_start_idx = 0
        for i, (date, quad) in enumerate(history):
            if quad != current_quad:
                duration = i - current_start_idx
                if duration > 0:
                    key = f"{current_quad}_to_{quad}"
                    if key in transition_durations:
                        transition_durations[key].append(duration)
                current_quad = quad
                current_start_idx = i

    def _avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else 0

    # Top streaks
    leading_streaks = [(n, s["days_in_quadrant"]) for n, s in current_streaks.items() if s["quadrant"] == "leading"]
    leading_streaks.sort(key=lambda x: x[1], reverse=True)
    improving_streaks = [(n, s["days_in_quadrant"]) for n, s in current_streaks.items() if s["quadrant"] == "improving"]
    improving_streaks.sort(key=lambda x: x[1], reverse=True)

    # Daily quadrant counts
    date_quadrant_counts = defaultdict(lambda: {"leading": 0, "improving": 0, "weakening": 0, "lagging": 0})
    for r in rows:
        date_quadrant_counts[r["date"]][r["quadrant"]] += 1

    quadrant_flow = [
        {"date": d, **counts}
        for d, counts in sorted(date_quadrant_counts.items())
    ]

    # --- New data blocks ---

    # 1. Transition distributions
    transition_distributions = {}
    for key, dur_list in transition_durations.items():
        if dur_list:
            transition_distributions[key] = _distribution(dur_list)

    # 2. Improving success rate
    improving_episodes = 0
    improving_to_leading_count = 0
    improving_to_lagging_count = 0
    for name, history in industry_history.items():
        history.sort(key=lambda x: x[0])
        for i in range(1, len(history)):
            prev_q = history[i - 1][1]
            curr_q = history[i][1]
            if prev_q == "improving":
                improving_episodes += 1
                if curr_q == "leading":
                    improving_to_leading_count += 1
                elif curr_q == "lagging":
                    improving_to_lagging_count += 1
    improving_success_rate = {
        "success_pct": round(improving_to_leading_count / improving_episodes * 100, 1) if improving_episodes else 0,
        "improving_to_leading": improving_to_leading_count,
        "improving_episodes": improving_episodes,
        "fell_to_lagging": improving_to_lagging_count,
    }

    # 3. Recent quadrant changes (latest snapshot vs previous)
    recent_changes: dict[str, list[str]] = {
        "entered_leading": [], "entered_improving": [],
        "left_leading": [], "left_improving": [],
    }
    if len(dates_sorted) >= 2:
        prev_date = dates_sorted[-2]
        latest_date = dates_sorted[-1]
        latest_quad_map = {}
        prev_quad_map = {}
        for r in rows:
            if r["date"] == latest_date:
                latest_quad_map[r["industry_name"]] = r["quadrant"]
            elif r["date"] == prev_date:
                prev_quad_map[r["industry_name"]] = r["quadrant"]
        for name, q in latest_quad_map.items():
            pq = prev_quad_map.get(name)
            if pq is None:
                continue
            if q == "leading" and pq != "leading":
                recent_changes["entered_leading"].append(name)
            if q == "improving" and pq != "improving":
                recent_changes["entered_improving"].append(name)
            if pq == "leading" and q != "leading":
                recent_changes["left_leading"].append(name)
            if pq == "improving" and q != "improving":
                recent_changes["left_improving"].append(name)

    # 4. Velocity & trend indicators from tail_json
    tail_rows = conn.execute(
        "SELECT industry_name, tail_json FROM rrg_snapshots WHERE date = ?",
        (dates_sorted[-1],)
    ).fetchall() if dates_sorted else []

    velocities = {}
    for tr in tail_rows:
        tail_data = _try_parse_tail(tr["tail_json"])
        if tail_data:
            speeds, accel, direction = _compute_velocity(tail_data)
            velocities[tr["industry_name"]] = {
                "speed": speeds, "acceleration": accel, "direction": direction,
            }

    # Sort by speed descending
    speed_ranking = sorted(velocities.items(), key=lambda x: x[1]["speed"], reverse=True)
    velocity = {
        "top_fastest": [(n, v["speed"]) for n, v in speed_ranking[:5]],
        "top_accelerating": sorted(
            [(n, v["acceleration"]) for n, v in velocities.items() if v["acceleration"] > 0],
            key=lambda x: x[1], reverse=True
        )[:5],
        "top_decelerating": sorted(
            [(n, v["acceleration"]) for n, v in velocities.items() if v["acceleration"] < 0],
            key=lambda x: x[1]
        )[:5],
    }

    # 5. Trend indicators
    toward = sum(1 for v in velocities.values() if v["direction"] == "toward_leading")
    away = sum(1 for v in velocities.values() if v["direction"] == "away_from_leading")
    stable_count = sum(1 for v in velocities.values() if v["direction"] == "stable")
    avg_rs_ratio = 0.0
    avg_rs_momentum = 0.0
    if tail_rows:
        rs_ratios = []
        rs_momentums = []
        for tr in tail_rows:
            conn_rs = tr["industry_name"]
            if conn_rs not in velocities:
                continue
            tail_data = _try_parse_tail(tr["tail_json"])
            if tail_data:
                rs_ratios.append(tail_data[-1].get("rs_ratio", 0))
                rs_momentums.append(tail_data[-1].get("rs_momentum", 0))
        if rs_ratios:
            avg_rs_ratio = round(sum(rs_ratios) / len(rs_ratios), 4)
            avg_rs_momentum = round(sum(rs_momentums) / len(rs_momentums), 4)
    trend_indicators = {
        "toward_leading": toward, "away_from_leading": away, "stable": stable_count,
        "avg_rs_ratio": avg_rs_ratio, "avg_rs_momentum": avg_rs_momentum,
    }

    conn.close()

    return {
        "avg_days_improving_to_leading": _avg(transition_durations.get("improving_to_leading", [])),
        "avg_days_leading_to_weakening": _avg(transition_durations.get("leading_to_weakening", [])),
        "avg_days_weakening_to_lagging": _avg(transition_durations.get("weakening_to_lagging", [])),
        "avg_days_lagging_to_improving": _avg(transition_durations.get("lagging_to_improving", [])),
        "current_streaks": current_streaks,
        "transition_counts": {
            "improving_to_leading": len(transition_durations.get("improving_to_leading", [])),
            "leading_to_weakening": len(transition_durations.get("leading_to_weakening", [])),
        },
        "top_longest_leading": leading_streaks[:10],
        "top_longest_improving": improving_streaks[:10],
        "quadrant_flow": quadrant_flow[-30:],
        "latest_date": dates_sorted[-1] if dates_sorted else "",
        "snapshots_count": len(dates_sorted),
        "transition_distributions": transition_distributions,
        "improving_success_rate": improving_success_rate,
        "recent_changes": recent_changes,
        "velocity": velocity,
        "trend_indicators": trend_indicators,
    }
