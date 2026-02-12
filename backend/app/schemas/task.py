from datetime import datetime

from pydantic import BaseModel


class TaskCreate(BaseModel):
    call_sid: str | None = None
    patient_name: str | None = None
    callback_number: str | None = None
    request_type: str
    assigned_role: str = "staff"
    priority: str = "normal"
    details: str | None = None


class TaskRead(BaseModel):
    id: int
    call_sid: str | None
    patient_name: str | None
    callback_number: str | None
    request_type: str
    assigned_role: str
    priority: str
    details: str | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
