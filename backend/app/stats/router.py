import psycopg
from fastapi import APIRouter
from fastapi import Depends
from psycopg.rows import dict_row

from ..auth.dependencies import get_current_user
from ..db import conninfo

router = APIRouter(prefix="/stats", tags=["stats"])

# Dashboard is for the people who operate/audit the digitization pipeline.
ALL_ROLES = ("operator", "checker", "certifier", "auditor", "admin")


@router.get("")
def dashboard(user: dict = Depends(get_current_user)):
    """PS #16: documents processed, extraction accuracy, validation status,
    pending verification cases, error statistics, state/district progress."""

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        records_by_state = {
            r["current_state"]: r["n"]
            for r in conn.execute(
                "SELECT current_state, count(*) AS n FROM land_records GROUP BY current_state"
            ).fetchall()
        }
        total_records = sum(records_by_state.values())

        fields = conn.execute(
            """SELECT state, count(*) AS n FROM field_values GROUP BY state"""
        ).fetchall()
        field_counts = {r["state"]: r["n"] for r in fields}
        total_fields = sum(field_counts.values())

        # Extraction accuracy proxy (PS #16 "extraction accuracy"): the share of
        # extracted fields a human never had to touch. Corrections are the
        # ground truth for wrong extractions, so 1 - correction rate is an
        # honest, auditable accuracy measure — no invented precision.
        corrections = conn.execute(
            "SELECT count(*) AS n FROM human_decisions WHERE decision_type = 'CORRECTION'"
        ).fetchone()["n"]
        accuracy = round(100.0 * (1 - corrections / total_fields), 1) if total_fields else None

        anomalies_by_rule = conn.execute(
            """SELECT rule_id, severity, status, count(*) AS n
               FROM anomalies GROUP BY rule_id, severity, status
               ORDER BY rule_id, severity, status"""
        ).fetchall()

        extraction = conn.execute(
            """SELECT engine_name, engine_version, status, count(*) AS n,
                      round(avg(extract(epoch FROM (finished_at - started_at)))::numeric, 1) AS avg_seconds
               FROM processing_runs
               WHERE kind = 'EXTRACT'
               GROUP BY engine_name, engine_version, status
               ORDER BY engine_name, status"""
        ).fetchall()

        api_calls = conn.execute(
            """SELECT route, status, count(*) AS n FROM api_usage GROUP BY route, status ORDER BY route"""
        ).fetchall()

        # District-wise progress: district (and village) live as extracted
        # field values, not columns. District when captured, else village.
        progress = conn.execute(
            """SELECT COALESCE(NULLIF(d.current_value, ''), NULLIF(v.current_value, ''), 'Unknown') AS region,
                      count(*) AS records,
                      count(*) FILTER (WHERE r.current_state IN ('VERIFIED', 'OFFICER_CERTIFIED', 'ARCHIVED')) AS confirmed,
                      count(*) FILTER (WHERE r.current_state = 'REVIEW_REQUIRED') AS pending_review
               FROM land_records r
               LEFT JOIN field_values d ON d.record_id = r.id AND d.field_type = 'district'
               LEFT JOIN field_values v ON v.record_id = r.id AND v.field_type = 'village'
               GROUP BY 1
               ORDER BY records DESC
               LIMIT 50"""
        ).fetchall()

    pending_review = records_by_state.get("REVIEW_REQUIRED", 0)

    return {
        "records": {
            "total": total_records,
            "by_state": records_by_state,
            "pending_verification": pending_review,
        },
        "fields": {
            "total": total_fields,
            "by_state": field_counts,
            "corrections": corrections,
            "accuracy_percent": accuracy,
        },
        "anomalies_by_rule": [dict(r) for r in anomalies_by_rule],
        "extraction_runs": [dict(r) for r in extraction],
        "api_calls": [dict(r) for r in api_calls],
        "progress": [dict(r) for r in progress],
    }
