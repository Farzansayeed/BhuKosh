-- 005_projections.sql — PROJECTIONS + DECISIONS (tables 8, 9, 12 of 14, plan §5)
-- Truth discipline: human_decisions is the append-only source of truth for human
-- actions; land_records/field_values are projections mutated ONLY via
-- records.service.apply_decision() (plan §5, single write path).

CREATE TABLE IF NOT EXISTS land_records (
    id               BIGSERIAL PRIMARY KEY,
    village_code     TEXT        NOT NULL,
    khata_no         TEXT,
    khasra_no        TEXT,
    current_state    TEXT        NOT NULL CHECK (current_state IN (
                         'INGESTED','EXTRACTED','VALIDATED','REVIEW_REQUIRED',
                         'VERIFIED','OFFICER_CERTIFIED','ARCHIVED','REJECTED','QUARANTINED')),
    claim_owner      TEXT,                          -- active review claim (423 semantics)
    claim_expires_at TIMESTAMPTZ,
    record_version   INTEGER     NOT NULL DEFAULT 1 CHECK (record_version >= 1),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS field_values (
    id                    BIGSERIAL PRIMARY KEY,
    record_id             BIGINT  NOT NULL REFERENCES land_records(id),
    field_type            TEXT    NOT NULL,
    occurrence            INTEGER NOT NULL DEFAULT 1 CHECK (occurrence >= 1),
    selected_candidate_id BIGINT  REFERENCES candidates(id),
    current_value         TEXT,
    raw_value             TEXT,
    norm_steps            JSONB   NOT NULL DEFAULT '[]',
    state                 TEXT    NOT NULL CHECK (state IN (
                              'CANDIDATE','NORMALIZED','VALIDATED','AUTO_ACCEPTED',
                              'REVIEW_REQUIRED','CORRECTED','HUMAN_VERIFIED','CERTIFIED')),
    updated_by_decision   BIGINT,                -- FK added by decision write path (id, not enforced yet)
    UNIQUE (record_id, field_type, occurrence)
);

CREATE TABLE IF NOT EXISTS human_decisions (
    id             BIGSERIAL PRIMARY KEY,
    record_id      BIGINT      NOT NULL REFERENCES land_records(id),
    field_id       BIGINT      REFERENCES field_values(id),
    decision_type  TEXT        NOT NULL CHECK (decision_type IN (
                       'CORRECTION','APPROVE','REJECT','CERTIFY','REOPEN','ANOMALY_RESOLVE')),
    before_value   JSONB,
    after_value    JSONB,
    candidate_id   BIGINT      REFERENCES candidates(id),
    reason         TEXT,
    actor_id       TEXT        NOT NULL,
    actor_role     TEXT        NOT NULL,
    record_version INTEGER     NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_records_identity ON land_records (village_code, khasra_no);
CREATE INDEX IF NOT EXISTS idx_fields_record    ON field_values (record_id);
CREATE INDEX IF NOT EXISTS idx_decisions_record ON human_decisions (record_id);
