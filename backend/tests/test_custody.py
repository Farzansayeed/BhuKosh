"""CUSTODY acceptance tests.

Storage is redirected to a tmp dir (sandbox fixture) and DB rows created here
are cleaned up by register_ref prefix. No external services touched.
"""
import hashlib

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

# Standard 1x1 PNG (valid magic + structure).
PNG1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000010000000500010d0a2db40000000049454e44ae426082"
)
# Second "PNG": valid magic, different bytes (sniffing only — custody never decodes).
PNG2 = b"\x89PNG\r\n\x1a\n" + b"page-2-payload"
FAKE_PDF = b"%PDF-1.4\n%% smoke\n"


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    return {
        role: client.post("/auth/login", json={"username": role, "password": pwd}).json()["access_token"]
        for role, pwd in PASSWORDS.items()
    }


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))

    def _clean():
        with psycopg.connect(conninfo()) as conn:
            conn.execute(
                """DELETE FROM pages WHERE document_id IN (
                       SELECT id FROM documents WHERE manifest_id IN (
                           SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-%'))"""
            )
            conn.execute(
                """DELETE FROM documents WHERE manifest_id IN (
                       SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-%')"""
            )
            conn.execute("DELETE FROM intake_manifests WHERE register_ref LIKE 'TEST-%'")
            conn.commit()

    _clean()  # pre-clean: a killed run must never poison the next one
    yield
    _clean()


