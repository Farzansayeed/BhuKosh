"""/documents/{id}/extract — evidence-bound extraction runs (PROCESSING stage).

Every Gemini call becomes an immutable processing_run; every extracted field
becomes a candidate row; the raw engine output is preserved as a
content-addressed object so any value can later be replayed against evidence
(plan §3A evidence replay).
"""
import json

import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import storage
from ..auth.dependencies import get_current_user, require_roles
from ..config import get_settings
from ..db import conninfo
from ..errors import Problem
from ..extract import gemini_client, rate_limit
from ..extract.schema import KHASRA_SCHEMA, PROMPT

router = APIRouter(tags=["processing"])

WRITE_ROLES = ("operator", "checker", "certifier", "admin")
PROMPT_ID, PROMPT_VERSION = "khasra_register", "1"
FIELDS = ("khasra_no", "owner_name", "area_raw", "village")


class ExtractIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    page_id: int | None = None


@router.post("/pages/{page_id}/extract-image", status_code=201)
def extract_page_image(page_id: int, user: dict = Depends(require_roles(*WRITE_ROLES))) -> dict:
    """Vision extraction: the stored page bytes ARE the engine input (evidence-bound).

    Input hash = SHA-256 of the exact scan bytes sent to the model, so the run
    replays against the identical image later.
    """
    s = get_settings()
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        page = conn.execute(
            """SELECT p.id, p.document_id, p.seq_no, d.storage_uri
                 FROM pages p JOIN documents d ON d.id = p.document_id
                WHERE p.id = %s""",
            (page_id,),
        ).fetchone()
    if page is None:
        raise Problem(404, "Not Found", "No such page.")

    rate_limit.enforce(user["username"], route="/extraction")

    image_bytes = storage.open_uri(page["storage_uri"])
    mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    input_hash = storage.sha256_bytes(image_bytes)

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute(
            """INSERT INTO processing_runs
                 (document_id, page_id, kind, engine_name, engine_version,
                  prompt_id, prompt_version, input_hash, status)
               VALUES (%s, %s, 'EXTRACT', 'gemini-vision', %s, %s, %s, %s, 'RUNNING')
               RETURNING *""",
            (page["document_id"], page_id, s.gemini_model, PROMPT_ID, PROMPT_VERSION, input_hash),
        ).fetchone()
        conn.commit()

    try:
        raw = gemini_client.structured_extract_image(image_bytes, mime, KHASRA_SCHEMA)
        raw_doc = {
            "run_id": run["id"],
            "model": s.gemini_model,
            "engine": "gemini-vision",
            "prompt_id": PROMPT_ID,
            "prompt_version": PROMPT_VERSION,
            "input_sha256": input_hash,
            "page_no": page["seq_no"],
            "result": raw,
        }
        raw_bytes = json.dumps(raw_doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
        uri = storage.save_document(raw_bytes, storage.sha256_bytes(raw_bytes))

        with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
            for f in FIELDS:
                v = raw.get(f)
                conn.execute(
                    """INSERT INTO candidates
                         (run_id, field_type, raw_value, value, is_unknown, nbest_rank, engine)
                       VALUES (%s, %s, %s, %s, %s, 1, 'gemini-vision')""",
                    (run["id"], f, v, v, v is None),
                )
            conn.execute(
                "UPDATE processing_runs SET status = 'SUCCEEDED', finished_at = now(), "
                "raw_output_uri = %s WHERE id = %s",
                (uri, run["id"]),
            )
            conn.execute(
                """INSERT INTO api_usage (username, route, status, model, prompt_chars, result_json)
                   VALUES (%s, '/extraction', 'ok', %s, 0, %s)""",
                (user["username"], s.gemini_model,
                 Jsonb({"run_id": run["id"], "mode": "image", "candidates": len(FIELDS)})),
            )
            conn.commit()
    except Exception as e:
        err = str(e) if isinstance(e, Problem) else e.__class__.__name__  # sanitized, always
        with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
            conn.execute(
                "UPDATE processing_runs SET status = 'FAILED', finished_at = now(), error = %s WHERE id = %s",
                (err, run["id"]),
            )
            conn.execute(
                "INSERT INTO api_usage (username, route, status, model, result_json) "
                "VALUES (%s, '/extraction', 'error', %s, %s)",
                (user["username"], s.gemini_model, Jsonb({"run_id": run["id"], "error": err})),
            )
            conn.commit()
        raise
    return {"run_id": run["id"], "status": "SUCCEEDED", "document_id": page["document_id"], "page_id": page_id, "fields": raw}


@router.post("/documents/{doc_id}/extract", status_code=201)
def extract_document(
    doc_id: int, body: ExtractIn, user: dict = Depends(require_roles(*WRITE_ROLES))
) -> dict:
    s = get_settings()
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone():
            raise Problem(404, "Not Found", "No such document.")
        if body.page_id is not None and not conn.execute(
            "SELECT id FROM pages WHERE id = %s AND document_id = %s", (body.page_id, doc_id)
        ).fetchone():
            raise Problem(422, "Validation Failed", "page_id does not belong to this document.")

    rate_limit.enforce(user["username"], route="/extraction")

    input_hash = storage.sha256_bytes(body.text.encode("utf-8"))
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute(
            """INSERT INTO processing_runs
                 (document_id, page_id, kind, engine_name, engine_version,
                  prompt_id, prompt_version, input_hash, status)
               VALUES (%s, %s, 'EXTRACT', 'gemini', %s, %s, %s, %s, 'RUNNING')
               RETURNING *""",
            (doc_id, body.page_id, s.gemini_model, PROMPT_ID, PROMPT_VERSION, input_hash),
        ).fetchone()
        conn.commit()

    try:
        raw = gemini_client.structured_extract(
            f"{PROMPT}\n\nRECORD TEXT:\n{body.text}", KHASRA_SCHEMA
        )
        raw_doc = {
            "run_id": run["id"],
            "model": s.gemini_model,
            "prompt_id": PROMPT_ID,
            "prompt_version": PROMPT_VERSION,
            "input_sha256": input_hash,
            "result": raw,
        }
        raw_bytes = json.dumps(raw_doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
        uri = storage.save_document(raw_bytes, storage.sha256_bytes(raw_bytes))

        with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
            for f in FIELDS:
                v = raw.get(f)
                conn.execute(
                    """INSERT INTO candidates
                         (run_id, field_type, raw_value, value, is_unknown, nbest_rank, engine)
                       VALUES (%s, %s, %s, %s, %s, 1, 'gemini')""",
                    (run["id"], f, v, v, v is None),
                )
            conn.execute(
                "UPDATE processing_runs SET status = 'SUCCEEDED', finished_at = now(), "
                "raw_output_uri = %s WHERE id = %s",
                (uri, run["id"]),
            )
            conn.execute(
                """INSERT INTO api_usage (username, route, status, model, prompt_chars, result_json)
                   VALUES (%s, '/extraction', 'ok', %s, %s, %s)""",
                (user["username"], s.gemini_model, len(body.text),
                 Jsonb({"run_id": run["id"], "candidates": len(FIELDS)})),
            )
            conn.commit()
    except Exception as e:
        err = str(e) if isinstance(e, Problem) else e.__class__.__name__  # sanitized, always
        with psycopg.connect(conninfo()) as conn:
            conn.execute(
                "UPDATE processing_runs SET status = 'FAILED', finished_at = now(), "
                "error = %s WHERE id = %s",
                (err, run["id"]),
            )
            conn.execute(
                "INSERT INTO api_usage (username, route, status, model, prompt_chars) "
                "VALUES (%s, '/extraction', 'error', %s, %s)",
                (user["username"], s.gemini_model, len(body.text)),
            )
            conn.commit()
        raise

    return _run_payload(run["id"])


def _run_payload(run_id: int) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute("SELECT * FROM processing_runs WHERE id = %s", (run_id,)).fetchone()
        cands = conn.execute(
            "SELECT * FROM candidates WHERE run_id = %s ORDER BY id", (run_id,)
        ).fetchall()
    return {"run": dict(run), "candidates": [dict(c) for c in cands]}


@router.get("/extraction/runs/{run_id}")
def get_run(run_id: int, user: dict = Depends(get_current_user)) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT id FROM processing_runs WHERE id = %s", (run_id,)).fetchone():
            raise Problem(404, "Not Found", "No such extraction run.")
    return _run_payload(run_id)


@router.get("/documents/{doc_id}/runs")
def list_runs(doc_id: int, user: dict = Depends(get_current_user)) -> list[dict]:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone():
            raise Problem(404, "Not Found", "No such document.")
        rows = conn.execute(
            "SELECT * FROM processing_runs WHERE document_id = %s ORDER BY id DESC", (doc_id,)
        ).fetchall()
    return [dict(r) for r in rows]
