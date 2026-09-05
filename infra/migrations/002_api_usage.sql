-- Per-user usage log for AI extraction calls.
-- Doubles as the sliding-window rate-limit source (see backend/app/extract/rate_limit.py):
-- only status='ok' rows count toward the window, so rejected/error calls never extend the wait.
CREATE TABLE IF NOT EXISTS api_usage (
    id           BIGSERIAL PRIMARY KEY,
    username     TEXT        NOT NULL,
    route        TEXT        NOT NULL DEFAULT '/extract',
    status       TEXT        NOT NULL CHECK (status IN ('ok', 'error', 'rejected')),
    model        TEXT,
    prompt_chars INTEGER,
    result_json  JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_api_usage_user_time ON api_usage (username, created_at DESC);
