from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.notification import Notification


def create_notification(
    db: Session,
    *,
    role: str,
    title: str,
    message: str,
    kind: str = "info",
    is_urgent: bool = False,
    call_sid: str | None = None,
    task_id: int | None = None,
    escalation_id: int | None = None,
) -> Notification:
    item = Notification(
        role=role,
        title=title,
        message=message,
        kind=kind,
        is_urgent=is_urgent,
        call_sid=call_sid,
        task_id=task_id,
        escalation_id=escalation_id,
    )
    db.add(item)
    db.flush()
    return item

