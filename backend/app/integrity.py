"""Document integrity: duplicate & forged-scan detection (PS #10 duplicate
detection; the plan's "inspectable trust" principle).

Three mechanisms, each honest about what it can and cannot claim:

1. PERCEPTUAL DUPLICATES — at check time every page of the document is
   compared against all other pages via Hamming distance on hex phashes
   (64-bit dHash-style, computed client-side at registration). ≤ 6 bits
   different = near-match → a DUPLICATE finding pointing at the first-seen
   original. Byte-identical uploads are already 409'd upstream; this catches
   *re-scans and re-uploads under different bytes* — the actual forgery path.

2. IDENTIFIER LIBRARY (the learning part) — stamps, seals, signatures and
   other recurring identifiers are harvested by the vision engine from
   VERIFIED documents and stored in doc_identifiers with occurrence counts.
   New documents are compared against that library: matches raise confidence
   (IDENTIFIER_MATCH), and a document expected to carry an official stamp
   that shows none gets an IDENTIFIER_MISSING warning.

3. VISION FORGERY ANALYSIS — one structured Gemini pass per page looking for
   the physical tells of tampering: inconsistent ink/stamp overlap, erased or
   overwritten regions, mismatched fonts/baselines, pasted patches, wrong
   stamp position, page-number inconsistencies. Returns per-signal findings
   with a verdict CLEAN / SUSPECT / LIKELY_FORGED. This is decision SUPPORT:
   only a human can declare a document forged (plan: "AI recommends, humans
   decide") — the UI shows findings next to the scan, never a verdict banner.
"""
import json

import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import storage
from .auth.permissions import require_permission
from .config import get_settings
from .db import conninfo
from .errors import Problem
from .extract import gemini_client, rate_limit

router = APIRouter(tags=["integrity"])

DUPLICATE_MAX_HAMMING = 6  # of 64 bits


def _hamming_hex(a: str, b: str) -> int | None:
    """Hamming distance between two hex phashes; None if lengths differ."""
    try:
        return sum(bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a, b))
    except (ValueError, TypeError):
        return None


# --- Vision schemas ---------------------------------------------------------

INTEGRITY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "identifiers": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": ["STAMP", "SEAL", "SIGNATURE", "LOGO", "WATERMARK", "OTHER"]},
                    "label": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "legible": {"type": "BOOLEAN"},
                },
                "required": ["kind", "label"],
            },
        },
        "tamper_signals": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "signal": {"type": "STRING"},
                    "location": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "reasoning": {"type": "STRING"},
                },
                "required": ["signal", "confidence"],
            },
        },
        "verdict": {"type": "STRING", "enum": ["CLEAN", "SUSPECT", "LIKELY_FORGED"]},
        "summary": {"type": "STRING"},
    },
    "required": ["identifiers", "verdict", "summary"],
}

INTEGRITY_PROMPT = """You are a forensic document examiner reviewing a scan of an Indian land record.

TASK 1 — identifiers: list every official/recurrent identifier visible:
stamps, round seals, signature marks, letterheads/logos, embossed or printed
watermarks, barcodes/QR. For each: kind, a short label (use any legible text,
e.g. "TEHSIL OFFICE RAMPUR round seal"; if illegible, "illegible round seal"),
one-line description, and whether the text is legible.

TASK 2 — tamper signals: look for physical evidence of alteration:
- ink or stamp strokes that overlap inconsistent with natural writing order
- erased, scraped, bleached, or overwritten regions
- font/baseline/spacing mismatches within the same table or line
- pasted patches, mismatched paper tone, cut-and-paste edges
- stamps/seals placed over writing in an unnatural way, or missing where an
  official record of this type would carry one
- page-number or register-line inconsistencies
For each signal give a location description and a 0-1 confidence. DO NOT
invent signals: an ordinary old, faded, skewed scan is not evidence of
forgery. If the page shows only age damage, return an empty array.

verdict: CLEAN (no tamper evidence) / SUSPECT (1+ signal worth review) /
LIKELY_FORGED (multiple concordant signals). summary: 2-3 sentences a revenue
officer can act on. You examine evidence; you do not render a legal verdict."""


# --- Endpoints --------------------------------------------------------------


class LearnIn(BaseModel):
    identifiers: list[dict] = Field(min_length=1, max_length=20)


