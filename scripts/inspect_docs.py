"""Read-only inspection of the documents/records state behind the user's bug report."""
import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
url = os.environ["DATABASE_URL"]

with psycopg.connect(url, row_factory=dict_row) as conn:
    print("=== 20 most recent documents ===")
    for r in conn.execute(
        """SELECT id, original_filename, sha256, size_bytes, uploaded_by, created_at
           FROM documents ORDER BY id DESC LIMIT 20"""
    ).fetchall():
        print(f"#{r['id']:4} {str(r['created_at'])[:19]} {r['uploaded_by']:9} "
              f"{r['size_bytes']:>9}B  {r['original_filename'][:40]:40} {r['sha256'][:12]}")

    print("\n=== sha256 values uploaded more than once (name changed, bytes identical) ===")
    for r in conn.execute(
        """SELECT sha256, count(*) AS n, count(DISTINCT original_filename) AS names,
                  string_agg(DISTINCT original_filename, ' | ') AS files
           FROM documents GROUP BY sha256 HAVING count(*) > 1
           ORDER BY n DESC LIMIT 10"""
    ).fetchall():
        print(f"x{r['n']} as {r['names']} name(s): {r['files'][:110]}  [{r['sha256'][:12]}]")

    print("\n=== processing runs: recent statuses ===")
    for r in conn.execute(
        """SELECT id, document_id, kind, engine_name, status, started_at
           FROM processing_runs ORDER BY id DESC LIMIT 15"""
    ).fetchall():
        print(f"run #{r['id']:4} doc {r['document_id']:4} {r['kind']:12} {str(r['engine_name'])[:12]:12} "
              f"{r['status']:10} {str(r['started_at'])[:19]}")

    print("\n=== records: count + most recent ===")
    n = conn.execute("SELECT count(*) AS n FROM land_records").fetchone()["n"]
    print(f"total land_records: {n}")
    for r in conn.execute(
        """SELECT id, current_state, village, khasra_no, created_at
           FROM land_records ORDER BY id DESC LIMIT 10"""
    ).fetchall():
        print(f"rec #{r['id']:4} {r['current_state']:18} {str(r['village'])[:18]:18} "
              f"khasra {str(r['khasra_no'])[:8]:8} {str(r['created_at'])[:19]}")
