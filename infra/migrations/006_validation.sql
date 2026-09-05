-- 006_validation.sql — VALIDATION stage (tables 10–11 of 14, plan §5)
-- validation_results is append-only (findings history); anomalies are finding
-- facts whose status is mutable (OPEN/RESOLVED).

CREATE TABLE IF NOT EXISTS validation_results (
    id           BIGSERIAL PRIMARY KEY,
    record_id    BIGINT      NOT NULL REFERENCES land_records(id),
    field_id     BIGINT      REFERENCES field_values(id),
    rule_id      TEXT        NOT NULL,
    rule_version TEXT        NOT NULL,
    severity     TEXT        NOT NULL CHECK (severity IN ('error', 'warn', 'info')),
    outcome      TEXT        NOT NULL CHECK (outcome IN ('pass', 'fail')),
    detail       JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS anomalies (
    id               BIGSERIAL PRIMARY KEY,
    record_id        BIGINT      NOT NULL REFERENCES land_records(id),
    rule_id          TEXT        NOT NULL,
    severity         TEXT        NOT NULL CHECK (severity IN ('error', 'warn', 'info')),
    status           TEXT        NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    explanation      JSONB       NOT NULL,  -- {rule, evidence[], calculation, related_record_id, recommended_action}
    resolved_by      TEXT,
    resolved_reason  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_validation_record ON validation_results (record_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_record  ON anomalies (record_id, status);
