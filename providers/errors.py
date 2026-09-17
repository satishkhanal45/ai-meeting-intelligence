"""Typed provider errors.

The providers used to flatten every failure into a bare ``RuntimeError``, so
callers could not tell an expired API key from a transient rate limit and had
no basis for deciding whether to retry or to fail over to another provider.

Every error carries :attr:`retryable`, which the retry helper consults.
"""

from __future__ import annotations

from typing import Optional


class ProviderError(Exception):
    """Base class for every provider failure."""

    #: Whether retrying the same call could plausibly succeed.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        #: Seconds the provider asked us to wait, when it said so.
        self.retry_after = retry_after

    def __str__(self) -> str:
        base = super().__str__()
        if self.provider:
            return f"[{self.provider}] {base}"
        return base


class ProviderAuthError(ProviderError):
    """Missing, invalid or unauthorised credentials. Retrying will not help."""

    retryable = False


class ProviderRateLimitError(ProviderError):
    """Rate limit or quota exhaustion. Retryable after a delay."""

    retryable = True


class ProviderTimeoutError(ProviderError):
    """The request exceeded its timeout."""

    retryable = True


class ProviderConnectionError(ProviderError):
    """The provider could not be reached."""

    retryable = True


class ProviderServerError(ProviderError):
    """The provider returned a 5xx."""

    retryable = True


class ProviderBadRequestError(ProviderError):
    """The request itself was rejected, e.g. context-length overflow."""

    retryable = False


class ProviderResponseError(ProviderError):
    """The provider replied, but the response could not be interpreted."""

    retryable = False


__all__ = [
    "ProviderError",
    "ProviderAuthError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderConnectionError",
    "ProviderServerError",
    "ProviderBadRequestError",
    "ProviderResponseError",
]
