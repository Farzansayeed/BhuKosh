-- 011_field_translations.sql — multilingual output of scanned values.
-- field_values.translations holds per-language renderings of the CURRENT value:
--   {"en": "...", "hi": "...", "gu": "..."}   (missing language or key = not rendered yet)
-- The original extracted value stays primary (evidence-first); translations are
-- advisory renderings produced by one Gemini pass per record, and human
-- CORRECTIONS wipe them so they are regenerated from the corrected value.
ALTER TABLE field_values ADD COLUMN IF NOT EXISTS translations JSONB;

-- Learning-hint reads already join field_values; a GIN keeps any future
-- translations lookups cheap. Small table; cheap insurance.
CREATE INDEX IF NOT EXISTS idx_field_values_translations
    ON field_values USING GIN (translations);
