"""Validation orchestration: run the rule registry against a record.

Writes append-only validation_results, upserts anomalies (one OPEN per
record+rule; auto-resolves stale system findings on re-validation), then
applies the plan §4 state rule: any open ERROR anomaly holds the record at
REVIEW_REQUIRED; a clean first validation advances EXTRACTED → VALIDATED.
"""
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..db import conninfo
from ..errors import Problem
from .registry import ANOMALY_RULES, RULES

POST_VALIDATION_STATES = ("VALIDATED", "REVIEW_REQUIRED", "VERIFIED", "OFFICER_CERTIFIED")


def _siblings(conn, rec: dict) -> list[dict]:
    """Other records with the same identity, plus their area field (for R-AREA-JUMP)."""
    return conn.execute(
        """SELECT lr.id AS record_id, lr.current_state,
                  fv.id AS area_field_id, fv.current_value AS area_raw
           FROM land_records lr
           JOIN field_values fv ON fv.record_id = lr.id AND fv.field_type = 'area_raw'
           WHERE lr.village_code = %s AND lr.khasra_no = %s AND lr.id <> %s""",
        (rec["village_code"], rec["khasra_no"], rec["id"]),
    ).fetchall()


def run_validation(record_id: int) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        fields = conn.execute(
            "SELECT * FROM field_values WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
        siblings = _siblings(conn, rec)

        result_rows, anomaly_specs = [], []
        for rule_id, rule in RULES.items():
            for finding in rule["run"](rec, fields, siblings):
                result_rows.append(
                    {
                        "record_id": record_id,
                        "field_id": finding.get("field_id"),
                        "rule_id": rule_id,
                        "rule_version": rule["version"],
                        "severity": finding.get("severity", "info") if finding["outcome"] == "fail" else "info",
                        "outcome": finding["outcome"],
                        "detail": finding.get("detail") or {},
                    }
                )
                if finding["outcome"] == "fail" and rule_id in ANOMALY_RULES:
                    anomaly_specs.append((rule_id, finding.get("severity", "error"), finding["explanation"]))

        for row in result_rows:
            conn.execute(
                """INSERT INTO validation_results
                     (record_id, field_id, rule_id, rule_version, severity, outcome, detail)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (row["record_id"], row["field_id"], row["rule_id"], row["rule_version"],
                 row["severity"], row["outcome"], Jsonb(row["detail"])),
            )

        for rule_id, severity, explanation in anomaly_specs:
            open_row = conn.execute(
                "SELECT id FROM anomalies WHERE record_id = %s AND rule_id = %s AND status = 'OPEN'",
                (record_id, rule_id),
            ).fetchone()
            if open_row:
                continue  # one OPEN finding per (record, rule)
            reopened = conn.execute(
                """SELECT id FROM anomalies
                   WHERE record_id = %s AND rule_id = %s AND status = 'RESOLVED'
                     AND resolved_by = 'system:revalidate' LIMIT 1""",
                (record_id, rule_id),
            ).fetchone()
            if reopened:  # system-resolved finding fires again → reopen it
                conn.execute(
                    """UPDATE anomalies SET status = 'OPEN', explanation = %s,
                       resolved_by = NULL, resolved_reason = NULL, resolved_at = NULL WHERE id = %s""",
                    (Jsonb(explanation), reopened["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO anomalies (record_id, rule_id, severity, explanation)
                       VALUES (%s, %s, %s, %s)""",
                    (record_id, rule_id, severity, Jsonb(explanation)),
                )

        # Auto-resolve system findings whose rule no longer fires (stale findings).
        firing = {rule_id for rule_id, _, _ in anomaly_specs}
        for rule_id in ANOMALY_RULES:
            if rule_id not in firing:
                conn.execute(
                    """UPDATE anomalies SET status = 'RESOLVED', resolved_by = 'system:revalidate',
                       resolved_reason = 'rule no longer fires on re-validation', resolved_at = now()
                       WHERE record_id = %s AND rule_id = %s AND status = 'OPEN' AND resolved_by IS NULL""",
                    (record_id, rule_id),
                )

        open_errors = conn.execute(
            "SELECT count(*) AS n FROM anomalies WHERE record_id = %s AND status = 'OPEN' AND severity = 'error'",
            (record_id,),
        ).fetchone()["n"]

        new_state = rec["current_state"]
        if open_errors and rec["current_state"] in ("EXTRACTED", "VALIDATED"):
            new_state = "REVIEW_REQUIRED"  # plan §4: open ERROR anomaly holds at REVIEW_REQUIRED
        elif not open_errors and rec["current_state"] == "EXTRACTED":
            new_state = "VALIDATED"
        if new_state != rec["current_state"]:
            conn.execute(
                "UPDATE land_records SET current_state = %s, record_version = record_version + 1 "
                "WHERE id = %s",
                (new_state, record_id),
            )
        conn.commit()

        results = conn.execute(
            "SELECT * FROM validation_results WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
        anomalies = conn.execute(
            "SELECT * FROM anomalies WHERE record_id = %s ORDER BY id", (record_id,)
        ).fetchall()
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()

    return {
        "record": dict(rec),
        "results": [dict(r) for r in results],
        "anomalies": [dict(a) for a in anomalies],
    }
