from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_sid: Mapped[str | None] = mapped_column(String(64), index=True)
    patient_name: Mapped[str | None] = mapped_column(String(128))
    callback_number: Mapped[str | None] = mapped_column(String(32))
    request_type: Mapped[str] = mapped_column(String(64))
    assigned_role: Mapped[str] = mapped_column(String(16), default="staff", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
