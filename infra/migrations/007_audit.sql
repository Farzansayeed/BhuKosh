-- 007_audit.sql — AUDIT stage (table 13 of 14, plan §5)
-- Append-only ENFORCED via trigger (UPDATE/DELETE raise). The table owner can
-- drop the trigger, so hosted hardening is a separate owner role — see README.

CREATE TABLE IF NOT EXISTS audit_events (
    seq          BIGSERIAL PRIMARY KEY,
    prev_hash    TEXT,
    payload_hash TEXT        NOT NULL,
    actor        TEXT        NOT NULL,
    action       TEXT        NOT NULL,
    entity_refs  JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'audit_events_no_update_delete') THEN
    EXECUTE $fn$
      CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $body$
      BEGIN
        RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP;
      END;
      $body$ LANGUAGE plpgsql;
    $fn$;
    EXECUTE 'CREATE TRIGGER audit_events_no_update_delete
             BEFORE UPDATE OR DELETE ON audit_events
             FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()';
  END IF;
END
$do$;
