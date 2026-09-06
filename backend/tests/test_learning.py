"""PS #13 learning loop: human corrections become extraction hints.

Flow test: project a stubbed record, CORRECT a field through the audited
decision path, then GET /learning/hints must show that exact correction in
the live prompt block. Unit test covers the block builder's shape; the
endpoint requires auth like every other read.
"""
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

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

CANNED_FULL = {
    "khasra_no": "२३४", "owner_name": "राम प्रसाद", "area_raw": "२-४० bigha", "village": "सलेमपुर",
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
        conn.execute(
            "DELETE FROM human_decisions WHERE record_id IN "
            "(SELECT id FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर')"
        )
        conn.execute(
            "DELETE FROM field_values WHERE record_id IN "
            "(SELECT id FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर')"
        )
        conn.execute(
            "DELETE FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर'"
        )
        conn.execute(
            "DELETE FROM candidates WHERE run_id IN (SELECT r.id FROM processing_runs r "
            "JOIN documents d ON d.id = r.document_id "
            "JOIN intake_manifests m ON m.id = d.manifest_id WHERE m.register_ref LIKE 'TEST-%')"
        )
        conn.execute(
            "DELETE FROM processing_runs WHERE document_id IN (SELECT d.id FROM documents d "
            "JOIN intake_manifests m ON m.id = d.manifest_id WHERE m.register_ref LIKE 'TEST-%')"
        )
        conn.execute(
            "DELETE FROM pages WHERE document_id IN (SELECT d.id FROM documents d "
            "JOIN intake_manifests m ON m.id = d.manifest_id WHERE m.register_ref LIKE 'TEST-%')"
        )
        conn.execute(
            "DELETE FROM documents WHERE manifest_id IN "
            "(SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-%')"
        )
        conn.execute("DELETE FROM intake_manifests WHERE register_ref LIKE 'TEST-%'")
        conn.execute("DELETE FROM api_usage WHERE id > %s OR model = 'test-stub'", (cap["id"],))
        conn.commit()


@pytest.fixture()
def stub_engine(monkeypatch):
    from app.extract import gemini_client

    monkeypatch.setattr(
        gemini_client, "structured_extract", lambda text, schema, **kw: dict(CANNED_FULL)
    )


def test_hints_require_auth():
    assert client.get("/learning/hints").status_code == 401


def test_correction_becomes_extraction_hint(tokens, stub_engine):
    uniq = str(time.time_ns())
    m = client.post(
        "/intake",
        json={"register_ref": f"TEST-KH-{uniq}", "centre": "c", "device": "d", "expected_count": 1},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    ).json()
    d = client.post(
        "/documents",
        data={"manifest_id": str(m["id"])},
        files={"file": ("t.png", b"\x89PNG\r\n\x1a\n" + uniq.encode(), "image/png")},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    ).json()
    run = client.post(
        f"/documents/{d['id']}/extract",
        json={"text": "x"},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    ).json()
    rec = client.post(
        f"/records/from-run/{run['run']['id']}",
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    ).json()
    rec_id, ver = rec["record"]["id"], rec["record"]["record_version"]

    # the human fixes the engine's khasra read through the audited path
    khasra = next(f for f in rec["fields"] if f["field_type"] == "khasra_no")
    r = client.post(
        f"/records/{rec_id}/decisions",
        json={"decision_type": "CORRECTION", "expected_version": ver,
              "field_id": khasra["id"], "after_value": "२३५", "reason": "test: misread digit"},
        headers={"Authorization": f"Bearer {tokens['checker']}"},
    )
    assert r.status_code == 200, r.text
    decision_id = r.json()["decision"]["id"]

    # the loop closes: that correction is now live extraction guidance
    h = client.get(
        "/learning/hints", headers={"Authorization": f"Bearer {tokens['auditor']}"}
    ).json()
    assert h["hints_count"] >= 1
    assert "khasra_no" in h["prompt_block"] and "२३५" in h["prompt_block"]
    assert "misread digit" in h["prompt_block"]
    assert any(s["decision_id"] == decision_id for s in h["sources"])


def test_hints_block_builder_shape():
    from app.learning.service import build_hints_block

    assert build_hints_block([]) == ""  # no corrections -> byte-identical legacy prompt
    block = build_hints_block([
        {"decision_id": 1, "field_type": "area_raw",
         "before_value": "२-४०", "after_value": "2.40",
         "reason": "prefer ASCII digits", "actor_id": "checker"},
    ])
    assert block.startswith("REVIEWER FEEDBACK")
    assert '- area_raw: the engine read "२-४०" but the correct value is "2.40" (prefer ASCII digits)' in block
    # identical before/after is not a lesson — dropped
    noop = build_hints_block([
        {"decision_id": 2, "field_type": "village",
         "before_value": "X", "after_value": "X", "reason": None, "actor_id": "checker"},
    ])
    assert noop == ""
