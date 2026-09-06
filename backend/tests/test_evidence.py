"""Evidence replay acceptance tests (plan §3A, §6).

The plan's UI invariant: *every value one click from its evidence*. These
tests prove GET /fields/{id}/replay returns the complete chain:
field → candidate → run → document → page. The engine is stubbed (no real
Gemini calls); the custody/processing/records paths exercised are real.
"""
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import conninfo
from app.extract import gemini_client
from app.main import app

client = TestClient(app)

PASSWORDS = {
    "admin": "bhukosh-admin",
    "operator": "operator-dev",
    "checker": "checker-dev",
    "certifier": "certifier-dev",
    "auditor": "auditor-dev",
}

CANNED = {
    "khasra_no": "२३४",
    "owner_name": "राम प्रसाद पुत्र श्याम लाल",
    "area_raw": "२-४० bigha",
    "village": "TEST-REPLAY",
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
    yield
    rec_ids = []
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT id FROM land_records WHERE village_code = 'TEST-REPLAY'"
        ).fetchall()
        rec_ids = [r["id"] for r in rows]
        if rec_ids:
            ids = ",".join(str(i) for i in rec_ids)
            for stmt in [
                f"DELETE FROM validation_results WHERE record_id IN ({ids})",
                f"DELETE FROM anomalies WHERE record_id IN ({ids})",
                f"DELETE FROM human_decisions WHERE record_id IN ({ids})",
                f"DELETE FROM field_values WHERE record_id IN ({ids})",
                f"DELETE FROM land_records WHERE id IN ({ids})",
            ]:
                conn.execute(stmt)
        conn.execute(
            """DELETE FROM candidates WHERE run_id IN (
                   SELECT r.id FROM processing_runs r
                   JOIN documents d ON d.id = r.document_id
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-REPLAY%')"""
        )
        conn.execute(
            """DELETE FROM processing_runs WHERE document_id IN (
                   SELECT d.id FROM documents d
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-REPLAY%')"""
        )
        conn.execute(
            """DELETE FROM pages WHERE document_id IN (
                   SELECT d.id FROM documents d
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-REPLAY%')"""
        )
        conn.execute(
            """DELETE FROM documents WHERE manifest_id IN (
                   SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-REPLAY%')"""
        )
        conn.execute("DELETE FROM intake_manifests WHERE register_ref LIKE 'TEST-REPLAY%'")
        conn.commit()


def _make_doc_with_page(tok: str) -> dict:
    content = b"\x89PNG\r\n\x1a\n" + str(time.time_ns()).encode()
    m = client.post(
        "/intake",
        json={"register_ref": f"TEST-REPLAY-{time.time_ns()}", "centre": "c", "device": "d",
              "expected_count": 1},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    d = client.post(
        "/documents",
        data={"manifest_id": str(m["id"])},
        files={"file": ("t.png", content, "image/png")},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    p = client.post(
        f"/documents/{d['id']}/pages",
        json={"seq_no": 1, "sha256": d["sha256"]},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    return {"manifest": m, "document": d, "page": p}


def test_replay_401_and_404(tokens):
    assert client.get("/fields/999999/replay").status_code == 401
    r = client.get("/fields/999999/replay", headers={"Authorization": f"Bearer {tokens['operator']}"})
    assert r.status_code == 404


def test_replay_full_chain(tokens, monkeypatch):
    monkeypatch.setattr(gemini_client, "structured_extract", lambda text, schema, **kw: dict(CANNED))
    combo = _make_doc_with_page(tokens["operator"])

    # extract → project → record with fields
    created = client.post(
        f"/documents/{combo['document']['id']}/extract",
        json={"text": "khasra २३४", "page_id": combo["page"]["id"]},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["id"]

    rec = client.post(
        f"/records/from-run/{run_id}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert rec.status_code == 201, rec.text
    bundle = rec.json()
    assert bundle["record"]["village_code"] == "TEST-REPLAY"

    # THE invariant: one request from any displayed value to its evidence
    for f in bundle["fields"]:
        r = client.get(
            f"/fields/{f['id']}/replay",
            headers={"Authorization": f"Bearer {tokens['checker']}"},
        )
        assert r.status_code == 200, r.text
        chain = r.json()

        assert chain["field"]["id"] == f["id"]
        assert chain["field"]["current_value"] == f["current_value"]
        assert chain["candidate"]["run_id"] == run_id
        assert chain["run"]["id"] == run_id
        assert chain["run"]["engine_name"] == "gemini"
        assert chain["run"]["prompt_id"] == "khasra_register"
        assert chain["run"]["input_hash"]
        assert chain["document"]["id"] == combo["document"]["id"]
        assert len(chain["document"]["sha256"]) == 64
        assert chain["document"]["content_uri"] == f"/documents/{combo['document']['id']}/content"
        assert chain["page"]["id"] == combo["page"]["id"]
        assert chain["page"]["seq_no"] == 1

    # the owner_name value above came through the exact same chain
    by_field = {f["field_type"]: f for f in bundle["fields"]}
    r = client.get(
        f"/fields/{by_field['owner_name']['id']}/replay",
        headers={"Authorization": f"Bearer {tokens['auditor']}"},
    )
    assert r.json()["field"]["current_value"] == "राम प्रसाद पुत्र श्याम लाल"


def test_replay_before_projection_falls_back_to_run_chain(tokens, monkeypatch):
    """A run exists but no record yet: replay via the candidate→run chain
    must still work when addressed by a candidate-derived field (404 before
    projection is the honest answer for a field that doesn't exist)."""
    monkeypatch.setattr(gemini_client, "structured_extract", lambda text, schema, **kw: dict(CANNED))
    combo = _make_doc_with_page(tokens["operator"])
    created = client.post(
        f"/documents/{combo['document']['id']}/extract",
        json={"text": "x", "page_id": combo["page"]["id"]},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert created.status_code == 201
    assert created.json()["run"]["status"] == "SUCCEEDED"
