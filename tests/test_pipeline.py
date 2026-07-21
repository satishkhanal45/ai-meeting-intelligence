"""Tests for the pipeline module.

Uses a mock provider to avoid actual API calls.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from models import ProviderResponse
from pipeline import PROVIDER_REGISTRY, get_provider, process_transcript, register_provider


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