def _make_manifest(tok: str, expected_count: int = 1, page_hashes: list[str] | None = None) -> dict:
    body = {
        "register_ref": "TEST-KH-001",
        "centre": "Tehsil Salempr",
        "device": "scan-01",
        "expected_count": expected_count,
    }
    if page_hashes is not None:
        body["page_hashes"] = page_hashes
    r = client.post("/intake", json=body, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 201, r.text
    return r.json()


def _upload(tok: str, manifest_id: int, content: bytes, mime: str = "image/png", name: str = "t.png") -> dict:
    r = client.post(
        "/documents",
        data={"manifest_id": str(manifest_id)},
        files={"file": (name, content, mime)},
        headers={"Authorization": f"Bearer {tok}"},
    )
    return r


def test_intake_requires_auth():
    r = client.post("/intake", json={"register_ref": "X", "centre": "c", "device": "d", "expected_count": 1})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_auditor_cannot_write(tokens):
    for path, kwargs in [
        ("/intake", {"json": {"register_ref": "X", "centre": "c", "device": "d", "expected_count": 1}}),
        ("/documents", {"data": {"manifest_id": "1"}, "files": {"file": ("a.png", PNG1, "image/png")}}),
    ]:
        r = client.post(path, headers={"Authorization": f"Bearer {tokens['auditor']}"}, **kwargs)
        assert r.status_code == 403
        assert r.json()["title"] == "Forbidden"


def test_create_manifest(tokens):
    m = _make_manifest(tokens["operator"], expected_count=3, page_hashes=["a" * 64, "b" * 64, "c" * 64])
    assert len(m["manifest_hash"]) == 64
    assert m["operator"] == "operator"
    assert m["verified_result"] is None
    # identical manifest → 409 (hash-of-hashes is unique)
    r = client.post(
        "/intake",
        json={
            "register_ref": "TEST-KH-001", "centre": "Tehsil Salempr", "device": "scan-01",
            "expected_count": 3, "page_hashes": ["a" * 64, "b" * 64, "c" * 64],
        },
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 409


def test_page_hash_count_must_match(tokens):
    r = client.post(
        "/intake",
        json={"register_ref": "TEST-KH-001", "centre": "c", "device": "d",
              "expected_count": 2, "page_hashes": ["a" * 64]},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 422


def test_upload_and_dedup(tokens):
    m = _make_manifest(tokens["operator"])
    r = _upload(tokens["operator"], m["id"], PNG1)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["sha256"] == hashlib.sha256(PNG1).hexdigest()
    assert doc["size_bytes"] == len(PNG1)
    # bytes actually on disk, content-addressed
    from app.storage import open_uri
    assert open_uri(doc["storage_uri"]) == PNG1
    # byte-identical re-upload → 409 + existing id (plan §6)
    r2 = _upload(tokens["operator"], m["id"], PNG1, name="different-name.png")
    assert r2.status_code == 409
    assert r2.json()["existing_id"] == doc["id"]


def test_upload_rejects_bad_mime_and_fake_magic(tokens):
    m = _make_manifest(tokens["operator"])
    assert _upload(tokens["operator"], m["id"], b"hello", "text/plain").status_code == 415
    # claims PDF, bytes are not %PDF → sniff failure
    r = _upload(tokens["operator"], m["id"], b"not a pdf", "application/pdf", name="t.pdf")
    assert r.status_code == 415
    # real pdf magic passes
    r = _upload(tokens["operator"], m["id"], FAKE_PDF, "application/pdf", name="t.pdf")
    assert r.status_code == 201


def test_upload_rejects_oversize(tokens, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 10)
    m = _make_manifest(tokens["operator"])
    r = _upload(tokens["operator"], m["id"], PNG1)  # 70 bytes > 10
    assert r.status_code == 413


def test_upload_requires_existing_manifest(tokens):
    r = _upload(tokens["operator"], 999999, PNG1)
    assert r.status_code == 404


def test_page_flow_and_manifest_verification(tokens):
    m = _make_manifest(tokens["operator"], expected_count=2)
    d1 = _upload(tokens["operator"], m["id"], PNG1, name="t-1.png").json()
    d2 = _upload(tokens["operator"], m["id"], PNG2, name="t-2.png").json()
    # duplicate seq on the same document → 409
    r = client.post(
        f"/documents/{d1['id']}/pages",
        json={"seq_no": 1, "sha256": d1["sha256"]},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201
    r = client.post(
        f"/documents/{d1['id']}/pages",
        json={"seq_no": 1},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 409
    # incomplete → still no verdict
    assert client.get(f"/intake/{m['id']}", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()[
        "verified_result"
    ] is None
    # second page completes the manifest → MATCH
    r = client.post(
        f"/documents/{d2['id']}/pages",
        json={"seq_no": 2, "sha256": d2["sha256"]},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201
    assert client.get(f"/intake/{m['id']}", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()[
        "verified_result"
    ] == "MATCH"
    # extra page → MISMATCH (unexpected page)
    r = client.post(
        f"/documents/{d2['id']}/pages",
        json={"seq_no": 3},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201
    assert client.get(f"/intake/{m['id']}", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()[
        "verified_result"
    ] == "MISMATCH"


def test_page_position_hash_mismatch(tokens):
    m = _make_manifest(tokens["operator"], expected_count=1, page_hashes=["ab" * 32])
    d = _upload(tokens["operator"], m["id"], PNG1).json()
    r = client.post(
        f"/documents/{d['id']}/pages",
        json={"seq_no": 1, "sha256": hashlib.sha256(PNG1).hexdigest()},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 422
    # the manifest-mandated hash passes and completes → MATCH
    r = client.post(
        f"/documents/{d['id']}/pages",
        json={"seq_no": 1, "sha256": "ab" * 32},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 201
    assert client.get(f"/intake/{m['id']}", headers={"Authorization": f"Bearer {tokens['operator']}"}).json()[
        "verified_result"
    ] == "MATCH"


def test_seq_beyond_hard_limit_rejected(tokens):
    m = _make_manifest(tokens["operator"], expected_count=1)
    d = _upload(tokens["operator"], m["id"], PNG1).json()
    # hard cap = expected + max(5, 10%) = 6 → seq 7 is rejected outright
    r = client.post(
        f"/documents/{d['id']}/pages",
        json={"seq_no": 7},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert r.status_code == 422


def test_reads_all_staff_and_content_roundtrip(tokens):
    m = _make_manifest(tokens["operator"])
    d = _upload(tokens["operator"], m["id"], PNG1).json()
    for role in ("checker", "certifier", "auditor"):
        r = client.get(f"/documents/{d['id']}", headers={"Authorization": f"Bearer {tokens[role]}"})
        assert r.status_code == 200
        assert r.json()["sha256"] == hashlib.sha256(PNG1).hexdigest()
        assert client.get(
            f"/documents/{d['id']}/pages", headers={"Authorization": f"Bearer {tokens[role]}"}
        ).status_code == 200
    r = client.get(f"/documents/{d['id']}/content", headers={"Authorization": f"Bearer {tokens['auditor']}"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG1
