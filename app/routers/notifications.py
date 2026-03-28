"""In-app notification list and read state (haulier/loader/admin users and driver sessions)."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_driver_optional, get_current_user_optional
from app.database import get_db

router = APIRouter()


class NotificationItem(BaseModel):
    id: int
    title: str
    body: Optional[str] = None
    link_url: Optional[str] = None
    kind: str
    read_at: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


def _session_actor(request: Request, db: Session):
    user = get_current_user_optional(request, db)
    if user is not None:
        return ("user", user.id)
    driver = get_current_driver_optional(request, db)
    if driver is not None:
        return ("driver", driver.id)
    return (None, None)


@router.get("/list", response_model=List[NotificationItem])
def list_notifications(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 40,
) -> List[NotificationItem]:
    kind, actor_id = _session_actor(request, db)
    if kind is None:
        raise HTTPException(status_code=401, detail="Login required")
    q = db.query(models.AppNotification).order_by(models.AppNotification.created_at.desc())
    if kind == "user":
        q = q.filter(models.AppNotification.user_id == actor_id)
    else:
        q = q.filter(models.AppNotification.driver_id == actor_id)
    rows = q.limit(min(max(limit, 1), 100)).all()
    out = []
    for n in rows:
        out.append(
            NotificationItem(
                id=n.id,
                title=n.title,
                body=n.body,
                link_url=n.link_url,
                kind=n.kind,
                read_at=n.read_at.isoformat() if n.read_at else None,
                created_at=n.created_at.isoformat() if n.created_at else "",
            )
        )
    return out


@router.get("/unread-count")
def unread_count(request: Request, db: Session = Depends(get_db)) -> dict:
    kind, actor_id = _session_actor(request, db)
    if kind is None:
        return {"count": 0}
    q = db.query(models.AppNotification).filter(models.AppNotification.read_at.is_(None))
    if kind == "user":
        q = q.filter(models.AppNotification.user_id == actor_id)
    else:
        q = q.filter(models.AppNotification.driver_id == actor_id)
    return {"count": q.count()}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    kind, actor_id = _session_actor(request, db)
    if kind is None:
        raise HTTPException(status_code=401, detail="Login required")
    n = db.get(models.AppNotification, notification_id)
    if not n:
        raise HTTPException(status_code=404, detail="Not found")
    if kind == "user" and n.user_id != actor_id:
        raise HTTPException(status_code=403, detail="Not yours")
    if kind == "driver" and n.driver_id != actor_id:
        raise HTTPException(status_code=403, detail="Not yours")
    n.read_at = datetime.now(timezone.utc)
    db.add(n)
    db.commit()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(request: Request, db: Session = Depends(get_db)) -> dict:
    kind, actor_id = _session_actor(request, db)
    if kind is None:
        raise HTTPException(status_code=401, detail="Login required")
    now = datetime.now(timezone.utc)
    q = db.query(models.AppNotification).filter(models.AppNotification.read_at.is_(None))
    if kind == "user":
        q = q.filter(models.AppNotification.user_id == actor_id)
    else:
        q = q.filter(models.AppNotification.driver_id == actor_id)
    for n in q.all():
        n.read_at = now
        db.add(n)
    db.commit()
    return {"ok": True}
