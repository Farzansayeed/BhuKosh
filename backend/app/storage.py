"""Object storage for the CUSTODY stage — local, content-addressed, zero deps.

storage_uri scheme: `file:<relative path under DATA_DIR>`. Everything is keyed
by SHA-256, so re-saving identical bytes is a no-op and orphan files are
harmless. A hosted backend (Supabase Storage) is a config swap later —
callers only ever see storage_uri strings.
"""
import hashlib
from pathlib import Path

from .config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root() -> Path:
    p = Path(get_settings().data_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_document(data: bytes, digest: str) -> str:
    """Store bytes under documents/<aa>/<sha256>; returns storage_uri."""
    root = _root()
    target = root / "documents" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return f"file:{target.relative_to(root).as_posix()}"


def open_uri(storage_uri: str) -> bytes:
    """Read back a storage_uri produced by save_document (traversal-guarded)."""
    if not storage_uri.startswith("file:"):
        raise ValueError(f"unsupported storage scheme: {storage_uri.split(':', 1)[0]!r}")
    root = _root()
    path = (root / storage_uri.removeprefix("file:")).resolve()
    if not path.is_relative_to(root):
        raise ValueError("storage uri escapes data dir")
    return path.read_bytes()
