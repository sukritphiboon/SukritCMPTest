from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://cmp:change-me@localhost:5432/cmp"
    redis_url: str = "redis://localhost:6379/0"
    # base64 encoded 32 random bytes (AES-256 key) used to protect device credentials
    encryption_key: str = ""
    # how often the worker polls every appliance (seconds); ARQ needs a divisor of 60
    telemetry_interval_seconds: int = 30
    telemetry_concurrency: int = 4
    # how often running backup jobs are checked on the appliance (must divide 60)
    job_poll_interval_seconds: int = 15
    # data older than this many intervals is reported as stale
    telemetry_stale_intervals: int = 3
    runway_window_days: int = 7
    # CMP-side backup schedules (the cron of each backup policy)
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Bangkok"  # one timezone for every policy cron
    scheduler_sync_seconds: int = 30  # how soon a changed policy is picked up
    scheduler_misfire_grace_seconds: int = 300  # a run that starts later than this after its time is skipped
    scheduler_max_parallel_starts: int = 4  # backups started at the same moment by one policy run
    # "user:key,user2:key2" - the user name is recorded as the actor in the audit log
    api_keys: str = ""
    # how the CMP reaches the arrays (mock servers speak plain http)
    device_https: bool = True
    device_verify_tls: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
