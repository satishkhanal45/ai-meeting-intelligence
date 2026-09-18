"""Provider package.

Imports and registers all available LLM providers so a simple
``from providers import ...`` makes them discoverable by the pipeline.
"""

from meeting_intelligence.providers.base_provider import BaseProvider
from meeting_intelligence.providers.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
)
from meeting_intelligence.providers.gemini_provider import GeminiProvider
from meeting_intelligence.providers.groq_provider import GroqProvider
from meeting_intelligence.providers.openrouter_provider import OpenRouterProvider

__all__ = [
    "BaseProvider",
    "GeminiProvider",
    "GroqProvider",
    "OpenRouterProvider",
    "ProviderError",
    "ProviderAuthError",
    "ProviderBadRequestError",
    "ProviderConnectionError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderServerError",
    "ProviderTimeoutError",
]
