"""Retry helpers for provider calls.

A single transient 429 used to lose a whole chunk of a transcript: the
pipeline caught the error, logged it, and carried on with that segment
missing from the summary. These helpers retry the failures worth retrying,
with exponential backoff, full jitter, and respect for ``Retry-After``.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

from logger import get_logger

from .errors import ProviderError

logger = get_logger(__name__)

T = TypeVar("T")


def _backoff_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Return an exponentially growing delay with full jitter.

    Full jitter (a uniform draw from ``[0, capped]``) rather than a fixed
    schedule, so concurrent workers that were rate-limited together do not all
    retry at the same instant and trigger the limit again.
    """
    capped = min(base_delay * (2**attempt), max_delay)
    return random.uniform(0, capped)


def _next_delay(exc: ProviderError, attempt: int, base_delay: float, max_delay: float) -> float:
    """Prefer the provider's own ``Retry-After`` over our backoff curve."""
    if exc.retry_after is not None and exc.retry_after >= 0:
        return min(exc.retry_after, max_delay)
    return _backoff_delay(attempt, base_delay, max_delay)


def _should_retry(exc: ProviderError, attempt: int, max_attempts: int) -> bool:
    return exc.retryable and attempt < max_attempts - 1


def _log_retry(exc: ProviderError, attempt: int, max_attempts: int, delay: float) -> None:
    logger.warning(
        "Provider call failed, retrying",
        extra={
            "provider": exc.provider,
            "model": exc.model,
            "error_type": type(exc).__name__,
            "status_code": exc.status_code,
            "attempt": attempt + 1,
            "max_attempts": max_attempts,
            "retry_in": round(delay, 2),
        },
    )


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
) -> T:
    """Call *fn*, retrying retryable :class:`ProviderError`s."""
    last: ProviderError | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except ProviderError as exc:
            last = exc
            if not _should_retry(exc, attempt, max_attempts):
                raise
            delay = _next_delay(exc, attempt, base_delay, max_delay)
            _log_retry(exc, attempt, max_attempts, delay)
            time.sleep(delay)
    assert last is not None  # unreachable: the loop either returns or raises
    raise last


async def acall_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
) -> T:
    """Async counterpart to :func:`call_with_retry`."""
    last: ProviderError | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except ProviderError as exc:
            last = exc
            if not _should_retry(exc, attempt, max_attempts):
                raise
            delay = _next_delay(exc, attempt, base_delay, max_delay)
            _log_retry(exc, attempt, max_attempts, delay)
            await asyncio.sleep(delay)
    assert last is not None  # unreachable
    raise last


__all__ = ["call_with_retry", "acall_with_retry"]
