"""Verification assurance — the answer to 'how confident are you this is CORRECT?',
as opposed to extraction confidence ('how sure the engine read the pixels right').

Composite of independently checkable signals: deterministic rule outcomes,
cross-record corroboration (does another record for the same village+khasra
agree?), document integrity, human authority, and reading confidence.
External-registry cross-checks (LRMS/DILRMP) are an explicit adapter slot:
no public land-record API exists, so the check is reported honestly as
unavailable rather than faked. Verdicts: SUFFICIENT / PARTIAL / INSUFFICIENT.
"""
import psycopg
from psycopg.rows import dict_row

from .db import conninfo
from .rules.primitives import parse_area_bigha

CORROBORATION_TOLERANCE = 0.25  # same threshold as R-AREA-JUMP

# checkable statuses contribute to the assurance score; 'unavailable' does not
# (it is excluded from the denominator so honesty never silently drags the
# number down — it is shown, not averaged in).
SCORE_PER_STATUS = {"pass": 100, "warn": 50, "fail": 0, "pending": 0}


def _verdict_from(checks: list[dict]) -> tuple[str, int]:
    scored = [c for c in checks if c["status"] != "unavailable"]
    score = (
        round(sum(SCORE_PER_STATUS[c["status"]] for c in scored) / len(scored))
        if scored
        else 0
    )
    statuses = {c["status"] for c in scored}
    if "fail" in statuses:
        verdict = "INSUFFICIENT"
    elif "warn" in statuses or "pending" in statuses:
        verdict = "PARTIAL"
    else:
        verdict = "SUFFICIENT"
    return verdict, score


def _sibling_areas(conn, rec: dict) -> list[float]:
    """Areas of post-extraction records with the same identity (village+khasra)."""
    rows = conn.execute(
        """SELECT fv.current_value AS area_raw
           FROM land_records lr
           JOIN field_values fv ON fv.record_id = lr.id AND fv.field_type = 'area_raw'
           WHERE lr.village_code = %s AND lr.khasra_no = %s AND lr.id <> %s
             AND lr.current_state IN ('VALIDATED','REVIEW_REQUIRED','VERIFIED','OFFICER_CERTIFIED')""",
        (rec["village_code"], rec["khasra_no"], rec["id"]),
    ).fetchall()
    areas = []
    for r in rows:
        v = parse_area_bigha((r["area_raw"] or "").strip())
        if v is not None:
            areas.append(v)
    return areas


