"""FastAPI routes for skill version management."""
from __future__ import annotations
import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skill", tags=["skill"])

SKILL_DIR = Path(".claude/skills/analyst-core")
SKILL_INDEX = SKILL_DIR / "index.json"


def _load_index() -> dict:
    if SKILL_INDEX.exists():
        try:
            return json.loads(SKILL_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"analyst": {"versions": [], "active_version": 0}}


def _save_index(index: dict) -> None:
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    SKILL_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _versions(index: dict) -> list[dict]:
    versions = index.get("analyst", {}).get("versions", [])
    return sorted(versions, key=lambda item: item.get("version", 0))


def _find_version(index: dict, version: int) -> dict | None:
    for entry in _versions(index):
        if entry.get("version") == version:
            return entry
    return None


@router.get("/versions")
def list_versions():
    """List all skill versions."""
    index = _load_index()
    return _versions(index)


@router.get("/summary")
def skill_summary():
    """Return display-ready skill version summary."""
    index = _load_index()
    versions = _versions(index)
    active_version = index.get("analyst", {}).get("active_version", 0)
    active = _find_version(index, active_version)
    usable_versions = [entry for entry in versions if entry.get("rule_count", 0) > 0]
    latest = versions[-1] if versions else None
    return {
        "active_version": active_version,
        "active": active,
        "latest": latest,
        "versions": versions,
        "usable_versions": usable_versions,
        "draft_count": len(versions) - len(usable_versions),
    }


@router.get("/versions/{v}")
def get_version(v: int):
    """Get the full SKILL.md content for a specific version."""
    index = _load_index()
    for entry in index.get("analyst", {}).get("versions", []):
        if entry.get("version") == v:
            fname = entry.get("file", "")
            file_path = SKILL_DIR / fname
            if file_path.exists():
                return {
                    "version": v,
                    "file": fname,
                    "content": file_path.read_text(encoding="utf-8"),
                    "metadata": entry,
                }
    raise HTTPException(404, f"Version {v} not found")


@router.get("/diff")
def diff_versions(v1: int = Query(...), v2: int = Query(...)):
    """Get diff between two skill versions. Returns added/removed lines."""
    def _get_lines(v):
        try:
            resp = get_version(v)
            return resp["content"].splitlines()
        except HTTPException:
            return []

    lines_v1 = _get_lines(v1)
    lines_v2 = _get_lines(v2)
    if not lines_v1 and not lines_v2:
        raise HTTPException(404, "Neither version found")

    added = [l for l in lines_v2 if l.strip() and l not in lines_v1]
    removed = [l for l in lines_v1 if l.strip() and l not in lines_v2]

    return {
        "v1": v1, "v2": v2,
        "v1_lines": len(lines_v1),
        "v2_lines": len(lines_v2),
        "added": added,
        "removed": removed,
        "net_change": len(added) - len(removed),
    }


@router.post("/generate")
def trigger_generate():
    """Manually trigger skill version generation."""
    from quantconclave.graph.skill_generator import generate_skill_version
    from quantconclave.default_config import DEFAULT_CONFIG as config
    result = generate_skill_version(config)
    return result


@router.get("/active")
def get_active():
    """Return current active skill version info."""
    index = _load_index()
    av = index.get("analyst", {}).get("active_version", 0)
    entry = _find_version(index, av)
    if entry:
        return {**entry, "active": True}
    return {"version": 0, "active": False}


@router.post("/active/{version}")
def set_active(version: int):
    """Set the skill version used by future analysis runs."""
    index = _load_index()
    entry = _find_version(index, version)
    if not entry:
        raise HTTPException(404, f"Version {version} not found")
    index.setdefault("analyst", {})["active_version"] = version
    _save_index(index)
    return {**entry, "active": True}
