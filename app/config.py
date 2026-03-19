from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: Literal["development", "production", "test"] = "development"

    database_url: str
    redis_url: str

    conversations_webhook_secret: str

    sunco_key_id: str
    sunco_key_secret: str
    sunco_app_id: str
    integration_key_id: str
    integration_key_secret: str
    zendesk_subdomain: str
    zendesk_api_token: str
    zendesk_agent_group_id: str

    rag_base_url: str
    rag_api_key: str

    flush_buffer_debounce_seconds: int = 30

    # Working-hours gate
    agent_timezone: str = "Asia/Bangkok"
    agent_working_days: list[int] = [0, 1, 2, 3, 4]  # 0=Mon … 6=Sun
    agent_working_hour_start: int = 9
    agent_working_hour_end: int = 18  # exclusive (replies sent for hour < end)
    agent_outside_hours_reply: str | None = None  # None = silent; set text to auto-reply

    db_pool_max_size: int = 10
    worker_pool_max_size: int = 10
    worker_max_jobs: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore
