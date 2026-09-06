"""Object storage — local content-addressed files OR Supabase Storage.

storage_uri schemes:
  `file:<relative path under DATA_DIR>`      — local dev / offline demo
  `supabase:<bucket object relative path>`   — hosted (Vercel/any serverless)

Everything is keyed by SHA-256, so re-saving identical bytes is a no-op in
both backends and orphan objects are harmless. Backend selection:

  STORAGE_BACKEND=file|supabase|auto   (default auto)
  auto -> supabase when SUPABASE_URL + SUPABASE_SERVICE_KEY are set, else file

The Supabase client is a deliberate zero-dependency stdlib (urllib) client:
the service-role key is server-only and the two calls we need are PUT-grade
simple. Callers only ever see storage_uri strings.
"""
import hashlib
import urllib.error
import urllib.request
from pathlib import Path

from .config import get_settings

BUCKET = "bhukosh"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- local ----

def _root() -> Path:
    p = Path(get_settings().data_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_local(data: bytes, rel: str) -> str:
    target = _root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return f"file:{rel}"


def _open_local(rel: str) -> bytes:
    root = _root()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise ValueError("storage uri escapes data dir")
    return path.read_bytes()


# ------------------------------------------------------------ supabase ----

def _supabase_conf() -> tuple[str, str] | None:
    s = get_settings()
    if s.supabase_url and s.supabase_service_key:
        return s.supabase_url.rstrip("/"), s.supabase_service_key
    return None


def _backend() -> str:
    explicit = get_settings().storage_backend.strip().lower()
    if explicit in ("file", "supabase"):
        return explicit
    return "supabase" if _supabase_conf() else "file"


def _sb_request(method: str, url: str, key: str, data: bytes | None = None,
                headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _save_supabase(data: bytes, rel: str) -> str:
    base, key = _supabase_conf()  # caller guarantees non-None
    url = f"{base}/storage/v1/object/{BUCKET}/{rel}"
    try:
        _sb_request("POST", url, key, data=data, headers={
            "Content-Type": "application/octet-stream",
            "x-upsert": "true",  # content-addressed: identical bytes -> no-op
        })
    except urllib.error.HTTPError as e:
        if e.code not in (200, 201, 409):  # 409 tolerated on upsert races
            raise RuntimeError(f"storage upload failed: HTTP {e.code}") from e
    return f"supabase:{rel}"


def _open_supabase(rel: str) -> bytes:
    conf = _supabase_conf()
    if conf is None:
        raise RuntimeError("supabase: storage uri but SUPABASE_URL/SERVICE_KEY not configured")
    base, key = conf
    return _sb_request("GET", f"{base}/storage/v1/object/authenticated/{BUCKET}/{rel}", key)


# ---------------------------------------------------------------- public ----

def _rel_for(digest: str) -> str:
    return f"documents/{digest[:2]}/{digest}"


def save_document(data: bytes, digest: str) -> str:
    """Store bytes under documents/<aa>/<sha256>; returns storage_uri."""
    rel = _rel_for(digest)
    if _backend() == "supabase":
        return _save_supabase(data, rel)
    return _save_local(data, rel)


def open_uri(storage_uri: str) -> bytes:
    """Read back a storage_uri produced by save_document (traversal-guarded)."""
    scheme, _, rest = storage_uri.partition(":")
    if scheme == "supabase":
        if ".." in rest.split("/"):
            raise ValueError("storage uri escapes bucket")
        return _open_supabase(rest)
    if scheme == "file":
        return _open_local(rest)
    raise ValueError(f"unsupported storage scheme: {scheme!r}")