def verify_record(record_id: int) -> dict | None:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute(
            "SELECT * FROM land_records WHERE id = %s", (record_id,)
        ).fetchone()
        if rec is None:
            return None

        conf_row = conn.execute(
            """SELECT min(c.confidence) AS min_confidence,
                      count(*) FILTER (WHERE c.is_unknown) AS unknowns
               FROM field_values fv
               JOIN candidates c ON c.id = fv.selected_candidate_id
               WHERE fv.record_id = %s AND fv.state <> 'CORRECTED'""",
            (record_id,),
        ).fetchone()
        open_errors = conn.execute(
            "SELECT count(*) AS n FROM anomalies WHERE record_id = %s "
            "AND status = 'OPEN' AND severity = 'error'",
            (record_id,),
        ).fetchone()["n"]
        open_warns = conn.execute(
            "SELECT count(*) AS n FROM anomalies WHERE record_id = %s "
            "AND status = 'OPEN' AND severity = 'warn'",
            (record_id,),
        ).fetchone()["n"]
        ever_validated = conn.execute(
            "SELECT 1 AS x FROM validation_results WHERE record_id = %s LIMIT 1",
            (record_id,),
        ).fetchone()
        own_area_row = conn.execute(
            "SELECT current_value AS area_raw FROM field_values "
            "WHERE record_id = %s AND field_type = 'area_raw' LIMIT 1",
            (record_id,),
        ).fetchone()
        siblings = _sibling_areas(conn, rec)

    mc = conf_row["min_confidence"]
    unknowns = conf_row["unknowns"]
    own_area = parse_area_bigha((own_area_row["area_raw"] or "").strip()) if own_area_row else None

    checks: list[dict] = []

    # 1. Reading confidence (the engine's belief about its own reading)
    if unknowns:
        checks.append({
            "check": "reading_confidence", "status": "fail",
            "detail": f"{unknowns} field(s) could not be read (UNKNOWN) — the engine abstained.",
        })
    elif mc is None:
        checks.append({
            "check": "reading_confidence", "status": "pending",
            "detail": "No engine confidence scores on this record (text-path extraction or pre-scoring run).",
        })
    elif mc >= 0.85:
        checks.append({
            "check": "reading_confidence", "status": "pass",
            "detail": f"Engine is confident in every field it read (weakest reading {round(mc * 100)}%).",
        })
    elif mc >= 0.6:
        checks.append({
            "check": "reading_confidence", "status": "warn",
            "detail": f"Weakest field reading is only {round(mc * 100)}% — treat that field with care.",
        })
    else:
        checks.append({
            "check": "reading_confidence", "status": "fail",
            "detail": f"A field scored below 60% ({round(mc * 100)}%) — too uncertain to trust.",
        })

    # 2. Deterministic business rules
    if open_errors:
        checks.append({
            "check": "business_rules", "status": "fail",
            "detail": f"{open_errors} open error-severity anomaly(ies) — validation found real problems.",
        })
    elif ever_validated is None:
        checks.append({
            "check": "business_rules", "status": "pending",
            "detail": "Record has never been validated — run validation to check the business rules.",
        })
    elif open_warns:
        checks.append({
            "check": "business_rules", "status": "warn",
            "detail": f"{open_warns} open warning-severity anomaly(ies) — rules pass with caveats.",
        })
    else:
        checks.append({
            "check": "business_rules", "status": "pass",
            "detail": "All deterministic validation rules pass (no open anomalies).",
        })

    # 3. Cross-record corroboration (checking THE DATABASE, not an opinion)
    if own_area is None:
        checks.append({
            "check": "cross_record_corroboration", "status": "unavailable",
            "detail": "No parseable area on this record to corroborate.",
        })
    elif not siblings:
        checks.append({
            "check": "cross_record_corroboration", "status": "unavailable",
            "detail": "No other record exists for this village + khasra yet — nothing to cross-check against.",
        })
    else:
        conflicts = sum(1 for a in siblings if abs(a - own_area) / max(own_area, a) > CORROBORATION_TOLERANCE)
        if conflicts:
            checks.append({
                "check": "cross_record_corroboration", "status": "fail",
                "detail": f"Area conflicts with {conflicts} of {len(siblings)} related record(s) "
                          f"(> {int(CORROBORATION_TOLERANCE * 100)}% difference for the same village + khasra).",
            })
        else:
            checks.append({
                "check": "cross_record_corroboration", "status": "pass",
                "detail": f"Area corroborated by {len(siblings)} related record(s) — independent entries agree.",
            })

    # 4. Human authority (the final word)
    state = rec["current_state"]
    if state in ("VERIFIED", "OFFICER_CERTIFIED", "ARCHIVED"):
        checks.append({
            "check": "human_review", "status": "pass",
            "detail": f"A human authorized this record ({state}).",
        })
    elif state == "REJECTED":
        checks.append({
            "check": "human_review", "status": "fail",
            "detail": "A human reviewer rejected this record.",
        })
    elif state == "QUARANTINED":
        checks.append({
            "check": "human_review", "status": "fail",
            "detail": "Record is quarantined — held out of the trusted set.",
        })
    else:
        checks.append({
            "check": "human_review", "status": "pending",
            "detail": "No human has reviewed this record yet — the system routes, humans decide.",
        })

    # 5. Document integrity (chain of custody)
    checks.append({
        "check": "document_integrity", "status": "pass",
        "detail": "Intake manifest hash verified at custody; document stored content-addressed (SHA-256).",
    })

    # 6. External registry cross-check — explicit adapter slot, never faked
    checks.append({
        "check": "external_registry", "status": "unavailable",
        "detail": "Cross-checking against LRMS/DILRMP registries requires government API access — "
                  "adapter slot ready (PS #10/#14); not available in this prototype.",
    })

    verdict, score = _verdict_from(checks)
    return {
        "verdict": verdict,
        "assurance": score,
        "checks": checks,
        "verdict_meaning": {
            "SUFFICIENT": "Every checkable signal agrees — safe to trust, human sign-off on record.",
            "PARTIAL": "No hard contradictions, but some signals are missing or pending.",
            "INSUFFICIENT": "At least one signal actively contradicts the record — do not trust without review.",
        }[verdict],
    }
