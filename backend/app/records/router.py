"""/records — projections + decisions endpoints (PROJECTIONS stage).

RBAC (plan §6): reads for all staff; claim/decisions checker+ (certify:
certifier/admin). Concurrency: 423 on someone else's active claim, 409 on
stale record_version — both via the single write path in service.py.
"""
import time

import psycopg
from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_user
from ..auth.permissions import require_permission
from ..db import conninfo, get_conn
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
def create_record_from_run(run_id: int, user: dict = Depends(require_permission("records:project"))) -> dict:
    """Project a succeeded EXTRACT run into a record (one projection per run)."""
    return service.create_from_run(run_id)


@router.get("/records")
def list_records(
    state: str | None = None,
    search: str | None = Query(default=None, max_length=120),
    sort_by: str = Query(default="id"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(get_current_user),
) -> list[dict]:
    """Search + sort + filter, executed entirely in Postgres.

    Search: the term matches land_records (id with PREFIX matching — "14"
    finds #145 — village, khasra) and every extracted field value (owner,
    survey, tehsil, district…). Fast path uses the pg_trgm GIN indexes
    (migration 010) with similarity ranking; if the extension is unavailable
    we fall back to a plain ILIKE scan — same results, same API.

    Sorting: whitelisted columns only (never interpolate user input into SQL).
    'confidence' sorts scored records first (best first), unscored last.
    """
    SORTS = {
        "id":          "r.id",
        "village":     "r.village_code",
        "khasra":      "r.khasra_no",
        "state":       "r.current_state",
        "version":     "r.record_version",
        "updated":     "r.updated_at",
        "created":     "r.created_at",
        "claim":       "r.claim_owner",
        "confidence":  "_conf",
        "relevance":   "_sim",
    }
    if sort_by not in SORTS:
        raise Problem(422, "Validation Failed",
                      f"sort_by must be one of {', '.join(sorted(SORTS))}.")
    order_sql = "ASC" if order == "asc" else "DESC"
    term = (search or "").strip()
    id_prefix = term.isdigit()  # "14" should find #145, not just exact "145"

    conn = get_conn()  # warm thread-local: search is latency-critical
    if True:
        global _has_trgm_cache
        if _has_trgm_cache is None:
            try:
                _has_trgm_cache = conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')"
                ).fetchone()["exists"]
            except Exception:  # noqa: BLE001
                _has_trgm_cache = False
        has_trgm = _has_trgm_cache

        params: list = []
        where = []
        if state:
            where.append("r.current_state = %s")
            params.append(state)
        if term:
            like = f"%{term}%"
            id_match = "r.id::text = %s" if not id_prefix else "r.id::text LIKE %s"
            id_param = term if not id_prefix else f"{term}%"
            if has_trgm:
                where.append(
                    f"(r.village_code %% %s OR r.khasra_no %% %s "
                    f"OR r.village_code ILIKE %s OR r.khasra_no ILIKE %s "
                    f"OR {id_match} "
                    f"OR EXISTS (SELECT 1 FROM field_values fv WHERE fv.record_id = r.id "
                    f"AND (fv.current_value %% %s OR fv.current_value ILIKE %s)))"
                )
                params += [term, term, like, like, id_param, term, like]
            else:
                where.append(
                    f"(r.village_code ILIKE %s OR r.khasra_no ILIKE %s OR {id_match} "
                    f"OR EXISTS (SELECT 1 FROM field_values fv WHERE fv.record_id = r.id "
                    f"AND fv.current_value ILIKE %s))"
                )
                params += [like, like, id_param, like]

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        if has_trgm and term:
            sim = ("GREATEST(similarity(r.village_code, %s), similarity(r.khasra_no, %s), "
                   "COALESCE((SELECT max(similarity(fv.current_value, %s)) FROM field_values fv "
                   "WHERE fv.record_id = r.id), 0))")
            sim_params = [term, term, term]
        else:
            sim = "0"
            sim_params = []

        conf = ("(SELECT min(c.confidence) FROM field_values fv "
                "JOIN candidates c ON c.id = fv.selected_candidate_id "
                "WHERE fv.record_id = r.id AND fv.state <> 'CORRECTED')")

        sort_expr = SORTS[sort_by]
        if sort_expr == "_sim":
            sort_expr = "_sim DESC NULLS LAST"
            order_suffix = ""
        elif sort_expr == "_conf":
            # scored records first when DESC (best first); NULLs always last
            sort_expr = f"_conf {order_sql} NULLS LAST"
            order_suffix = ""
        else:
            order_suffix = order_sql

        # Slim list payload: the heavy candidates join only runs when
        # confidence is actually used (sort_by=confidence); else NULL.
        want_conf = "_conf" in sort_expr
        conf_sql = conf if want_conf else "NULL"
        sql = f"""
            SELECT r.*, {sim} AS _sim, {conf_sql} AS _conf
            FROM land_records r
            {where_sql}
            ORDER BY {sort_expr} {order_suffix}, r.id DESC
            LIMIT %s
        """.replace("{sim}", sim)  # keep it explicit; f-string nesting got messy
        all_params = sim_params + params + [limit]
        t0 = time.perf_counter()
        rows = conn.execute(sql, all_params).fetchall()
        query_ms = round((time.perf_counter() - t0) * 1000, 1)

    out = []
    for r in rows:
        d = dict(r)
        d.pop("_sim", None)
        mc = d.pop("_conf", None)
        d["confidence"] = {
            "min_confidence": round(float(mc), 2) if mc is not None else None,
            "band": ("high" if mc >= 0.85 else "medium" if mc >= 0.6 else "low") if mc is not None else "unknown",
        }
        out.append(d)
    _last_search_stats["ms"] = query_ms
    return out


# exposed via /records/_debug/search-stats for tuning (dev only, harmless)
_last_search_stats: dict = {"ms": None}
_has_trgm_cache: bool | None = None  # extension presence can't change mid-flight


@router.get("/records/{record_id}")
def get_record(record_id: int, user: dict = Depends(get_current_user)) -> dict:
    return _record_bundle(record_id)


@router.get("/records/{record_id}/history")
def record_history(record_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Past vs current: the complete versioned story of one record.

    current: the live field values (what an export would contain).
    past: one entry per decision that changed a value or state — before →
    after, who, when, why — read from human_decisions (the append-only
    source of truth; current values are a projection of this history).
    timeline: state transitions with timestamps from the same decisions.
    """
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        current = conn.execute(
            """SELECT fv.id AS field_id, fv.field_type, fv.current_value, fv.state,
                      fv.occurrence, c.confidence, c.is_unknown
                 FROM field_values fv
                 LEFT JOIN candidates c ON c.id = fv.selected_candidate_id
                WHERE fv.record_id = %s ORDER BY fv.id""",
            (record_id,),
        ).fetchall()
        decisions = conn.execute(
            """SELECT d.id, d.decision_type, d.field_id, fv.field_type,
                      d.before_value, d.after_value, d.reason, d.actor_id, d.actor_role,
                      d.record_version, d.created_at
                 FROM human_decisions d
                 LEFT JOIN field_values fv ON fv.id = d.field_id
                WHERE d.record_id = %s ORDER BY d.id""",
            (record_id,),
        ).fetchall()

    changes = []
    timeline = []
    for d in decisions:
        entry = dict(d)
        if d["decision_type"] == "CORRECTION" and d["field_type"]:
            changes.append({
                "field_type": d["field_type"],
                "field_id": d["field_id"],
                "before": (d["before_value"] or {}).get("value"),
                "after": (d["after_value"] or {}).get("value"),
                "reason": d["reason"],
                "actor": d["actor_id"], "role": d["actor_role"],
                "at": d["created_at"],
                "record_version": d["record_version"],
            })
        if d["before_value"] and d["before_value"].get("record_state"):
            timeline.append({
                "from_state": d["before_value"]["record_state"],
                "to_state": (d["after_value"] or {}).get("record_state"),
                "decision_type": d["decision_type"],
                "actor": d["actor_id"], "role": d["actor_role"],
                "reason": d["reason"],
                "at": d["created_at"],
                "record_version": d["record_version"],
            })

    return {
        "record": dict(rec),
        "current": [dict(f) for f in current],
        "changes": changes,
        "timeline": timeline,
        "decision_count": len(decisions),
    }


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
def claim_record(record_id: int, user: dict = Depends(require_permission("records:claim"))) -> dict:
    return service.claim(record_id, user)


@router.post("/records/{record_id}/release")
def release_record(record_id: int, user: dict = Depends(require_permission("records:claim"))) -> dict:
    return service.release(record_id, user)


@router.post("/records/{record_id}/decisions")
def decide(
    record_id: int, body: DecisionIn, user: dict = Depends(require_permission("records:decide"))
) -> dict:
    payload = body.model_dump()
    result = service.apply_decision(user, record_id, payload)
    return {**result, "fields": _record_bundle(record_id)["fields"]}
