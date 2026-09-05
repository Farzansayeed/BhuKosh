-- 003_custody.sql — CUSTODY + EVIDENCE spine (tables 2–5 of 14, plan §5)
-- Immutable by convention (INSERT-only code paths); audit_events (table 13) gets
-- the enforced REVOKE + trigger treatment later.

CREATE TABLE IF NOT EXISTS intake_manifests (
    id              BIGSERIAL PRIMARY KEY,
    register_ref    TEXT        NOT NULL,
    centre          TEXT        NOT NULL,
    operator        TEXT        NOT NULL,
    device          TEXT        NOT NULL,
    expected_count  INTEGER     NOT NULL CHECK (expected_count > 0),
    page_hashes     TEXT[]      NOT NULL DEFAULT '{}',
    manifest_hash   TEXT        NOT NULL UNIQUE,   -- hash-of-hashes over canonical manifest JSON
    signature       TEXT,
    verified_result TEXT        CHECK (verified_result IN ('MATCH', 'MISMATCH')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id                BIGSERIAL PRIMARY KEY,
    sha256            TEXT        NOT NULL UNIQUE,  -- byte-identical re-upload = 409 (plan §6)
    original_filename TEXT        NOT NULL,
    mime              TEXT        NOT NULL,
    size_bytes        BIGINT      NOT NULL CHECK (size_bytes >= 0),
    storage_uri       TEXT        NOT NULL,
    manifest_id       BIGINT      NOT NULL REFERENCES intake_manifests(id),
    superseded_by     BIGINT      REFERENCES documents(id),  -- rescan workflow (later stage)
    uploaded_by       TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pages (
    id               BIGSERIAL PRIMARY KEY,
    document_id      BIGINT  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq_no           INTEGER NOT NULL CHECK (seq_no >= 1),
    sha256           TEXT,                 -- of the page image when registered with content hash
    phash            TEXT,                 -- perceptual hash (near-dup warnings, PROCESSING stage)
    width            INTEGER,
    height           INTEGER,
    preprocessed_uri TEXT,                 -- filled by PROCESSING stage
    grid_layout      JSONB,                -- filled by PROCESSING stage
    status           TEXT   NOT NULL DEFAULT 'REGISTERED',
    UNIQUE (document_id, seq_no)
);

CREATE TABLE IF NOT EXISTS evidence_crops (
    id          BIGSERIAL PRIMARY KEY,
    page_id     BIGINT    NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    bbox        INTEGER[] NOT NULL,       -- [x1, y1, x2, y2]
    crop_hash   TEXT      NOT NULL,
    storage_uri TEXT      NOT NULL,
    UNIQUE (page_id, crop_hash)
);

CREATE INDEX IF NOT EXISTS idx_pages_doc_seq     ON pages (document_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_documents_manifest ON documents (manifest_id);
