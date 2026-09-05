"""VALIDATION acceptance tests.

Covers: clean record → VALIDATED; UNKNOWN field → error anomaly +
REVIEW_REQUIRED; warnings never block; the MVP cross-document area jump
(+192%); anomaly resolution through the audited decision path (RBAC, reason,
already-resolved 409) and system auto-resolution on re-validation.
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

CLEAN = {"khasra_no": "२३४", "owner_name": "राम प्रसाद", "area_raw": "२-४० bigha", "village": "सलेमपुर"}
WITH_UNKNOWN = {**CLEAN, "owner_name": None}
JUMP = {**CLEAN, "area_raw": "७-०० bigha"}  # 7.00 vs 2.40 → +192%
BAD_AREA = {**CLEAN, "area_raw": "क्षेत्रफल अज्ञात"}  # unparseable → warn only
BAD_KHASRA = {**CLEAN, "khasra_no": "न/मालूम नहीं"}  # pattern warn


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
        for stmt in [
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
            if "api_usage" in stmt:  # only this one has placeholders
                conn.execute(stmt, (cap["id"],))
            else:
                conn.execute(stmt)
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


def _validate(tok: str, rec_id: int):
    return client.post(f"/records/{rec_id}/validate", headers={"Authorization": f"Bearer {tok}"})


def test_clean_record_validates_to_validated(tokens):
    rec = _project(tokens["operator"], CLEAN)["record"]
    assert rec["current_state"] == "EXTRACTED"
    r = _validate(tokens["operator"], rec["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record"]["current_state"] == "VALIDATED"
    assert body["record"]["record_version"] == rec["record_version"] + 1
    assert body["anomalies"] == []
    by_rule = {x["rule_id"]: x["outcome"] for x in body["results"]}
    assert by_rule["R-KHASRA-PATTERN"] == "pass"
    assert by_rule["R-AREA-FORMAT"] == "pass"          # २-४० parses to 2.40
    assert by_rule["R-VILLAGE-MISSING"] == "pass"
    assert by_rule["R-UNKNOWN-FIELD"] == "pass"
    assert by_rule["R-AREA-JUMP"] == "pass"            # no comparable sibling yet


def test_unknown_field_creates_error_anomaly_and_routes_to_review(tokens):
    rec = _project(tokens["operator"], WITH_UNKNOWN)["record"]  # projection already routed it
    assert rec["current_state"] == "REVIEW_REQUIRED"
    r = _validate(tokens["checker"], rec["id"])
    assert r.status_code == 200
    body = r.json()
    assert body["record"]["current_state"] == "REVIEW_REQUIRED"  # open ERROR holds it
    anomaly = next(a for a in body["anomalies"] if a["rule_id"] == "R-UNKNOWN-FIELD")
    assert anomaly["status"] == "OPEN" and anomaly["severity"] == "error"
    exp = anomaly["explanation"]
    assert exp["rule"] == "R-UNKNOWN-FIELD" and exp["evidence"] and exp["recommended_action"]
    fail = next(x for x in body["results"] if x["rule_id"] == "R-UNKNOWN-FIELD" and x["outcome"] == "fail")


def test_warnings_never_block(tokens):
    rec = _project(tokens["operator"], {**BAD_AREA})["record"]
    r = _validate(tokens["operator"], rec["id"])
    body = r.json()
    warns = [x for x in body["results"] if x["outcome"] == "fail" and x["severity"] == "warn"]
    assert {x["rule_id"] for x in warns} == {"R-AREA-FORMAT"}
    assert body["anomalies"] == []          # warns are not anomaly-grade
    assert body["record"]["current_state"] == "VALIDATED"  # no open ERROR → advances

    rec2 = _project(tokens["operator"], BAD_KHASRA)["record"]
    body2 = _validate(tokens["operator"], rec2["id"]).json()
    assert any(x["rule_id"] == "R-KHASRA-PATTERN" and x["outcome"] == "fail" for x in body2["results"])
    assert body2["record"]["current_state"] == "VALIDATED"


def test_area_jump_cross_document_join(tokens):
    first = _project(tokens["operator"], CLEAN)["record"]
    _validate(tokens["operator"], first["id"])  # → VALIDATED (comparable)

    second = _project(tokens["operator"], JUMP)["record"]
    body = _validate(tokens["operator"], second["id"]).json()
    jump = next(a for a in body["anomalies"] if a["rule_id"] == "R-AREA-JUMP")
    assert jump["status"] == "OPEN" and jump["severity"] == "error"
    exp = jump["explanation"]
    assert exp["related_record_id"] == first["id"]
    assert "2.40" in exp["calculation"] and "7.00" in exp["calculation"]
    assert body["record"]["current_state"] == "REVIEW_REQUIRED"


def test_anomaly_resolution_audited(tokens):
    rec = _project(tokens["operator"], WITH_UNKNOWN)["record"]
    body = _validate(tokens["checker"], rec["id"]).json()
    anomaly = next(a for a in body["anomalies"] if a["status"] == "OPEN")
    ver = body["record"]["record_version"]
    h = {"Authorization": f"Bearer {tokens['checker']}"}

    # operator can read anomalies but not resolve
    assert client.get("/anomalies?status=OPEN",
                      headers={"Authorization": f"Bearer {tokens['operator']}"}).status_code == 200
    r = client.post(f"/anomalies/{anomaly['id']}/resolve",
                    json={"expected_version": ver, "reason": "visually re-checked the crop"},
                    headers={"Authorization": f"Bearer {tokens['operator']}"})
    assert r.status_code == 403

    # no reason → 422 (schema-enforced)
    r = client.post(f"/anomalies/{anomaly['id']}/resolve",
                    json={"expected_version": ver}, headers=h)
    assert r.status_code == 422

    # proper resolve → RESOLVED + decision row + version bump
    r = client.post(f"/anomalies/{anomaly['id']}/resolve",
                    json={"expected_version": ver, "reason": "visually re-checked the crop"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["anomaly"]["status"] == "RESOLVED"
    assert r.json()["anomaly"]["resolved_by"] == "checker"
    assert r.json()["decision"]["decision_type"] == "ANOMALY_RESOLVE"
    assert r.json()["record"]["record_version"] == ver + 1

    # double resolve → 409
    r = client.post(f"/anomalies/{anomaly['id']}/resolve",
                    json={"expected_version": ver + 1, "reason": "again"}, headers=h)
    assert r.status_code == 409

    # decision history includes it
    hist = client.get(f"/records/{rec['id']}", headers=h).json()["decisions"]
    assert any(d["decision_type"] == "ANOMALY_RESOLVE" for d in hist)


def test_revalidate_auto_resolves_stale_system_findings(tokens):
    rec = _project(tokens["operator"], WITH_UNKNOWN)["record"]
    _validate(tokens["checker"], rec["id"])
    # officer corrects the unknown field
    bundle = client.get(f"/records/{rec['id']}", headers={"Authorization": f"Bearer {tokens['checker']}"}).json()
    field = next(f for f in bundle["fields"] if f["current_value"] is None)
    r = client.post(f"/records/{rec['id']}/decisions",
                    json={"decision_type": "CORRECTION", "expected_version": bundle["record"]["record_version"],
                          "field_id": field["id"], "after_value": "राम प्रसाद", "reason": "read from crop"},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert r.status_code == 200
    ver = r.json()["record"]["record_version"]
    # re-validate: R-UNKNOWN-FIELD no longer fires → system auto-resolves the
    # anomaly; the record STAYS at REVIEW_REQUIRED — only a human APPROVE
    # advances it out (plan §4: the system routes IN, humans decide OUT).
    body = _validate(tokens["operator"], rec["id"]).json()
    fixed = next(a for a in body["anomalies"] if a["rule_id"] == "R-UNKNOWN-FIELD")
    assert fixed["status"] == "RESOLVED" and fixed["resolved_by"] == "system:revalidate"
    assert body["record"]["current_state"] == "REVIEW_REQUIRED"
    ver = body["record"]["record_version"]
    r = client.post(f"/records/{rec['id']}/decisions",
                    json={"decision_type": "APPROVE", "expected_version": ver},
                    headers={"Authorization": f"Bearer {tokens['checker']}"})
    assert r.status_code == 200, r.text
    assert r.json()["record"]["current_state"] == "VERIFIED"
