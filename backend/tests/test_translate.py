"""Multilingual output of scanned values (migration 011, /records/{id}/translate).

Engine stubbed — no real Gemini quota consumed. Asserts: permission gate,
per-field EN/HI/GU renderings stored in field_values.translations, bundle
exposure, and that human CORRECTIONS wipe translations (regenerate from the
corrected value).
"""
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.db import conninfo
from app.main import app

client = TestClient(app)

PASSWORDS = {
    "admin": "bhukosh-admin",
    "operator": "operator-dev",
    "auditor": "auditor-dev",
}

CANNED = {
    "khasra_no": {"en": "559", "hi": "५५९", "gu": "૫૫૯"},
    "owner_name": {"en": "Bhulaji Khodaji", "hi": "भुलाजी खोडाजी", "gu": "ભુલાજી ખોડાજી"},
    "area_raw": {"en": "41.76", "hi": "४१.७६", "gu": "૪૧.૭૬"},
    "village": {"en": "Odhav", "hi": "ओढव", "gu": "ઓઢવ"},
}


@pytest.fixture(scope="module")
def tokens() -> dict[str, dict]:
    return {
        role: client.post("/auth/login", json={"username": role, "password": pwd}).json()
        for role, pwd in PASSWORDS.items()
    }


@pytest.fixture()
def stub_engine(monkeypatch):
    """Patch the translate module's engine call with canned per-field renderings."""
    import app.i18n_output as i18n_output

    monkeypatch.setattr(
        i18n_output,
        "structured_extract",
        lambda text, schema, **kw: {k: dict(v) for k, v in CANNED.items()},
    )


@pytest.fixture()
def a_translatable_record(tokens) -> int:
    """A record whose field_values exist; reuse one with values, else make one."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            """SELECT fv.record_id
                 FROM field_values fv
                WHERE fv.current_value IS NOT NULL
                GROUP BY fv.record_id
               HAVING count(*) >= 2
                ORDER BY fv.record_id DESC LIMIT 1"""
        ).fetchone()
    return int(row["record_id"]) if row else 0


def test_translate_requires_permission(tokens):
    r = client.post(
        "/records/1/translate",
        headers={"Authorization": f"Bearer {tokens['auditor']['access_token']}"},
    )
    assert r.status_code == 403  # auditor lacks extract:run


def test_translate_stores_per_field_renderings(tokens, stub_engine, a_translatable_record):
    rid = a_translatable_record
    assert rid, "no translatable record available in the shared DB"
    r = client.post(
        f"/records/{rid}/translate",
        headers={"Authorization": f"Bearer {tokens['admin']['access_token']}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fields_translated"] >= 2
    for field_type, langs in body["renderings"].items():
        assert set(langs.keys()) == {"en", "hi", "gu"}

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """SELECT field_type, current_value, translations
                 FROM field_values WHERE record_id = %s AND translations IS NOT NULL""",
            (rid,),
        ).fetchall()
    assert rows
    for row in rows:
        tr = row["translations"]
        assert tr["en"] == CANNED[row["field_type"]]["en"]
        assert tr["gu"] == CANNED[row["field_type"]]["gu"]
        # originals untouched — evidence stays primary
        assert row["current_value"] is not None


def test_bundle_exposes_translations(tokens, stub_engine, a_translatable_record):
    rid = a_translatable_record
    ha = {"Authorization": f"Bearer {tokens['admin']['access_token']}"}
    client.post(f"/records/{rid}/translate", headers=ha)
    b = client.get(f"/records/{rid}", headers=ha).json()
    with_tr = [f for f in b["fields"] if f.get("translations")]
    assert with_tr, "bundle must carry the translations column"


def test_correction_wipes_translations(tokens, stub_engine, a_translatable_record):
    rid = a_translatable_record
    ha = {"Authorization": f"Bearer {tokens['admin']['access_token']}"}
    client.post(f"/records/{rid}/translate", headers=ha)

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rec = conn.execute(
            "SELECT id, record_version, current_state FROM land_records WHERE id = %s", (rid,)
        ).fetchone()
        fv = conn.execute(
            """SELECT fv.id FROM field_values fv
                 JOIN candidates c ON c.id = fv.selected_candidate_id
                WHERE fv.record_id = %s AND fv.current_value IS NOT NULL
                LIMIT 1""",
            (rid,),
        ).fetchone()
    if not fv or rec["current_state"] not in ("EXTRACTED", "VALIDATED", "REVIEW_REQUIRED"):
        pytest.skip("no correctable field/state available right now")

    r = client.post(
        f"/records/{rid}/decisions",
        headers=ha,
        json={
            "decision_type": "CORRECTION",
            "expected_version": rec["record_version"],
            "field_id": fv["id"],
            "after_value": "CORRECTED-VALUE",
            "reason": "test: wipe translations",
        },
    )
    assert r.status_code == 200, r.text
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT current_value, translations FROM field_values WHERE id = %s",
            (fv["id"],),
        ).fetchone()
    assert row["current_value"] == "CORRECTED-VALUE"
    assert row["translations"] is None, "correction must wipe stale renderings"
