"""Read-only: why did the recent extraction runs fail?"""
import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
url = os.environ["DATABASE_URL"]

with psycopg.connect(url, row_factory=dict_row) as conn:
    cols = [r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'processing_runs'"
    ).fetchall()]
    print("processing_runs columns:", cols)
    print()
    for r in conn.execute(
        """SELECT * FROM processing_runs WHERE status = 'FAILED' ORDER BY id DESC LIMIT 6"""
    ).fetchall():
        print(f"--- run #{r['id']} doc {r['document_id']} kind={r['kind']} engine={r['engine_name']}")
        for k, v in r.items():
            if k in ("id", "document_id", "kind", "engine_name"):
                continue
            s = str(v)
            if s and s not in ("None", ""):
                print(f"    {k}: {s[:300]}")
    print()
    # stuck runs
    for r in conn.execute(
        """SELECT id, document_id, status, started_at FROM processing_runs
           WHERE status NOT IN ('SUCCEEDED','FAILED') ORDER BY id DESC LIMIT 5"""
    ).fetchall():
        print(f"STUCK? run #{r['id']} doc {r['document_id']} status={r['status']} started {r['started_at']}")
