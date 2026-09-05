"""Per-user sliding-window rate limit, backed by the api_usage table.

Counts successful calls (status='ok') per user per route in the last window;
errors and rejections don't count, so failed calls never extend the wait.
Works identically against local Postgres and Supabase (plain SQL only).
"""
import psycopg
from psycopg.rows import dict_row

from ..config import get_settings
from ..db import conninfo
from ..errors import Problem


def enforce(username: str, *, route: str = "/extract") -> None:
    s = get_settings()
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT count(*) AS n
            FROM api_usage
            WHERE username = %s
              AND route = %s
              AND status = 'ok'
              AND created_at >= now() - make_interval(secs => %s)
            """,
            (username, route, float(s.extract_rate_window_seconds)),
        ).fetchone()
    if row["n"] >= s.extract_rate_limit_per_min:
        raise Problem(
            429,
            "Too Many Requests",
            f"Extraction limit of {s.extract_rate_limit_per_min} per "
            f"{s.extract_rate_window_seconds}s reached. Retry shortly.",
            type_="https://httpstatuses.com/429",
            retry_after_seconds=s.extract_rate_window_seconds,
        )
