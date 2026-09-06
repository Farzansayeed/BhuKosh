"""/stats dashboard (PS #16): auth gate + payload shape.

Read-only endpoint, so no fixtures/cleanup needed — assertions are
data-independent by construction (totals must equal the sum of their parts).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_stats_requires_auth():
    assert client.get("/stats").status_code == 401


def test_stats_payload_shape():
    tok = client.post(
        "/auth/login", json={"username": "auditor", "password": "auditor-dev"}
    ).json()["access_token"]
    body = client.get("/stats", headers={"Authorization": f"Bearer {tok}"}).json()

    assert set(body) == {
        "records", "fields", "anomalies_by_rule", "extraction_runs", "api_calls", "progress",
    }
    rec = body["records"]
    assert {"total", "by_state", "pending_verification"} <= set(rec)
    assert rec["total"] == sum(rec["by_state"].values())
    assert rec["pending_verification"] == rec["by_state"].get("REVIEW_REQUIRED", 0)

    fields = body["fields"]
    assert {"total", "by_state", "corrections", "accuracy_percent"} <= set(fields)
    assert fields["total"] == sum(fields["by_state"].values())
    acc = fields["accuracy_percent"]
    assert acc is None or 0.0 <= acc <= 100.0

    for row in body["progress"]:
        assert {"region", "records", "confirmed", "pending_review"} <= set(row)
        assert row["confirmed"] + row["pending_review"] <= row["records"]

    for run in body["extraction_runs"]:
        assert {"engine_name", "engine_version", "status", "n", "avg_seconds"} <= set(run)
