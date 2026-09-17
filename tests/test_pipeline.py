"""Tests for the pipeline module.

Uses a mock provider to avoid actual API calls.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import utils
from models import ProviderResponse
from pipeline import (
    PROVIDER_REGISTRY,
    _chunk_cache_key,
    get_provider,
    process_transcript,
    register_provider,
)
from utils import cache_chunk_summary, clear_chunk_cache, get_cached_chunk_summary


class MockProvider:
    """A provider that returns predefined responses without calling any API."""

    def __init__(self, responses: dict[str, str] = None):
        self._responses = responses or {}
        self._call_count = 0
        self._model = "mock-model"

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        self._call_count += 1
        return ProviderResponse(
            content=self._responses.get("generate", "Mock summary response"),
            model=self._model,
            input_tokens=10,
            output_tokens=20,
            processing_time=0.01,
        )

    def generate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        self._call_count += 1
        return ProviderResponse(
            content=self._responses.get("generate_json", '{"title": "Mock Meeting", "participants": ["Alice"], "action_items": [], "deadlines": [], "decisions": []}'),
            model=self._model,
            input_tokens=10,
            output_tokens=20,
            processing_time=0.01,
        )


@pytest.fixture(autouse=True)
def _temp_db(monkeypatch, tmp_path):
    """Keep pipeline runs off the developer's real ``data/meetings.db``.

    ``process_transcript`` persists every meeting it builds, so without this the
    suite writes test fixtures into real application data.
    """
    import database

    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "pipeline_test.db"))
    database.init_db()


@pytest.fixture(autouse=True)
def _register_mock_provider():
    register_provider("mock", MockProvider)
    yield
    # Clean up after test
    PROVIDER_REGISTRY.pop("mock", None)


@pytest.fixture
def _mock_graph_json_response():
    """Patch the pipeline's knowledge graph prompt to return valid graph JSON."""
    graph_json = json.dumps({
        "entities": [
            {"id": "person-alice", "label": "Alice", "type": "person", "properties": {}},
            {"id": "task-fix", "label": "Fix bug", "type": "task", "properties": {"priority": "high"}},
        ],
        "relationships": [
            {"source": "person-alice", "target": "task-fix", "label": "assigns"},
        ],
    })
    return graph_json


class TestProcessTranscript:
    def test_empty_transcript(self):
        meeting = process_transcript("", provider_name="mock")
        assert meeting.title == "Empty Transcript"
        assert meeting.action_items == []
        assert meeting.deadlines == []
        assert meeting.decisions == []

    def test_whitespace_transcript(self):
        meeting = process_transcript("   \n\n  ", provider_name="mock")
        assert meeting.title == "Empty Transcript"

    def test_basic_transcript(self, _mock_graph_json_response):
        text = """Alice: Let's discuss the sprint.
Bob: I finished the authentication module.
Alice: Great, let's deploy it."""
        meeting = process_transcript(text, provider_name="mock")
        assert meeting.id
        assert meeting.provider == "mock"
        assert meeting.processing_time > 0

    def test_participant_detection(self):
        text = """Alice: Hello
Bob: Hi there
Charlie: Hey everyone"""
        meeting = process_transcript(text, provider_name="mock")
        detected = meeting.participants
        assert len(detected) > 0

    def test_provider_not_found(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            process_transcript("Hello", provider_name="nonexistent_provider")

    def test_get_provider_registered(self):
        provider = get_provider("mock")
        assert provider is not None
        assert provider.name == "mock"

    def test_get_provider_unregistered(self):
        if "nonexistent" in PROVIDER_REGISTRY:
            del PROVIDER_REGISTRY["nonexistent"]
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("nonexistent")


class TestProviderRegistration:
    def test_register_and_retrieve(self):
        register_provider("test_prov", MockProvider)
        provider = get_provider("test_prov")
        assert isinstance(provider, MockProvider)
        PROVIDER_REGISTRY.pop("test_prov", None)

    def test_register_overwrites(self):
        register_provider("test_overwrite", MockProvider)
        register_provider("test_overwrite", MockProvider)
        provider = get_provider("test_overwrite")
        assert isinstance(provider, MockProvider)
        PROVIDER_REGISTRY.pop("test_overwrite", None)


class TestChunkCacheKey:
    """The cache key must cover everything that can change a chunk summary.

    Keying on the chunk text alone meant that re-processing a transcript with a
    different provider returned the *previous* provider's summaries, while the
    meeting was recorded as having been produced by the new one.
    """

    def setup_method(self):
        clear_chunk_cache()

    def teardown_method(self):
        clear_chunk_cache()

    def test_provider_is_part_of_the_key(self):
        a = _chunk_cache_key("some text", "groq", "llama", 0.3)
        b = _chunk_cache_key("some text", "gemini", "llama", 0.3)
        assert a != b

    def test_model_is_part_of_the_key(self):
        a = _chunk_cache_key("some text", "groq", "llama-3.3", 0.3)
        b = _chunk_cache_key("some text", "groq", "llama-3.1", 0.3)
        assert a != b

    def test_temperature_is_part_of_the_key(self):
        a = _chunk_cache_key("some text", "groq", "llama", 0.3)
        b = _chunk_cache_key("some text", "groq", "llama", 0.9)
        assert a != b

    def test_prompt_version_is_part_of_the_key(self):
        key = _chunk_cache_key("some text", "groq", "llama", 0.3)
        with patch("pipeline.PROMPT_VERSION", "different-version"):
            assert _chunk_cache_key("some text", "groq", "llama", 0.3) != key

    def test_identical_context_hits_the_cache(self):
        args = ("some text", "groq", "llama", 0.3)
        assert _chunk_cache_key(*args) == _chunk_cache_key(*args)

    def test_switching_provider_does_not_reuse_summaries(self):
        text = "Alice: We shipped the release.\nBob: Nice work everyone."

        first = MockProvider({"generate": "SUMMARY FROM PROVIDER ONE"})
        second = MockProvider({"generate": "SUMMARY FROM PROVIDER TWO"})
        register_provider("prov_one", lambda: first)
        register_provider("prov_two", lambda: second)
        try:
            process_transcript(text, provider_name="prov_one")
            meeting = process_transcript(text, provider_name="prov_two")
            # The second run must call the second provider, not replay the first.
            assert "PROVIDER TWO" in meeting.summary.executive_summary
        finally:
            PROVIDER_REGISTRY.pop("prov_one", None)
            PROVIDER_REGISTRY.pop("prov_two", None)


class TestChunkCacheBounds:
    def test_cache_evicts_oldest_entries(self):
        clear_chunk_cache()
        try:
            for i in range(utils.CHUNK_CACHE_MAX_ENTRIES + 10):
                cache_chunk_summary(f"key-{i}", f"summary-{i}")
            assert len(utils._chunk_summary_cache) == utils.CHUNK_CACHE_MAX_ENTRIES
            assert get_cached_chunk_summary("key-0") is None
            last = utils.CHUNK_CACHE_MAX_ENTRIES + 9
            assert get_cached_chunk_summary(f"key-{last}") == f"summary-{last}"
        finally:
            clear_chunk_cache()
