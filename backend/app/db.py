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
