"""Admin governance API — the highest-authority surface.

Everything here requires an admin-scoped permission (admin:users,
admin:permissions, admin:override), and every action is written to the
hash-chained audit trail. The admin can:

  * list and create users, reset any password, change any role,
    deactivate/reactivate accounts              (admin:users)
  * flip any role→permission grant live and see which roles already
    hold a permission ("same as so-and-so role") (admin:permissions)
  * force-release a stuck review claim           (admin:override)
"""
import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .audit import service as audit_service
from .db import conninfo
from .errors import Problem
from .auth import security
from .auth.dependencies import get_current_user
from .auth.permissions import (
    PERMISSIONS,
    ROLE_DEFAULTS,
    invalidate_cache,
    permissions_overlap,
    require_permission,
)

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_ROLES = ("admin", "operator", "checker", "certifier", "auditor")


class CreateUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8)
    role: str


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8)


class ChangeRoleIn(BaseModel):
    role: str


class SetActiveIn(BaseModel):
    is_active: bool


class SetPermissionIn(BaseModel):
    allowed: bool


def _check_role(role: str) -> str:
    if role not in VALID_ROLES:
        raise Problem(422, "Validation Failed",
                      f"role must be one of {', '.join(VALID_ROLES)}.")
    return role


def _audit(conn, user: dict, action: str, refs: dict) -> None:
    audit_service.append(conn, user["username"], action, refs)


# --- Users ---------------------------------------------------------------


@router.get("/users")
def list_users(user: dict = Depends(require_permission("admin:users"))) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            """SELECT u.id, u.username, u.role, u.is_active, u.created_at,
                      (SELECT COUNT(*) FROM human_decisions d
                        WHERE d.actor_id = u.username) AS decisions
               FROM users u ORDER BY u.id"""
        ).fetchall()
    return {"users": [dict(r) for r in rows]}


@router.post("/users")
def create_user(body: CreateUserIn, user: dict = Depends(require_permission("admin:users"))) -> dict:
    _check_role(body.role)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        dup = conn.execute("SELECT 1 AS x FROM users WHERE username = %s", (body.username,)).fetchone()
        if dup:
            raise Problem(409, "Conflict", f"Username '{body.username}' already exists.")
        row = conn.execute(
            """INSERT INTO users (username, password_hash, role)
               VALUES (%s, %s, %s) RETURNING id, username, role, is_active, created_at""",
            (body.username, security.hash_password(body.password), body.role),
        ).fetchone()
        _audit(conn, user, "admin.user.create",
               {"target_user": body.username, "role": body.role})
        conn.commit()
    return dict(row)


@router.post("/users/{username}/password")
def reset_password(
    username: str, body: ResetPasswordIn,
    user: dict = Depends(require_permission("admin:users")),
) -> dict:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
        if not row:
            raise Problem(404, "Not Found", f"No user '{username}'.")
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE username = %s",
            (security.hash_password(body.new_password), username),
        )
        _audit(conn, user, "admin.user.reset_password", {"target_user": username})
        conn.commit()
    return {"username": username, "password_reset": True}


@router.post("/users/{username}/role")
def change_role(username: str, body: ChangeRoleIn,
                user: dict = Depends(require_permission("admin:users"))) -> dict:
    _check_role(body.role)
    if username == user["username"] and body.role != "admin":
        raise Problem(422, "Validation Failed",
                      "You cannot demote your own admin account — that would lock out user management.")
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id, role FROM users WHERE username = %s", (username,)).fetchone()
        if not row:
            raise Problem(404, "Not Found", f"No user '{username}'.")
        conn.execute("UPDATE users SET role = %s WHERE username = %s", (body.role, username))
        _audit(conn, user, "admin.user.change_role",
               {"target_user": username, "before_role": row["role"], "after_role": body.role})
        conn.commit()
    return {"username": username, "role": body.role}


