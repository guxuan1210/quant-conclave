"""Tests for the skill generator enhancements: dedup, cap, grouping, template."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import capitalradar.graph.skill_generator as sg


@pytest.fixture()
def isolated_skill_dir(tmp_path, monkeypatch):
    """Point SKILL_DIR at a temp dir so tests never touch real versions."""
    d = tmp_path / "skills"
    monkeypatch.setattr(sg, "SKILL_DIR", d)
    monkeypatch.setattr(sg, "SKILL_INDEX", d / "index.json")
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _exp(eid, content, abstract, category="other", created="2026-07-01", status="active"):
    return {"id": eid, "content": content, "lesson_abstract": abstract,
            "category": category, "created_at": created, "status": status}


def test_dedup_exact_and_near(isolated_skill_dir):
    exps = [
        _exp(1, "Rule about RSI oversold trap", "oversold_trap", "technical", "2026-07-01"),
        _exp(2, "Rule about RSI oversold trap", "oversold_trap", "technical", "2026-07-02"),  # exact dup
        _exp(3, "Completely different margin balance rule", "margin_trap", "capital_flow", "2026-07-03"),
        _exp(4, "Northbound systematic withdrawal pattern", "northbound", "capital_flow", "2026-07-04"),
    ]
    deduped = sg._dedup_rules(exps)
    assert len(deduped) == 3  # one exact dup removed


def test_dedup_keeps_newest(isolated_skill_dir):
    exps = [
        _exp(1, "Same content", "abstract_a", "other", "2026-07-01"),
        _exp(2, "Same content", "abstract_a", "other", "2026-07-05"),
    ]
    deduped = sg._dedup_rules(exps)
    assert len(deduped) == 1
    assert deduped[0]["id"] == 2  # newest kept


def test_grouping_by_category(isolated_skill_dir):
    exps = [
        _exp(1, "Flow rule A", "flow_a", "capital_flow", "2026-07-01"),
        _exp(2, "Confidence rule B", "conf_b", "confidence_calibration", "2026-07-02"),
        _exp(3, "Flow rule C", "flow_c", "capital_flow", "2026-07-03"),
    ]
    blocks = sg._experiences_to_rules(exps)
    cats = [b for b in blocks if b.startswith("### Category:")]
    assert len(cats) == 2
    assert any("capital_flow" in c for c in cats)
    assert any("confidence_calibration" in c for c in cats)
    assert any(b.startswith("#### Rule:") for b in blocks)


def test_max_rules_cap_archives_oldest(monkeypatch, isolated_skill_dir):
    """31 experiences → only 30 survive; the oldest is archived."""
    from capitalradar.advisory import experience_store as es
    archived_ids = []
    monkeypatch.setattr(sg, "MAX_RULES", 30)
    monkeypatch.setattr(sg, "AUTO_ARCHIVE_TRIM", True)
    monkeypatch.setattr(es, "archive_experience", lambda eid: archived_ids.append(eid) or True)

    exps = [_exp(i, f"Content for experience {i}", f"abstract_{i}",
                 "other", f"2026-07-{i:02d}") for i in range(1, 32)]
    deduped = sg._dedup_rules(exps)  # 31 unique
    assert len(deduped) == 31
    capped = deduped[:sg.MAX_RULES]
    assert len(capped) == 30
    archived = sg._archive_oldest_active(deduped, sg.MAX_RULES)
    assert archived == 1
    assert len(archived_ids) == 1


def test_template_slice_markers_preserved(isolated_skill_dir):
    """build_instrument_context slices on ## Experience-Derived Rules → ## Core
    Principles; both must exist in that order and the slice must contain rules."""
    blocks = sg._experiences_to_rules([
        _exp(1, "Some rule content", "rule_x", "capital_flow", "2026-07-01"),
    ])
    md = sg._build_skill_md(99, blocks, 1, None, recipes=["1. **rule_x** — snippet"])

    rs = md.find("## Experience-Derived Rules")
    cs = md.find("## Core Principles")
    assert rs >= 0 and cs > rs  # markers present and ordered
    sliced = md[rs:cs]
    assert "### Category:" in sliced
    assert "#### Rule:" in sliced
    # New sections appear before the rules block
    assert md.find("## Description") < rs
    assert md.find("## Recipes") < rs
    assert md.find("## Components") < rs


def test_generate_writes_version_and_activates(monkeypatch, isolated_skill_dir):
    from capitalradar.advisory import experience_store as es
    monkeypatch.setattr(es, "list_experiences", lambda status="": [
        _exp(1, "Active flow rule", "flow_rule", "capital_flow", "2026-07-01"),
    ])
    monkeypatch.setattr(sg, "_extract_active_rule_hashes", lambda: set())

    result = sg.generate_skill_version({})
    assert result["version"] == 1
    assert result["rule_count"] == 1
    assert result["active_exp_count"] == 1
    # File written
    assert Path(result["file_path"]).exists()
    # Index updated + activated
    index = sg._load_skill_index()
    assert index["analyst"]["active_version"] == 1
    assert len(index["analyst"]["versions"]) == 1
    # Content has the new template sections
    text = Path(result["file_path"]).read_text(encoding="utf-8")
    assert "## Description" in text
    assert "## Experience-Derived Rules" in text
