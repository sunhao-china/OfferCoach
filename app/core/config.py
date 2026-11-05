"""应用配置：pydantic-settings，环境变量 OFFERCOACH_* / .env 可覆盖。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OFFERCOACH_", env_file=".env", extra="ignore")

    prompts_dir: str = "app/prompts/templates"
    intent_confidence_threshold: float = 0.7
    intent_max_retries: int = 1


settings = Settings()
