"""Groq provider implementation."""

from __future__ import annotations

import time
from typing import Any, Optional

import groq
from groq import AsyncGroq, Groq

from config import settings
from models import ProviderResponse

from .base_provider import BaseProvider
from .errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
)

DEFAULT_MODEL = "llama-3.3-70b-versatile"

#: Models this provider exposes, for the /api/providers endpoint.
AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]


class GroqProvider(BaseProvider):
    """Provider for Groq API (Llama and friends)."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model
        self._client: Any = None
        self._aclient: Any = None

    # ── Clients ──────────────────────────────────────────────────────────

    def _api_key(self) -> str:
        api_key = settings.get_api_key("groq")
        if not api_key:
            raise ProviderAuthError(
                "Groq API key is not configured. Set GROQ_API_KEY in .env",
                provider=self.name,
                model=self._model,
            )
        return api_key

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = Groq(api_key=self._api_key(), timeout=settings.request_timeout)
        return self._client

    def _ensure_aclient(self) -> Any:
        if self._aclient is None:
            self._aclient = AsyncGroq(api_key=self._api_key(), timeout=settings.request_timeout)
        return self._aclient

    @property
    def name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._model

    # ── Request building and response parsing ────────────────────────────

    def _build_kwargs(self, prompt: str, temperature: float, system_prompt: str, json_mode: bool) -> dict:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def _parse(self, response: Any, elapsed: float, json_mode: bool) -> ProviderResponse:
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content if choice else "") or ""
        usage = response.usage
        return ProviderResponse(
            content=self._strip_json_fences(text) if json_mode else text,
            model=self._model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            processing_time=elapsed,
        )

    # ── Raw calls ────────────────────────────────────────────────────────

    def _generate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        client = self._ensure_client()
        start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                **self._build_kwargs(prompt, temperature, system_prompt, json_mode)
            )
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._parse(response, time.perf_counter() - start, json_mode)

    async def _agenerate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        client = self._ensure_aclient()
        start = time.perf_counter()
        try:
            response = await client.chat.completions.create(
                **self._build_kwargs(prompt, temperature, system_prompt, json_mode)
            )
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._parse(response, time.perf_counter() - start, json_mode)

    # ── Error translation ────────────────────────────────────────────────

    def _translate(self, exc: Exception) -> ProviderError:
        """Map a Groq SDK exception onto the shared error taxonomy."""
        if isinstance(exc, ProviderError):
            return exc

        ctx = {"provider": self.name, "model": self._model}
        message = str(exc)

        if isinstance(exc, groq.APITimeoutError):
            return ProviderTimeoutError(f"Request timed out: {message}", **ctx)
        if isinstance(exc, groq.APIConnectionError):
            return ProviderConnectionError(f"Could not reach Groq: {message}", **ctx)
        if isinstance(exc, groq.RateLimitError):
            return ProviderRateLimitError(
                f"Rate limited: {message}",
                status_code=429,
                retry_after=_retry_after(exc),
                **ctx,
            )
        if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
            return ProviderAuthError(f"Authentication failed: {message}", status_code=401, **ctx)
        if isinstance(exc, groq.BadRequestError):
            return ProviderBadRequestError(f"Request rejected: {message}", status_code=400, **ctx)
        if isinstance(exc, groq.InternalServerError):
            status = getattr(exc, "status_code", 500)
            return ProviderServerError(f"Groq server error: {message}", status_code=status, **ctx)
        if isinstance(exc, groq.APIStatusError):
            status = getattr(exc, "status_code", None)
            if status is not None and status >= 500:
                return ProviderServerError(f"Groq server error: {message}", status_code=status, **ctx)
            return ProviderBadRequestError(f"Groq rejected the request: {message}", status_code=status, **ctx)
        if isinstance(exc, (AttributeError, IndexError, TypeError)):
            return ProviderResponseError(f"Unexpected response shape: {message}", **ctx)
        return ProviderError(f"Groq generation failed: {message}", **ctx)


def _retry_after(exc: Exception) -> Optional[float]:
    """Read a ``Retry-After`` header off an SDK exception, when present."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    for key in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
        raw = headers.get(key)
        if raw:
            try:
                return float(str(raw).rstrip("s"))
            except (TypeError, ValueError):
                continue
    return None
