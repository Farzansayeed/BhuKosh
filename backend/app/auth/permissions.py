"""Permission catalog + dynamic role→permission grants (009_admin.sql).

`require_permission()` is the live gate: it reads the effective matrix from
the role_permissions table at request time (cached briefly), falling back to
the static defaults below if the table is unavailable. The admin can flip any
grant at runtime via the admin API — no deploy needed.

`permissions_overlap()` powers the admin UI's "who else can do this" hint:
when the admin tries to grant permission X to role R, it lists the other
roles that already hold X — satisfying the requirement "if he does something
that already someone else has, show that the permission is same as so-and-so
role".
"""
from fastapi import Depends
from psycopg import connect as pg_connect
from psycopg.rows import dict_row

from ..db import conninfo
from ..errors import Problem
from .dependencies import get_current_user

# --- The permission catalog (stable slugs; the DB FK enforces membership) ---

PERMISSIONS: dict[str, str] = {
    # custody / documents
    "documents:write":    "Register documents, pages, and intake manifests",
    "documents:read":     "Browse documents and pages",
    # AI extraction
    "extract:run":        "Run AI extraction (text or vision) on documents",
    # records / workflow
    "records:project":    "Project extraction runs into land records",
    "records:claim":      "Claim/release records for review",
    "records:decide":     "Approve, reject, correct fields, resolve anomalies",
    "records:certify":    "Certify records (officer signature)",
    "records:reopen":     "Reopen VERIFIED / OFFICER_CERTIFIED / REJECTED records",
    "records:reopen_finalized": "Reopen finalized records (VERIFIED / OFFICER_CERTIFIED) — admin authority",
    # validation
    "rules:validate":     "Run the validation rule engine on records",
    # audit
    "audit:read":         "Read the audit trail and verify the chain",
    # exports
    "exports:create":     "Generate JSON/CSV exports",
    "exports:read":       "Download generated exports",
    # admin
    "admin:users":        "Create users, reset passwords, change roles",
    "admin:permissions":  "Change role permissions",
    "admin:override":     "Force-release claims and override record locks",
}

# Static defaults = the previous RBAC matrix, so behavior is unchanged on day
# one (checker/certifier keep extract:run — the legacy /extract route allowed
# operator/checker/certifier/admin). Keep in sync with 009_admin.sql.
ROLE_DEFAULTS: dict[str, set[str]] = {
    "admin":     set(PERMISSIONS),
    "operator":  {"documents:write", "documents:read", "extract:run", "records:project",
                  "rules:validate", "exports:create", "exports:read"},
    "checker":   {"documents:read", "extract:run", "records:claim", "records:decide",
                  "records:project", "records:reopen", "rules:validate", "exports:create",
                  "exports:read", "audit:read"},
    "certifier": {"documents:read", "extract:run", "records:claim", "records:decide",
                  "records:project", "records:reopen", "records:certify", "rules:validate",
                  "exports:create", "exports:read", "audit:read"},
    "auditor":   {"documents:read", "audit:read", "exports:read"},
}

_CACHE: dict = {"matrix": None, "loaded_at": 0.0}
_CACHE_TTL_SECONDS = 10.0


def _load_matrix() -> dict[str, set[str]]:
    """Effective role→permissions from the DB, TTL-cached. DB failure → defaults."""
    import time

    now = time.monotonic()
    if _CACHE["matrix"] is not None and now - _CACHE["loaded_at"] < _CACHE_TTL_SECONDS:
        return _CACHE["matrix"]
    try:
        with pg_connect(conninfo(), row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT role, permission FROM role_permissions WHERE allowed"
            ).fetchall()
        matrix: dict[str, set[str]] = {r: set() for r in ROLE_DEFAULTS}
        for row in rows:
            matrix.setdefault(row["role"], set()).add(row["permission"])
        _CACHE["matrix"] = matrix
        _CACHE["loaded_at"] = now
        return matrix
    except Exception:  # noqa: BLE001 — never lock the app out over a cache/DB hiccup
        return {r: set(p) for r, p in ROLE_DEFAULTS.items()}


def invalidate_cache() -> None:
    _CACHE["matrix"] = None


def role_can(role: str, permission: str) -> bool:
    return permission in _load_matrix().get(role, set())


def require_permission(permission: str):
    """Gate factory replacing require_roles: checks the live permission matrix."""

    def dep(user: dict = Depends(get_current_user)) -> dict:
        if not role_can(user["role"], permission):
            raise Problem(
                403, "Forbidden",
                f"Role '{user['role']}' lacks permission '{permission}'. "
                f"An admin can grant it under Admin → Permissions.",
            )
        return user

    return dep


def permissions_overlap(permission: str, exclude_role: str | None = None) -> list[dict]:
    """Which roles already hold `permission` — the admin UI's overlap hint."""
    matrix = _load_matrix()
    return sorted(
        ({"role": r, "permission": permission} for r, perms in matrix.items()
         if permission in perms and r != exclude_role),
        key=lambda d: d["role"],
    )
