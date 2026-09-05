-- 004_processing.sql — PROCESSING stage (tables 6–7 of 14, plan §5)
-- processing_runs is event-sourced: rows are created RUNNING and closed once
-- (SUCCEEDED/FAILED); after a terminal status they are never edited again.

CREATE TABLE IF NOT EXISTS processing_runs (
    id                 BIGSERIAL PRIMARY KEY,
    document_id        BIGINT      NOT NULL REFERENCES documents(id),
    page_id            BIGINT      REFERENCES pages(id),
    kind               TEXT        NOT NULL CHECK (kind IN ('PREPROCESS','LAYOUT','EXTRACT','VALIDATE','JOIN','EXPORT')),
    engine_name        TEXT        NOT NULL,
    engine_version     TEXT,
    prompt_id          TEXT,
    prompt_version     TEXT,
    preprocess_version TEXT,
    rulebook_version   TEXT,
    config             JSONB,
    input_hash         TEXT        NOT NULL,          -- sha256 of the exact engine input
    raw_output_uri     TEXT,                          -- preserved raw engine output (storage layer)
    status             TEXT        NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    error              TEXT,                          -- sanitized (class name), never raw upstream bodies
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS candidates (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT      NOT NULL REFERENCES processing_runs(id),
    crop_id     BIGINT      REFERENCES evidence_crops(id),  -- bound by LAYOUT stage; nullable until then
    field_type  TEXT        NOT NULL,
    raw_value   TEXT,
    value       TEXT,                -- normalized form (normalization stage fills; = raw for now)
    is_unknown  BOOLEAN     NOT NULL DEFAULT FALSE,
    confidence  REAL,                -- nullable: engine-dependent
    nbest_rank  INTEGER     NOT NULL DEFAULT 1,
    engine      TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_document  ON processing_runs (document_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates (run_id, nbest_rank);
