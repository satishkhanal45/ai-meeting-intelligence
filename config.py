"""Central configuration module.

Loads environment variables, validates API keys, and provides typed
configuration objects used throughout the application.
"""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
MEETINGS_DIR = PROJECT_ROOT / "meetings"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(DATA_DIR / "meetings.db")

ProviderName = Literal["gemini", "groq", "openrouter"]


class Settings(BaseSettings):
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")

    default_provider: ProviderName = Field(default="gemini", alias="DEFAULT_PROVIDER")
    default_temperature: float = Field(default=0.3, alias="DEFAULT_TEMPERATURE")
    default_chunk_size: int = Field(default=1000, alias="DEFAULT_CHUNK_SIZE")
    default_chunk_overlap: int = Field(default=200, alias="DEFAULT_CHUNK_OVERLAP")

    model_config = {"env_file": ".env", "extra": "ignore"}

    def is_provider_configured(self, provider: ProviderName) -> bool:
        key_map: dict[ProviderName, str] = {
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
            "openrouter": self.openrouter_api_key,
        }
        return bool(key_map.get(provider, ""))

    def get_configured_providers(self) -> list[ProviderName]:
        return [p for p in ["gemini", "groq", "openrouter"] if self.is_provider_configured(p)]

    def get_api_key(self, provider: ProviderName) -> str:
        key_map: dict[ProviderName, str] = {
            "gemini": self.gemini_api_key,
            "groq": self.groq_api_key,
            "openrouter": self.openrouter_api_key,
        }
        return key_map.get(provider, "")


settings = Settings()

__all__ = [
    "settings",
    "Settings",
    "ProviderName",
    "PROJECT_ROOT",
    "DATA_DIR",
    "MEETINGS_DIR",
    "DB_PATH",
]
