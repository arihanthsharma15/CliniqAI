from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.escalation import Escalation
from app.schemas.escalation import EscalationCreate, EscalationRead
from app.services.notification_store import create_notification

router = APIRouter()


@router.get("/", response_model=list[EscalationRead])
def list_escalations(role: str | None = None, db: Session = Depends(get_db)) -> list[EscalationRead]:
    query = db.query(Escalation)
    return query.order_by(Escalation.created_at.desc()).all()


@router.post("/", response_model=EscalationRead)
def create_escalation(payload: EscalationCreate, db: Session = Depends(get_db)) -> EscalationRead:
    escalation = Escalation(**payload.model_dump())
    db.add(escalation)
    db.flush()
    for role in ("staff", "doctor"):
        create_notification(
            db,
            role=role,
            title="Urgent escalation created",
            message=f"{escalation.reason} ({escalation.call_sid})",
            kind="escalation",
            is_urgent=True,
            call_sid=escalation.call_sid,
            escalation_id=escalation.id,
        )
    db.commit()
    db.refresh(escalation)
    return escalation
