from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.call import Call
from app.models.escalation import Escalation

router = APIRouter()


@router.get("/")
def get_metrics(db: Session = Depends(get_db)) -> dict:
    calls_today = db.query(func.count(Call.id)).scalar() or 0
    escalations = db.query(func.count(Escalation.id)).scalar() or 0
    escalation_rate = (escalations / calls_today) if calls_today else None
    return {
        "calls_today": calls_today,
        "escalation_rate": escalation_rate,
        "avg_call_duration_sec": None,
    }
