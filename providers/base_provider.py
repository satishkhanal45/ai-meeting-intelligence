"""Abstract base class for LLM providers.

All providers must implement ``generate`` and ``generate_json`` methods.
The rest of the application depends only on this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import ProviderResponse


class BaseProvider(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    def generate(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        """Send a prompt to the LLM and return the text response.

        Parameters
        ----------
        prompt : str
            The user message / prompt.
        temperature : float
            Sampling temperature (0.0–1.0).
        system_prompt : str
            Optional system-level instruction.

        Returns
        -------
        ProviderResponse
            Parsed response with content, model name, and token usage.
        """
        ...

    @abstractmethod
    def generate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        """Send a prompt and request JSON output.

        Default implementation calls ``generate`` and attempts to strip
        markdown fences. Providers that support a ``response_format`` or
        ``json_object`` mode should override this method.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. ``"gemini"``)."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name of the default model (e.g. ``"gemini-2.0-flash"``)."""
        ...

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
