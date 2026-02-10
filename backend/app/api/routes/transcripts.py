from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.transcript import Transcript
from app.schemas.transcript import TranscriptRead

router = APIRouter()


@router.get("/{call_sid}", response_model=TranscriptRead)
def get_transcript(call_sid: str, db: Session = Depends(get_db)) -> TranscriptRead:
    transcript = db.query(Transcript).filter(Transcript.call_sid == call_sid).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript
