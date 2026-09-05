"""/exports — trusted output with provenance (plan §5 table 14, plan §8).

An export is an immutable snapshot: the record bundle PLUS an evidence
manifest (document hashes, page sequence, engine/prompt/rulebook versions,
validation findings, decision history). JSON and CSV today; PDF deferred.
"""
import csv
import io
import json

import psycopg
from fastapi import APIRouter, Depends, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import storage
from ..auth.dependencies import get_current_user, require_roles
from ..db import conninfo
from ..errors import Problem
from ..rules.registry import REGISTRY_VERSION

router = APIRouter(tags=["exports"])

WRITE_ROLES = ("operator", "checker", "certifier", "admin")


class ExportIn(BaseModel):
    format: str = Field(pattern="^(json|csv)$")  # pdf deferred (needs a renderer)


def _bundle(record_id: int) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute("SELECT * FROM land_records WHERE id = %s", (record_id,)).fetchone()
        if not rec:
            raise Problem(404, "Not Found", "No such record.")
        fields = conn.execute(
            """SELECT fv.*, c.value AS candidate_value, c.is_unknown,
                      c.run_id, pr.engine_name, pr.engine_version,
                      pr.prompt_id, pr.prompt_version
               FROM field_values fv
               LEFT JOIN candidates c ON c.id = fv.selected_candidate_id
               LEFT JOIN processing_runs pr ON pr.id = c.run_id
               WHERE fv.record_id = %s ORDER BY fv.id""",
            (record_id,),
        ).fetchall()
        doc_rows = conn.execute(
            """SELECT d.sha256, d.original_filename, d.mime, d.size_bytes, p.seq_no
               FROM pages p JOIN documents d ON d.id = p.document_id
               WHERE p.document_id IN (
                   SELECT r.document_id FROM processing_runs r
                   JOIN candidates c ON c.run_id = r.id
                   JOIN field_values fv ON fv.selected_candidate_id = c.id
                   WHERE fv.record_id = %s)
               ORDER BY p.seq_no""",
            (record_id,),
        ).fetchall()
        validation = conn.execute(
            "SELECT rule_id, rule_version, severity, outcome, detail FROM validation_results "
            "WHERE record_id = %s ORDER BY id",
            (record_id,),
        ).fetchall()
        anomalies = conn.execute(
            "SELECT rule_id, severity, status, explanation FROM anomalies WHERE record_id = %s ORDER BY id",
            (record_id,),
        ).fetchall()
        decisions = conn.execute(
            """SELECT decision_type, before_value, after_value, reason, actor_id, actor_role,
                      record_version, created_at
               FROM human_decisions WHERE record_id = %s ORDER BY id""",
            (record_id,),
        ).fetchall()

    fields_out = []
    for f in fields:
        d = dict(f)
        d.pop("norm_steps", None)
        fields_out.append(d)
    return {
        "record": dict(rec),
        "fields": fields_out,
        "evidence": {
            "documents": [dict(d) for d in doc_rows],
            "rulebook_version": REGISTRY_VERSION,
            "validation": [dict(v) for v in validation],
            "anomalies": [dict(a) for a in anomalies],
            "decisions": [dict(d) for d in decisions],
        },
    }


def _to_csv(bundle: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["field_type", "value", "state", "is_unknown", "engine", "prompt_version"])
    for f in bundle["fields"]:
        w.writerow([
            f["field_type"], f["current_value"], f["state"], f["is_unknown"],
            f.get("engine_name"), f.get("prompt_version"),
        ])
    w.writerow([])
    w.writerow(["validation_rule", "version", "severity", "outcome"])
    for v in bundle["evidence"]["validation"]:
        w.writerow([v["rule_id"], v["rule_version"], v["severity"], v["outcome"]])
    w.writerow([])
    w.writerow(["decision_type", "actor", "actor_role", "reason", "record_version", "at"])
    for d in bundle["evidence"]["decisions"]:
        w.writerow([d["decision_type"], d["actor_id"], d["actor_role"],
                    d["reason"], d["record_version"], d["created_at"]])
    return buf.getvalue().encode("utf-8")


@router.post("/records/{record_id}/exports", status_code=201)
def create_export(record_id: int, body: ExportIn, user: dict = Depends(require_roles(*WRITE_ROLES))) -> dict:
    bundle = _bundle(record_id)
    manifest = {
        "record_id": record_id,
        "record_version": bundle["record"]["record_version"],
        "rulebook_version": REGISTRY_VERSION,
        "document_hashes": [d["sha256"] for d in bundle["evidence"]["documents"]],
        "decision_count": len(bundle["evidence"]["decisions"]),
        "open_anomalies": sum(1 for a in bundle["evidence"]["anomalies"] if a["status"] == "OPEN"),
    }
    if body.format == "json":
        data = json.dumps(bundle, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        media = "application/json"
    else:
        data = _to_csv(bundle)
        media = "text/csv"

    uri = storage.save_document(data, storage.sha256_bytes(data))
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            """INSERT INTO exports (record_id, format, storage_uri, evidence_manifest, created_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (record_id, body.format, uri, Jsonb(manifest), user["username"]),
        ).fetchone()
        conn.commit()
    return dict(row)


@router.get("/exports")
def list_exports(record_id: int, user: dict = Depends(get_current_user)) -> list[dict]:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT id, record_id, format, created_by, created_at, evidence_manifest "
            "FROM exports WHERE record_id = %s ORDER BY id DESC",
            (record_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/exports/{export_id}/download")
def download(export_id: int, user: dict = Depends(get_current_user)) -> Response:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = %s", (export_id,)).fetchone()
    if not row:
        raise Problem(404, "Not Found", "No such export.")
    try:
        data = storage.open_uri(row["storage_uri"])
    except (ValueError, OSError) as e:
        raise Problem(410, "Gone", "Export content is unavailable.") from e
    media = "application/json" if row["format"] == "json" else "text/csv"
    ext = "json" if row["format"] == "json" else "csv"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="bhukosh-record-{row["record_id"]}.{ext}"'},
    )
