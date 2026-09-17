"""Central configuration module.

Loads environment variables, validates API keys, and provides typed
configuration objects used throughout the application.
"""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

_QUOTE_CHARS = "\"'"

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
    default_temperature: float = Field(default=0.3, ge=0.0, le=2.0, alias="DEFAULT_TEMPERATURE")
    default_chunk_size: int = Field(default=1000, ge=100, le=100_000, alias="DEFAULT_CHUNK_SIZE")
    default_chunk_overlap: int = Field(default=200, ge=0, alias="DEFAULT_CHUNK_OVERLAP")

    # Comma-separated browser origins allowed to call the API. A wildcard is not
    # valid alongside credentialed requests, so the default names the dev server.
    allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="ALLOWED_ORIGINS",
    )

    # ── Provider resilience ──────────────────────────────────────────────
    request_timeout: float = Field(default=120.0, gt=0, alias="REQUEST_TIMEOUT")
    connect_timeout: float = Field(default=15.0, gt=0, alias="CONNECT_TIMEOUT")
    max_retry_attempts: int = Field(default=4, ge=1, le=10, alias="MAX_RETRY_ATTEMPTS")
    retry_base_delay: float = Field(default=0.5, ge=0, alias="RETRY_BASE_DELAY")
    retry_max_delay: float = Field(default=30.0, ge=0, alias="RETRY_MAX_DELAY")

    # How many chunk summaries may be in flight at once. Chunks were summarised
    # serially, making wall-clock time linear in transcript length.
    max_concurrent_requests: int = Field(
        default=5, ge=1, le=50, alias="MAX_CONCURRENT_REQUESTS"
    )

    # Providers tried in order when the requested one fails outright. Empty
    # disables failover.
    provider_fallback_chain: str = Field(default="", alias="PROVIDER_FALLBACK_CHAIN")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @field_validator("*", mode="before")
    @classmethod
    def _strip_surrounding_quotes(cls, value):
        """Tolerate quoted values in ``.env``.

        ``python-dotenv`` strips surrounding quotes, but Docker's ``--env-file``
        and compose's ``env_file:`` pass them through verbatim. Without this,
        ``DEFAULT_PROVIDER="groq"`` arrives as the 5-character string
        ``"groq"`` and fails validation inside a container.
        """
        if isinstance(value, str) and len(value) >= 2:
            if value[0] == value[-1] and value[0] in _QUOTE_CHARS:
                return value[1:-1]
        return value

    @field_validator("default_chunk_overlap")
    @classmethod
    def _overlap_below_chunk_size(cls, value: int, info) -> int:
        chunk_size = info.data.get("default_chunk_size")
        if chunk_size is not None and value >= chunk_size:
            raise ValueError(
                f"DEFAULT_CHUNK_OVERLAP ({value}) must be smaller than "
                f"DEFAULT_CHUNK_SIZE ({chunk_size})"
            )
        return value

    def get_allowed_origins(self) -> list[str]:
        """Return the configured CORS origins as a list."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def get_fallback_chain(self) -> list[str]:
        """Return configured fallback providers, keeping only configured ones."""
        names = [p.strip() for p in self.provider_fallback_chain.split(",") if p.strip()]
        return [n for n in names if n in ("gemini", "groq", "openrouter")]

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
