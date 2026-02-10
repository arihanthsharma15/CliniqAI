from datetime import datetime

from pydantic import BaseModel


class EscalationCreate(BaseModel):
    call_sid: str
    reason: str
    details: str | None = None


class EscalationRead(BaseModel):
    id: int
    call_sid: str
    reason: str
    details: str | None
    created_at: datetime

    class Config:
        from_attributes = True
