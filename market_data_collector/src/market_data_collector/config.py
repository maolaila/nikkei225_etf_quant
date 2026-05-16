from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    jquants_api_key: str | None = Field(default=None, validation_alias="JQUANTS_API_KEY")
    twelvedata_api_key: str | None = Field(default=None, validation_alias="TWELVEDATA_API_KEY")
    data_dir: Path = Field(default=PROJECT_ROOT / "data", validation_alias="DATA_DIR")
    default_provider: Literal["jquants", "twelvedata", "yfinance"] = Field(
        default="jquants",
        validation_alias="DEFAULT_PROVIDER",
    )
    jquants_enable_minute: bool = Field(default=False, validation_alias="JQUANTS_ENABLE_MINUTE")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir
        if path.is_absolute():
            return path
        parts = path.parts
        if parts and parts[0] == "market_data_collector":
            return PROJECT_ROOT / Path(*parts[1:])
        return (Path.cwd() / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def masked_secret(value: str | None) -> str:
    if not value:
        return "not configured"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