@router.post("/users/{username}/active")
def set_active(username: str, body: SetActiveIn,
               user: dict = Depends(require_permission("admin:users"))) -> dict:
    if username == user["username"] and not body.is_active:
        raise Problem(422, "Validation Failed", "You cannot deactivate your own account.")
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id, is_active FROM users WHERE username = %s", (username,)).fetchone()
        if not row:
            raise Problem(404, "Not Found", f"No user '{username}'.")
        conn.execute("UPDATE users SET is_active = %s WHERE username = %s",
                     (body.is_active, username))
        _audit(conn, user, "admin.user.set_active",
               {"target_user": username, "is_active": body.is_active})
        conn.commit()
    return {"username": username, "is_active": body.is_active}


# --- Permissions ---------------------------------------------------------


@router.get("/permissions")
def get_permissions(_user: dict = Depends(get_current_user)) -> dict:
    """Catalog + effective matrix + which roles default to each permission."""
    from .auth.permissions import _load_matrix
    matrix = _load_matrix()
    return {
        "catalog": [{"permission": p, "description": d} for p, d in PERMISSIONS.items()],
        "roles": list(VALID_ROLES),
        "matrix": {r: sorted(ps) for r, ps in matrix.items()},
        "defaults": {r: sorted(ps) for r, ps in ROLE_DEFAULTS.items()},
    }


@router.get("/permissions/{permission}/overlap")
def permission_overlap(permission: str, exclude_role: str | None = None,
                       user: dict = Depends(require_permission("admin:permissions"))) -> dict:
    if permission not in PERMISSIONS:
        raise Problem(404, "Not Found", f"Unknown permission '{permission}'.")
    return {
        "permission": permission,
        "held_by": permissions_overlap(permission, exclude_role),
        "message": (
            ", ".join(o["role"] for o in permissions_overlap(permission, exclude_role))
            or "no other role holds this permission"
        ),
    }


@router.post("/permissions/{role}/{permission}")
def set_permission(role: str, permission: str, body: SetPermissionIn,
                   user: dict = Depends(require_permission("admin:permissions"))) -> dict:
    _check_role(role)
    if permission not in PERMISSIONS:
        raise Problem(404, "Not Found", f"Unknown permission '{permission}'.")
    if role == "admin" and not body.allowed:
        raise Problem(422, "Validation Failed",
                      "Admin always retains all permissions — the platform's last-resort authority.")
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        holders = conn.execute(
            "SELECT role FROM role_permissions WHERE permission = %s AND allowed AND role <> %s",
            (permission, role),
        ).fetchall()
        conn.execute(
            """INSERT INTO role_permissions (role, permission, allowed, updated_by)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (role, permission)
               DO UPDATE SET allowed = EXCLUDED.allowed,
                             updated_by = EXCLUDED.updated_by,
                             updated_at = now()""",
            (role, permission, body.allowed, user["username"]),
        )
        _audit(conn, user, "admin.permission.set",
               {"role": role, "permission": permission, "allowed": body.allowed,
                "also_held_by": [h["role"] for h in holders]})
        conn.commit()
    invalidate_cache()
    return {
        "role": role, "permission": permission, "allowed": body.allowed,
        "note": (f"Note: {' + '.join(h['role'] for h in holders)} already "
                 f"{'holds' if len(holders) == 1 else 'hold'} '{permission}' — "
                 f"this role now has the same permission as them."
                 if holders else None),
    }


# --- Overrides ------------------------------------------------------------


@router.post("/records/{record_id}/force-release")
def force_release(record_id: int, user: dict = Depends(require_permission("admin:override"))) -> dict:
    """Break any claim on a record regardless of owner — unblocks a stuck review."""
    from ..records import service as records_service
    admin = dict(user)
    admin["role"] = "admin"  # release() already permits admin; belt and braces
    try:
        records_service.release(record_id, admin)
    except Problem as e:
        if e.status == 409:
            return {"record_id": record_id, "released": True, "note": "no active claim"}
        raise
    invalidate_cache()
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        _audit(conn, user, "admin.record.force_release", {"record_id": record_id})
        conn.commit()
    return {"record_id": record_id, "released": True}
