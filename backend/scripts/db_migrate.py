"""Apply SQL migrations in lexical order:  python -m scripts.db_migrate"""
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import conninfo  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "infra" / "migrations"


def main() -> None:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"= {path.name} (already applied)")
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            print(f"+ {path.name}")
        conn.commit()


if __name__ == "__main__":
    main()
