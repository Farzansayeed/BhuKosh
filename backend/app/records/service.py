"""Records domain: the single audited write path (plan §5).

`apply_decision()` is the ONLY way projections change. One transaction per
decision: row lock → optimistic-version check (409) → claim check (423) →
RBAC → state-machine transition → decision row → projection update.

Record lifecycle (plan §4):
  INGESTED → EXTRACTED → VALIDATED → REVIEW_REQUIRED → VERIFIED
  → OFFICER_CERTIFIED → ARCHIVED, with REJECTED/QUARANTINED exits and
  REOPEN (reason mandatory) back to REVIEW_REQUIRED.
"""
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..db import conninfo
from ..errors import Problem

CLAIM_MINUTES = 15

DECIDE_ROLES = ("checker", "certifier", "admin")
CERTIFY_ROLES = ("certifier", "admin")

# record state machine: allowed (from, to) pairs
TRANSITIONS: set[tuple[str, str]] = {
    ("EXTRACTED", "VALIDATED"),
    ("EXTRACTED", "REVIEW_REQUIRED"),
    ("EXTRACTED", "REJECTED"),
    ("EXTRACTED", "QUARANTINED"),
    ("VALIDATED", "REVIEW_REQUIRED"),
    ("VALIDATED", "REJECTED"),
    ("REVIEW_REQUIRED", "VERIFIED"),
    ("REVIEW_REQUIRED", "REJECTED"),
    ("VERIFIED", "OFFICER_CERTIFIED"),
    ("OFFICER_CERTIFIED", "ARCHIVED"),
    ("VERIFIED", "REVIEW_REQUIRED"),          # REOPEN
    ("OFFICER_CERTIFIED", "REVIEW_REQUIRED"),  # REOPEN
    ("REJECTED", "REVIEW_REQUIRED"),           # REOPEN
}

# decision_type -> (allowed from-states, target state, roles, reason required?)
DECISIONS: dict[str, tuple[tuple[str, ...], str, tuple[str, ...], bool]] = {
    "APPROVE":   (("REVIEW_REQUIRED",), "VERIFIED", DECIDE_ROLES, False),
    "REJECT":    (("EXTRACTED", "VALIDATED", "REVIEW_REQUIRED"), "REJECTED", DECIDE_ROLES, True),
    "CERTIFY":   (("VERIFIED",), "OFFICER_CERTIFIED", CERTIFY_ROLES, False),
    "REOPEN":    (("VERIFIED", "OFFICER_CERTIFIED", "REJECTED"), "REVIEW_REQUIRED", DECIDE_ROLES, True),
}


def _claim_active(rec: dict) -> bool:
    return bool(rec["claim_owner"]) and rec["claim_expires_at"] is not None


def claim(record_id: int, user: dict) -> dict:
    """Take/refresh the review claim. Someone else's active claim → 423."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute(
            "SELECT * FROM land_records WHERE id = %s FOR UPDATE", (record_id,)
        ).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        if _claim_active(rec) and rec["claim_owner"] != user["username"]:
            raise Problem(
                423, "Locked",
                f"Record is claimed by '{rec['claim_owner']}' until {rec['claim_expires_at']}.",
            )
        conn.execute(
            "UPDATE land_records SET claim_owner = %s, "
            "claim_expires_at = now() + make_interval(mins => %s) WHERE id = %s",
            (user["username"], CLAIM_MINUTES, record_id),
        )
        conn.commit()
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
    return {k: rec[k] for k in ("id", "claim_owner", "claim_expires_at", "record_version")}


def release(record_id: int, user: dict) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute(
            "SELECT * FROM land_records WHERE id = %s FOR UPDATE", (record_id,)
        ).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        if not _claim_active(rec):
            raise Problem(409, "Conflict", "No active claim on this record.")
        if rec["claim_owner"] != user["username"] and user["role"] != "admin":
            raise Problem(423, "Locked", f"Claim is held by '{rec['claim_owner']}'.")
        conn.execute(
            "UPDATE land_records SET claim_owner = NULL, claim_expires_at = NULL WHERE id = %s",
            (record_id,),
        )
        conn.commit()
    return {"id": record_id, "claim_owner": None}


def create_from_run(run_id: int) -> dict:
    """Project a SUCCEEDED EXTRACT run into land_records + field_values.

    One run projects once (409 on reuse). Any UNKNOWN candidate routes the
    record straight to REVIEW_REQUIRED (plan §4: UNKNOWN ⇒ insufficient).
    """
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute(
            "SELECT * FROM processing_runs WHERE id = %s AND kind = 'EXTRACT'", (run_id,)
        ).fetchone()
        if not run:
            raise Problem(404, "Not Found", "No such EXTRACT run.")
        if run["status"] != "SUCCEEDED":
            raise Problem(422, "Validation Failed", f"Run is {run['status']}, not SUCCEEDED.")
        already = conn.execute(
            """SELECT 1 AS x FROM field_values fv
               JOIN candidates c ON c.id = fv.selected_candidate_id
               WHERE c.run_id = %s LIMIT 1""",
            (run_id,),
        ).fetchone()
        if already:
            raise Problem(409, "Conflict", "This run has already been projected into a record.")

        cands = conn.execute(
            "SELECT * FROM candidates WHERE run_id = %s ORDER BY id", (run_id,)
        ).fetchall()
        by_field = {c["field_type"]: c for c in cands}
        khasra = by_field.get("khasra_no")
        village = by_field.get("village")
        has_unknown = any(c["is_unknown"] for c in cands)
        state = "REVIEW_REQUIRED" if has_unknown else "EXTRACTED"

        rec = conn.execute(
            """INSERT INTO land_records (village_code, khasra_no, current_state)
               VALUES (%s, %s, %s) RETURNING *""",
            (village["value"] if village and village["value"] else "UNKNOWN",
             khasra["value"] if khasra else "UNKNOWN",
             state),
        ).fetchone()
        fields = []
        for c in cands:
            fv = conn.execute(
                """INSERT INTO field_values
                     (record_id, field_type, selected_candidate_id, current_value, raw_value, state)
                   VALUES (%s, %s, %s, %s, %s, 'NORMALIZED') RETURNING *""",
                (rec["id"], c["field_type"], c["id"], c["value"], c["raw_value"]),
            ).fetchone()
            fields.append(dict(fv))
        conn.commit()
    return {"record": dict(rec), "fields": fields}


def apply_decision(user: dict, record_id: int, payload: dict) -> dict:
    """THE write path. Lock → version → claim → RBAC → transition → decision row."""
    dtype = payload.get("decision_type", "")
    if dtype == "CORRECTION":
        return _apply_correction(user, record_id, payload)
    spec = DECISIONS.get(dtype)
    if not spec:
        raise Problem(422, "Validation Failed",
                      f"decision_type must be one of {sorted(list(DECISIONS) + ['CORRECTION'])}.")
    from_states, target, roles, reason_required = spec
    if user["role"] not in roles:
        raise Problem(403, "Forbidden", f"Role '{user['role']}' cannot perform {dtype}.")
    reason = (payload.get("reason") or "").strip()
    if reason_required and not reason:
        raise Problem(422, "Validation Failed", f"{dtype} requires a reason.")

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute(
            "SELECT * FROM land_records WHERE id = %s FOR UPDATE", (record_id,)
        ).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        if rec["record_version"] != payload.get("expected_version"):
            raise Problem(
                409, "Conflict",
                f"Stale record_version: expected {payload.get('expected_version')}, "
                f"current {rec['record_version']}.",
                current_version=rec["record_version"],
            )
        if _claim_active(rec) and rec["claim_owner"] != user["username"]:
            raise Problem(423, "Locked",
                          f"Record is claimed by '{rec['claim_owner']}'. Claim it first.")
        if rec["current_state"] not in from_states:
            raise Problem(
                422, "Validation Failed",
                f"{dtype} not allowed from {rec['current_state']} "
                f"(allowed from: {', '.join(from_states)}).",
            )

        if dtype == "APPROVE":
            conn.execute(
                "UPDATE field_values SET state = 'HUMAN_VERIFIED' "
                "WHERE record_id = %s AND current_value IS NOT NULL",
                (record_id,),
            )
        elif dtype == "CERTIFY":
            conn.execute(
                "UPDATE field_values SET state = 'CERTIFIED' WHERE record_id = %s",
                (record_id,),
            )

        decision = conn.execute(
            """INSERT INTO human_decisions
                 (record_id, decision_type, before_value, after_value, reason,
                  actor_id, actor_role, record_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (record_id, dtype,
             Jsonb({"record_state": rec["current_state"]}),
             Jsonb({"record_state": target}),
             reason or None, user["username"], user["role"], rec["record_version"]),
        ).fetchone()
        conn.execute(
            "UPDATE land_records SET current_state = %s, record_version = record_version + 1 "
            "WHERE id = %s",
            (target, record_id),
        )
        conn.commit()
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
    return {"record": dict(rec), "decision": dict(decision)}


def _apply_correction(user: dict, record_id: int, payload: dict) -> dict:
    if user["role"] not in DECIDE_ROLES:
        raise Problem(403, "Forbidden", f"Role '{user['role']}' cannot correct fields.")
    new_value = payload.get("after_value")
    if not isinstance(new_value, str) or not new_value.strip():
        raise Problem(422, "Validation Failed", "CORRECTION requires a non-empty after_value string.")
    field_id = payload.get("field_id")
    if not field_id:
        raise Problem(422, "Validation Failed", "CORRECTION requires field_id.")

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute(
            "SELECT * FROM land_records WHERE id = %s FOR UPDATE", (record_id,)
        ).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        if rec["record_version"] != payload.get("expected_version"):
            raise Problem(
                409, "Conflict",
                f"Stale record_version: expected {payload.get('expected_version')}, "
                f"current {rec['record_version']}.",
                current_version=rec["record_version"],
            )
        if _claim_active(rec) and rec["claim_owner"] != user["username"]:
            raise Problem(423, "Locked", f"Record is claimed by '{rec['claim_owner']}'.")
        if rec["current_state"] not in ("EXTRACTED", "VALIDATED", "REVIEW_REQUIRED"):
            raise Problem(422, "Validation Failed",
                          f"CORRECTION not allowed from {rec['current_state']}.")
        field = conn.execute(
            "SELECT * FROM field_values WHERE id = %s AND record_id = %s",
            (field_id, record_id),
        ).fetchone()
        if not field:
            raise Problem(404, "Not Found", "No such field on this record.")

        decision = conn.execute(
            """INSERT INTO human_decisions
                 (record_id, field_id, decision_type, before_value, after_value,
                  candidate_id, reason, actor_id, actor_role, record_version)
               VALUES (%s, %s, 'CORRECTION', %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (record_id, field_id,
             Jsonb({"value": field["current_value"]}),
             Jsonb({"value": new_value.strip()}),
             field["selected_candidate_id"], (payload.get("reason") or "").strip() or None,
             user["username"], user["role"], rec["record_version"]),
        ).fetchone()
        conn.execute(
            """UPDATE field_values
               SET current_value = %s, state = 'CORRECTED', updated_by_decision = %s
               WHERE id = %s""",
            (new_value.strip(), decision["id"], field_id),
        )
        conn.execute(
            "UPDATE land_records SET record_version = record_version + 1 WHERE id = %s",
            (record_id,),
        )
        conn.commit()
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
    return {"record": dict(rec), "decision": dict(decision)}
