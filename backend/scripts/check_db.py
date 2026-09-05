"""Verify DB connectivity:  python -m scripts.check_db

Prints which connection mode is active, the server version, and user count.
Never prints the password.
"""
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.db import conninfo  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    s = get_settings()
    parts = conninfo_to_dict(conninfo())
    target = f"{parts.get('host')}:{parts.get('port')}/{parts.get('dbname')} as {parts.get('user')}"
    mode = "DATABASE_URL (hosted)" if s.database_url else "PG_* parts (local portable)"
    print(f"target [{mode}]: {target}")

    with psycopg.connect(conninfo(), row_factory=dict_row, connect_timeout=15) as conn:
        ver = conn.execute("SELECT version() AS v").fetchone()["v"]
        print("server:", ver.split(",")[0])
        try:
            n = conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
            print(f"users table: {n} rows")
        except psycopg.errors.UndefinedTable:
            print("users table: not migrated yet (run python -m scripts.db_migrate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
