"""Evidence replay — the one-click-from-value rule (plan §3A, §6).

GET /fields/{id}/replay returns the complete chain behind any displayed
value: field_value → selected candidate → processing run (engine, prompt
version, input hash) → document + page (hashes, sequence) plus a content
URI for the source bytes. No screen may show a value that isn't one
request away from this.
"""
import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from .auth.dependencies import get_current_user
from .db import conninfo
from .errors import Problem

router = APIRouter(tags=["evidence"])


@router.get("/fields/{field_id}/replay")
def replay(field_id: int, user: dict = Depends(get_current_user)) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        field = conn.execute("SELECT * FROM field_values WHERE id = %s", (field_id,)).fetchone()
        if not field:
            raise Problem(404, "Not Found", "No such field.")
        cand = None
        if field["selected_candidate_id"]:
            cand = conn.execute(
                """SELECT c.*, cr.bbox AS crop_bbox
                     FROM candidates c LEFT JOIN evidence_crops cr ON cr.id = c.crop_id
                    WHERE c.id = %s""",
                (field["selected_candidate_id"],),
            ).fetchone()
        run = None
        if cand:
            run = conn.execute(
                "SELECT * FROM processing_runs WHERE id = %s", (cand["run_id"],)
            ).fetchone()
        doc = page = None
        if run:
            page = conn.execute(
                "SELECT * FROM pages WHERE id = %s", (run["page_id"],)
            ).fetchone() if run["page_id"] else None
            doc = conn.execute(
                "SELECT * FROM documents WHERE id = %s", (run["document_id"],)
            ).fetchone()

    if not cand or not run:
        raise Problem(404, "Not Found", "Field has no bound candidate/run yet (not extracted).")

    return {
        "field": {
            "id": field["id"],
            "record_id": field["record_id"],
            "field_type": field["field_type"],
            "current_value": field["current_value"],
            "raw_value": field["raw_value"],
            "state": field["state"],
        },
        "candidate": {
            "id": cand["id"],
            "run_id": cand["run_id"],
            "field_type": cand["field_type"],
            "raw_value": cand["raw_value"],
            "value": cand["value"],
            "is_unknown": cand["is_unknown"],
            "confidence": cand["confidence"],
            "nbest_rank": cand["nbest_rank"],
            "engine": cand["engine"],
            "crop_id": cand["crop_id"],
            "crop_bbox": cand.get("crop_bbox"),
        },
        "run": {
            "id": run["id"],
            "kind": run["kind"],
            "engine_name": run["engine_name"],
            "engine_version": run["engine_version"],
            "prompt_id": run["prompt_id"],
            "prompt_version": run["prompt_version"],
            "input_hash": run["input_hash"],
            "raw_output_uri": run["raw_output_uri"],
            "status": run["status"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        },
        "document": (
            {
                "id": doc["id"],
                "sha256": doc["sha256"],
                "original_filename": doc["original_filename"],
                "mime": doc["mime"],
                "size_bytes": doc["size_bytes"],
                "content_uri": f"/documents/{doc['id']}/content",
            }
            if doc
            else None
        ),
        "page": (
            {
                "id": page["id"],
                "seq_no": page["seq_no"],
                "sha256": page["sha256"],
                "status": page["status"],
            }
            if page
            else None
        ),
    }
