"""AUDIT + EXPORTS acceptance tests — completes the 14-table core model.

Audit: every decision emits a chained event in the same transaction; the
chain verifies; UPDATE/DELETE are trigger-blocked; a forged row (crafted via
SQL with a wrong hash) is detected by the verifier. Exports: JSON bundle with
evidence manifest, immutability trigger, download roundtrip.
"""
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import conninfo
from app.main import app
from app.rules.registry import REGISTRY_VERSION

client = TestClient(app)

PASSWORDS = {
    "admin": "bhukosh-admin",
    "operator": "operator-dev",
    "checker": "checker-dev",
    "certifier": "certifier-dev",
    "auditor": "auditor-dev",
}

CLEAN = {"khasra_no": "२३४", "owner_name": "राम प्रसाद", "area_raw": "२-४० bigha", "village": "सलेमपुर"}


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
        rec_ids = "SELECT id FROM land_records WHERE village_code LIKE 'TEST-%' OR village_code = 'सलेमपुर'"
        conn.execute("ALTER TABLE exports DISABLE TRIGGER exports_immutable")  # test-data cleanup only
        for stmt in [
            f"DELETE FROM exports WHERE record_id IN ({rec_ids})",
            f"DELETE FROM validation_results WHERE record_id IN ({rec_ids})",
            f"DELETE FROM anomalies WHERE record_id IN ({rec_ids})",
            f"DELETE FROM human_decisions WHERE record_id IN ({rec_ids})",
            f"DELETE FROM field_values WHERE record_id IN ({rec_ids})",
            f"DELETE FROM land_records WHERE id IN ({rec_ids})",
            """DELETE FROM candidates WHERE run_id IN (
                   SELECT r.id FROM processing_runs r
                   JOIN documents d ON d.id = r.document_id
                   JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')""",
            """DELETE FROM processing_runs WHERE document_id IN (
                   SELECT d.id FROM documents d JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')""",
            """DELETE FROM pages WHERE document_id IN (
                   SELECT d.id FROM documents d JOIN intake_manifests m ON m.id = d.manifest_id
                   WHERE m.register_ref LIKE 'TEST-%')""",
            """DELETE FROM documents WHERE manifest_id IN (
                   SELECT id FROM intake_manifests WHERE register_ref LIKE 'TEST-%')""",
            "DELETE FROM intake_manifests WHERE register_ref LIKE 'TEST-%'",
            "DELETE FROM api_usage WHERE id > %s OR model = 'test-stub'",
        ]:
            if "api_usage" in stmt:
                conn.execute(stmt, (cap["id"],))
            else:
                conn.execute(stmt)
        conn.execute("ALTER TABLE exports ENABLE TRIGGER exports_immutable")
        conn.commit()


def _project(tok: str, canned: dict) -> dict:
    from app.extract import gemini_client

    orig = gemini_client.structured_extract
    gemini_client.structured_extract = lambda text, schema, **kw: dict(canned)
    try:
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
        run = client.post(
            f"/documents/{d['id']}/extract",
            json={"text": "x"},
            headers={"Authorization": f"Bearer {tok}"},
        ).json()
        return client.post(
            f"/records/from-run/{run['run']['id']}",
            headers={"Authorization": f"Bearer {tok}"},
        ).json()
    finally:
        gemini_client.structured_extract = orig


def test_every_decision_emits_chained_event(tokens):
    h = {"Authorization": f"Bearer {tokens['checker']}"}
    ha = {"Authorization": f"Bearer {tokens['auditor']}"}  # audit feed: admin/auditor only
    rec = _project(tokens["operator"], CLEAN)
    rec_id = rec["record"]["id"]

    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REJECT", "expected_version": 1, "reason": "audit-test"},
                    headers=h)
    assert r.status_code == 200

    events = client.get("/audit/events", headers=ha, params={"after_seq": 0, "limit": 1000}).json()
    mine = [e for e in events if e["entity_refs"].get("record_id") == rec_id]
    assert len(mine) == 1
    ev = mine[0]
    assert ev["action"] == "record.REJECT"
    assert ev["actor"] == "checker"
    assert ev["entity_refs"]["decision_id"] and ev["entity_refs"]["from_state"] == "EXTRACTED"

    head = client.get("/audit/chain-head", headers=ha).json()
    assert head["payload_hash"] == ev["payload_hash"]

    # reopen too → second event, chained to the first
    r = client.post(f"/records/{rec_id}/decisions",
                    json={"decision_type": "REOPEN", "expected_version": 2, "reason": "audit-test-2"},
                    headers=h)
    assert r.status_code == 200
    events = client.get("/audit/events", headers=ha, params={"limit": 1000}).json()
    mine = sorted([e for e in events if e["entity_refs"].get("record_id") == rec_id], key=lambda e: e["seq"])
    assert len(mine) == 2
    assert mine[1]["prev_hash"] == mine[0]["payload_hash"]


