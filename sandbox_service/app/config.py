from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_token: str = "local-sandbox-token-change-me"
    runtime_image: str = "python:3.13-slim"
    execution_timeout_seconds: int = 5
    docker_api_timeout_seconds: int = 10
    memory_limit: str = "128m"
    nano_cpus: int = 500_000_000
    pids_limit: int = 64
    workspace_tmpfs_size: str = "32m"
    temp_tmpfs_size: str = "16m"
    shm_size: str = "16m"
    max_output_bytes: int = 64 * 1024
    max_concurrency: int = 2
    allowed_env_keys: str = "LANG,LC_ALL,TZ,PYTHONUNBUFFERED"
    stale_container_ttl_seconds: int = 5 * 60

    model_config = SettingsConfigDict(
        env_prefix="SANDBOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_env_key_set(self) -> set[str]:
        return {item.strip().upper() for item in self.allowed_env_keys.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
