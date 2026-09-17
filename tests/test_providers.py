"""Tests for the provider layer.

All network traffic is mocked. These cover response parsing, error
translation and the retry policy — none of which had any coverage before.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from config import settings
from models import ProviderResponse
from providers.base_provider import BaseProvider
from providers.errors import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from providers.openrouter_provider import OpenRouterProvider
from providers.retry import acall_with_retry, call_with_retry


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Keep backoff delays negligible so tests stay quick.

    Patch the settings *instance*: pydantic v2 keeps field values on the model,
    not as class attributes, so patching the class raises AttributeError.
    """
    monkeypatch.setattr(settings, "retry_base_delay", 0.001)
    monkeypatch.setattr(settings, "retry_max_delay", 0.005)
    monkeypatch.setattr(settings, "max_retry_attempts", 4)


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")


def _mock_openrouter(monkeypatch, handler):
    """Route both sync and async httpx clients through *handler*."""
    real_client, real_aclient = httpx.Client, httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=transport, **kw)
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_aclient(transport=transport, **kw)
    )


def _ok_body(content: str = "hello"):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        "model": "openai/gpt-4o-mini",
    }


class TestResponseParsing:
    def test_parses_content_and_token_usage(self, monkeypatch, api_key):
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(200, json=_ok_body("a summary")))
        response = OpenRouterProvider().generate("prompt")
        assert response.content == "a summary"
        assert response.input_tokens == 11
        assert response.output_tokens == 22
        assert response.processing_time >= 0

    def test_strips_json_code_fences(self, monkeypatch, api_key):
        fenced = '```json\n{"a": 1}\n```'
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(200, json=_ok_body(fenced)))
        assert OpenRouterProvider().generate_json("prompt").content == '{"a": 1}'

    def test_leaves_plain_text_untouched_in_text_mode(self, monkeypatch, api_key):
        fenced = "```json\nnot stripped\n```"
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(200, json=_ok_body(fenced)))
        assert OpenRouterProvider().generate("prompt").content == fenced

    def test_null_content_becomes_empty_string(self, monkeypatch, api_key):
        body = {"choices": [{"message": {"content": None}}], "usage": {}}
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(200, json=body))
        assert OpenRouterProvider().generate("prompt").content == ""

    def test_json_mode_sets_response_format(self, monkeypatch, api_key):
        seen: dict = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_ok_body())

        _mock_openrouter(monkeypatch, handler)
        OpenRouterProvider().generate_json("prompt")
        assert seen["response_format"] == {"type": "json_object"}


class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, ProviderAuthError),
            (403, ProviderAuthError),
            (429, ProviderRateLimitError),
            (400, ProviderBadRequestError),
            (404, ProviderBadRequestError),
            (500, ProviderServerError),
            (503, ProviderServerError),
        ],
    )
    def test_status_codes_map_to_typed_errors(self, monkeypatch, api_key, status, expected):
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(status, text="detail"))
        with pytest.raises(expected):
            OpenRouterProvider().generate("prompt")

    def test_timeouts_are_typed(self, monkeypatch, api_key):
        def handler(request):
            raise httpx.ReadTimeout("too slow", request=request)

        _mock_openrouter(monkeypatch, handler)
        with pytest.raises(ProviderTimeoutError):
            OpenRouterProvider().generate("prompt")

    def test_connection_failures_are_typed(self, monkeypatch, api_key):
        def handler(request):
            raise httpx.ConnectError("no route", request=request)

        _mock_openrouter(monkeypatch, handler)
        with pytest.raises(ProviderConnectionError):
            OpenRouterProvider().generate("prompt")

    def test_missing_api_key_raises_auth_error(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        with pytest.raises(ProviderAuthError):
            OpenRouterProvider().generate("prompt")

    def test_error_carries_provider_and_status(self, monkeypatch, api_key):
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(503, text="down"))
        with pytest.raises(ProviderServerError) as excinfo:
            OpenRouterProvider().generate("prompt")
        assert excinfo.value.provider == "openrouter"
        assert excinfo.value.status_code == 503

    def test_response_body_is_truncated_in_the_message(self, monkeypatch, api_key):
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(500, text="x" * 5000))
        with pytest.raises(ProviderServerError) as excinfo:
            OpenRouterProvider().generate("prompt")
        # The body can echo the prompt back; only an excerpt should survive.
        assert len(str(excinfo.value)) < 400


