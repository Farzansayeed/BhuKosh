# BhuKosh — SIH26018

Intelligent Land Record Digitization & Validation System (Smart India Hackathon 2026, Ministry of Rural Development).

BhuKosh turns scans of handwritten land registers — in any Indian script — into validated digital records.
Every value the AI extracts stays bound to its evidence: click any field to see the exact pixels it was read
from, the engine run that produced it, and the human decisions behind it, all on a tamper-evident audit chain.
Business rules catch impossible data — area jumps, missing owners, unreadable fields — before a human ever
reviews, and every human correction teaches the extractor not to repeat the mistake. To see it in 60 seconds:
log in at the app link below as `checker` / `checker-dev`, open a pending record, and click **Evidence**.

**Live:** app → https://bhukosh.vercel.app · API → https://bhukosh-api.vercel.app · API docs (OpenAPI) → https://bhukosh-api.vercel.app/docs

---

## Why BhuKosh is different

Most digitization demos stop at "the AI read the text." BhuKosh is built for the part governments actually
care about: **can you trust it, and can you prove it?**

1. **Evidence-bound AI.** An extraction run's input hash is the SHA-256 of the exact bytes sent to the model.
   Replay any value: candidate → run (engine, prompt version, input hash) → document/page (hashes, sequence)
   → and for vision runs, the cropped pixels the value was read from.
2. **Validation before humans.** Five deterministic, versioned business rules run on every record; only
   anomalies (with plain-language explanations) reach a reviewer. Uncertain fields (engine-reported confidence
   < 0.6, or unreadable) route the record to review automatically — they never enter the database silently.
3. **Tamper-evident provenance.** The audit trail is a hash chain enforced by database triggers — rows cannot
   be edited or deleted, and `/audit/verify` re-checks the whole chain. Exports are immutable snapshots with
   an evidence manifest (document hashes, rulebook version, full decision trail).
4. **A real workflow, not a form.** Claim locks (HTTP 423), optimistic versioning (HTTP 409), mandatory
   reasons for rejections/corrections/reopens, and one audited write path for every human decision.
5. **It learns from its reviewers.** Human corrections are injected as few-shot guidance into future
   extraction prompts — and the loop is inspectable at `GET /learning/hints` (see the exact lessons the
   engine currently carries, and the decision trail behind each one).
6. **Multilingual from day one.** Verified live on Devanagari (UP khatauni registers) and Gujarati (VF-6
   inheritance forms); the extraction schema is script-agnostic by design.
7. **Runs anywhere.** Identical code against a local offline Postgres (courts/tahsils with no internet) or
   a cloud deployment (Supabase + Vercel) — selected by one environment variable.

## How it works — the pipeline

```mermaid
flowchart TD
    SCAN["📄 SOURCE — scanned registers<br/>handwritten · any Indic script · images & PDFs"]
    MANIFEST["📝 EVIDENCE — signed intake manifest<br/>register ref · centre · device<br/>hash-of-hashes verification"]
    STORE[("🗄️ Content-addressed storage<br/>documents + pages keyed by SHA-256<br/>byte-identical re-upload → 409")]
    LAYOUT["🔍 LAYOUT — the vision engine reads the scan<br/>per field: value · bounding box · confidence 0–1"]
    CROPS["✂️ Evidence crops<br/>the exact pixels per field,<br/>content-addressed"]
    EXTRACT["🤖 EXTRACTION — 12 fields, schema-locked JSON<br/>raw engine output preserved verbatim<br/>every run logs engine · model · prompt version · input hash"]
    RULES{"⚖️ REASONING — 5 versioned rules<br/>pattern · area format · missing identity<br/>unknown field · cross-record area jump"}
    VALIDATED["VALIDATED"]
    REVIEW["REVIEW_REQUIRED<br/>anomaly with a plain-language<br/>explanation + linked evidence"]
    HUMAN["👤 VERIFICATION — humans decide<br/>claim · correct · approve · reject<br/>reopen · certify — every step audited,<br/>versioned, reason-tracked"]
    VERIFIED["VERIFIED"]
    CERTIFIED["OFFICER_CERTIFIED"]
    OUTPUT["📦 TRUSTED OUTPUT<br/>immutable JSON/CSV exports + evidence manifests<br/>dashboards · OpenAPI for LRMS / DILRMP / GIS"]
    AUDIT[("🔗 PROVENANCE — hash-chained audit log<br/>trigger-enforced append-only<br/>verified on demand at /audit/verify")]

    SCAN --> MANIFEST --> STORE --> LAYOUT
    LAYOUT --> CROPS
    LAYOUT --> EXTRACT
    EXTRACT --> RULES
    RULES -->|"clean"| VALIDATED
    RULES -->|"anomaly"| REVIEW
    EXTRACT -->|"confidence below 0.6, or unreadable field"| REVIEW
    VALIDATED --> HUMAN
    REVIEW --> HUMAN
    HUMAN --> VERIFIED --> CERTIFIED --> OUTPUT
    HUMAN -.->|"reject → reopen"| REVIEW
    CROPS -.->|"judge sees the pixels"| HUMAN
    HUMAN -.->|"corrections become few-shot hints (learning loop)"| EXTRACT
    HUMAN -.-> AUDIT
    RULES -.-> AUDIT
    OUTPUT -.-> AUDIT

    classDef state fill:#1d4ed8,color:#fff,font-weight:bold;
    classDef store fill:#065f46,color:#fff;
    class VALIDATED,REVIEW,VERIFIED,CERTIFIED state
    class STORE,AUDIT store
```

