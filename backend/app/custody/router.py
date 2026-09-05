"""CUSTODY: intake manifests, documents (SHA-256 idempotent upload), pages.

Plan §5 tables 2–4 (evidence_crops table exists from migration 003; its
producers arrive with PROCESSING). Error contract: RFC-7807 — 409 duplicate
upload (plan §6 idempotency), 413 too large, 415 unsupported type, 422
schema/sequence violations.
"""
import hashlib
import json

import psycopg
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from .. import storage
from ..auth.dependencies import get_current_user, require_roles
from ..config import get_settings
from ..db import conninfo
from ..errors import Problem

router = APIRouter(tags=["custody"])

WRITE_ROLES = ("operator", "checker", "certifier", "admin")

# Type allowlist with magic-byte sniffing — never trust the declared MIME alone.
MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


def _sniff(mime: str, data: bytes) -> bool:
    return any(data.startswith(sig) for sig in MAGIC.get(mime, ()))


class ManifestIn(BaseModel):
    register_ref: str = Field(min_length=1, max_length=200)
    centre: str = Field(min_length=1, max_length=200)
    device: str = Field(min_length=1, max_length=200)
    expected_count: int = Field(ge=1, le=10_000)
    page_hashes: list[str] = Field(default_factory=list, max_length=10_000)
    signature: str | None = Field(default=None, max_length=2000)


