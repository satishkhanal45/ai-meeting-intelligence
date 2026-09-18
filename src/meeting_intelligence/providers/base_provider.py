"""Abstract base class for LLM providers.

Subclasses implement two raw calls, :meth:`_generate_raw` and
:meth:`_agenerate_raw`, and translate their SDK's exceptions into the typed
errors in :mod:`providers.errors`. Everything shared — retries, backoff, JSON
fence stripping — lives here, so a provider is roughly a hundred lines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from meeting_intelligence.config import settings
from meeting_intelligence.models import ProviderResponse

from .retry import acall_with_retry, call_with_retry


class BaseProvider(ABC):
    """Abstract interface for LLM providers."""

    # ── Subclass contract ────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. ``"gemini"``)."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name of the model in use (e.g. ``"gemini-2.0-flash"``)."""
        ...

    @abstractmethod
    def _generate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        """Perform one request. Must raise a :class:`ProviderError` on failure."""
        ...

    @abstractmethod
    async def _agenerate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        """Async counterpart to :meth:`_generate_raw`."""
        ...

    # ── Public API ───────────────────────────────────────────────────────

    def generate(
        self, prompt: str, temperature: float = 0.3, system_prompt: str = ""
    ) -> ProviderResponse:
        """Send a prompt and return the text response, retrying transient failures."""
        return call_with_retry(
            lambda: self._generate_raw(prompt, temperature, system_prompt, False),
            **self._retry_config(),
        )

    def generate_json(
        self, prompt: str, temperature: float = 0.3, system_prompt: str = ""
    ) -> ProviderResponse:
        """Send a prompt requesting JSON output, retrying transient failures."""
        return call_with_retry(
            lambda: self._generate_raw(prompt, temperature, system_prompt, True),
            **self._retry_config(),
        )

    async def agenerate(
        self, prompt: str, temperature: float = 0.3, system_prompt: str = ""
    ) -> ProviderResponse:
        """Async :meth:`generate`."""
        return await acall_with_retry(
            lambda: self._agenerate_raw(prompt, temperature, system_prompt, False),
            **self._retry_config(),
        )

    async def agenerate_json(
        self, prompt: str, temperature: float = 0.3, system_prompt: str = ""
    ) -> ProviderResponse:
        """Async :meth:`generate_json`."""
        return await acall_with_retry(
            lambda: self._agenerate_raw(prompt, temperature, system_prompt, True),
            **self._retry_config(),
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _retry_config() -> dict:
        return {
            "max_attempts": settings.max_retry_attempts,
            "base_delay": settings.retry_base_delay,
            "max_delay": settings.retry_max_delay,
        }

    def _strip_json_fences(self, text: str) -> str:
        """Remove markdown JSON code fences if present."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].strip().lower().startswith("```json"):
                lines = lines[1:]
            elif lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text
