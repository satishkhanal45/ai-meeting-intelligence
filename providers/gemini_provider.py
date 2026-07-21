"""Google Gemini provider implementation."""

from __future__ import annotations

import time
from typing import Any

from google import genai
from google.genai import types as genai_types

from config import settings
from models import ProviderResponse

from .base_provider import BaseProvider


class GeminiProvider(BaseProvider):
    """Provider for Google Gemini models."""

    def __init__(self, model: str = "gemini-2.0-flash") -> None:
        self._model = model
        self._client: Any = None
        self._initialized = False

    def _ensure_client(self) -> None:
        if not self._initialized:
            api_key = settings.get_api_key("gemini")
            if not api_key:
                raise ValueError(
                    "Gemini API key is not configured. Set GEMINI_API_KEY in .env"
                )
            self._client = genai.Client(api_key=api_key)
            self._initialized = True

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        self._ensure_client()
        start = time.perf_counter()

        contents = [prompt]
        config = genai_types.GenerateContentConfig(
            temperature=temperature,
        )
        if system_prompt:
            config.system_instruction = system_prompt

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
            elapsed = time.perf_counter() - start

            text = response.text if response.text is not None else ""
            usage = response.usage_metadata if hasattr(response, "usage_metadata") else None

            return ProviderResponse(
                content=text,
                model=self._model,
                input_tokens=usage.prompt_token_count if usage else 0,
                output_tokens=usage.candidates_token_count if usage else 0,
                processing_time=elapsed,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(f"Gemini generation failed: {exc}") from exc

    def generate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        self._ensure_client()
        start = time.perf_counter()

        contents = [prompt]
        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
        )
        if system_prompt:
            config.system_instruction = system_prompt

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
            elapsed = time.perf_counter() - start

            text = response.text if response.text is not None else ""
            usage = response.usage_metadata if hasattr(response, "usage_metadata") else None

            return ProviderResponse(
                content=self._strip_json_fences(text),
                model=self._model,
                input_tokens=usage.prompt_token_count if usage else 0,
                output_tokens=usage.candidates_token_count if usage else 0,
                processing_time=elapsed,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start
            raise RuntimeError(f"Gemini JSON generation failed: {exc}") from exc
