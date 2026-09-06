-- 010_search_history_integrity.sql — search, history, and document integrity.
--
-- SEARCH (fastest sane option for substring + fuzzy + multilingual):
--   pg_trgm GIN indexes make ILIKE '%term%' and similarity() index-accelerated
--   across village/khasra and every extracted field value — including
--   Devanagari/Gujarati text. No external engine, no sync, transactional.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_records_village_trgm ON land_records USING gin (village_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_records_khasra_trgm  ON land_records USING gin (khasra_no gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fields_value_trgm    ON field_values USING gin (current_value gin_trgm_ops);

-- HISTORY (past vs current): records were UPDATE-only until now, so the state
-- timeline already lives in human_decisions + audit_events. Adding updated_at
-- so the list can sort by recency honestly.
ALTER TABLE land_records ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE land_records SET updated_at = created_at WHERE updated_at IS NULL;

CREATE OR REPLACE FUNCTION land_records_touch_updated_at() RETURNS trigger AS $body$
BEGIN
  IF NEW.record_version <> OLD.record_version THEN
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END;
$body$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS land_records_touch ON land_records;
CREATE TRIGGER land_records_touch
  BEFORE UPDATE ON land_records
  FOR EACH ROW EXECUTE FUNCTION land_records_touch_updated_at();

-- DOCUMENT INTEGRITY (duplicate/forged detection):
--   integrity_findings: one row per (page, check). kind DUPLICATE pages point
--   at their original; FORGED_SUSPECT carries the vision engine's verdict.
--   doc_identifiers: the learned reference library — stamps, seals, signatures,
--   logos and other recurring identifiers harvested from VERIFIED documents,
--   with occurrence counts. New documents are compared against it.
CREATE TABLE IF NOT EXISTS integrity_findings (
    id           BIGSERIAL PRIMARY KEY,
    page_id      BIGINT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    kind         TEXT   NOT NULL CHECK (kind IN ('DUPLICATE', 'FORGED_SUSPECT', 'IDENTIFIER_MATCH', 'IDENTIFIER_MISSING')),
    severity     TEXT   NOT NULL DEFAULT 'warn' CHECK (severity IN ('info', 'warn', 'error')),
    detail       JSONB  NOT NULL DEFAULT '{}',   -- engine verdict, matched identifiers, similarity
    original_page_id BIGINT REFERENCES pages(id), -- for DUPLICATE: the first-seen original
    engine       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_integrity_page ON integrity_findings (page_id);

CREATE TABLE IF NOT EXISTS doc_identifiers (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT   NOT NULL CHECK (kind IN ('STAMP', 'SEAL', 'SIGNATURE', 'LOGO', 'WATERMARK', 'OTHER')),
    label        TEXT   NOT NULL,               -- e.g. "TEHSIL OFFICE RAMPUR round seal"
    page_id      BIGINT NOT NULL REFERENCES pages(id),     -- page it was learned from
    crop_id      BIGINT REFERENCES evidence_crops(id),     -- cropped identifier image
    embedding    JSONB,                          -- engine descriptor for future matching
    source       TEXT   NOT NULL DEFAULT 'vision' CHECK (source IN ('vision', 'human')),
    verified     BOOLEAN NOT NULL DEFAULT FALSE,           -- learned from a VERIFIED document
    times_seen   INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (label, kind)
);
CREATE INDEX IF NOT EXISTS idx_identifiers_kind ON doc_identifiers (kind, verified);
