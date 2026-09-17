"""OpenRouter provider implementation.

OpenRouter provides access to many models via a single API. This
implementation uses ``httpx`` for maximum compatibility.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from config import settings
from models import ProviderResponse

from .base_provider import BaseProvider

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(BaseProvider):
    """Provider for OpenRouter API (multi-model gateway)."""

    def __init__(self, model: str = "openai/gpt-4o-mini") -> None:
        self._model = model
        self._api_key: str = ""
        self._initialized = False

    def _ensure_client(self) -> None:
        if not self._initialized:
            self._api_key = settings.get_api_key("openrouter")
            if not self._api_key:
                raise ValueError(
                    "OpenRouter API key is not configured. Set OPENROUTER_API_KEY in .env"
                )
            self._initialized = True

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def model_name(self) -> str:
        return self._model

    def _call_api(
        self, messages: list[dict[str, str]], temperature: float, json_mode: bool = False
    ) -> ProviderResponse:
        self._ensure_client()
        start = time.perf_counter()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
                response = client.post(OPENROUTER_API_URL, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()

            elapsed = time.perf_counter() - start

            choice = data.get("choices", [{}])[0]
            text = choice.get("message", {}).get("content", "")
            usage = data.get("usage", {})

            return ProviderResponse(
                content=self._strip_json_fences(text or "") if json_mode else (text or ""),
                model=data.get("model", self._model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                processing_time=elapsed,
            )
        except httpx.HTTPStatusError as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(
                f"OpenRouter HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.TimeoutException as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(f"OpenRouter request timed out: {exc}") from exc
        except Exception as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(f"OpenRouter generation failed: {exc}") from exc

    def generate(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._call_api(messages, temperature, json_mode=False)

    def generate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._call_api(messages, temperature, json_mode=True)
