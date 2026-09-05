-- 008_exports.sql — EXPORTS stage (table 14 of 14, plan §5). Immutable.

CREATE TABLE IF NOT EXISTS exports (
    id                BIGSERIAL PRIMARY KEY,
    record_id         BIGINT      NOT NULL REFERENCES land_records(id),
    format            TEXT        NOT NULL CHECK (format IN ('json', 'csv', 'pdf')),
    storage_uri       TEXT        NOT NULL,
    evidence_manifest JSONB       NOT NULL,
    created_by        TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exports_record ON exports (record_id);

DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'exports_immutable') THEN
    EXECUTE $fn$
      CREATE FUNCTION exports_immutable_guard() RETURNS trigger AS $body$
      BEGIN
        RAISE EXCEPTION 'exports is immutable (attempted %)', TG_OP;
      END;
      $body$ LANGUAGE plpgsql;
    $fn$;
    EXECUTE 'CREATE TRIGGER exports_immutable
             BEFORE UPDATE OR DELETE ON exports
             FOR EACH ROW EXECUTE FUNCTION exports_immutable_guard()';
  END IF;
END
$do$;
