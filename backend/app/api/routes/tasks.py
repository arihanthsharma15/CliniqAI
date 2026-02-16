from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskRead, TaskStatusUpdate
from app.services.notification_store import create_notification

router = APIRouter()
DOCTOR_VISIBLE_TYPES = {"medication_refill", "prescription_refill", "escalation"}


@router.get("/", response_model=list[TaskRead])
def list_tasks(role: str | None = None, db: Session = Depends(get_db)) -> list[TaskRead]:
    query = db.query(Task)
    if role == "doctor":
        # Doctor dashboard should always see:
        # 1) refill/medical tasks assigned to doctor
        # 2) all escalation tasks (including requested_human escalations routed to staff)
        query = query.filter(
            or_(
                and_(
                    Task.assigned_role == "doctor",
                    Task.request_type.in_(("medication_refill", "prescription_refill", "medical_question")),
                ),
                Task.request_type == "escalation",
            )
        )
    elif role == "staff":
        query = query.filter(Task.assigned_role == "staff")
    return query.order_by(Task.created_at.desc()).all()


@router.post("/", response_model=TaskRead)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    task = Task(**payload.model_dump())
    db.add(task)
    db.flush()
    create_notification(
        db,
        role=task.assigned_role,
        title="New task assigned",
        message=f"Task #{task.id}: {task.request_type.replace('_', ' ')}",
        kind="task_created",
        is_urgent=(task.priority == "high"),
        call_sid=task.call_sid,
        task_id=task.id,
    )
    db.commit()
    db.refresh(task)
    return task


@router.get("/queue/staff", response_model=list[TaskRead])
def staff_queue(db: Session = Depends(get_db)) -> list[TaskRead]:
    return (
        db.query(Task)
        .filter(Task.status == "pending", Task.assigned_role == "staff")
        .order_by(Task.created_at.desc())
        .all()
    )


@router.get("/queue/doctor", response_model=list[TaskRead])
def doctor_queue(db: Session = Depends(get_db)) -> list[TaskRead]:
    return (
        db.query(Task)
        .filter(
            Task.status == "pending",
            or_(
                and_(
                    Task.assigned_role == "doctor",
                    Task.request_type.in_(("medication_refill", "prescription_refill", "medical_question")),
                ),
                Task.request_type == "escalation",
            ),
        )
        .order_by(Task.created_at.desc())
        .all()
    )


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}/status", response_model=TaskRead)
def update_task_status(task_id: int, payload: TaskStatusUpdate, db: Session = Depends(get_db)) -> TaskRead:
    valid = {"pending", "in_progress", "completed"}
    if payload.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use one of: {sorted(valid)}")
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = payload.status
    create_notification(
        db,
        role=task.assigned_role,
        title="Task status updated",
        message=f"Task #{task.id} moved to {payload.status.replace('_', ' ')}",
        kind="task_status",
        is_urgent=False,
        call_sid=task.call_sid,
        task_id=task.id,
    )
    db.commit()
    db.refresh(task)
    return task
