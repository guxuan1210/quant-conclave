
"""FastAPI router for advisory experience management."""
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from capitalradar.advisory.experience_store import (
    list_experiences, approve_experience, archive_experience,
    reactivate_experience, update_experience, get_active_experiences,
    get_injection_log, create_experience,
)
from capitalradar.advisory.extraction import extract_patterns_from_log

from capitalradar.agents.utils.memory import CapitalRadarMemoryLog

router = APIRouter(prefix="/api/advisory", tags=["advisory"])


class ExperienceUpdate(BaseModel):
    content: str | None = None
    category: str | None = None
    lesson_abstract: str | None = None


@router.get("/experiences")
def api_list_experiences(status: str = ""):
    return list_experiences(status)


@router.get("/experiences/active")
def api_get_active():
    return get_active_experiences()


@router.put("/experiences/{eid}/approve")
def api_approve(eid: int):
    ok = approve_experience(eid)
    if not ok:
        raise HTTPException(404, "Experience not found or not pending")
    return {"status": "active"}


@router.put("/experiences/{eid}/archive")
def api_archive(eid: int):
    ok = archive_experience(eid)
    if not ok:
        raise HTTPException(404, "Experience not found")
    return {"status": "archived"}


@router.put("/experiences/{eid}/reactivate")
def api_reactivate(eid: int):
    ok = reactivate_experience(eid)
    if not ok:
        raise HTTPException(404, "Experience not found or not archived")
    return {"status": "active"}


@router.put("/experiences/{eid}")
def api_update_experience(eid: int, body: ExperienceUpdate):
    ok = update_experience(eid, body.content, body.category, body.lesson_abstract)
    if not ok:
        raise HTTPException(404, "Experience not found")
    return {"status": "updated"}

@router.post("/experiences/extract")
def api_extract(config: dict | None = Body(default=None)):
    if config is None:
        from capitalradar.default_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG
    memory_log = CapitalRadarMemoryLog(config)
    proposals = extract_patterns_from_log(memory_log)
    created = []
    for p in proposals:
        eid = create_experience(
            content=p["proposed_content"],
            category=p["proposed_category"],
            lesson_abstract=p.get("pattern_type", ""),
            source_ticker=", ".join(p.get("tickers", [])),
        )
        created.append({"id": eid, "content": p["proposed_content"]})
    return {"proposals": len(created), "items": created}

@router.get("/experiences/log")
def api_injection_log(limit: int = 50):
    return get_injection_log(limit)
