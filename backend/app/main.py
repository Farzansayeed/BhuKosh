import psycopg

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from .auth.router import router as auth_router
from .config import get_settings
from .custody.router import router as custody_router
from .db import conninfo
from .errors import Problem, problem_handler, validation_handler
from .extract.router import router as extract_router

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_exception_handler(Problem, problem_handler)
app.add_exception_handler(RequestValidationError, validation_handler)
app.include_router(auth_router)
app.include_router(extract_router)
app.include_router(custody_router)


@app.get("/health")
def health():
    try:
        with psycopg.connect(conninfo()) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "app": settings.app_name, "env": settings.env, "database": "ok"}
    except Exception as e:  # noqa: BLE001 — health must never 500
        return {"status": "degraded", "database": "unavailable", "detail": str(e)}
