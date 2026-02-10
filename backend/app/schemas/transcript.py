from datetime import datetime

from pydantic import BaseModel


class TranscriptRead(BaseModel):
    id: int
    call_sid: str
    text: str
    created_at: datetime

    class Config:
        from_attributes = True
