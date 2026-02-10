from fastapi import FastAPI

from app.api.routes import analytics, calls, escalations, tasks, transcripts
from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401


def create_app() -> FastAPI:
    app = FastAPI(title="AI Clinic Call Assistant API")

    @app.on_event("startup")
    def startup() -> None:
        Base.metadata.create_all(bind=engine)

    # Routers
    app.include_router(calls.router, prefix="/api/calls", tags=["calls"])
    app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
    app.include_router(transcripts.router, prefix="/api/transcripts", tags=["transcripts"])
    app.include_router(escalations.router, prefix="/api/escalations", tags=["escalations"])
    app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])

    # Root endpoint (fix Not Found issue)
    @app.get("/")
    def root() -> dict:
        return {
            "service": "AI Clinic Call Assistant API",
            "docs": "/docs",
            "health": "/healthz",
        }

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
