"""Skill Generator: convert resolved experiences into versioned SKILL.md files.

Called only after explicit user confirmation from the Skill Control UI.
Reads active experiences from the advisory_experience table, formats them as
rules/sections, and writes v{N+1} to .claude/skills/analyst-core/.

Enhancements over the original generator (aligned with the QuantSpace SKILL.md
convention):

- **Dedup**: identical rule content (by normalized hash) is emitted once;
  near-identical rules (same lesson_abstract, SequenceMatcher >= 0.85) keep
  only the newest. Rules already present in the currently-active version are
  skipped entirely.
- **Cap**: at most ``MAX_RULES`` rules enter the SKILL.md; the oldest active
  experiences beyond the cap are auto-archived.
- **Grouping**: rules are grouped by ``category`` with ``### Category:``
  section headers.
- **Template**: the generated SKILL.md gains QuantSpace-style ``## Description``
  / ``## Prerequisites`` / ``## Components`` / ``## Recipes`` sections above
  the ``## Experience-Derived Rules`` block. The two markers that
  ``build_instrument_context`` slices on (``## Experience-Derived Rules`` →
  ``## Core Principles``) keep their relative order, so injection is unchanged.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_DIR = Path(".claude/skills/analyst-core")
SKILL_INDEX = SKILL_DIR / "index.json"

MAX_RULES = 30
AUTO_ARCHIVE_TRIM = True


def _parse_version(fname: str) -> int:
    """Extract version number from filename like 'v3-2026-06-27-name.md'."""
    m = re.match(r"v(\d+)", fname)
    return int(m.group(1)) if m else 0


def _next_version() -> int:
    """Determine the next version number."""
    if not SKILL_DIR.exists():
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        return 1
    versions = [0]
    for f in SKILL_DIR.iterdir():
        if f.suffix == ".md":
            v = _parse_version(f.name)
            if v:
                versions.append(v)
    return max(versions) + 1


def _load_skill_index() -> dict:
    """Load existing skill index, or return default."""
    if SKILL_INDEX.exists():
        try:
            return json.loads(SKILL_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"analyst": {"versions": []}}


def _save_skill_index(index: dict) -> None:
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    SKILL_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _content_hash(content: str) -> str:
    """Normalized content hash used for exact dedup."""
    normalized = re.sub(r"\s+", " ", content.strip()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _extract_active_rule_hashes() -> set[str]:
    """Return the set of rule-content hashes present in the active version."""
    path = get_active_skill_path()
    if not path or not os.path.exists(path):
        return set()
    text = Path(path).read_text(encoding="utf-8")
    # Pull every `#### Rule:` / `### Rule:` block's content.
    blocks = re.findall(r"#+\s*Rule:.*?\n(.*?)(?=\n#+\s*Rule:|\n## |\Z)", text, re.DOTALL)
    return {_content_hash(b.strip()) for b in blocks if b.strip()}


def _dedup_rules(experiences: list[dict]) -> list[dict]:
    """Remove exact + near-duplicate experiences, keeping the newest.

    - Exact dedup: same normalized content hash.
    - Near dedup: same lesson_abstract and SequenceMatcher similarity >= 0.85.
    - Cross-version: skip any rule already present in the active version.
    """
    active_hashes = _extract_active_rule_hashes()

    # Sort newest-first so dedup keeps the most recent entry (None created_at → oldest).
    experiences = sorted(
        experiences,
        key=lambda e: e.get("created_at") or "",
        reverse=True,
    )

    seen_hashes: set[str] = set()
    seen_abstracts: dict[str, str] = {}
    kept: list[dict] = []
    for exp in experiences:
        content = exp.get("content", "")
        if not content:
            continue
        h = _content_hash(content)
        if h in seen_hashes or h in active_hashes:
            continue  # exact duplicate (or already in active version)
        abstract = exp.get("lesson_abstract", "") or ""
        near_dup = False
        if abstract:
            # Near-dup requires BOTH the same lesson_abstract AND high content
            # similarity (≥0.85). Different abstracts with similar wording are
            # treated as distinct rules, not duplicates.
            for prev_abstract, prev_content in seen_abstracts.items():
                if prev_abstract == abstract and prev_content:
                    if difflib.SequenceMatcher(None, content, prev_content).ratio() >= 0.85:
                        near_dup = True
                        break
        if near_dup:
            continue
        seen_hashes.add(h)
        seen_abstracts[abstract] = content
        kept.append(exp)
    return kept


def _experiences_to_rules(experiences: list[dict]) -> list[str]:
    """Convert deduped experiences into category-grouped rule blocks.

    Output shape (grouped by category, newest category first):

        ### Category: capital_flow
        #### Rule: <lesson_abstract or category>
        <content>

    The ``### Category:`` / ``#### Rule:`` headers preserve enough of the old
    ``### Rule:`` marker for any downstream text extraction to still find rules.
    """
    from collections import defaultdict

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for exp in experiences:
        by_cat[exp.get("category", "other") or "other"].append(exp)

    rules: list[str] = []
    for cat in sorted(by_cat.keys()):
        entries = by_cat[cat]
        rules.append(f"### Category: {cat}")
        for exp in entries:
            content = exp.get("content", "")
            abstract = exp.get("lesson_abstract", "") or cat
            if not content:
                continue
            rules.append(f"#### Rule: {abstract}")
            rules.append("")
            rules.append(content.strip())
            rules.append("")
    return rules


def _top_recipes(rules: list[dict], limit: int = 3) -> list[str]:
    """Turn the top-N rules into 'When X → do Y' recipe snippets."""
    from collections import defaultdict

    by_abstract = defaultdict(int)
    for exp in rules:
        a = exp.get("lesson_abstract", "")
        if a:
            by_abstract[a] += 1
    top = [a for a, _ in sorted(by_abstract.items(), key=lambda kv: -kv[1])[:limit]]

    recipes = []
    for i, abstract in enumerate(top, 1):
        matched = next((e for e in rules if e.get("lesson_abstract", "") == abstract), None)
        if matched is None:
            continue
        content = (matched.get("content", "") or "").strip()
        snippet = " ".join(content.split())[:160]
        recipes.append(f"{i}. **{abstract}** — {snippet}")
    return recipes


def _build_skill_md(
    version: int,
    rule_blocks: list[str],
    exp_count: int,
    stats: dict | None = None,
    recipes: list[str] | None = None,
) -> str:
    """Build the full SKILL.md content for this version."""
    today = datetime.now().strftime("%Y-%m-%d")
    # Rule count = number of `#### Rule:` blocks, not total markdown lines.
    rule_count = sum(1 for b in rule_blocks if b.startswith("#### Rule:"))

    lines = [
        "---",
        "name: quantconclave-analyst-core",
        f"description: Auto-generated skill v{version} from resolved backtest experiences. "
        "Use when running the QuantConclave deep analysis pipeline (7 analysts + bull/bear "
        "debate + risk debate + Portfolio Manager). Injected into every analyst SystemMessage "
        "via build_instrument_context. Especially when capital flow (主力资金) is involved.",
        f"version: {version}",
        f"created: {today}",
    ]
    if stats:
        lines.append(f"source_loop: calibration run | bearish_acc={stats.get('accuracy', '?')}%")
    lines.extend([
        "---",
        "# QuantConclave Analyst Core Skill",
        "",
        f"*Auto-generated v{version} on {today}*",
        "",
    ])

    # ── Description / Prerequisites / Components / Recipes (QuantSpace style) ──
    lines.extend([
        "## Description",
        "",
        "由已解决回测经验自动提炼的提示规则集，随经验累积滚动更新。"
        f"当前版本由 {exp_count} 条活跃经验提炼为 {rule_count} 条规则（上限 {MAX_RULES}，超出自动归档最旧）。",
        "",
        "## Prerequisites",
        "",
        f"- 注入路径：quantconclave/agents/utils/agent_utils.py::build_instrument_context",
        f"- 活跃版本 v{version}，规则 {rule_count} 条（来自 {exp_count} 条活跃经验）",
        "- 注入范围：全部分析流水线 agent（7 分析师 + 牛熊辩论 + 风险辩论 + 投资经理）",
        "",
    ])

    if stats:
        lines.extend([
            "## Current Stats",
            f"- Total resolved entries: {stats.get('total', '?')}",
            f"- Bearish accuracy: {stats.get('accuracy', '?')}%",
            f"- Bearish rate: {stats.get('bearish_rate', '?')}%",
            "",
        ])

    lines.extend([
        "## Components",
        "",
        "| Section | Purpose |",
        "|---------|---------|",
        "| Experience-Derived Rules | 从已解决回测/经验中提取的可执行规则，按 category 分组 |",
        "| Core Principles | 5 条固定基石原则（主力资金优先/信任资金不信叙事/对称操纵/5-20日/散户优先） |",
        "",
    ])

    if recipes:
        lines.extend([
            "## Recipes",
            "",
            *recipes,
            "",
        ])

    # ── Experience-Derived Rules (the block build_instrument_context slices) ──
    if rule_blocks:
        lines.append("## Experience-Derived Rules")
        lines.append("")
        lines.extend(rule_blocks)
        lines.append("")

    lines.extend([
        "## Core Principles",
        "",
        "1. **Smart money first** -- Capital flow (主力资金) is the primary signal.",
        "2. **Trust money, not narrative** -- When capital flow contradicts sentiment, trust the flow.",
        "3. **Symmetric manipulation** -- Bull traps AND bear traps are equally dangerous.",
        "4. **5-20 day horizon** -- Long-term structural trends are context only.",
        "5. **Chinese retail first** -- Highlight actionable price levels.",
        "",
    ])

    return "\n".join(lines)


def _archive_oldest_active(experiences: list[dict], keep: int) -> int:
    """Archive the oldest active experiences beyond ``keep``. Returns count."""
    if not AUTO_ARCHIVE_TRIM or len(experiences) <= keep:
        return 0
    from quantconclave.advisory.experience_store import archive_experience

    to_archive = experiences[keep:]  # already sorted newest-first
    archived = 0
    for exp in to_archive:
        eid = exp.get("id")
        if eid:
            try:
                archive_experience(eid)
                archived += 1
            except Exception as e:
                logger.warning("Failed to archive experience %s: %s", eid, e)
    return archived


def generate_skill_version(config: dict) -> dict:
    """Generate or update the Analyst SKILL.md based on resolved experiences.

    Returns dict with version, file_path, rule_count.
    """
    from quantconclave.advisory.experience_store import list_experiences

    all_exp = list_experiences()
    active = [e for e in all_exp if e.get("status") == "active"]

    stats = None
    try:
        results_dir = config.get("results_dir", "")
        if results_dir:
            import sqlite3

            db_path = str(Path(results_dir) / "results.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                row = conn.execute(
                    "SELECT stats FROM calibration_runs ORDER BY run_date DESC LIMIT 1"
                ).fetchone()
                conn.close()
                if row and row[0]:
                    stats = json.loads(row[0])
    except Exception as e:
        logger.warning("Could not load calibration stats: %s", e)

    # Dedup + cap
    deduped = _dedup_rules(active)
    capped = deduped[:MAX_RULES]
    archived = _archive_oldest_active(deduped, MAX_RULES)
    if archived:
        logger.info("Archived %d oldest active experiences beyond the %d-rule cap", archived, MAX_RULES)

    rule_blocks = _experiences_to_rules(capped)
    recipes = _top_recipes(capped)
    rule_count = sum(1 for b in rule_blocks if b.startswith("#### Rule:"))

    # No new rules vs the active version → do not bump the version number or
    # write an empty snapshot. Report the current active version instead.
    if rule_count == 0:
        active_path = get_active_skill_path()
        active_ver = get_active_skill_version()
        logger.info("No new rules to generate; keeping active version v%d", active_ver)
        return {
            "version": active_ver or 0,
            "file_path": active_path or "",
            "rule_count": 0,
            "active_exp_count": len(active),
            "archived_count": archived,
            "no_changes": True,
        }

    version = _next_version()
    md_content = _build_skill_md(version, rule_blocks, len(capped), stats, recipes)

    today = datetime.now().strftime("%Y-%m-%d")
    fname = f"v{version}-{today}-auto-generated.md"
    file_path = SKILL_DIR / fname
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    file_path.write_text(md_content, encoding="utf-8")

    index = _load_skill_index()
    index["analyst"]["versions"].append({
        "version": version,
        "file": fname,
        "created": today,
        "rule_count": rule_count,
        "active_exp_count": len(capped),
    })
    index["analyst"]["active_version"] = version
    _save_skill_index(index)

    logger.info(
        "Generated skill v%d (%s) with %d rules (%d active, %d archived)",
        version, fname, rule_count, len(capped), archived,
    )
    return {
        "version": version,
        "file_path": str(file_path),
        "rule_count": rule_count,
        "active_exp_count": len(capped),
        "archived_count": archived,
    }


def get_active_skill_path() -> str | None:
    """Return the file path of the currently active skill version."""
    index = _load_skill_index()
    av = index.get("analyst", {}).get("active_version")
    versions = index.get("analyst", {}).get("versions", [])
    for v in versions:
        if v.get("version") == av:
            return str(SKILL_DIR / v["file"])
    return None


def get_active_skill_version() -> int:
    """Return the version number of the currently active skill."""
    index = _load_skill_index()
    return index.get("analyst", {}).get("active_version", 0)