Solid arrows are the happy path; dotted arrows are the guarantees that make it
trustworthy — evidence for the reviewer, feedback for the engine, and an
append-only audit trail around every decision. The stage-by-stage detail:

| Stage | What actually happens | Where it lives |
|---|---|---|
| SOURCE | Scanned pages enter through a **signed intake manifest** (register ref, centre, device, expected count, per-page SHA-256 hash-of-hashes verification). | `intake_manifests` |
| EVIDENCE | Documents are stored **content-addressed by SHA-256** — byte-identical re-uploads deduplicate (HTTP 409). Pages are registered with sequence + hashes. | `documents`, `pages` |
| LAYOUT | The vision engine reads the scan and returns, per field: the value, a **bounding box** (0–1000 normalized), and a **confidence score**. Each box is cut from the scan (with padding) into a content-addressed **evidence crop**. | `evidence_crops`, `candidates.crop_id` |
| EXTRACTION | 12 land-record fields extracted per page (see Features). Raw engine output is preserved verbatim in storage; the run records engine, model, prompt id + version, input hash, status, and timings. | `processing_runs`, `candidates`, `api_usage` |
| REASONING | Versioned deterministic rules validate the extraction: pattern sanity, area-format convention, missing identity, unreadable fields, and cross-record area jumps. Failures create **anomalies with plain-language explanations**. | `validation_results`, `anomalies` |
| VERIFICATION | Records project into a strict state machine. Humans claim, approve, correct, reject, reopen, and certify — every step is a versioned, reason-tracked, hash-chained decision. | `land_records`, `field_values`, `human_decisions` |
| PROVENANCE | Every decision emits an audit event in the same transaction; the chain is append-only by trigger and verifiable on demand. Exports freeze record + evidence manifest. | `audit_events`, `exports` |
| TRUSTED OUTPUT | Certified records, immutable JSON/CSV exports with evidence manifests, OpenAPI for downstream LRMS/DILRMP consumption, dashboards for oversight. | `/exports`, `/stats` |

## Features

**AI extraction (12 fields, any Indic script)**
- Core fields gate workflow routing: `khasra_no`, `owner_name`, `area_raw`, `village`
- Extended fields captured when the document carries them: `khata_no`, `survey_no`, `tehsil`, `district`,
  `land_classification`, `ownership_type`, `mutation_ref`, `registration_ref`
- Two paths: **vision** (the stored scan itself is the engine input — images and scanned PDFs) and **text**
  (pasted register lines, fast path)
- Schema-locked structured output; per-field **confidence 0–1**; core field < 0.6 or unreadable → review
- Prompt versions are a ledger: every run records the exact prompt version that produced it

**Validation & anomalies**
- `R-KHASRA-PATTERN` (warn) · `R-AREA-FORMAT` (warn, २-४० = 2.40 bigha convention) · `R-VILLAGE-MISSING` (info)
- `R-UNKNOWN-FIELD` (error, unreadable core field) · `R-AREA-JUMP` (error, cross-record area jump > 25% for
  the same village + khasra — catches misreads and flags suspicious mutations)
- Anomaly explanations are plain language with linked evidence records; open errors hold the record at review;
  stale findings auto-resolve on re-validation (audited)

**Human review workflow**
- States: `INGESTED → EXTRACTED → VALIDATED → REVIEW_REQUIRED → VERIFIED → OFFICER_CERTIFIED → ARCHIVED`,
  plus `REJECTED` / `QUARANTINED` exits and `REOPEN` back to review
- Claim/release with 15-minute locks; optimistic versioning; corrections update the field and keep the full
  before/after trail; certifier sign-off is a distinct, gated step

**Evidence & audit**
- `GET /fields/{id}/replay` — the complete chain behind any displayed value, including the visual crop
- `GET /crops/{id}/image` — the exact pixels, JWT-required
- Hash-chained audit log (trigger-enforced append-only), chain head + full-chain verification endpoints
- Learning loop: corrections → few-shot extraction guidance (`GET /learning/hints`)

**Dashboards & exports**
- `GET /stats` + Dashboard UI: records by state, accuracy proxy (1 − correction rate), pending review,
  open anomalies by rule, engine run stats (counts, avg duration), district/village progress
- Immutable JSON/CSV exports with evidence manifests; OpenAPI schema for government integrations

## Roles (RBAC)

Every endpoint is role-gated on a JWT; the UI adapts to the token's role.

| Capability | operator | checker | certifier | auditor | admin |
|---|:-:|:-:|:-:|:-:|:-:|
| Intake, upload, page registration | ✓ | ✓ | ✓ | | ✓ |
| Extraction (text + vision) | ✓ | ✓ | ✓ | | ✓ |
| Project run → record, validate | ✓ | ✓ | ✓ | | ✓ |
| Claim / release / approve / reject / correct / reopen / resolve anomalies | | ✓ | ✓ | | ✓ |
| CERTIFY (final officer sign-off) | | | ✓ | | ✓ |
| Read records, evidence, audit chain, dashboards, exports | ✓ | ✓ | ✓ | ✓ | ✓ |
| Download exports | ✓ | ✓ | ✓ | ✓ | ✓ |

Seeded dev users (demo — see caveats): `admin/bhukosh-admin`, `operator/operator-dev`,
`checker/checker-dev`, `certifier/certifier-dev`, `auditor/auditor-dev`.

## API reference

All endpoints are JWT-authenticated except `/health`. Interactive docs: `/docs` on the API.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login` · `POST /auth/refresh` · `GET /auth/me` |
| Custody (source of truth for scans) | `POST /intake` · `GET /intake/{id}` · `POST /documents` · `GET /documents/{id}` · `POST /documents/{id}/pages` · `GET /documents/{id}/content` |
| Extraction | `POST /extract` (text prototype) · `POST /documents/{id}/extract` (evidence-bound text) · `POST /pages/{id}/extract-image` (vision: images + PDFs) · `GET /extraction/runs/{id}` · `GET /documents/{id}/runs` |
| Evidence | `GET /fields/{id}/replay` · `GET /crops/{id}/image` |
| Records & workflow | `GET /records` · `GET /records/{id}` · `POST /records/from-run/{id}` · `POST /records/{id}/claim` · `POST /records/{id}/release` · `POST /records/{id}/decisions` (approve/reject/certify/reopen/correct) · `POST /records/{id}/validate` · `GET /records/{id}/validation` |
| Anomalies | `GET /anomalies` · `GET /anomalies/{id}` · `POST /anomalies/{id}/resolve` |
| Audit | `GET /audit/events` · `GET /audit/chain-head` · `GET /audit/verify` |
| Exports | `POST /records/{id}/exports` · `GET /exports` · `GET /exports/{id}/download` |
| Insights | `GET /stats` · `GET /learning/hints` |
| Ops | `GET /health` |

Extraction is rate-limited per user (sliding window, default 5/min) and every engine call is logged to
`api_usage`. The Gemini key lives only on the server — clients never see it.

## Architecture & services

```
Browser (React/Vite SPA, same-origin /api)
   │
   ├─ Vercel (frontend project, bhukosh.vercel.app)      ── rewrites /api/* ──┐
   ├─ Vercel (Python serverless, bhukosh-api.vercel.app) ◄────────────────────┘
   │     └─ FastAPI app (this repo, backend/)
   │
   ├─ Supabase Postgres (transaction pooler :6543, sslmode=require)   ← all 14 tables
   ├─ Supabase Storage (private `bhukosh` bucket)                     ← scan bytes + crops + raw outputs
   └─ Google Gemini (gemini-3.7-flash, server-held key)               ← vision + text extraction
```

- **Database (current):** Supabase Postgres in production — dedicated app role, transaction pooler, TLS.
  **Offline mode:** a portable local PostgreSQL 16 cluster (`infra/pgdata`) driven by `PG_*` vars — same
  migrations, same seed scripts, zero internet needed. `DATABASE_URL` set vs empty picks the mode.
- **Object storage:** Supabase Storage (private bucket) in production; local `data/` directory offline.
  `app/storage.py` auto-selects (`STORAGE_BACKEND=auto`). Everything is content-addressed by SHA-256.
- **AI engine:** Google Gemini, schema-locked JSON output, temperature 0. Transient 429/503s retry with
  backoff; engine failures mark the run FAILED with a sanitized error and full usage logging.
- **Data model:** 14 core tables across 8 migrations — users, api_usage, intake_manifests, documents, pages,
  evidence_crops, processing_runs, candidates, land_records, field_values, human_decisions, anomalies,
  validation_results, audit_events, exports.
- **Deployment:** push to `master` → both Vercel projects build and deploy automatically (Root Directories
  `backend/` and `frontend/`). Secrets live in Vercel's encrypted store; nothing sensitive is in the repo.

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

Tests: `cd backend && .venv/Scripts/python -m pytest -q` (stubbed engine — no API quota burned).

Demo data (8 records across pipeline states, incl. a live area-jump anomaly pair — works against local or
the production API): `cd backend && .venv/Scripts/python -m scripts.seed_demo [BASE_URL]`

Health checks: `python -m scripts.check_db` · `python -m scripts.check_gemini` · engine probe: `python -m scripts.probe_extract`

### Frontend (dev)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 — proxies /api to the backend on :8000
npm run build      # production bundle in dist/
```

## Environment

`.env.example` documents every key; copy to `.env` and fill in. `.env` is gitignored.

| Key | Purpose |
|---|---|
| `DATABASE_URL` | Supabase Postgres URI (pooler :6543). Empty → local mode via `PG_*` |
| `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` | Local Postgres connection |
| `JWT_SECRET`, `JWT_ALG`, `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS` | Auth |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | AI engine (default `gemini-3.7-flash`) |
| `EXTRACT_RATE_LIMIT_*` | Per-user extraction quota |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `STORAGE_BACKEND` | Object storage (auto by default) |
| `APP_NAME`, `ENV`, `HOST`, `PORT` | Basics |

## Project layout

```
sih26018/
├── backend/
│   ├── api/index.py            # Vercel serverless entrypoint
│   ├── app/                    # the FastAPI application
│   │   ├── auth/               #   login, refresh, RBAC dependencies
│   │   ├── custody/            #   intake manifests, documents, pages (chain of custody)
│   │   ├── processing/         #   extraction runs + vision path + evidence crops
│   │   ├── extract/            #   Gemini client, schemas, prompt ledger, rate limit
│   │   ├── records/            #   land_records state machine + decisions
│   │   ├── rules/              #   validation registry + anomaly service
│   │   ├── evidence.py         #   /fields/{id}/replay provenance chain
│   │   ├── audit/              #   hash-chained audit events + verification
│   │   ├── exports/            #   immutable JSON/CSV snapshots + manifests
│   │   ├── stats/              #   /stats dashboard aggregation
│   │   ├── learning/           #   corrections → extraction hints (PS #13)
│   │   ├── storage.py          #   Supabase Storage ↔ local disk, auto-selected
│   │   ├── config.py, db.py, errors.py
│   │   └── main.py             #   app assembly + /health
│   ├── scripts/                # db_migrate, seed_users, seed_demo, smoke_*, check_*, gen_secret
│   ├── tests/                  # pytest suite (stubbed engine — CI-safe, no quota)
│   └── requirements.txt
├── frontend/                   # React + Vite SPA
│   └── src/pages/              # Login, Dashboard, Records, RecordView, Upload, Audit
├── infra/
│   ├── migrations/             # 8 SQL migrations (the whole 14-table data model)
│   └── pgdata/                 # portable local Postgres cluster (offline mode)
└── scripts/pg_start.bat        # local PG helper
```

Every module maps to one pipeline stage — `custody` is SOURCE/EVIDENCE, `processing` is
LAYOUT/EXTRACTION, `rules` is REASONING, `records` is VERIFICATION, `audit` + `exports`
are PROVENANCE/TRUSTED OUTPUT — so the codebase reads the same way the pitch does.

## What to expect (honest caveats)

- **The seeded credentials are public** (they're in this repo on purpose, so judges can log in). Before any
  real deployment: rotate passwords, disable seed users, issue real ones.
- **Gemini quota is shared** across the free-tier key — heavy testing can hit 429s; the client retries with
  backoff and surfaces a sanitized problem if the engine is unavailable.
- **Serverless cold starts**: the first request after idle takes a few seconds.
- **Prototype-level by design:** the learning loop is prompt-injection (few-shot), not fine-tuning;
  government-system integration is via the documented API + evidence-manifested exports (no direct LRMS
  adapters exist to test against); accuracy claims are demo-verified, not benchmarked.
- Extraction quality depends on scan legibility — that's exactly what the confidence scoring + review
  routing exists for.

## Roadmap

Review-queue ranking (priority by anomaly severity + confidence) · evaluation harness with labeled
benchmark sets · PWA install for field tablets · multi-page register batch intake · direct LRMS/DILRMP +
GIS adapters · fine-tuning on accumulated correction pairs · GIS overlay for khasra maps.
