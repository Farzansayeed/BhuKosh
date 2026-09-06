"""/audit — chain reads and verification (plan §6: admin/auditor only)."""
import psycopg
from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row

from ..auth.dependencies import get_current_user
from ..auth.permissions import require_permission
from ..db import conninfo
from . import service

router = APIRouter(prefix="/audit", tags=["audit"])

AUDIT_ROLES = ("admin", "auditor")


@router.get("/events")
def list_events(
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    user: dict = Depends(require_permission("audit:read")),
) -> list[dict]:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE seq > %s ORDER BY seq LIMIT %s",
            (after_seq, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/chain-head")
def head(user: dict = Depends(require_permission("audit:read"))) -> dict:
    return service.chain_head() or {"seq": None, "payload_hash": None}


@router.post("/verify")
def verify(user: dict = Depends(require_permission("audit:read"))) -> dict:
    return service.verify()
