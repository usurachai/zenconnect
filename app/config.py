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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore
