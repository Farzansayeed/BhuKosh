"""/extract acceptance tests.

No real Gemini calls: the first three run against the real app but stop before
the engine (no auth / wrong role / rate limit); the success test monkeypatches
the engine client with a canned response. All rows this module adds to
api_usage are cleaned up after every test.
"""
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


@pytest.fixture(scope="module")
def tokens() -> dict[str, dict]:
    return {
        role: client.post("/auth/login", json={"username": role, "password": pwd}).json()
        for role, pwd in PASSWORDS.items()
    }


@pytest.fixture(autouse=True)
def clean_usage_rows():
    sql = "DELETE FROM api_usage WHERE result_json ? '__test' OR model = 'test-stub'"
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        conn.execute(sql)  # pre-clean: rate-limit rows from a previous test must not carry over
        conn.commit()
    yield
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        conn.execute(sql)
        conn.commit()


def test_extract_requires_auth():
    r = client.post("/extract", json={"text": "some record"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_extract_forbidden_for_auditor(tokens):
    r = client.post(
        "/extract",
        json={"text": "some record"},
        headers={"Authorization": f"Bearer {tokens['auditor']['access_token']}"},
    )
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Forbidden"


def test_extract_rate_limited(tokens):
    limit = get_settings().extract_rate_limit_per_min
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        for _ in range(limit):
            conn.execute(
                "INSERT INTO api_usage (username, route, status, model) "
                "VALUES ('operator', '/extract', 'ok', 'test-stub')"
            )
        conn.commit()
    r = client.post(
        "/extract",
        json={"text": "some record"},
        headers={"Authorization": f"Bearer {tokens['operator']['access_token']}"},
    )
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(get_settings().extract_rate_window_seconds)
    body = r.json()
    assert body["title"] == "Too Many Requests"


def test_extract_success_with_stubbed_engine(tokens, monkeypatch):
    from app.extract import gemini_client

    canned = {
        "khasra_no": "२३४",
        "owner_name": "राम प्रसाद",
        "area_raw": "२-४० bigha",
        "village": None,
        "__test": True,
    }
    monkeypatch.setattr(gemini_client, "structured_extract", lambda text, schema, **kw: canned)
    r = client.post(
        "/extract",
        json={"text": "khasra २३४ test record"},
        headers={"Authorization": f"Bearer {tokens['operator']['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["khasra_no"] == "२३४"
