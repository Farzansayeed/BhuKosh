# BhuKosh — SIH26018

Intelligent Land Record Digitization & Validation System (Smart India Hackathon 2026, Ministry of Rural Development).

An evidence-and-validation layer for India's digitized land-record ecosystem:
**SOURCE → EVIDENCE → LAYOUT → EXTRACTION → REASONING → VERIFICATION → PROVENANCE → TRUSTED OUTPUT**

**Live:** app → https://bhukosh.vercel.app · API → https://bhukosh-api.vercel.app

**Capabilities:** 12-field multilingual extraction (any Indic script, vision + text paths) with per-field
confidence scoring; scanned-PDF support; per-field evidence crops (see the exact pixels each value was read
from); five deterministic validation rules + cross-record anomaly detection; full claim/approve/reject/certify
workflow with hash-chained audit trail; CSV/JSON exports with evidence manifests; role-based dashboards
(PS #16: records processed, accuracy proxy, pending review, anomalies, district/village progress).

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

Demo data (8 records across pipeline states, incl. an area-jump anomaly pair — works
against local or the production API): `cd backend && .venv/Scripts/python -m scripts.seed_demo [BASE_URL]`

## AI extraction (prototype slice)

`POST /extract` — JWT-authenticated (roles: operator/checker/certifier/admin). Send `{"text": "<register line>"}`;
returns schema-locked JSON (`khasra_no`, `owner_name`, `area_raw`, `village`) with Devanagari preserved.
The **Gemini key lives only on the server** — clients never see it. Per-user sliding-window rate limit
(default 5/min, tunable via `EXTRACT_RATE_LIMIT_*` in `.env`), usage logged to `api_usage`.

Health checks: `python -m scripts.check_db` · `python -m scripts.check_gemini` · engine probe: `python -m scripts.probe_extract`

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
- [x] EXTRACTION (prototype slice) — /extract with server-held Gemini key, per-user rate limit, usage log
- [x] CUSTODY + EVIDENCE — intake manifests, documents (SHA-256 dedup 409), pages, evidence_crops, content-addressed storage, manifest verification
- [x] PROCESSING — evidence-bound extraction runs (processing_runs, candidates), raw-output preservation, shared rate-limit bucket; **vision extraction** (`POST /pages/{id}/extract-image`): the stored scan itself is the engine input (any Indic script — Gujarati VF-6 verified live), input hash = SHA-256 of the page bytes
- [x] PROJECTIONS + DECISIONS — land_records/field_values state machine, apply_decision() single write path, claim lock (423) + optimistic versioning (409), human_decisions append-only
- [x] VALIDATION — versioned deterministic rule registry (UP-profile v1), validation_results + anomalies, cross-document area-jump join, audited anomaly resolution, system auto-resolution on re-validation
- [x] AUDIT — hash-chained audit_events (trigger-enforced append-only), emitted in the decision transaction, /audit/events + chain-head + verify
- [x] EXPORTS — immutable JSON/CSV snapshots with evidence manifest (document hashes, rulebook version, decision trail), trigger-enforced
- [x] DATA MODEL COMPLETE — all 14 core tables of plan §5
- [x] EVIDENCE REPLAY + LAYOUT — GET /fields/{id}/replay returns the full provenance chain (candidate → run → document → page) **including the visual evidence crop**: vision runs return per-field bounding boxes, the exact pixels are cut from the scan (content-addressed), and the UI renders them in the Evidence dialog
- [x] VISION UI — Upload page has a vision mode (scan in, fields + crops out, any Indic script); Evidence dialog shows the crop image for every vision-extracted value
- [x] WEB UI — React/Vite workspace: login (RBAC-aware), records list, record view with per-field evidence chain + corrections + decisions, upload-to-record pipeline, audit viewer, JSON/CSV export download (Hindi/English labels)
- [ ] REMAINING: review-queue ranking, evaluation harness, demo kit (seed data, offline mode, PWA install, video)

## Deployment (Vercel + Supabase)

Two Vercel projects, one shared Supabase backend:

- **API** — https://bhukosh-api.vercel.app (`backend/` as a Python serverless function;
  entrypoint `backend/api/index.py`, deps from `backend/requirements.txt`)
- **App** — https://bhukosh.vercel.app (Vite build; `/api/*` is rewritten server-side to the
  API URL, so the browser stays same-origin and no CORS is needed)

Both projects watch this repo (Root Directory: `backend` / `frontend` respectively), so a
push to `master` builds and deploys each side automatically.

Server-side env vars (set via `vercel env add`, stored as secrets): `DATABASE_URL` (Supabase
transaction pooler, port 6543), `JWT_SECRET`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY`, `ENV=production`. Serverless has no persistent disk, so document bytes
live in a **private Supabase Storage bucket** (`bhukosh`) — `app/storage.py` picks the backend
automatically (`STORAGE_BACKEND=auto`: Supabase when `SUPABASE_URL`+`SUPABASE_SERVICE_KEY` are
set, local `data/` files otherwise, which keeps the offline demo path intact).

Pushing to `master` redeploys both projects (GitHub integration).

## Frontend (dev)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 — proxies /api to the backend on :8000
npm run build      # production bundle in dist/
```

Login with any seeded dev user. The UI adapts to the JWT's role: checkers see claim/release/
reject/correct, certifiers additionally see Certify, auditors/admins see the Audit page.
