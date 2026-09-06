"""PROCESSING acceptance tests.

Engine is stubbed (no real Gemini calls): the app-under-test path is real
— run rows, candidates, storage of raw output, sanitized failures, shared
rate-limit bucket. DB rows are cleaned via the TEST- manifest prefix.
"""
import hashlib
import json
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import conninfo
from app.main import app

client = TestClient(app)

PASSWORDS = {
    "admin": "bhukosh-admin",
    "operator": "operator-dev",
    "checker": "checker-dev",
    "certifier": "certifier-dev",
    "auditor": "auditor-dev",
}

PNG1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000010000000500010d0a2db40000000049454e44ae426082"
)
PNG2 = b"\x89PNG\r\n\x1a\n" + b"page-2-payload"

CANNED = {
    "khasra_no": "२३४",
    "owner_name": None,  # engine refused to guess → is_unknown candidate
    "area_raw": "२-४० bigha",
    "village": "सलेमपुर",
}


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    return {
        role: client.post("/auth/login", json={"username": role, "password": pwd}).json()["access_token"]
        for role, pwd in PASSWORDS.items()
    }


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))
    cap = {"id": 0}
    with psycopg.connect(conninfo()) as conn:
        cap["id"] = conn.execute("SELECT COALESCE(max(id), 0) AS m FROM api_usage").fetchone()[0]
    yield
    with psycopg.connect(conninfo()) as conn:
        # vision tests project runs into records: clear those projections first
        conn.execute(
            """DELETE FROM field_values WHERE record_id IN (
                   SELECT id FROM land_records WHERE village_code LIKE 'TEST-%')"""
        )
        conn.execute(
            "DELETE FROM land_records WHERE village_code LIKE 'TEST-%'"
        )
        conn.execute(
            """DELETE FROM candidates WHERE run_id IN (
                   SELECT r.id FROM processing_runs r
                   JOIN documents d ON d.id = r.document_id
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')"""
        )
        conn.execute(
            """DELETE FROM processing_runs WHERE document_id IN (
                   SELECT d.id FROM documents d
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')"""
        )
        conn.execute(
            """DELETE FROM pages WHERE document_id IN (
                   SELECT d.id FROM documents d
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')"""
        )
        conn.execute(
            """DELETE FROM documents WHERE manifest_id IN (
                   SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-%')"""
        )
        conn.execute("DELETE FROM intake_manifests WHERE register_ref LIKE 'TEST-%'")
        conn.execute("DELETE FROM api_usage WHERE id > %s OR model = 'test-stub'", (cap["id"],))
        conn.commit()


def _make_doc_with_page(tok: str, content: bytes | None = None, mime: str = "image/png") -> dict:
    # documents.sha256 and intake_manifests.manifest_hash are both globally unique,
    # so every call needs unique page bytes AND a unique manifest body.
    if content is None:
        content = b"\x89PNG\r\n\x1a\n" + hashlib.sha256(str(time.time_ns()).encode()).digest()
    m = client.post(
        "/intake",
        json={"register_ref": f"TEST-KH-{time.time_ns()}", "centre": "c", "device": "d", "expected_count": 1},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    d = client.post(
        "/documents",
        data={"manifest_id": str(m["id"])},
        files={"file": ("t.pdf" if mime == "application/pdf" else "t.png", content, mime)},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    p = client.post(
        f"/documents/{d['id']}/pages",
        json={"seq_no": 1, "sha256": d["sha256"]},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    return {"manifest": m, "document": d, "page": p}


def _extract(tok: str, doc_id: int, **json) -> object:
    return client.post(
        f"/documents/{doc_id}/extract",
        json=json or {"text": "khasra २३४ test record"},
        headers={"Authorization": f"Bearer {tok}"},
    )


def test_run_requires_auth():
    r = client.post("/documents/1/extract", json={"text": "x"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_run_forbidden_for_auditor(tokens):
    r = _extract(tokens["auditor"], 1)
    assert r.status_code == 403


def test_unknown_document_and_bad_page(tokens):
    assert _extract(tokens["operator"], 999999).status_code == 404
    combo = _make_doc_with_page(tokens["operator"])
    other = _make_doc_with_page(tokens["operator"], PNG2)
    r = _extract(tokens["operator"], combo["document"]["id"], text="x", page_id=other["page"]["id"])
    assert r.status_code == 422


def test_run_lifecycle_with_stubbed_engine(tokens, monkeypatch):
    from app.extract import gemini_client

    monkeypatch.setattr(gemini_client, "structured_extract", lambda text, schema, **kw: dict(CANNED))
    combo = _make_doc_with_page(tokens["operator"])

    r = _extract(tokens["operator"], combo["document"]["id"], text="khasra २३४", page_id=combo["page"]["id"])
    assert r.status_code == 201, r.text
    payload = r.json()
    run, cands = payload["run"], payload["candidates"]

    assert run["status"] == "SUCCEEDED"
    assert run["kind"] == "EXTRACT"
    assert run["engine_name"] == "gemini"
    assert run["prompt_id"] == "khasra_register"
    assert run["page_id"] == combo["page"]["id"]
    assert run["raw_output_uri"].split(":", 1)[0] in ("file", "supabase")
    assert run["finished_at"] is not None

    assert len(cands) == 4
    by_field = {c["field_type"]: c for c in cands}
    assert by_field["khasra_no"]["value"] == "२३४"
    assert by_field["khasra_no"]["is_unknown"] is False
    assert by_field["owner_name"]["is_unknown"] is True  # engine said null → UNKNOWN, not a guess
    assert all(c["nbest_rank"] == 1 for c in cands)

    # raw output preserved and readable, contains the full provenance envelope
    from app.storage import open_uri
    raw = json.loads(open_uri(run["raw_output_uri"]))
    assert raw["run_id"] == run["id"]
    assert raw["result"] == CANNED
    assert raw["input_sha256"] == run["input_hash"]

    # usage logged under the shared /extraction bucket
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM api_usage WHERE route = '/extraction' AND username = 'operator' AND status = 'ok'"
        ).fetchone()["n"]
    assert n >= 1


def test_failed_run_sanitizes_error(tokens, monkeypatch):
    from app.extract import gemini_client

    def boom(text, schema, **kw):
        raise RuntimeError("SECRET upstream body xyz")

    monkeypatch.setattr(gemini_client, "structured_extract", boom)
    combo = _make_doc_with_page(tokens["operator"], PNG2)
    quiet = TestClient(app, raise_server_exceptions=False)
    r = quiet.post(
        f"/documents/{combo['document']['id']}/extract",
        json={"text": "x"},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 500
    assert "SECRET" not in r.text

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        run = conn.execute(
            "SELECT status, error FROM processing_runs WHERE document_id = %s "
            "ORDER BY id DESC LIMIT 1",
            (combo["document"]["id"],),
        ).fetchone()
    assert run["status"] == "FAILED"
    assert run["error"] == "RuntimeError"  # class name only — no upstream detail


def test_rate_limit_shared_with_prototype_route(tokens):
    limit = get_settings().extract_rate_limit_per_min
    with psycopg.connect(conninfo()) as conn:
        for _ in range(limit):
            conn.execute(
                "INSERT INTO api_usage (username, route, status, model) "
                "VALUES ('operator', '/extraction', 'ok', 'test-stub')"
            )
        conn.commit()
    combo = _make_doc_with_page(tokens["operator"], PNG2)
    r = _extract(tokens["operator"], combo["document"]["id"], text="x")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(get_settings().extract_rate_window_seconds)


def test_get_run_and_list_runs(tokens, monkeypatch):
    from app.extract import gemini_client

    monkeypatch.setattr(gemini_client, "structured_extract", lambda text, schema, **kw: dict(CANNED))
    combo = _make_doc_with_page(tokens["operator"], PNG2)
    created = _extract(tokens["operator"], combo["document"]["id"], text="x").json()

    r = client.get(
        f"/extraction/runs/{created['run']['id']}",
        headers={"Authorization": f"Bearer {tokens['auditor']}"},
    )
    assert r.status_code == 200
    assert len(r.json()["candidates"]) == 4

    r = client.get(
        f"/documents/{combo['document']['id']}/runs",
        headers={"Authorization": f"Bearer {tokens['checker']}"},
    )
    assert r.status_code == 200
    assert any(row["id"] == created["run"]["id"] for row in r.json())

    assert client.get(
        "/extraction/runs/999999", headers={"Authorization": f"Bearer {tokens['operator']}"}
    ).status_code == 404


# ------------------------------------------------------- vision (image) path ----

def test_vision_extract_lifecycle(tokens, monkeypatch):
    """Image extraction: run bound to stored page bytes, crops bound to fields."""
    from app.extract import gemini_client

    monkeypatch.setattr(
        gemini_client, "structured_extract_image",
        lambda image_bytes, mime, schema, **kw: {
            "khasra_no": "૫૫૯", "owner_name": "ભુલાજી ખોડાજી",
            "area_raw": "41.76", "village": "ઓઢવ",
            # engine-returned boxes: two valid, one degenerate (must be skipped), one null
            "khasra_no_bbox": [100, 100, 300, 140],
            "owner_name_bbox": [100, 200, 400, 240],
            "area_raw_bbox": [0, 0, 0, 0],
            "village_bbox": None,
        },
    )
    # A real (uniquely-colored) PNG: crops must decode actual pixels.
    import io as _io
    from PIL import Image as _Image
    _buf = _io.BytesIO()
    _Image.new("RGB", (60, 40), (time.time_ns() // 1000 % 251 + 1,) * 3).save(_buf, "PNG")
    combo = _make_doc_with_page(tokens["operator"], content=_buf.getvalue())
    page_id = combo["page"]["id"]

    r = client.post(
        f"/pages/{page_id}/extract-image",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["status"] == "SUCCEEDED"
    assert payload["page_id"] == page_id
    assert payload["fields"]["area_raw"] == "41.76"

    run_id = payload["run_id"]
    detail = client.get(
        f"/extraction/runs/{run_id}",
        headers={"Authorization": f"Bearer {tokens['auditor']}"},
    ).json()
    run = detail["run"]
    assert run["status"] == "SUCCEEDED"
    assert run["engine_name"] == "gemini-vision"
    assert run["prompt_version"] == "2"
    assert run["document_id"] == combo["document"]["id"]
    assert run["page_id"] == page_id
    # input hash = sha256 of the exact stored page bytes
    assert run["input_hash"] == combo["document"]["sha256"]
    assert run["raw_output_uri"].split(":", 1)[0] in ("file", "supabase")

    by_field = {c["field_type"]: c for c in detail["candidates"]}
    assert set(by_field) == {"khasra_no", "owner_name", "area_raw", "village"}
    assert all(c["engine"] == "gemini-vision" for c in detail["candidates"])
    assert by_field["area_raw"]["value"] == "41.76"
    assert not any(c["is_unknown"] for c in detail["candidates"])

    # LAYOUT: exactly the two valid boxes produced crop-linked candidates.
    linked = {f: c["crop_id"] for f, c in by_field.items() if c["crop_id"]}
    assert set(linked) == {"khasra_no", "owner_name"}
    assert payload["crops"] == linked

    # Crop pixels: authenticated PNG download, 401 without a token.
    cid = linked["khasra_no"]
    img = client.get(f"/crops/{cid}/image", headers={"Authorization": f"Bearer {tokens['auditor']}"})
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/crops/{cid}/image").status_code == 401
    assert client.get(
        "/crops/999999/image", headers={"Authorization": f"Bearer {tokens['auditor']}"}
    ).status_code == 404


def test_vision_run_failure_is_sanitized(tokens, monkeypatch):
    """Engine blowup -> FAILED run with a sanitized error class, never a 500."""
    from app.extract import gemini_client
    from app.errors import Problem

    def boom(image_bytes, mime, schema, **kw):
        raise Problem(502, "Engine Error", "Extraction engine returned HTTP 503.")

    monkeypatch.setattr(gemini_client, "structured_extract_image", boom)
    combo = _make_doc_with_page(tokens["operator"])

    r = client.post(
        f"/pages/{combo['page']['id']}/extract-image",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 502, r.text
    assert "503" in r.json()["detail"]

    runs = client.get(
        f"/documents/{combo['document']['id']}/runs",
        headers={"Authorization": f"Bearer {tokens['checker']}"},
    ).json()
    failed = [x for x in runs if x["status"] == "FAILED"]
    # Problem errors store their (already client-safe) message; raw exceptions store only the class name.
    assert failed and "HTTP 503" in failed[0]["error"]


def test_low_confidence_routes_to_review(tokens, monkeypatch):
    """PS #11: any field below 0.6 confidence -> record lands in REVIEW_REQUIRED."""
    from app.extract import gemini_client

    monkeypatch.setattr(
        gemini_client, "structured_extract_image",
        lambda image_bytes, mime, schema, **kw: {
            "khasra_no": "૫૫૯", "owner_name": "ભુલાજી ખોડાજી",
            "area_raw": "41.76", "village": "TEST-REPLAY",
            "khasra_no_confidence": 0.98, "owner_name_confidence": 0.42,
            "area_raw_confidence": 0.95, "village_confidence": None,
        },
    )
    import io as _io
    from PIL import Image as _Image

    _buf = _io.BytesIO()
    _t = time.time_ns()
    _Image.new("RGB", (60, 40), (_t % 251 + 1,) * 3).save(_buf, "PNG")
    combo = _make_doc_with_page(tokens["operator"], content=_buf.getvalue())

    r = client.post(
        f"/pages/{combo['page']['id']}/extract-image",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201, r.text

    detail = client.get(
        f"/extraction/runs/{r.json()['run_id']}",
        headers={"Authorization": f"Bearer {tokens['auditor']}"},
    ).json()
    by_field = {c["field_type"]: c for c in detail["candidates"]}
    assert by_field["owner_name"]["confidence"] == 0.42
    assert by_field["village"]["confidence"] is None  # out-of-range/missing -> NULL

    rec = client.post(
        f"/records/from-run/{r.json()['run_id']}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert rec.status_code == 201, rec.text
    assert rec.json()["record"]["current_state"] == "REVIEW_REQUIRED"


def test_high_confidence_stays_extracted(tokens, monkeypatch):
    """All fields confident + known -> EXTRACTED (no forced review)."""
    from app.extract import gemini_client

    monkeypatch.setattr(
        gemini_client, "structured_extract_image",
        lambda image_bytes, mime, schema, **kw: {
            "khasra_no": "૨૩୪", "owner_name": "राम प्रसाद",
            "area_raw": "२-४०", "village": "TEST-REPLAY",
            "khasra_no_confidence": 0.95, "owner_name_confidence": 0.9,
            "area_raw_confidence": 0.88, "village_confidence": 0.93,
        },
    )
    import io as _io
    from PIL import Image as _Image

    _buf = _io.BytesIO()
    _t = time.time_ns()
    _Image.new("RGB", (60, 40), (_t % 251 + 1,) * 3).save(_buf, "PNG")
    combo = _make_doc_with_page(tokens["operator"], content=_buf.getvalue())

    r = client.post(
        f"/pages/{combo['page']['id']}/extract-image",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201, r.text
    rec = client.post(
        f"/records/from-run/{r.json()['run_id']}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert rec.status_code == 201, rec.text
    assert rec.json()["record"]["current_state"] == "EXTRACTED"


def test_pdf_vision_run(tokens, monkeypatch):
    """PS #8: a scanned PDF extracts via vision, without crop attempts."""
    from app.extract import gemini_client

    captured = {}

    def fake_extract(image_bytes, mime, schema, **kw):
        captured["mime"] = mime
        return {
            "khasra_no": "१२३", "owner_name": "किसान",
            "area_raw": "1.2", "village": "TEST-REPLAY",
            "khasra_no_bbox": [10, 10, 50, 20],
            "owner_name_bbox": None, "area_raw_bbox": None, "village_bbox": None,
            "khasra_no_confidence": 0.9, "owner_name_confidence": 0.9,
            "area_raw_confidence": 0.9, "village_confidence": 0.9,
        }

    monkeypatch.setattr(gemini_client, "structured_extract_image", fake_extract)

    minimal_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
        b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\n%%EOF"
    )
    combo = _make_doc_with_page(tokens["operator"], content=minimal_pdf, mime="application/pdf")
    assert combo["document"]["mime"] == "application/pdf"

    r = client.post(
        f"/pages/{combo['page']['id']}/extract-image",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["mime"] == "application/pdf"
    assert captured["mime"] == "application/pdf"
    assert payload["fields"]["khasra_no"] == "१२३"
    assert payload["crops"] == {}  # no crop math on PDFs

    detail = client.get(
        f"/extraction/runs/{payload['run_id']}",
        headers={"Authorization": f"Bearer {tokens['auditor']}"},
    ).json()
    cand = {c["field_type"]: c for c in detail["candidates"]}
    assert cand["khasra_no"]["crop_id"] is None
    assert cand["khasra_no"]["confidence"] == 0.9
