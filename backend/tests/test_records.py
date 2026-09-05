"""PROJECTIONS + DECISIONS acceptance tests.

Runs are created through the real processing endpoint with a stubbed engine,
then projected through the real /records endpoints. Covers: projection
idempotency, RBAC, claim 423, stale version 409, reason-required decisions,
state-machine rejections, correction semantics.
"""
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

CANNED_FULL = {  # no unknowns → record starts EXTRACTED
    "khasra_no": "२३४", "owner_name": "राम प्रसाद", "area_raw": "२-४० bigha", "village": "सलेमपुर",
}
CANNED_PARTIAL = {**CANNED_FULL, "owner_name": None}  # unknown → REVIEW_REQUIRED


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
        conn.execute("DELETE FROM human_decisions WHERE record_id IN (SELECT id FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर')")
        conn.execute("DELETE FROM field_values WHERE record_id IN (SELECT id FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर')")
        conn.execute("DELETE FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर'")
        conn.execute(
            """DELETE FROM candidates WHERE run_id IN (
                   SELECT r.id FROM processing_runs r
                   JOIN documents d ON d.id = r.document_id
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')"""
        )
        conn.execute(
            """DELETE FROM processing_runs WHERE document_id IN (
                   SELECT d.id FROM documents d JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')"""
        )
        conn.execute(
            """DELETE FROM pages WHERE document_id IN (
                   SELECT d.id FROM documents d JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')"""
        )
        conn.execute(
            """DELETE FROM documents WHERE manifest_id IN (
                   SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-%')"""
        )
        conn.execute("DELETE FROM intake_manifests WHERE register_ref LIKE 'TEST-%'")
        conn.execute("DELETE FROM api_usage WHERE id > %s OR model = 'test-stub'", (cap["id"],))
        conn.commit()


