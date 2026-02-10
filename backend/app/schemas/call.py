from datetime import datetime

from pydantic import BaseModel


class CallCreate(BaseModel):
    call_sid: str
    from_number: str | None = None
    to_number: str | None = None
    status: str | None = None


class CallRead(BaseModel):
    id: int
    call_sid: str
    from_number: str | None
    to_number: str | None
    status: str | None
    created_at: datetime

    class Config:
        from_attributes = True
