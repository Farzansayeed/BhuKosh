import base64
import hashlib
import hmac
import json
import secrets
import time

from ..config import get_settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- Password hashing: stdlib scrypt (zero dependencies, salted, constant-time compare) ---

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64url(salt)}${_b64url(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(
            password.encode(), salt=_b64url_decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(_b64url_decode(dk))
        )
        return hmac.compare_digest(calc, _b64url_decode(dk))
    except Exception:
        return False


# --- JWT (HS256, minimal, stdlib-only) ---


def _encode_token(payload: dict) -> str:
    s = get_settings()
    header = _b64url(json.dumps({"alg": s.jwt_alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(s.jwt_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


def create_access_token(sub: str, role: str) -> str:
    now = int(time.time())
    s = get_settings()
    return _encode_token({"sub": sub, "role": role, "typ": "access", "iat": now, "exp": now + s.access_token_minutes * 60})


def create_refresh_token(sub: str, role: str) -> str:
    now = int(time.time())
    s = get_settings()
    return _encode_token({"sub": sub, "role": role, "typ": "refresh", "iat": now, "exp": now + s.refresh_token_days * 86400})


def decode_token(token: str) -> dict:
    """Raise ValueError on any invalid/expired token."""
    s = get_settings()
    try:
        header_b64, body_b64, sig_b64 = token.split(".")
    except ValueError as e:
        raise ValueError("malformed token") from e
    expected = hmac.new(s.jwt_secret.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(expected), sig_b64):
        raise ValueError("bad signature")
    header = json.loads(_b64url_decode(header_b64))
    if header.get("alg") != s.jwt_alg:
        raise ValueError("bad algorithm")
    payload = json.loads(_b64url_decode(body_b64))
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise ValueError("token expired")
    return payload