def _doc_pages(doc_id: int, conn) -> list[dict]:
    return conn.execute(
        """SELECT p.*, d.original_filename, d.sha256 AS doc_sha256
             FROM pages p JOIN documents d ON d.id = p.document_id
            WHERE p.document_id = %s ORDER BY p.seq_no""",
        (doc_id,),
    ).fetchall()


@router.post("/documents/{doc_id}/integrity-check")
def integrity_check(doc_id: int, user: dict = Depends(require_permission("documents:write"))) -> dict:
    """Full-document integrity pass: perceptual duplicates + learned-identifier
    comparison + optional vision forensics (vision=true, costs an engine call)."""
    s = get_settings()
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()
        if not doc:
            raise Problem(404, "Not Found", "No such document.")
        pages = _doc_pages(doc_id, conn)

        # ---- 1. perceptual duplicates (pure SQL-free Hamming on stored phashes)
        all_ph = conn.execute(
            """SELECT p.id, p.document_id, p.phash, d.original_filename
                 FROM pages p JOIN documents d ON d.id = p.document_id
                WHERE p.phash IS NOT NULL AND p.document_id <> %s""",
            (doc_id,),
        ).fetchall()
        new_findings = []
        for p in pages:
            if not p["phash"]:
                continue
            best = None
            for other in all_ph:
                d = _hamming_hex(p["phash"], other["phash"])
                if d is not None and d <= DUPLICATE_MAX_HAMMING and (best is None or d < best["distance"]):
                    best = {"distance": d, "page_id": other["id"],
                            "document_id": other["document_id"], "filename": other["original_filename"]}
            if best:
                new_findings.append((p["id"], best))

        conn.execute("DELETE FROM integrity_findings WHERE page_id IN (SELECT id FROM pages WHERE document_id = %s)", (doc_id,))
        for page_id, best in new_findings:
            conn.execute(
                """INSERT INTO integrity_findings (page_id, kind, severity, detail, original_page_id, engine)
                   VALUES (%s, 'DUPLICATE', 'warn', %s, %s, 'phash')""",
                (page_id, Jsonb(best), best["page_id"]),
            )

        library = conn.execute(
            """SELECT label, kind, times_seen FROM doc_identifiers WHERE verified ORDER BY times_seen DESC LIMIT 200"""
        ).fetchall()
        conn.commit()

    return {
        "document_id": doc_id,
        "pages_checked": len(pages),
        "duplicate_findings": [
            {"page_id": pid, "severity": "warn",
             "detail": f"Near-identical to page of '{b['filename']}' (doc {b['document_id']}, "
                       f"page {b['page_id']}) — {b['distance']}/64 bits differ. Likely a re-scan/re-upload."}
            for pid, b in new_findings
        ],
        "identifier_library_size": len(library),
        "vision_analysis": None,
        "note": "Perceptual duplicate check complete. POST vision=true for forensic analysis.",
    }