def test_chain_verifies_and_tamper_is_blocked(tokens):
    h = {"Authorization": f"Bearer {tokens['auditor']}"}
    v = client.post("/audit/verify", headers=h).json()
    assert v["valid"] is True and v["first_invalid_seq"] is None

    # UPDATE/DELETE are trigger-blocked (append-only enforcement)
    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(conninfo()) as conn:
            conn.execute("UPDATE audit_events SET action = 'tampered' WHERE seq = 1")
    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(conninfo()) as conn:
            conn.execute("DELETE FROM audit_events WHERE seq = 1")


def test_forged_row_is_detected_by_verifier(tokens):
    """Inject a forged event (right linkage, wrong hash) and prove the verifier
    catches it — then self-clean so the shared chain stays healthy."""
    marker = f"forged.action.{time.time_ns()}"
    try:
        with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
            last = conn.execute(
                "SELECT payload_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            conn.execute(
                """INSERT INTO audit_events (prev_hash, payload_hash, actor, action, entity_refs)
                   VALUES (%s, %s, 'forged-user', %s, '{}')""",
                (last["payload_hash"], "0" * 64, marker),
            )
            conn.commit()
        v = client.post("/audit/verify", headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
        assert v["valid"] is False
        assert v["reason"].startswith("payload hash mismatch")
        assert v["first_invalid_seq"] > 0
    finally:  # remove the forged row via a scoped trigger-disable
        with psycopg.connect(conninfo()) as conn:
            conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete")
            conn.execute("DELETE FROM audit_events WHERE action = %s", (marker,))
            conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update_delete")
            conn.commit()
    v2 = client.post("/audit/verify", headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
    assert v2["valid"] is True


def test_audit_rbac(tokens):
    h = {"Authorization": f"Bearer {tokens['operator']}"}
    for path, method in [("/audit/events", "get"), ("/audit/chain-head", "get"), ("/audit/verify", "post")]:
        r = getattr(client, method)(path, headers=h)
        assert r.status_code == 403, (path, r.status_code)


def test_export_json_bundle_with_evidence_manifest(tokens):
    h = {"Authorization": f"Bearer {tokens['checker']}"}
    rec = _project(tokens["operator"], CLEAN)
    rec_id = rec["record"]["id"]
    client.post(f"/records/{rec_id}/validate", headers=h)

    r = client.post(f"/records/{rec_id}/exports", json={"format": "json"}, headers=h)
    assert r.status_code == 201, r.text
    exp = r.json()
    assert exp["format"] == "json"
    manifest = exp["evidence_manifest"]
    assert manifest["rulebook_version"] == REGISTRY_VERSION
    # validation advanced the record one version before export
    assert manifest["record_version"] == rec["record"]["record_version"] + 1

    # decision trail + audit events referenced in the bundle on download
    dl = client.get(f"/exports/{exp['id']}/download", headers=h)
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/json")
    bundle = dl.json()
    assert bundle["record"]["id"] == rec_id
    assert len(bundle["fields"]) == 4
    assert bundle["evidence"]["rulebook_version"] == REGISTRY_VERSION
    assert isinstance(bundle["evidence"]["validation"], list)

    # immutability trigger
    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(conninfo()) as conn:
            conn.execute("UPDATE exports SET created_by = 'attacker' WHERE id = %s", (exp["id"],))


def test_export_csv_and_rbac(tokens):
    h = {"Authorization": f"Bearer {tokens['certifier']}"}
    rec = _project(tokens["operator"], CLEAN)
    rec_id = rec["record"]["id"]
    r = client.post(f"/records/{rec_id}/exports", json={"format": "csv"}, headers=h)
    assert r.status_code == 201
    dl = client.get(f"/exports/{r.json()['id']}/download", headers=h)
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("text/csv")
    assert b"field_type" in dl.content and "२३४".encode() in dl.content

    # auditor is read-only for exports too
    ra = client.post(f"/records/{rec_id}/exports", json={"format": "json"},
                     headers={"Authorization": f"Bearer {tokens['auditor']}"})
    assert ra.status_code == 403
