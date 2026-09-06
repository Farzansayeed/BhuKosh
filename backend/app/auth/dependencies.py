from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import get_conn
from ..errors import Problem
from . import security

bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise Problem(401, "Unauthorized", "Missing bearer token.")
    try:
        payload = security.decode_token(creds.credentials)
    except ValueError as e:
        raise Problem(401, "Unauthorized", f"Invalid token: {e}") from e
    if payload.get("typ") != "access":
        raise Problem(401, "Unauthorized", "Not an access token.")
    # Warm thread-local connection: auth runs on EVERY request, so a fresh
    # TLS+auth round-trip per call is the app's biggest fixed cost.
    conn = get_conn()
    row = conn.execute(
        "SELECT id, username, role, is_active FROM users WHERE username = %s",
        (payload["sub"],),
    ).fetchone()
    if not row or not row["is_active"]:
        raise Problem(401, "Unauthorized", "Unknown or inactive user.")
    return row


def require_roles(*roles: str):
    """RBAC factory: require_roles('checker', 'admin') -> 403 unless user.role is allowed."""
    allowed = set(roles)

    def dep(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed:
            raise Problem(
                403,
                "Forbidden",
                f"Role '{user['role']}' lacks required role(s): {', '.join(sorted(allowed))}.",
            )
        return user

    return dep
