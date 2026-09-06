"""Hash-chained audit log (plan §5 table 13).

`append()` runs INSIDE the decision transaction (single write path), chaining
each event to the previous row's payload_hash. `verify()` walks the whole
chain recomputing hashes — any edit, fork, or forged row is detected. Rows
cannot be updated or deleted (trigger), so the only way in is INSERT.

Concurrency: appends serialize on a transaction-scoped Postgres advisory lock
(pg_advisory_xact_lock). Without it, two simultaneous transactions could both
read the same "last" row and chain off it — a forked chain (a real bug we hit
once: two admin actions in the same second produced seq 140/141 with the same
parent). The lock is cluster-wide, so local and cloud writers serialize too.
"""
import hashlib
import json

import psycopg
from psycopg.rows import dict_row

from ..db import conninfo

# Arbitrary fixed key for pg_advisory_xact_lock — just needs to be unique
# within the database's advisory-lock namespace.
_APPEND_LOCK_KEY = 745638923111


def _canonical(entity_refs: dict) -> str:
    return json.dumps(entity_refs, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str | None, actor: str, action: str, entity_refs: dict) -> str:
    basis = f"{prev_hash or ''}|{actor}|{action}|{_canonical(entity_refs)}"
    return hashlib.sha256(basis.encode()).hexdigest()


def append(conn, actor: str, action: str, entity_refs: dict) -> int:
    """Insert one chained event using an existing transaction/conn. Returns seq."""
    # Serialize read-last-hash + insert across ALL transactions/processes.
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_APPEND_LOCK_KEY,))
    last = conn.execute(
        "SELECT payload_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = last["payload_hash"] if last else None
    row = conn.execute(
        """INSERT INTO audit_events (prev_hash, payload_hash, actor, action, entity_refs)
           VALUES (%s, %s, %s, %s, %s) RETURNING seq""",
        (prev_hash, compute_hash(prev_hash, actor, action, entity_refs),
         actor, action, json.dumps(entity_refs, default=str)),
    ).fetchone()
    return row["seq"]


def chain_head() -> dict | None:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT seq, payload_hash, created_at FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def verify() -> dict:
    """Recompute the full chain. Returns validity, length, and first bad seq."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY seq").fetchall()

    prev_hash = None
    roots = 0
    for r in rows:
        if r["prev_hash"] is None:
            roots += 1
            if roots > 1:
                return {"valid": False, "length": len(rows), "first_invalid_seq": r["seq"],
                        "reason": "forked chain: multiple roots"}
        if r["prev_hash"] != prev_hash:
            return {"valid": False, "length": len(rows), "first_invalid_seq": r["seq"],
                    "reason": "broken link to previous event"}
        expected = compute_hash(r["prev_hash"], r["actor"], r["action"], r["entity_refs"])
        if r["payload_hash"] != expected:
            return {"valid": False, "length": len(rows), "first_invalid_seq": r["seq"],
                    "reason": "payload hash mismatch (content altered or forged)"}
        prev_hash = r["payload_hash"]
    return {"valid": True, "length": len(rows), "first_invalid_seq": None}