def _make_run(tok: str, canned: dict) -> dict:
    """Stubbed-engine run: manifest → doc → extract run."""
    from app.extract import gemini_client

    gemini_client.__dict__.setdefault("_orig", gemini_client.structured_extract)
    uniq = str(time.time_ns())
    m = client.post(
        "/intake",
        json={"register_ref": f"TEST-KH-{uniq}", "centre": "c", "device": "d", "expected_count": 1},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    d = client.post(
        "/documents",
        data={"manifest_id": str(m["id"])},
        files={"file": ("t.png", b"\x89PNG\r\n\x1a\n" + uniq.encode(), "image/png")},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    r = client.post(
        f"/documents/{d['id']}/extract",
        json={"text": "x"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def stub_engine(monkeypatch):
    from app.extract import gemini_client

    def _set(canned):
        monkeypatch.setattr(gemini_client, "structured_extract", lambda text, schema, **kw: dict(canned))
    return _set


def test_projection_and_idempotency(tokens, stub_engine):
    stub_engine(CANNED_FULL)
    run = _make_run(tokens["operator"], CANNED_FULL)
    r = client.post(
        f"/records/from-run/{run['run']['id']}",
        headers={"Authorization": f"Bearer {tokens['checker']}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    rec = body["record"]
    assert rec["current_state"] == "EXTRACTED"  # no unknowns
    assert rec["khasra_no"] == "२३४"
    assert rec["village_code"] == "सलेमपुर"
    assert len(body["fields"]) == 4
    assert all(f["selected_candidate_id"] for f in body["fields"])

    # second projection of the same run → 409
    r2 = client.post(
        f"/records/from-run/{run['run']['id']}",
        headers={"Authorization": f"Bearer {tokens['checker']}"},
    )
    assert r2.status_code == 409


def test_unknown_routes_to_review_required(tokens, stub_engine):
    stub_engine(CANNED_PARTIAL)
    run = _make_run(tokens["operator"], CANNED_PARTIAL)
    r = client.post(f"/records/from-run/{run['run']['id']}",
                    headers={"Authorization": f"Bearer {tokens['operator']}"})
    assert r.status_code == 201
    assert r.json()["record"]["current_state"] == "REVIEW_REQUIRED"


def test_projection_rbac_and_reads(tokens, stub_engine):
    stub_engine(CANNED_FULL)
    run = _make_run(tokens["operator"], CANNED_FULL)
    assert client.post(f"/records/from-run/{run['run']['id']}",
                       headers={"Authorization": f"Bearer {tokens['auditor']}"}).status_code == 403
    rec_id = client.post(f"/records/from-run/{run['run']['id']}",
                         headers={"Authorization": f"Bearer {tokens['operator']}"}).json()["record"]["id"]

    bundle = client.get(f"/records/{rec_id}", headers={"Authorization": f"Bearer {tokens['auditor']}"}).json()
    assert len(bundle["fields"]) == 4 and bundle["decisions"] == []

    listed = client.get("/records?state=EXTRACTED", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    assert any(x["id"] == rec_id for x in listed)
    assert client.get("/records/999999", headers={"Authorization": f"Bearer {tokens['checker']}"}).status_code == 404


def test_claim_lock_423_and_release(tokens, stub_engine):
    stub_engine(CANNED_FULL)
    run = _make_run(tokens["operator"], CANNED_FULL)
    rec_id = client.post(f"/records/from-run/{run['run']['id']}",
                         headers={"Authorization": f"Bearer {tokens['checker']}"}).json()["record"]["id"]

    c1 = client.post(f"/records/{rec_id}/claim", headers={"Authorization": f"Bearer {tokens['certifier']}"})
    assert c1.status_code == 200
    # someone else's active claim → 423
    c2 = client.post(f"/records/{rec_id}/claim", headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert c2.status_code == 423
    # decision while claimed by someone else → 423
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "APPROVE", "expected_version": 1},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert r.status_code == 423
    # release → now claimable
    rel = client.post(f"/records/{rec_id}/release", headers={"Authorization": f"Bearer {tokens['certifier']}"})
    assert rel.status_code == 200
    assert client.post(f"/records/{rec_id}/claim",
                       headers={"Authorization": f"Bearer {tokens['checker']}"}).status_code == 200


def test_approve_flow_and_field_states(tokens, stub_engine):
    stub_engine(CANNED_PARTIAL)  # starts REVIEW_REQUIRED
    run = _make_run(tokens["operator"], CANNED_PARTIAL)
    body = client.post(f"/records/from-run/{run['run']['id']}",
                       headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    rec_id, ver = body["record"]["id"], body["record"]["record_version"]

    # APPROVE from EXTRACTED would be illegal — but we're in REVIEW_REQUIRED, so it works
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "APPROVE", "expected_version": ver},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert r.status_code == 200, r.text
    assert r.json()["record"]["current_state"] == "VERIFIED"
    assert r.json()["record"]["record_version"] == ver + 1
    verified = [f for f in r.json()["fields"] if f["state"] == "HUMAN_VERIFIED"]
    assert len(verified) == 3  # null-valued owner_name stays out


def test_stale_version_409_and_bad_transition(tokens, stub_engine):
    stub_engine(CANNED_FULL)
    run = _make_run(tokens["operator"], CANNED_FULL)
    body = client.post(f"/records/from-run/{run['run']['id']}",
                       headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    rec_id, ver = body["record"]["id"], body["record"]["record_version"]
    h = {"Authorization": f"Bearer {tokens['checker']}"}

    # advance the record once (REJECTED, version 2)
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REJECT", "expected_version": ver, "reason": "x"},
                    headers=h)
    assert r.status_code == 200

    # replaying the pre-decision version (1, still >= 1) → stale → 409
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REOPEN", "expected_version": ver, "reason": "stale"},
                    headers=h)
    assert r.status_code == 409
    assert r.json()["title"] == "Conflict"

    # wrong state for CERTIFY (record is REJECTED) → 422
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "CERTIFY", "expected_version": ver + 1},
                    headers={"Authorization": f"Bearer {tokens['certifier']}"})
    assert r.status_code == 422


def test_reject_needs_reason_reopen_roundtrip(tokens, stub_engine):
    stub_engine(CANNED_FULL)
    run = _make_run(tokens["operator"], CANNED_FULL)
    body = client.post(f"/records/from-run/{run['run']['id']}",
                       headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    rec_id, ver = body["record"]["id"], body["record"]["record_version"]
    h = {"Authorization": f"Bearer {tokens['checker']}"}

    # REJECT without reason → 422
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REJECT", "expected_version": ver}, headers=h)
    assert r.status_code == 422
    # with reason → REJECTED
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REJECT", "expected_version": ver, "reason": "blurry page"},
                    headers=h)
    assert r.status_code == 200 and r.json()["record"]["current_state"] == "REJECTED"
    # REOPEN (reason mandatory) → back to REVIEW_REQUIRED
    ver2 = r.json()["record"]["record_version"]
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REOPEN", "expected_version": ver2, "reason": "rescanned"},
                    headers=h)
    assert r.status_code == 200 and r.json()["record"]["current_state"] == "REVIEW_REQUIRED"
    # decision history is append-only and complete
    hist = client.get(f"/records/{rec_id}", headers=h).json()["decisions"]
    assert [d["decision_type"] for d in hist] == ["REJECT", "REOPEN"]


def test_certify_rbac_and_correction(tokens, stub_engine):
    stub_engine(CANNED_FULL)
    run = _make_run(tokens["operator"], CANNED_FULL)
    body = client.post(f"/records/from-run/{run['run']['id']}",
                       headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    rec_id, ver = body["record"]["id"], body["record"]["record_version"]
    fields = body["fields"]

    # checker cannot certify
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "CERTIFY", "expected_version": ver},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert r.status_code == 403

    # correction by checker: fix khasra
    khasra_field = next(f for f in fields if f["field_type"] == "khasra_no")
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "CORRECTION", "expected_version": ver,
                          "field_id": khasra_field["id"], "after_value": "२३५", "reason": "misread"},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert r.status_code == 200, r.text
    fixed = next(f for f in r.json()["fields"] if f["id"] == khasra_field["id"])
    assert fixed["current_value"] == "२३५" and fixed["state"] == "CORRECTED"

    # approve → verify → certify (certifier)
    h = {"Authorization": f"Bearer {tokens['certifier']}"}
    ver = r.json()["record"]["record_version"]
    # APPROVE is illegal from EXTRACTED → 422
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "APPROVE", "expected_version": ver}, headers=h)
    assert r.status_code == 422
    # route to REVIEW_REQUIRED via REJECT → REOPEN, then APPROVE
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REJECT", "expected_version": ver, "reason": "requeue"},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    ver = r.json()["record"]["record_version"]
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REOPEN", "expected_version": ver, "reason": "fixed"},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    ver = r.json()["record"]["record_version"]
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "APPROVE", "expected_version": ver}, headers=h)
    assert r.status_code == 200 and r.json()["record"]["current_state"] == "VERIFIED"
    ver = r.json()["record"]["record_version"]
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "CERTIFY", "expected_version": ver}, headers=h)
    assert r.status_code == 200 and r.json()["record"]["current_state"] == "OFFICER_CERTIFIED"
    certified = [f for f in r.json()["fields"] if f["state"] == "CERTIFIED"]
    assert len(certified) == 4
