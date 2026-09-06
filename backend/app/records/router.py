"""/records — projections + decisions endpoints (PROJECTIONS stage).

RBAC (plan §6): reads for all staff; claim/decisions checker+ (certify:
certifier/admin). Concurrency: 423 on someone else's active claim, 409 on
stale record_version — both via the single write path in service.py.
"""
import psycopg
from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_user, require_roles
from ..db import conninfo
from ..errors import Problem
from . import service

router = APIRouter(tags=["records"])

DECIDE_ROLES = service.DECIDE_ROLES
PROJECTION_ROLES = ("operator", "checker", "certifier", "admin")  # projection is mechanical


class DecisionIn(BaseModel):
    decision_type: str
    expected_version: int = Field(ge=1)
    field_id: int | None = None
    anomaly_id: int | None = None
    after_value: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)


def _confidence_summary(record_id: int, conn) -> dict:
    """Document-level confidence (PS #11): the WORST self-reported field score.
    Min, not average — a record is exactly as trustworthy as its least-certain
    reading. Human-corrected fields drop out (the human settled them)."""
    row = conn.execute(
        """SELECT min(c.confidence) AS min_confidence,
                  count(*) FILTER (WHERE c.confidence IS NOT NULL) AS scored_fields,
                  count(*) AS total_fields
           FROM field_values fv
           JOIN candidates c ON c.id = fv.selected_candidate_id
           WHERE fv.record_id = %s AND fv.state <> 'CORRECTED'""",
        (record_id,),
    ).fetchone()
    mc = row["min_confidence"]
    if mc is None:
        return {
            "min_confidence": None, "band": "unknown",
            "scored_fields": row["scored_fields"], "total_fields": row["total_fields"],
        }
    band = "high" if mc >= 0.85 else "medium" if mc >= 0.6 else "low"
    return {
        "min_confidence": round(float(mc), 2),
        "band": band,
        "scored_fields": row["scored_fields"],
        "total_fields": row["total_fields"],
    }


def _record_bundle(record_id: int) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        fields = conn.execute(
            "SELECT * FROM field_values WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM human_decisions WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
        confidence = _confidence_summary(record_id, conn)
    return {
        "record": dict(rec),
        "fields": [dict(f) for f in fields],
        "decisions": [dict(d) for d in decisions],
        "confidence": confidence,
    }


@router.post("/records/from-run/{run_id}", status_code=201)
def create_record_from_run(run_id: int, user: dict = Depends(require_roles(*PROJECTION_ROLES))) -> dict:
    """Project a succeeded EXTRACT run into a record (one projection per run)."""
    return service.create_from_run(run_id)


@router.get("/records")
def list_records(
    state: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(get_current_user),
) -> list[dict]:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if state:
            rows = conn.execute(
                "SELECT * FROM land_records WHERE current_state = %s ORDER BY id DESC LIMIT %s",
                (state, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM land_records ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["confidence"] = _confidence_summary(r["id"], conn)
            out.append(d)
        return out


@router.get("/records/{record_id}")
def get_record(record_id: int, user: dict = Depends(get_current_user)) -> dict:
    return _record_bundle(record_id)


@router.get("/records/{record_id}/documents")
def record_documents(record_id: int, user: dict = Depends(get_current_user)) -> list[dict]:
    """The complete source file(s) behind a record — every document a selected
    candidate's extraction run read from. Served via /documents/{id}/content."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """SELECT DISTINCT d.id, d.original_filename, d.mime, d.size_bytes,
                      d.sha256, d.created_at,
                      (SELECT count(*) FROM pages p WHERE p.document_id = d.id) AS page_count
               FROM documents d
               WHERE d.id IN (
                   SELECT pr.document_id
                   FROM field_values fv
                   JOIN candidates c ON c.id = fv.selected_candidate_id
                   JOIN processing_runs pr ON pr.id = c.run_id
                   WHERE fv.record_id = %s
               )
               ORDER BY d.id""",
            (record_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/records/{record_id}/claim")
def claim_record(record_id: int, user: dict = Depends(require_roles(*DECIDE_ROLES))) -> dict:
    return service.claim(record_id, user)


@router.post("/records/{record_id}/release")
def release_record(record_id: int, user: dict = Depends(require_roles(*DECIDE_ROLES))) -> dict:
    return service.release(record_id, user)


@router.post("/records/{record_id}/decisions")
def decide(
    record_id: int, body: DecisionIn, user: dict = Depends(require_roles(*DECIDE_ROLES))
) -> dict:
    payload = body.model_dump()
    result = service.apply_decision(user, record_id, payload)
    return {**result, "fields": _record_bundle(record_id)["fields"]}
