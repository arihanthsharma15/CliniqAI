from datetime import datetime

from pydantic import BaseModel


class NotificationRead(BaseModel):
    id: int
    role: str
    title: str
    message: str
    kind: str
    is_urgent: bool
    is_read: bool
    call_sid: str | None = None
    task_id: int | None = None
    escalation_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True