def _manifest_hash(body: ManifestIn) -> str:
    canonical = json.dumps(
        {
            "register_ref": body.register_ref,
            "centre": body.centre,
            "device": body.device,
            "expected_count": body.expected_count,
            "page_hashes": body.page_hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post("/intake", status_code=201)
def create_manifest(body: ManifestIn, user: dict = Depends(require_roles(*WRITE_ROLES))) -> dict:
    """Signed intake manifest: ordered page hashes + hash-of-hashes (plan §2 step 1)."""
    if body.page_hashes and len(body.page_hashes) != body.expected_count:
        raise Problem(422, "Validation Failed", "page_hashes length must equal expected_count.")
    mh = _manifest_hash(body)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        dup = conn.execute(
            "SELECT id FROM intake_manifests WHERE manifest_hash = %s", (mh,)
        ).fetchone()
        if dup:
            raise Problem(409, "Conflict", "Identical intake manifest already exists.", existing_id=dup["id"])
        row = conn.execute(
            """INSERT INTO intake_manifests
                 (register_ref, centre, operator, device, expected_count,
                  page_hashes, manifest_hash, signature)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (body.register_ref, body.centre, user["username"], body.device,
             body.expected_count, body.page_hashes, mh, body.signature),
        ).fetchone()
        conn.commit()
    return dict(row)


@router.get("/intake/{manifest_id}")
def get_manifest(manifest_id: int, user: dict = Depends(get_current_user)) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM intake_manifests WHERE id = %s", (manifest_id,)
        ).fetchone()
    if not row:
        raise Problem(404, "Not Found", "No such intake manifest.")
    return dict(row)


@router.post("/documents", status_code=201)
async def upload_document(
    manifest_id: int = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    """Idempotent upload: byte-identical re-upload → 409 with the existing id (plan §2 step 2)."""
    s = get_settings()
    data = await file.read()
    if not data:
        raise Problem(422, "Validation Failed", "Empty upload.")
    if len(data) > s.max_upload_bytes:
        raise Problem(413, "Payload Too Large", f"Upload exceeds {s.max_upload_bytes} bytes.")
    mime = file.content_type or ""
    if mime not in MAGIC or not _sniff(mime, data):
        raise Problem(415, "Unsupported Media Type", f"Allowed types: {', '.join(sorted(MAGIC))}.")

    digest = storage.sha256_bytes(data)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute(
            "SELECT id FROM intake_manifests WHERE id = %s", (manifest_id,)
        ).fetchone():
            raise Problem(404, "Not Found", "No such intake manifest.")
        existing = conn.execute(
            "SELECT id FROM documents WHERE sha256 = %s", (digest,)
        ).fetchone()
        if existing:
            raise Problem(
                409, "Conflict",
                "Byte-identical document already exists (idempotent upload).",
                existing_id=existing["id"],
            )
        uri = storage.save_document(data, digest)
        row = conn.execute(
            """INSERT INTO documents
                 (sha256, original_filename, mime, size_bytes, storage_uri, manifest_id, uploaded_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id, sha256, original_filename, mime, size_bytes,
                         storage_uri, manifest_id, uploaded_by, created_at""",
            (digest, file.filename or "unnamed", mime, len(data), uri, manifest_id, user["username"]),
        ).fetchone()
        conn.commit()
    return dict(row)


class PageIn(BaseModel):
    seq_no: int = Field(ge=1)
    sha256: str | None = Field(default=None, max_length=128)
    phash: str | None = Field(default=None, max_length=128)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


@router.post("/documents/{doc_id}/pages", status_code=201)
def register_page(doc_id: int, body: PageIn, user: dict = Depends(require_roles(*WRITE_ROLES))) -> dict:
    """Register a page against its document; verifies against the intake manifest.

    Pages beyond the declared count are accepted (up to a bounded overshoot) and
    flip the manifest verdict to MISMATCH — unexpected pages are detected facts,
    not registration errors.
    """
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        doc = conn.execute(
            "SELECT id, manifest_id FROM documents WHERE id = %s", (doc_id,)
        ).fetchone()
        if not doc:
            raise Problem(404, "Not Found", "No such document.")
        manifest = conn.execute(
            "SELECT expected_count, page_hashes FROM intake_manifests WHERE id = %s",
            (doc["manifest_id"],),
        ).fetchone()
        hashes = manifest["page_hashes"] or []
        slots = len(hashes) or manifest["expected_count"]
        # Unexpected pages are recorded, not rejected — detection is the point
        # (plan §2 step 1). Bounded overshoot: expected +10%, minimum 5 slots.
        overshoot_limit = slots + max(5, -(-slots // 10))
        if body.seq_no <= len(hashes) and body.sha256 and body.sha256 != hashes[body.seq_no - 1]:
            raise Problem(
                422, "Validation Failed",
                f"Page sha256 does not match manifest position {body.seq_no}.",
            )
        if body.seq_no > overshoot_limit:
            raise Problem(
                422, "Validation Failed",
                f"seq_no {body.seq_no} exceeds expected {slots} pages "
                f"(registration limit {overshoot_limit}).",
            )
        dup = conn.execute(
            "SELECT id FROM pages WHERE document_id = %s AND seq_no = %s",
            (doc_id, body.seq_no),
        ).fetchone()
        if dup:
            raise Problem(
                409, "Conflict",
                f"Page {body.seq_no} already registered for this document.",
                existing_id=dup["id"],
            )
        row = conn.execute(
            """INSERT INTO pages (document_id, seq_no, sha256, phash, width, height)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (doc_id, body.seq_no, body.sha256, body.phash, body.width, body.height),
        ).fetchone()
        # Manifest verification: total pages across the manifest's documents.
        n = conn.execute(
            """SELECT count(*) AS n
               FROM pages p JOIN documents d ON d.id = p.document_id
               WHERE d.manifest_id = %s""",
            (doc["manifest_id"],),
        ).fetchone()["n"]
        if n == manifest["expected_count"]:
            conn.execute(
                "UPDATE intake_manifests SET verified_result = 'MATCH' "
                "WHERE id = %s AND verified_result IS NULL",
                (doc["manifest_id"],),
            )
        elif n > manifest["expected_count"]:
            conn.execute(
                "UPDATE intake_manifests SET verified_result = 'MISMATCH' WHERE id = %s",
                (doc["manifest_id"],),
            )
        conn.commit()
    return dict(row)


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, user: dict = Depends(get_current_user)) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()
    if not row:
        raise Problem(404, "Not Found", "No such document.")
    return dict(row)


@router.get("/documents/{doc_id}/pages")
def list_pages(doc_id: int, user: dict = Depends(get_current_user)) -> list[dict]:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone():
            raise Problem(404, "Not Found", "No such document.")
        rows = conn.execute(
            "SELECT * FROM pages WHERE document_id = %s ORDER BY seq_no", (doc_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/documents/{doc_id}/content")
def get_document_content(doc_id: int, user: dict = Depends(get_current_user)) -> Response:
    """Serve the stored bytes (evidence replay's ground floor; signed URLs come later)."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        doc = conn.execute(
            "SELECT mime, storage_uri FROM documents WHERE id = %s", (doc_id,)
        ).fetchone()
    if not doc:
        raise Problem(404, "Not Found", "No such document.")
    try:
        data = storage.open_uri(doc["storage_uri"])
    except (ValueError, OSError) as e:
        raise Problem(410, "Gone", "Stored content is unavailable.") from e
    return Response(content=data, media_type=doc["mime"])
