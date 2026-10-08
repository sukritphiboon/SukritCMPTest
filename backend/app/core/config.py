from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://cmp:change-me@localhost:5432/cmp"
    redis_url: str = "redis://localhost:6379/0"
    # base64 encoded 32 random bytes (AES-256 key) used to protect device credentials
    encryption_key: str = ""
    telemetry_interval_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
