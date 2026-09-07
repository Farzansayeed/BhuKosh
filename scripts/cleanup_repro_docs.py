"""Delete ONLY the REPRO test rows created while diagnosing the upload bug.

Tight predicate: documents must match repro_<X>.pdf + 618 bytes + uploaded_by
'operator'; manifests must match register_ref LIKE 'REPRO-%'. Prints everything
it deletes. Local run: set DATABASE_URL="" to target the local cluster.
"""
import os
import sys

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
url = os.environ.get("DATABASE_URL", "")
local = len(sys.argv) > 1 and sys.argv[1] == "--local"
if local:
    # Local portable cluster: PG_* settings from .env (same as app.db conninfo fallback)
    pw = os.environ.get("PG_PASSWORD", "")
    host = os.environ.get("PG_HOST", "127.0.0.1")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DATABASE", "bhukosh")
    user = os.environ.get("PG_USER", "bhukosh")
    url = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

with psycopg.connect(url, row_factory=dict_row) as conn:
    docs = conn.execute(
        """SELECT id, original_filename, size_bytes, uploaded_by, manifest_id
             FROM documents
            WHERE original_filename LIKE 'repro\\_%' ESCAPE '\\'
              AND size_bytes = 618 AND uploaded_by = 'operator'"""
    ).fetchall()
    manifests = conn.execute(
        "SELECT id, register_ref FROM intake_manifests WHERE register_ref LIKE 'REPRO-%'"
    ).fetchall()

    if not docs and not manifests:
        print("nothing to clean")
        sys.exit(0)

    print("deleting documents:")
    for d in docs:
        print(f"  doc #{d['id']} {d['original_filename']} {d['size_bytes']}B manifest={d['manifest_id']}")
    print("deleting manifests:")
    for m in manifests:
        print(f"  manifest #{m['id']} {m['register_ref']}")

    if "--dry-run" in sys.argv:
        print("dry run — nothing deleted")
        sys.exit(0)

    n_pages = conn.execute(
        "DELETE FROM pages WHERE document_id = ANY(%s) RETURNING id",
        ([d["id"] for d in docs],),
    ).rowcount
    # ids were captured by the strict guarded SELECT above — no LIKE needed here
    # (psycopg requires %% escaping for literal % in parameterized queries)
    n_docs = conn.execute(
        "DELETE FROM documents WHERE id = ANY(%s)",
        ([d["id"] for d in docs],),
    ).rowcount
    n_man = 0
    if manifests:
        n_man = conn.execute(
            "DELETE FROM intake_manifests WHERE id = ANY(%s)",
            ([m["id"] for m in manifests],),
        ).rowcount
    conn.commit()
    print(f"deleted: {n_docs} documents, {n_pages} pages, {n_man} manifests")
