from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.escalation import Escalation
from app.schemas.escalation import EscalationCreate, EscalationRead

router = APIRouter()


@router.post("/", response_model=EscalationRead)
def create_escalation(payload: EscalationCreate, db: Session = Depends(get_db)) -> EscalationRead:
    escalation = Escalation(**payload.model_dump())
    db.add(escalation)
    db.commit()
    db.refresh(escalation)
    return escalation
