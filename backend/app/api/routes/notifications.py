from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.notification import Notification
from app.schemas.notification import NotificationRead

router = APIRouter()


@router.get("/", response_model=list[NotificationRead])
def list_notifications(
    role: str = Query(..., pattern="^(staff|doctor)$"),
    unread_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[NotificationRead]:
    query = db.query(Notification).filter(Notification.role == role)
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    return query.order_by(Notification.created_at.desc()).limit(max(1, min(limit, 200))).all()


@router.patch("/{notification_id}/read", response_model=NotificationRead)
def mark_read(notification_id: int, db: Session = Depends(get_db)) -> NotificationRead:
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    item.is_read = True
    db.commit()
    db.refresh(item)
    return item


@router.patch("/read-all")
def mark_all_read(
    role: str = Query(..., pattern="^(staff|doctor)$"),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    count = (
        db.query(Notification)
        .filter(Notification.role == role, Notification.is_read.is_(False))
        .update({"is_read": True}, synchronize_session=False)
    )
    db.commit()
    return {"updated": int(count)}
