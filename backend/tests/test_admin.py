"""Admin governance: user management, live permissions, admin-only reopen.

Runs against the real database (same harness style as test_auth_rbac) so the
permission matrix and audit writes are exercised for real. Created users are
cleaned up best-effort (deactivated) at the end.
"""
import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from app.auth.permissions import invalidate_cache
from app.db import conninfo
from app.main import app

PASSWORDS = {
    "admin": "bhukosh-admin",
    "operator": "operator-dev",
    "checker": "checker-dev",
    "certifier": "certifier-dev",
    "auditor": "auditor-dev",
}
ADMIN = {"Authorization": ""}  # filled in fixture

client = TestClient(app)


@pytest.fixture(scope="module")
def admin_auth() -> dict:
    pair = client.post("/auth/login", json={"username": "admin", "password": PASSWORDS["admin"]}).json()
    invalidate_cache()
    return pair


def _hdr(pair: dict) -> dict:
    return {"Authorization": f"Bearer {pair['access_token']}"}


@pytest.fixture(scope="module")
def role_tokens() -> dict:
    out = {}
    for role, pw in PASSWORDS.items():
        out[role] = client.post("/auth/login", json={"username": role, "password": pw}).json()
    invalidate_cache()
    return out


# --- Permission matrix (dynamic RBAC) -------------------------------------


def test_matrix_defaults_match_legacy_rbac(admin_auth):
    """The seeded matrix must equal the pre-009 static behavior."""
    r = client.get("/admin/permissions", headers=_hdr(admin_auth))
    assert r.status_code == 200
    body = r.json()
    assert set(body["roles"]) == {"admin", "operator", "checker", "certifier", "auditor"}
    m = body["matrix"]
    assert set(m["admin"]) >= {"admin:users", "admin:permissions", "admin:override"}
    assert "records:certify" in m["certifier"] and "records:certify" not in m["checker"]
    assert "extract:run" in m["operator"] and "extract:run" not in m["auditor"]
    assert "audit:read" in m["auditor"] and "documents:write" not in m["auditor"]


def test_admin_endpoints_are_role_gated(role_tokens):
    for role, pair in role_tokens.items():
        r = client.get("/admin/users", headers=_hdr(pair))
        assert (r.status_code == 200) == (role == "admin"), (role, r.status_code)


def test_overlap_endpoint_lists_other_holders(role_tokens):
    r = client.get("/admin/permissions/extract:run/overlap?exclude_role=admin",
                   headers=_hdr(role_tokens["admin"]))
    assert r.status_code == 200
    roles = {o["role"] for o in r.json()["held_by"]}
    assert roles == {"operator", "checker", "certifier"}


# --- User management -------------------------------------------------------


@pytest.fixture(scope="module")
def created_user(admin_auth):
    username = "zz_admin_test_user"
    # pre-clean any leftover from an earlier aborted run
    try:
        with psycopg.connect(conninfo()) as conn:
            conn.execute("DELETE FROM users WHERE username = %s", (username,))
            conn.commit()
    except Exception:
        pass
    r = client.post("/admin/users", headers=_hdr(admin_auth),
                    json={"username": username, "password": "test-pass-123", "role": "checker"})
    assert r.status_code in (200, 201), r.text
    yield username, r.json()
    # cleanup: remove the row entirely so re-runs can recreate it
    try:
        with psycopg.connect(conninfo()) as conn:
            conn.execute("DELETE FROM users WHERE username = %s", (username,))
            conn.commit()
    except Exception:
        pass


def test_create_user_and_login(created_user):
    username, row = created_user
    assert row["role"] == "checker"
    r = client.post("/auth/login", json={"username": username, "password": "test-pass-123"})
    assert r.status_code == 200 and r.json()["role"] == "checker"


def test_duplicate_username_409(created_user, admin_auth):
    username, _ = created_user
    r = client.post("/admin/users", headers=_hdr(admin_auth),
                    json={"username": username, "password": "test-pass-123", "role": "checker"})
    assert r.status_code == 409


def test_reset_password_and_relogin(created_user, admin_auth):
    username, _ = created_user
    r = client.post(f"/admin/users/{username}/password", headers=_hdr(admin_auth),
                    json={"new_password": "new-pass-456"})
    assert r.status_code == 200
    assert client.post("/auth/login", json={"username": username, "password": "test-pass-123"}).status_code == 401
    ok = client.post("/auth/login", json={"username": username, "password": "new-pass-456"})
    assert ok.status_code == 200


def test_change_role_updates_token_role(created_user, admin_auth):
    username, _ = created_user
    r = client.post(f"/admin/users/{username}/role", headers=_hdr(admin_auth), json={"role": "auditor"})
    assert r.status_code == 200
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT role FROM users WHERE username = %s", (username,)).fetchone()
    assert row["role"] == "auditor"


def test_admin_cannot_demote_self(admin_auth):
    r = client.post("/admin/users/admin/role", headers=_hdr(admin_auth), json={"role": "checker"})
    assert r.status_code == 422


