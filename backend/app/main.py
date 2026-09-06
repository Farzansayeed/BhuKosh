import psycopg

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from .audit.router import router as audit_router
from .admin import router as admin_router
from .auth.router import router as auth_router
from .config import get_settings
from .custody.router import router as custody_router
from .db import conninfo
from .errors import Problem, problem_handler, validation_handler
from .extract.router import router as extract_router
from .exports.router import router as exports_router
from .evidence import router as evidence_router
from .integrity import router as integrity_router
from .i18n_output import router as i18n_output_router
from .learning.router import router as learning_router
from .processing.router import router as processing_router
from .records.router import router as records_router
from .rules.router import router as validation_router
from .stats.router import router as stats_router
from .verification_router import router as verification_router

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_exception_handler(Problem, problem_handler)
app.add_exception_handler(RequestValidationError, validation_handler)
app.include_router(auth_router)
app.include_router(extract_router)
app.include_router(custody_router)
app.include_router(processing_router)
app.include_router(records_router)
app.include_router(validation_router)
app.include_router(audit_router)
app.include_router(exports_router)
app.include_router(evidence_router)
app.include_router(integrity_router)
app.include_router(i18n_output_router)
app.include_router(stats_router)
app.include_router(learning_router)
app.include_router(verification_router)
app.include_router(admin_router)


@app.get("/health")
def health():
    try:
        with psycopg.connect(conninfo()) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "app": settings.app_name, "env": settings.env, "database": "ok"}
    except Exception as e:  # noqa: BLE001 — health must never 500
        return {"status": "degraded", "database": "unavailable", "detail": str(e)}
