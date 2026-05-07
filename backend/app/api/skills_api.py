"""Skills marketplace API."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import skills_service

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("/installed", response_model=List[schemas.SkillOut])
def installed(db: Session = Depends(get_db)):
    return db.query(models.Skill).order_by(models.Skill.installed_at.desc()).all()


@router.get("/marketplace")
async def marketplace(category: str = "finance", q: str = ""):
    items = await skills_service.list_marketplace(category=category, keyword=q)
    return {"items": items}


@router.post("/install", response_model=schemas.SkillOut)
def install(payload: schemas.SkillInstallPayload, db: Session = Depends(get_db)):
    return skills_service.install_skill(db, payload.model_dump())


@router.delete("/{skill_id}")
def uninstall(skill_id: str, db: Session = Depends(get_db)):
    ok = skills_service.uninstall_skill(db, skill_id)
    return {"ok": ok}


@router.post("/{skill_id}/toggle")
def toggle(skill_id: str, enabled: bool = True, db: Session = Depends(get_db)):
    row = db.query(models.Skill).filter_by(skill_id=skill_id).first()
    if not row:
        return {"ok": False}
    row.enabled = enabled
    db.commit()
    return {"ok": True, "enabled": enabled}
