from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "BhuKosh"
    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8000

    # Postgres — full connection string (Supabase/Neon/etc.) overrides PG_* parts when set
    database_url: str = ""

    # Postgres (portable local instance fallback)
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_database: str = "bhukosh"
    pg_user: str = "bhukosh"
    pg_password: str = ""
    pgdata_dir: str = ""
    pgbin: str = ""

    # Auth
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_alg: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    # Extraction engine (Mode A / demo fixtures)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # /extract per-user rate limit (sliding window over successful calls)
    extract_rate_limit_per_min: int = 5
    extract_rate_window_seconds: int = 60

    # Custody / storage
    data_dir: str = "../data"
    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MB


@lru_cache
def get_settings() -> Settings:
    return Settings()
