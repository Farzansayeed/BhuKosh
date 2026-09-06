import psycopg
from psycopg.rows import dict_row

from .config import get_settings

# Supabase routes every connection through PgBouncer in transaction-pooling
# mode, which cannot preserve server-side prepared statements across
# transactions. psycopg3 auto-prepares any query executed 5+ times on one
# connection (our per-field candidate inserts cross that threshold), and the
# pooled server then fails with DuplicatePreparedStatement. The documented fix
# is to disable auto-prepare for pooled connections. Applied globally so every
# call site (routers, services, scripts, tests) is covered.
_psycopg_connect = psycopg.connect


def _connect_pooler_safe(*args, **kwargs):
    conn = _psycopg_connect(*args, **kwargs)
    conn.prepare_threshold = None
    return conn


psycopg.connect = _connect_pooler_safe


def conninfo() -> str:
    s = get_settings()
    if s.database_url:
        url = s.database_url
        if "sslmode=" not in url:  # hosted Postgres (e.g. Supabase) requires TLS
            url += ("&" if "?" in url else "?") + "sslmode=require"
        return url
    info = (
        f"host={s.pg_host} port={s.pg_port} dbname={s.pg_database} "
        f"user={s.pg_user} password={s.pg_password}"
    )
    if s.pg_host not in ("127.0.0.1", "localhost"):
        info += " sslmode=require"
    return info


def get_connection():
    """FastAPI dependency: one dict-row connection per request."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        yield conn


# --- Warm connection reuse (hot paths) --------------------------------------
#
# Every psycopg.connect() to the hosted pooler costs a fresh DNS + TLS + auth
# round-trip (~0.5-2 s). For per-request hot paths (auth on EVERY endpoint,
# records search) that dwarfs the actual query time. Here we keep ONE warm,
# autocommit, dict-row connection per worker thread: ping, reuse, reconnect on
# failure. Read-only use only — write paths keep their own transactional
# `with psycopg.connect(...)` blocks.
import threading
import time  # noqa: E402

_tls = threading.local()
_PING_AFTER_IDLE_S = 30.0


def get_conn():
    """Thread-local warm connection in autocommit mode (SELECT-only paths).

    Reused as-is; a liveness ping runs only after 30s idle, and a failed
    first query is the caller's signal to retry once on a fresh connection
    (get_conn(retry=True) helper below)."""
    conn = getattr(_tls, "conn", None)
    now = time.monotonic()
    if conn is not None and not conn.closed:
        if now - getattr(_tls, "last_used", 0) < _PING_AFTER_IDLE_S:
            _tls.last_used = now
            return conn
        try:  # idle long enough to warrant a cheap ping
            conn.execute("SELECT 1")
            _tls.last_used = now
            return conn
        except Exception:  # noqa: BLE001 — stale: rebuild
            try:
                conn.close()
            except Exception:
                pass
    conn = psycopg.connect(conninfo(), row_factory=dict_row)
    conn.autocommit = True
    conn.prepare_threshold = None  # PgBouncer transaction-pooling safe
    _tls.conn = conn
    _tls.last_used = now
    return conn


def reset_conn():
    """Drop the thread's warm connection (call once after a query failure)."""
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _tls.conn = None
