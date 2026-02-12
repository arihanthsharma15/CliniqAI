from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskRead

router = APIRouter()


@router.get("/", response_model=list[TaskRead])
def list_tasks(db: Session = Depends(get_db)) -> list[TaskRead]:
    return db.query(Task).order_by(Task.created_at.desc()).all()


@router.post("/", response_model=TaskRead)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    task = Task(**payload.model_dump())
    db.add(task)
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
        .filter(Task.status == "pending", Task.assigned_role == "doctor")
        .order_by(Task.created_at.desc())
        .all()
    )


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
