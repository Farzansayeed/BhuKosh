import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from pydantic import BaseModel

from ..db import conninfo, get_connection
from ..errors import Problem
from . import security
from .dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str


def _issue_pair(username: str, role: str) -> TokenPair:
    return TokenPair(
        access_token=security.create_access_token(username, role),
        refresh_token=security.create_refresh_token(username, role),
        role=role,
    )


def _fetch_user(username: str) -> dict | None:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT id, username, role, password_hash, is_active FROM users WHERE username = %s",
            (username,),
        ).fetchone()


@router.post("/login", response_model=TokenPair)
def login(body: LoginIn) -> TokenPair:
    row = _fetch_user(body.username)
    if not row or not row["is_active"] or not security.verify_password(body.password, row["password_hash"]):
        raise Problem(401, "Unauthorized", "Invalid username or password.")
    return _issue_pair(row["username"], row["role"])


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshIn) -> TokenPair:
    try:
        payload = security.decode_token(body.refresh_token)
    except ValueError as e:
        raise Problem(401, "Unauthorized", f"Invalid refresh token: {e}") from e
    if payload.get("typ") != "refresh":
        raise Problem(401, "Unauthorized", "Not a refresh token.")
    row = _fetch_user(payload["sub"])
    if not row or not row["is_active"]:
        raise Problem(401, "Unauthorized", "Unknown or inactive user.")
    return _issue_pair(row["username"], row["role"])


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"]}
