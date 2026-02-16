from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="info")
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    call_sid: Mapped[str | None] = mapped_column(String(64), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    escalation_id: Mapped[int | None] = mapped_column(
        ForeignKey("escalations.id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

