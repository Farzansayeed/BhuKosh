"""Seed dev users (idempotent):  python -m scripts.seed_users"""
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.auth.security import hash_password  # noqa: E402
from app.db import conninfo  # noqa: E402

# (username, password, role) — dev only, never use these passwords anywhere real.
DEV_USERS = [
    ("admin", "bhukosh-admin", "admin"),
    ("operator", "operator-dev", "operator"),
    ("checker", "checker-dev", "checker"),
    ("certifier", "certifier-dev", "certifier"),
    ("auditor", "auditor-dev", "auditor"),
]


def main() -> None:
    with psycopg.connect(conninfo()) as conn:
        for username, password, role in DEV_USERS:
            conn.execute(
                """INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)
                   ON CONFLICT (username) DO UPDATE
                     SET password_hash = EXCLUDED.password_hash,
                         role = EXCLUDED.role,
                         is_active = TRUE""",
                (username, hash_password(password), role),
            )
        conn.commit()
    print(f"seeded {len(DEV_USERS)} dev users")


if __name__ == "__main__":
    main()
