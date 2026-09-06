"""Concurrency regression: parallel appends must NOT fork the chain.

Before the advisory-lock fix, two simultaneous appends could both read the
same "last" row and chain off it — a forked chain (observed live as
seq 140/141 stamped the same second). This test fires 8 appends from 8
threads against the real database and asserts verify() still reports a
single valid chain.
"""
import threading

import psycopg
from fastapi.testclient import TestClient

from app.audit import service as audit_service
from app.db import conninfo
from app.main import app

client = TestClient(app)


def _login_admin() -> dict:
    r = client.post("/auth/login", json={"username": "admin", "password": "bhukosh-admin"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_parallel_appends_do_not_fork_chain():
    h = _login_admin()
    before = audit_service.verify()

    results = []
    errors = []

    def worker(i: int):
        try:
            with psycopg.connect(conninfo(), row_factory=psycopg.rows.dict_row) as conn:
                seq = audit_service.append(
                    conn, "admin", "test.parallel_append",
                    {"worker": i, "thread": threading.get_ident()},
                )
                conn.commit()
                results.append(seq)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"append failures: {errors}"
    assert len(results) == 8 and len(set(results)) == 8, "seqs must be unique"

    after = audit_service.verify()
    assert after["valid"] is True, f"chain forked under concurrency: {after}"
    assert after["length"] == before["length"] + 8, (before, after)
    assert after["first_invalid_seq"] is None
