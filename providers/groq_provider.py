"""Groq provider implementation."""

from __future__ import annotations

import time
from typing import Any

from groq import Groq

from config import settings
from models import ProviderResponse

from .base_provider import BaseProvider


class GroqProvider(BaseProvider):
    """Provider for Groq API (Llama, Mixtral, etc.)."""

    def __init__(self, model: str = "llama-3.3-70b-versatile") -> None:
        self._model = model
        self._client: Any = None
        self._initialized = False

    def _ensure_client(self) -> None:
        if not self._initialized:
            api_key = settings.get_api_key("groq")
            if not api_key:
                raise ValueError(
                    "Groq API key is not configured. Set GROQ_API_KEY in .env"
                )
            self._client = Groq(api_key=api_key)
            self._initialized = True

    @property
    def name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        self._ensure_client()
        start = time.perf_counter()

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
            )
            elapsed = time.perf_counter() - start

            choice = response.choices[0] if response.choices else None
            text = choice.message.content if choice else ""
            usage = response.usage

            return ProviderResponse(
                content=text or "",
                model=self._model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                processing_time=elapsed,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(f"Groq generation failed: {exc}") from exc

    def generate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        self._ensure_client()
        start = time.perf_counter()

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            elapsed = time.perf_counter() - start

            choice = response.choices[0] if response.choices else None
            text = choice.message.content if choice else ""
            usage = response.usage

            return ProviderResponse(
                content=self._strip_json_fences(text or ""),
                model=self._model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                processing_time=elapsed,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(f"Groq JSON generation failed: {exc}") from exc
