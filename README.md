# BhuKosh — SIH26018

Intelligent Land Record Digitization & Validation System (Smart India Hackathon 2026, Ministry of Rural Development).

An evidence-and-validation layer for India's digitized land-record ecosystem:
**SOURCE → EVIDENCE → EXTRACTION → REASONING → VERIFICATION → PROVENANCE → TRUSTED OUTPUT**

Full architecture: see the master plan (v4.1, frozen).

## Quickstart (dev)

```bash
# 1. Backend deps
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Git Bash: .venv/Scripts/pip install -r requirements.txt

# 2. Local Postgres (portable, already initialized under infra/pgdata)
scripts/pg_start.bat          # or: infra/pg16/pgsql/bin/pg_ctl -D infra/pgdata -l logs/pg.log start

# 3. Migrations + seed dev users
cd backend && .venv/Scripts/python -m scripts.db_migrate && .venv/Scripts/python -m scripts.seed_users

# 4. Run API
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Dev users (seeded by `scripts/seed_users.py`): `admin/bhukosh-admin`, `operator/operator-dev`,
`checker/checker-dev`, `certifier/certifier-dev`, `auditor/auditor-dev`.

Tests: `cd backend && .venv/Scripts/python -m pytest -q`

## Database: local (offline) vs Supabase (shared dev)

The app picks its database from `.env`:

- **`DATABASE_URL` set** → hosted Postgres (Supabase). Use the **transaction pooler** URI
  (port `6543` on the pooler host) and keep `sslmode=require` (added automatically if missing).
  Connect as a dedicated app role (e.g. `bhukosh_app.<project-ref>`), not the owner `postgres` role.
- **`DATABASE_URL` empty** → the local portable cluster on `127.0.0.1:5432` via `PG_*` vars
  (the offline demo path — plan §15).

Helper: `python -m scripts.check_db` prints the active mode, target, server version and user count
(never the password). Same migrations and seed scripts run against either backend.

`.env.example` documents every key; copy it to `.env` and fill in your own values. `.env` is
gitignored — never commit real credentials.

## Status

- [x] FOUNDATION — repo, portable Postgres, config, auth (JWT + RBAC), health, authz tests
- [ ] DATA MODEL — 14-table migrations
- [ ] CUSTODY → EVIDENCE → PROCESSING → EXTRACTION → VALIDATION → REVIEW → REASONING → AUDIT → EXPORT → EVALUATION → DEMO
