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
from . import crops as crops_svc
from ..auth.dependencies import get_current_user, require_roles
from ..config import get_settings
from ..db import conninfo
from ..errors import Problem
from ..extract import gemini_client, rate_limit
from ..learning import service as learning_service
from ..extract.schema import (
    ALL_FIELDS,
    CORE_FIELDS,
    FIELD_BBOXES_SCHEMA,
    FIELD_CONFIDENCE_SCHEMA,
    KHASRA_SCHEMA,
    PROMPT,
    VISION_ANNOTATE,
)

router = APIRouter(tags=["processing"])

WRITE_ROLES = ("operator", "checker", "certifier", "admin")
PROMPT_ID, PROMPT_VERSION = "khasra_register", "4"
FIELDS = ALL_FIELDS          # extraction captures every field the document carries
ROUTING_FIELDS = CORE_FIELDS  # workflow routing keys off the core set only


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
    # Honest mime from magic bytes — PDFs (PS #8) pass straight to the engine,
    # which reads them natively. Crops only make sense for single-page images.
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif image_bytes[:5] == b"%PDF-":
        mime = "application/pdf"
    else:
        mime = "image/jpeg"
    is_pdf = mime == "application/pdf"
    input_hash = storage.sha256_bytes(image_bytes)

    # PS #13: reviewer corrections ride along as few-shot guidance.
    hints = learning_service.build_hints_block()
    hints_count = sum(1 for ln in hints.splitlines() if ln.startswith("- "))

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute(
            """INSERT INTO processing_runs
                 (document_id, page_id, kind, engine_name, engine_version,
                  prompt_id, prompt_version, input_hash, status, config)
               VALUES (%s, %s, 'EXTRACT', 'gemini-vision', %s, %s, %s, %s, 'RUNNING', %s)
               RETURNING *""",
            (page["document_id"], page_id, s.gemini_model, PROMPT_ID, PROMPT_VERSION,
             input_hash, Jsonb({"hints_count": hints_count})),
        ).fetchone()
        conn.commit()

    try:
        combined = {
            **KHASRA_SCHEMA["properties"],
            **FIELD_BBOXES_SCHEMA["properties"],
            **FIELD_CONFIDENCE_SCHEMA["properties"],
        }
        vision_schema = {"type": "OBJECT", "properties": combined}
        prompt_text = f"{PROMPT}\n\n{VISION_ANNOTATE}" + (f"\n\n{hints}" if hints else "")
        raw = gemini_client.structured_extract_image(
            image_bytes, mime, vision_schema, prompt=prompt_text
        )
        fields = {f: raw.get(f) for f in FIELDS if raw.get(f) is not None or f in CORE_FIELDS}
        bboxes = {f: raw.get(f"{f}_bbox") for f in FIELDS if raw.get(f) is not None}
        confs = {f: raw.get(f"{f}_confidence") for f in fields}

        # LAYOUT: materialize one evidence crop per non-null bbox (images only —
        # bbox pixel math is undefined for PDFs).
        # (Page pixels = the document scan; pages carry metadata, documents carry bytes.)
        crop_rows: dict[str, dict] = {}
        if any(bboxes.values()) and not is_pdf:
            for f, bb in bboxes.items():
                if bb:
                    try:
                        crop_rows[f] = crops_svc.store_crop(page_id, page["storage_uri"], bb)
                    except ValueError:
                        pass  # degenerate box from the engine: skip the crop, keep the value

        raw_doc = {
            "run_id": run["id"],
            "model": s.gemini_model,
            "engine": "gemini-vision",
            "prompt_id": PROMPT_ID,
            "prompt_version": PROMPT_VERSION,
            "input_sha256": input_hash,
            "page_no": page["seq_no"],
            "result": {
                **fields,
                **{f"{f}_bbox": bboxes[f] for f in fields if f in bboxes},
                **{f"{f}_confidence": confs[f] for f in fields},
            },
        }
        raw_bytes = json.dumps(raw_doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
        uri = storage.save_document(raw_bytes, storage.sha256_bytes(raw_bytes))

        with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
            stored: dict[str, int | None] = {}
            for f, cr in crop_rows.items():
                row = conn.execute(
                    """INSERT INTO evidence_crops (page_id, bbox, crop_hash, storage_uri)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (page_id, crop_hash) DO UPDATE SET bbox = EXCLUDED.bbox
                       RETURNING id""",
                    (cr["page_id"], cr["bbox"], cr["crop_hash"], cr["storage_uri"]),
                ).fetchone()
                stored[f] = row["id"]
            for f, v in fields.items():
                conf = confs[f]
                conf = conf if isinstance(conf, (int, float)) and 0 <= conf <= 1 else None
                conn.execute(
                    """INSERT INTO candidates
                         (run_id, crop_id, field_type, raw_value, value, is_unknown,
                          confidence, nbest_rank, engine)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 'gemini-vision')""",
                    (run["id"], stored.get(f), f, v, v, v is None, conf),
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
                 Jsonb({"run_id": run["id"], "mode": "image-layout",
                        "candidates": sum(1 for v in fields.values() if v is not None),
                        "crops": len(stored),
                        "min_confidence": min([c for c in confs.values() if c is not None], default=None)})),
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
    return {
        "run_id": run["id"],
        "status": "SUCCEEDED",
        "document_id": page["document_id"],
        "page_id": page_id,
        "mime": mime,
        "fields": fields,
        "crops": {f: cid for f, cid in stored.items() if cid},
    }


@router.get("/crops/{crop_id}/image")
def get_crop_image(crop_id: int, user: dict = Depends(get_current_user)) -> object:
    """The exact pixels a value was read from (Response: image/png)."""
    from fastapi import Response

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        crop = conn.execute(
            "SELECT crop_hash, storage_uri FROM evidence_crops WHERE id = %s", (crop_id,)
        ).fetchone()
    if crop is None:
        raise Problem(404, "Not Found", "No such crop.")
    data = storage.open_uri(crop["storage_uri"])
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=31536000, immutable",
                             "X-Crop-Sha256": crop["crop_hash"]})


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
    hints = learning_service.build_hints_block()
    hints_count = sum(1 for ln in hints.splitlines() if ln.startswith("- "))
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute(
            """INSERT INTO processing_runs
                 (document_id, page_id, kind, engine_name, engine_version,
                  prompt_id, prompt_version, input_hash, status, config)
               VALUES (%s, %s, 'EXTRACT', 'gemini', %s, %s, %s, %s, 'RUNNING', %s)
               RETURNING *""",
            (doc_id, body.page_id, s.gemini_model, PROMPT_ID, PROMPT_VERSION,
             input_hash, Jsonb({"hints_count": hints_count})),
        ).fetchone()
        conn.commit()

    try:
        prompt_text = f"{PROMPT}\n\nRECORD TEXT:\n{body.text}" + (f"\n\n{hints}" if hints else "")
        raw = gemini_client.structured_extract(prompt_text, KHASRA_SCHEMA)
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
            # Core fields are always recorded (absent ⇒ unknown ⇒ review);
            # extended fields only when the engine actually returned them.
            text_fields = {
                f: raw.get(f) for f in FIELDS if raw.get(f) is not None or f in CORE_FIELDS
            }
            for f, v in text_fields.items():
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
                 Jsonb({"run_id": run["id"], "candidates": len(text_fields)})),
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
