"""Migrate `file:` documents to Supabase Storage (one-off, idempotent).

Why: doc 145 (record 67's source scan) was uploaded while the backend ran
locally, so it was saved to local disk (`file:` scheme). The shared DB row
then became unreachable from the HOSTED backend — its data_dir doesn't have
that file — so /documents/145/content returned 410 and the Source Documents
panel showed nothing.

Fix: read the bytes locally, re-save through storage.save_document (now
resolving to supabase), verify the SHA-256 of the round-tripped bytes matches
the documents.sha256 column, then flip storage_uri in the DB. Idempotent:
re-running skips docs that no longer have `file:` URIs.

Usage:  python scripts/migrate_file_docs_to_supabase.py [--dry-run]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage  # noqa: E402
from app.db import conninfo  # noqa: E402

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        docs = conn.execute(
            "SELECT id, sha256, mime, storage_uri FROM documents "
            "WHERE storage_uri LIKE 'file:%' ORDER BY id"
        ).fetchall()
        if not docs:
            print("Nothing to migrate: no `file:` documents.")
            return
        for doc in docs:
            print(f"doc {doc['id']}  {doc['storage_uri']}")
            try:
                data = storage.open_uri(doc["storage_uri"])
            except Exception as e:  # noqa: BLE001
                print(f"  SKIP — local file unreadable: {e}")
                continue
            digest = storage.sha256_bytes(data)
            if digest != doc["sha256"]:
                print(f"  SKIP — bytes don't match sha256 (local copy is corrupt/modified)")
                continue
            new_uri = storage.save_document(data, digest)  # uploads to supabase
            print(f"  -> {new_uri} ({len(data)} bytes, sha256 verified)")
            if not dry_run:
                conn.execute(
                    "UPDATE documents SET storage_uri = %s WHERE id = %s",
                    (new_uri, doc["id"]),
                )
        if not dry_run:
            conn.commit()
            print("Committed.")
        else:
            conn.rollback()
            print("Dry run — rolled back.")


if __name__ == "__main__":
    main()