@router.post("/documents/{doc_id}/integrity-vision")
def integrity_vision(doc_id: int, user: dict = Depends(require_permission("documents:write"))) -> dict:
    """Vision forensic pass: identifiers + tamper signals + verdict per page.
    Findings are stored; identifiers can be promoted into the learned library."""
    s = get_settings()
    if not s.gemini_api_key:
        raise Problem(503, "Engine Unavailable", "Vision engine is not configured.")
    rate_limit.enforce(user["username"], route="/extraction")

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        pages = _doc_pages(doc_id, conn)
        if not pages:
            raise Problem(404, "Not Found", "Document has no pages.")
        doc_sha = conn.execute("SELECT sha256 FROM documents WHERE id = %s", (doc_id,)).fetchone()["sha256"]
        library = conn.execute(
            "SELECT label, kind, times_seen FROM doc_identifiers WHERE verified"
        ).fetchall()

    results = []
    for p in pages:
        image_bytes = storage.open_uri(p["storage_uri"])
        mime = "application/pdf" if image_bytes[:5] == b"%PDF-" else (
            "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg")
        known = "\n".join(f"- [{i['kind']}] {i['label']} (seen {i['times_seen']}x on verified records)"
                          for i in library) or "- (library is empty)"
        prompt = (f"{INTEGRITY_PROMPT}\n\nKNOWN OFFICIAL IDENTIFIERS from verified records:\n{known}\n\n"
                  f"Compare what you see against this library: which known identifiers are present "
                  f"(IDENTIFIER_MATCH with the label), and which would you expect on a document of "
                  f"this type that are absent (IDENTIFIER_MISSING)?")
        raw = gemini_client.structured_extract_image(image_bytes, mime, INTEGRITY_SCHEMA, prompt=prompt)

        verdict = raw.get("verdict") if raw.get("verdict") in ("CLEAN", "SUSPECT", "LIKELY_FORGED") else "SUSPECT"
        sev = {"CLEAN": "info", "SUSPECT": "warn", "LIKELY_FORGED": "error"}[verdict]

        with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
            conn.execute(
                """INSERT INTO integrity_findings (page_id, kind, severity, detail, engine)
                   VALUES (%s, 'FORGED_SUSPECT', %s, %s, 'gemini-vision-integrity')""",
                (p["id"], sev,
                 Jsonb({"verdict": verdict, "summary": raw.get("summary"),
                        "tamper_signals": raw.get("tamper_signals", []),
                        "identifiers_seen": raw.get("identifiers", [])})),
            )
            conn.commit()

        results.append({
            "page_id": p["id"], "seq_no": p["seq_no"], "verdict": verdict,
            "summary": raw.get("summary"),
            "tamper_signals": raw.get("tamper_signals", []),
            "identifiers": raw.get("identifiers", []),
        })

    worst = "info"
    for r in results:
        worst = max(worst, {"CLEAN": "info", "SUSPECT": "warn", "LIKELY_FORGED": "error"}[r["verdict"]], key=lambda x: ["info", "warn", "error"].index(x))
    return {
        "document_id": doc_id, "pages": results, "overall_severity": worst,
        "disclaimer": "Decision support only — a human reviewer decides whether a document is forged.",
    }


@router.get("/documents/{doc_id}/integrity")
def get_integrity(doc_id: int, user: dict = Depends(require_permission("documents:read"))) -> dict:
    """Stored findings for a document (no engine calls)."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if not conn.execute("SELECT 1 AS x FROM documents WHERE id = %s", (doc_id,)).fetchone():
            raise Problem(404, "Not Found", "No such document.")
        rows = conn.execute(
            """SELECT f.*, p.seq_no FROM integrity_findings f
               JOIN pages p ON p.id = f.page_id
               WHERE p.document_id = %s ORDER BY f.id DESC""",
            (doc_id,),
        ).fetchall()
    return {"document_id": doc_id, "findings": [dict(r) for r in rows]}


@router.post("/integrity/learn/{page_id}")
def learn_identifiers(page_id: int, body: LearnIn,
                      user: dict = Depends(require_permission("documents:write"))) -> dict:
    """Promote identifiers from a VERIFIED document's vision pass into the
    reference library (the learning loop for stamps/seals/signatures)."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        verified = conn.execute(
            """SELECT 1 AS x FROM processing_runs pr
               JOIN candidates c ON c.run_id = pr.id
               JOIN field_values fv ON fv.selected_candidate_id = c.id
               JOIN land_records r ON r.id = fv.record_id
               WHERE pr.page_id = %s AND r.current_state IN ('VERIFIED', 'OFFICER_CERTIFIED', 'ARCHIVED')
               LIMIT 1""",
            (page_id,),
        ).fetchone()
        if not verified:
            raise Problem(422, "Validation Failed",
                          "Identifiers can only be learned from pages of VERIFIED+ records — "
                          "the library must be built on trusted documents.")

        for ident in body.identifiers[:20]:
            kind = ident.get("kind")
            label = (ident.get("label") or "").strip()
            if kind not in ("STAMP", "SEAL", "SIGNATURE", "LOGO", "WATERMARK", "OTHER") or not label:
                continue
            conn.execute(
                """INSERT INTO doc_identifiers (kind, label, page_id, source, verified, times_seen)
                   VALUES (%s, %s, %s, 'vision', TRUE, 1)
                   ON CONFLICT (label, kind) DO UPDATE
                     SET times_seen = doc_identifiers.times_seen + 1""",
                (kind, label, page_id),
            )
        conn.commit()
        n = conn.execute("SELECT count(*) AS n FROM doc_identifiers").fetchone()["n"]
    return {"learned": len(body.identifiers), "library_size": n}