def test_deactivate_blocks_login(admin_auth):
    username = "zz_admin_deac_user"
    client.post("/admin/users", headers=_hdr(admin_auth),
                json={"username": username, "password": "test-pass-123", "role": "operator"})
    r = client.post(f"/admin/users/{username}/active", headers=_hdr(admin_auth), json={"is_active": False})
    assert r.status_code == 200
    assert client.post("/auth/login", json={"username": username, "password": "test-pass-123"}).status_code == 401


# --- Permission toggle enforcement (live) ----------------------------------


def test_permission_toggle_blocks_and_restores(admin_auth):
    """Revoke exports:create from operator → 403; restore → 200. Live matrix."""
    with psycopg.connect(conninfo(), row_factory=psycopg.rows.dict_row) as conn:
        op_pw = "operator-dev"
    op = client.post("/auth/login", json={"username": "operator", "password": op_pw}).json()
    invalidate_cache()

    # find a record to export (any record works; 403 must precede 404)
    rec = client.get("/records?limit=1", headers=_hdr(op)).json()
    rec_id = rec[0]["id"] if rec else 0

    r = client.post("/admin/permissions/operator/exports:create", headers=_hdr(admin_auth),
                    json={"allowed": False})
    assert r.status_code == 200
    invalidate_cache()
    blocked = client.post(f"/records/{rec_id}/exports", headers=_hdr(op), json={"format": "json"})
    assert blocked.status_code == 403, blocked.text

    r2 = client.post("/admin/permissions/operator/exports:create", headers=_hdr(admin_auth),
                     json={"allowed": True})
    assert r2.status_code == 200
    invalidate_cache()
    restored = client.post(f"/records/{rec_id}/exports", headers=_hdr(op), json={"format": "json"})
    assert restored.status_code in (200, 201, 404), restored.text  # 404 only if no records exist

    # admin revoking from admin must fail (last-resort authority)
    r3 = client.post("/admin/permissions/admin/exports:create", headers=_hdr(admin_auth),
                     json={"allowed": False})
    assert r3.status_code == 422


# --- Admin-only reopen of finalized records --------------------------------


def test_admin_only_reopen_finalized(admin_auth):
    """VERIFIED/OFFICER_CERTIFIED reopen → checker 403, admin 200."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            """SELECT r.id, r.record_version, r.claim_owner FROM land_records r
               WHERE r.current_state IN ('VERIFIED','OFFICER_CERTIFIED') ORDER BY r.id DESC LIMIT 1"""
        ).fetchone()
    if not row:
        pytest.skip("no finalized record available to test reopen against")

    checker = client.post("/auth/login", json={"username": "checker", "password": PASSWORDS["checker"]}).json()
    body = {"decision_type": "REOPEN", "expected_version": row["record_version"],
            "reason": "governance test reopen"}
    if row["claim_owner"] not in (None, "checker", "admin"):
        body["expected_version"] = row["record_version"]  # claim check will 423 both roles equally

    r_checker = client.post(f"/records/{row['id']}/decisions", headers=_hdr(checker), json=body)
    r_admin = client.post(f"/records/{row['id']}/decisions", headers=_hdr(admin_auth), json=body)

    if row["claim_owner"] not in (None, "checker", "admin"):
        # someone holds the claim: both should be blocked by 423, not by RBAC
        assert r_checker.status_code == 423 and r_admin.status_code == 423
        return
    # checker must be forbidden BEFORE the state check (role gate in write path)
    assert r_checker.status_code == 403, r_checker.text
    assert r_admin.status_code == 200, r_admin.text
    # restore state: admin reopens, so bring it back via APPROVE if it landed at REVIEW_REQUIRED
    state = r_admin.json()["record"]["current_state"]
    if state == "REVIEW_REQUIRED":
        client.post(f"/records/{row['id']}/decisions", headers=_hdr(admin_auth),
                    json={"decision_type": "APPROVE",
                          "expected_version": r_admin.json()["record"]["record_version"]})


def test_rejected_reopen_still_allows_checker():
    """REJECTED records remain reopenable by deciding roles (not admin-only)."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            """SELECT id, record_version, claim_owner FROM land_records
               WHERE current_state = 'REJECTED' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if not row:
        pytest.skip("no rejected record available")
    checker = client.post("/auth/login", json={"username": "checker", "password": PASSWORDS["checker"]}).json()
    r = client.post(f"/records/{row['id']}/decisions", headers=_hdr(checker),
                    json={"decision_type": "REOPEN", "expected_version": row["record_version"],
                          "reason": "rejected-reopen test"})
    if row["claim_owner"] not in (None, "checker"):
        assert r.status_code == 423  # claim block, not RBAC
    else:
        assert r.status_code == 200, r.text
        # restore: leave it back at REJECTED for determinism of other tests
        rj = client.post(f"/records/{row['id']}/decisions", headers=_hdr(checker),
                         json={"decision_type": "REJECT",
                               "expected_version": r.json()["record"]["record_version"],
                               "reason": "restore after reopen test"})
        assert rj.status_code == 200, rj.text


# --- Audit coverage of admin actions ----------------------------------------


def test_admin_actions_are_audited(admin_auth):
    r = client.post("/auth/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    assert r.status_code == 200
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT action FROM audit_events WHERE actor = 'admin' AND action LIKE 'admin.%' ORDER BY seq DESC LIMIT 5"
        ).fetchall()
    assert rows, "no admin.* audit events found"
