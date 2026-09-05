import psycopg
from psycopg.rows import dict_row

from .config import get_settings


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
