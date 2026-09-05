"""Validation + anomalies endpoints (VALIDATION stage).

RBAC (plan §6): validation reads for all staff, running validation operator+,
anomaly resolution checker+ — always through the single decision write path.
"""
import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_user, require_roles
from ..db import conninfo
from ..errors import Problem
from ..records.service import apply_decision
from . import service

router = APIRouter(tags=["validation"])

WRITE_ROLES = ("operator", "checker", "certifier", "admin")


class ResolveIn(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/records/{record_id}/validate")
def validate(record_id: int, user: dict = Depends(require_roles(*WRITE_ROLES))) -> dict:
    return service.run_validation(record_id)


@router.get("/records/{record_id}/validation")
def get_validation(record_id: int, user: dict = Depends(get_current_user)) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT id FROM land_records WHERE id = %s", (record_id,)).fetchone():
            raise Problem(404, "Not Found", "No such record.")
        results = conn.execute(
            "SELECT * FROM validation_results WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
        anomalies = conn.execute(
            "SELECT * FROM anomalies WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
    return {"results": [dict(r) for r in results], "anomalies": [dict(a) for a in anomalies]}


@router.get("/anomalies")
def list_anomalies(
    status: str | None = None, user: dict = Depends(get_current_user)
) -> list[dict]:
    if status and status not in ("OPEN", "RESOLVED"):
        raise Problem(422, "Validation Failed", "status must be OPEN or RESOLVED.")
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = (
            conn.execute("SELECT * FROM anomalies WHERE status = %s ORDER BY id DESC", (status,)).fetchall()
            if status
            else conn.execute("SELECT * FROM anomalies ORDER BY id DESC").fetchall()
        )
    return [dict(r) for r in rows]


@router.get("/anomalies/{anomaly_id}")
def get_anomaly(anomaly_id: int, user: dict = Depends(get_current_user)) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM anomalies WHERE id = %s", (anomaly_id,)).fetchone()
    if not row:
        raise Problem(404, "Not Found", "No such anomaly.")
    return dict(row)


@router.post("/anomalies/{anomaly_id}/resolve")
def resolve_anomaly(anomaly_id: int, body: ResolveIn, user: dict = Depends(require_roles("checker", "certifier", "admin"))) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        anomaly = conn.execute(
            "SELECT record_id FROM anomalies WHERE id = %s", (anomaly_id,)
        ).fetchone()
    if not anomaly:
        raise Problem(404, "Not Found", "No such anomaly.")
    payload = {
        "decision_type": "ANOMALY_RESOLVE",
        "anomaly_id": anomaly_id,
        "expected_version": body.expected_version,
        "reason": body.reason,
    }
    result = apply_decision(user, anomaly["record_id"], payload)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        updated = conn.execute("SELECT * FROM anomalies WHERE id = %s", (anomaly_id,)).fetchone()
    return {**result, "anomaly": dict(updated)}
