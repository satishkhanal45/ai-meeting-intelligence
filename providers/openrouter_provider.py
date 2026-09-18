"""OpenRouter provider implementation.

OpenRouter fronts many models behind one API. This implementation speaks HTTP
directly via ``httpx`` rather than pulling in another SDK.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

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

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "openai/gpt-4o-mini"

#: Not verified against the live API -- no OpenRouter key was configured when
#: this list was written. See https://openrouter.ai/models for the current set.
AVAILABLE_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct",
]


class OpenRouterProvider(BaseProvider):
    """Provider for OpenRouter API (multi-model gateway)."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def model_name(self) -> str:
        return self._model

    def _headers(self) -> dict[str, str]:
        api_key = settings.get_api_key("openrouter")
        if not api_key:
            raise ProviderAuthError(
                "OpenRouter API key is not configured. Set OPENROUTER_API_KEY in .env",
                provider=self.name,
                model=self._model,
            )
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout)

    def _body(self, prompt: str, temperature: float, system_prompt: str, json_mode: bool) -> dict:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _parse(self, data: dict, elapsed: float, json_mode: bool) -> ProviderResponse:
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                f"Unexpected response shape: {exc}", provider=self.name, model=self._model
            ) from exc
        usage = data.get("usage") or {}
        return ProviderResponse(
            content=self._strip_json_fences(text) if json_mode else text,
            model=data.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            processing_time=elapsed,
        )

    def _generate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        headers = self._headers()
        body = self._body(prompt, temperature, system_prompt, json_mode)
        start = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.post(OPENROUTER_API_URL, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._parse(data, time.perf_counter() - start, json_mode)

    async def _agenerate_raw(
        self, prompt: str, temperature: float, system_prompt: str, json_mode: bool
    ) -> ProviderResponse:
        headers = self._headers()
        body = self._body(prompt, temperature, system_prompt, json_mode)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.post(OPENROUTER_API_URL, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise self._translate(exc) from exc
        return self._parse(data, time.perf_counter() - start, json_mode)

    def _translate(self, exc: Exception) -> ProviderError:
        """Map an httpx failure onto the shared error taxonomy."""
        if isinstance(exc, ProviderError):
            return exc

        ctx = {"provider": self.name, "model": self._model}

        if isinstance(exc, httpx.TimeoutException):
            return ProviderTimeoutError(f"Request timed out: {exc}", **ctx)
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            # The body can echo the prompt back, so keep only a short excerpt.
            detail = exc.response.text[:200]
            if status == 429:
                return ProviderRateLimitError(
                    f"Rate limited: {detail}",
                    status_code=429,
                    retry_after=_retry_after(exc.response),
                    **ctx,
                )
            if status in (401, 403):
                return ProviderAuthError(f"Authentication failed: {detail}", status_code=status, **ctx)
            if status >= 500:
                return ProviderServerError(f"OpenRouter server error: {detail}", status_code=status, **ctx)
            return ProviderBadRequestError(f"Request rejected: {detail}", status_code=status, **ctx)
        if isinstance(exc, httpx.TransportError):
            return ProviderConnectionError(f"Could not reach OpenRouter: {exc}", **ctx)
        if isinstance(exc, ValueError):
            return ProviderResponseError(f"Response was not valid JSON: {exc}", **ctx)
        return ProviderError(f"OpenRouter generation failed: {exc}", **ctx)


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None
