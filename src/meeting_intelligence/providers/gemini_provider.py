"""Google Gemini provider implementation."""

from __future__ import annotations

import time
from typing import Any, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from meeting_intelligence.config import settings
from meeting_intelligence.models import ProviderResponse

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

# An alias rather than a pinned version. Google retires concrete model ids, and
# a stale default is a hard 404 on every call: "gemini-2.0-flash" was the
# default here until it stopped existing. The alias always resolves to the
# current flash model, and callers who need reproducibility can pin one below.
DEFAULT_MODEL = "gemini-flash-latest"

#: Verified against the live API. `models.list()` is not a reliable guide --
#: it advertises models that then 404 on generateContent -- so this list is
#: checked by calling each one.
AVAILABLE_MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
]


class GeminiProvider(BaseProvider):
    """Provider for Google Gemini models."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            api_key = settings.get_api_key("gemini")
            if not api_key:
                raise ProviderAuthError(
                    "Gemini API key is not configured. Set GEMINI_API_KEY in .env",
                    provider=self.name,
                    model=self._model,
                )
            self._client = genai.Client(
                api_key=api_key,
                http_options=genai_types.HttpOptions(
                    timeout=int(settings.request_timeout * 1000)  # milliseconds
                ),
            )
        return self._client

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    def _build_config(
        self, temperature: float, system_prompt: str, json_mode: bool
    ) -> genai_types.GenerateContentConfig:
        config = genai_types.GenerateContentConfig(temperature=temperature)
        if json_mode:
            config.response_mime_type = "application/json"
        if system_prompt:
            config.system_instruction = system_prompt
        return config

    def _parse(self, response: Any, elapsed: float, json_mode: bool) -> ProviderResponse:
        text = response.text if response.text is not None else ""
        usage = getattr(response, "usage_metadata", None)
        return ProviderResponse(
            content=self._strip_json_fences(text) if json_mode else text,
            model=self._model,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0 if usage else 0,
            processing_time=elapsed,
        )

    def _generate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        client = self._ensure_client()
        start = time.perf_counter()
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[prompt],
                config=self._build_config(temperature, system_prompt, json_mode),
            )
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._parse(response, time.perf_counter() - start, json_mode)

    async def _agenerate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        client = self._ensure_client()
        start = time.perf_counter()
        try:
            response = await client.aio.models.generate_content(
                model=self._model,
                contents=[prompt],
                config=self._build_config(temperature, system_prompt, json_mode),
            )
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._parse(response, time.perf_counter() - start, json_mode)

    def _translate(self, exc: Exception) -> ProviderError:
        """Map a google-genai exception onto the shared error taxonomy."""
        if isinstance(exc, ProviderError):
            return exc

        ctx = {"provider": self.name, "model": self._model}
        message = str(exc)
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)

        if isinstance(exc, genai_errors.ServerError):
            return ProviderServerError(f"Gemini server error: {message}", status_code=status, **ctx)
        if isinstance(exc, genai_errors.ClientError):
            if status == 429:
                return ProviderRateLimitError(
                    f"Rate limited: {message}",
                    status_code=429,
                    retry_after=_retry_after(exc),
                    **ctx,
                )
            if status in (401, 403):
                return ProviderAuthError(f"Authentication failed: {message}", status_code=status, **ctx)
            return ProviderBadRequestError(f"Request rejected: {message}", status_code=status, **ctx)
        if isinstance(exc, genai_errors.APIError):
            if status is not None and status >= 500:
                return ProviderServerError(f"Gemini server error: {message}", status_code=status, **ctx)
            return ProviderError(f"Gemini API error: {message}", status_code=status, **ctx)
        if isinstance(exc, TimeoutError):
            return ProviderTimeoutError(f"Request timed out: {message}", **ctx)
        if isinstance(exc, ConnectionError):
            return ProviderConnectionError(f"Could not reach Gemini: {message}", **ctx)
        if isinstance(exc, (AttributeError, IndexError, TypeError)):
            return ProviderResponseError(f"Unexpected response shape: {message}", **ctx)
        return ProviderError(f"Gemini generation failed: {message}", **ctx)


def _retry_after(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None
