"""Repair a forked/broken audit chain linkage.

Background: before the advisory-lock fix in app/audit/service.py, two
simultaneous appends could both read the same "last" row and chain off it,
forking the chain (observed live: seq 140/141 stamped the same second). The
events themselves are genuine — only their linkage is wrong.

This script re-links every event from the first invalid seq onward:
prev_hash[i] = payload_hash[i-1], payload_hash[i] recomputed over the
UNCHANGED payload (actor/action/entity_refs). Event content is never altered,
so this is a linkage repair, not a rewrite of history. The repair itself is
audited: an audit.chain_repair event is appended, and the append-only trigger
is re-enabled immediately after.

Run from the repo root:  backend/.venv/Scripts/python backend/scripts/repair_audit_chain.py
Requires connecting as the table OWNER (Supabase DSN works) so the trigger can
be temporarily dropped.
"""
import hashlib
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.audit.service import _canonical, chain_head, verify  # noqa: E402
from app.db import conninfo  # noqa: E402


def recompute(basis: str) -> str:
    return hashlib.sha256(basis.encode()).hexdigest()


def main() -> None:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(745638923112)")  # distinct from append lock
        rows = conn.execute("SELECT * FROM audit_events ORDER BY seq").fetchall()

        # find first broken seq (walk the chain exactly like verify())
        prev_hash = None
        first_bad = None
        for r in rows:
            if r["prev_hash"] != prev_hash:
                first_bad = r["seq"]
                break
            prev_hash = r["payload_hash"]

        if first_bad is None:
            print("chain is intact — nothing to repair")
            return

        fixed = 0
        prev = next((r["payload_hash"] for r in rows if r["seq"] == first_bad - 1), None) \
            if first_bad > 1 else None
        if first_bad == 1:
            prev = None

        conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete")
        try:
            for r in rows:
                if r["seq"] < first_bad:
                    continue
                new_payload = recompute(
                    f"{prev or ''}|{r['actor']}|{r['action']}|{_canonical(r['entity_refs'])}"
                )
                conn.execute(
                    "UPDATE audit_events SET prev_hash = %s, payload_hash = %s WHERE seq = %s",
                    (prev, new_payload, r["seq"]),
                )
                prev = new_payload
                fixed += 1
        finally:
            conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update_delete")

        # audit the repair itself (uses the fixed append path, inside this txn)
        conn.execute("SELECT pg_advisory_xact_lock(745638923111)")
        last = conn.execute(
            "SELECT payload_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        refs = {"first_invalid_seq": first_bad, "events_relinked": fixed, "tool": "repair_audit_chain.py"}
        basis = f"{last['payload_hash'] if last else ''}|admin|audit.chain_repair|{_canonical(refs)}"
        conn.execute(
            """INSERT INTO audit_events (prev_hash, payload_hash, actor, action, entity_refs)
               VALUES (%s, %s, %s, %s, %s)""",
            (last["payload_hash"] if last else None, hashlib.sha256(basis.encode()).hexdigest(),
             "admin", "audit.chain_repair", json.dumps(refs)),
        )
        conn.commit()

        rows2 = conn.execute("SELECT count(*) AS n FROM audit_events").fetchone()
    print(f"relinked {fixed} events from seq {first_bad} onward; chain now has {rows2['n']} events")
    print("verify:", verify())


if __name__ == "__main__":
    main()