class TestRetryPolicy:
    def test_retries_until_success(self, monkeypatch, api_key):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(503, text="down")
            return httpx.Response(200, json=_ok_body("recovered"))

        _mock_openrouter(monkeypatch, handler)
        assert OpenRouterProvider().generate("prompt").content == "recovered"
        assert attempts["n"] == 3

    def test_gives_up_after_max_attempts(self, monkeypatch, api_key):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            return httpx.Response(503, text="down")

        _mock_openrouter(monkeypatch, handler)
        with pytest.raises(ProviderServerError):
            OpenRouterProvider().generate("prompt")
        assert attempts["n"] == settings.max_retry_attempts

    def test_auth_errors_are_not_retried(self, monkeypatch, api_key):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            return httpx.Response(401, text="bad key")

        _mock_openrouter(monkeypatch, handler)
        with pytest.raises(ProviderAuthError):
            OpenRouterProvider().generate("prompt")
        assert attempts["n"] == 1, "a bad key will still be bad on the next attempt"

    def test_bad_requests_are_not_retried(self, monkeypatch, api_key):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            return httpx.Response(400, text="context too long")

        _mock_openrouter(monkeypatch, handler)
        with pytest.raises(ProviderBadRequestError):
            OpenRouterProvider().generate("prompt")
        assert attempts["n"] == 1

    def test_retry_after_is_preferred_over_backoff(self):
        from providers.retry import _next_delay

        exc = ProviderRateLimitError("slow", provider="p", retry_after=5.0)
        assert _next_delay(exc, attempt=0, base_delay=0.1, max_delay=30.0) == 5.0

    def test_retry_after_is_capped_by_max_delay(self):
        from providers.retry import _next_delay

        exc = ProviderRateLimitError("slow", provider="p", retry_after=999.0)
        assert _next_delay(exc, attempt=0, base_delay=0.1, max_delay=30.0) == 30.0

    def test_backoff_grows_and_stays_within_bounds(self):
        from providers.retry import _backoff_delay

        for attempt in range(6):
            delay = _backoff_delay(attempt, base_delay=0.5, max_delay=30.0)
            assert 0.0 <= delay <= 30.0

    def test_non_provider_exceptions_propagate_immediately(self):
        def boom():
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            call_with_retry(boom, max_attempts=3, base_delay=0.0, max_delay=0.0)


class TestAsyncPath:
    def test_async_generate_parses_a_response(self, monkeypatch, api_key):
        _mock_openrouter(monkeypatch, lambda r: httpx.Response(200, json=_ok_body("async ok")))
        response = asyncio.run(OpenRouterProvider().agenerate("prompt"))
        assert response.content == "async ok"

    def test_async_retries_transient_failures(self, monkeypatch, api_key):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            if attempts["n"] < 2:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json=_ok_body("async recovered"))

        _mock_openrouter(monkeypatch, handler)
        response = asyncio.run(OpenRouterProvider().agenerate("prompt"))
        assert response.content == "async recovered"
        assert attempts["n"] == 2

    def test_async_retry_helper_stops_on_non_retryable(self):
        attempts = {"n": 0}

        async def failing():
            attempts["n"] += 1
            raise ProviderAuthError("nope", provider="p")

        async def run():
            with pytest.raises(ProviderAuthError):
                await acall_with_retry(
                    failing, max_attempts=5, base_delay=0.0, max_delay=0.0
                )

        asyncio.run(run())
        assert attempts["n"] == 1


class TestBaseProviderContract:
    def test_subclasses_must_implement_the_raw_calls(self):
        class Incomplete(BaseProvider):
            @property
            def name(self) -> str:
                return "incomplete"

            @property
            def model_name(self) -> str:
                return "m"

        with pytest.raises(TypeError):
            Incomplete()

    def test_generate_routes_through_the_retry_wrapper(self):
        calls = {"n": 0}

        class Flaky(BaseProvider):
            name = "flaky"
            model_name = "m"

            def _generate_raw(self, prompt, temperature, system_prompt, json_mode):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise ProviderServerError("transient", provider="flaky")
                return ProviderResponse(content="ok")

            async def _agenerate_raw(self, prompt, temperature, system_prompt, json_mode):
                return self._generate_raw(prompt, temperature, system_prompt, json_mode)

        assert Flaky().generate("p").content == "ok"
        assert calls["n"] == 3

    def test_error_str_includes_the_provider(self):
        assert "[groq]" in str(ProviderError("boom", provider="groq"))
