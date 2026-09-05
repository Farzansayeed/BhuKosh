import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import require_roles
from app.errors import Problem, problem_handler
from app.main import app

ALL_ROLES = ["admin", "operator", "checker", "certifier", "auditor"]
PASSWORDS = {
    "admin": "bhukosh-admin",
    "operator": "operator-dev",
    "checker": "checker-dev",
    "certifier": "certifier-dev",
    "auditor": "auditor-dev",
}

client = TestClient(app)


def _login(username: str, password: str):
    return client.post("/auth/login", json={"username": username, "password": password})


@pytest.fixture(scope="module")
def tokens() -> dict[str, dict]:
    return {role: _login(role, PASSWORDS[role]).json() for role in ALL_ROLES}


def test_login_success(tokens):
    for role, pair in tokens.items():
        assert pair["access_token"] and pair["refresh_token"]
        assert pair["token_type"] == "bearer"
        assert pair["role"] == role


def test_login_wrong_password_is_problem_json():
    r = _login("admin", "wrong-password")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Unauthorized"


def test_me_requires_token():
    assert client.get("/auth/me").status_code == 401


def test_me_with_token(tokens):
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['checker']['access_token']}"})
    assert r.status_code == 200
    assert r.json()["role"] == "checker"


def test_refresh_flow(tokens):
    r = client.post("/auth/refresh", json={"refresh_token": tokens["admin"]["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]
    # access token must not work as refresh token
    r2 = client.post("/auth/refresh", json={"refresh_token": tokens["admin"]["access_token"]})
    assert r2.status_code == 401


def test_authz_matrix(tokens):
    """Full RBAC matrix: each role passes its own gate, 403 on everyone else's."""
    probe = FastAPI()
    probe.add_exception_handler(Problem, problem_handler)
    for role in ALL_ROLES:
        probe.add_api_route(
            f"/only-{role}",
            lambda r=role: {"ok": True},
            methods=["GET"],                dependencies=[Depends(require_roles(role))],
        )
    probe_client = TestClient(probe)
    for role, pair in tokens.items():
        for gate in ALL_ROLES:
            r = probe_client.get(f"/only-{gate}", headers={"Authorization": f"Bearer {pair['access_token']}"})
            assert r.status_code == (200 if role == gate else 403), (role, gate, r.status_code, r.text)
