"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_intelligence.models import (
    ActionItem,
    Deadline,
    Decision,
    GraphData,
    Meeting,
    Summary,
    Transcript,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_transcript() -> str:
    # Resolve from this file, not the working directory, so the suite passes
    # regardless of where pytest is invoked from.
    return (PROJECT_ROOT / "meetings" / "sample_transcript.txt").read_text()


@pytest.fixture
def sample_cleaned_transcript() -> str:
    return """Alice: Let's start the sprint planning.
Bob: I completed the authentication refactor.
Priya: Great work Bob. I'll set up the security review."""


@pytest.fixture
def sample_meeting() -> Meeting:
    return Meeting(
        id="test-meeting-001",
        title="Sprint Planning — Week 42",
        date="2026-07-20T10:00:00",
        participants=["Alice Chen", "Bob Martinez", "Priya Sharma"],
        provider="gemini",
        processing_time=12.5,
        transcript=Transcript(
            raw_text="Alice: Hello everyone",
            cleaned_text="Alice: Hello everyone",
        ),
        summary=Summary(executive_summary="Team discussed sprint goals."),
        action_items=[
            ActionItem(owner="Bob", task="Security review", priority="high", status="open"),
            ActionItem(owner="Priya", task="Database migration", priority="medium", status="done"),
        ],
        deadlines=[
            Deadline(description="Security review", date="2026-07-24", type="explicit"),
        ],
        decisions=[
            Decision(decision="Rate limit at 100 RPM", rationale="Monitoring first"),
        ],
        graph_data=GraphData(
            graph_json=json.dumps({
                "entities": [
                    {"id": "person-alice", "label": "Alice Chen", "type": "person", "properties": {}},
                    {"id": "task-review", "label": "Security review", "type": "task", "properties": {"priority": "high"}},
                ],
                "relationships": [
                    {"source": "person-alice", "target": "task-review", "label": "assigns"},
                ],
            })
        ),
    )


@pytest.fixture
def sample_graph_data() -> GraphData:
    return GraphData(
        graph_json=json.dumps({
            "entities": [
                {"id": "person-alice", "label": "Alice Chen", "type": "person", "properties": {}},
                {"id": "person-bob", "label": "Bob Martinez", "type": "person", "properties": {}},
                {"id": "task-review", "label": "Security review", "type": "task", "properties": {"priority": "high", "status": "open"}},
                {"id": "deadline-thu", "label": "Thursday", "type": "deadline", "properties": {"date": "2026-07-24"}},
            ],
            "relationships": [
                {"source": "person-alice", "target": "task-review", "label": "assigns"},
                {"source": "person-bob", "target": "task-review", "label": "owns"},
                {"source": "task-review", "target": "deadline-thu", "label": "due_on"},
            ],
        })
    )


@pytest.fixture
def mock_provider_response():
    return {
        "content": "Test response content",
        "model": "test-model",
        "input_tokens": 50,
        "output_tokens": 100,
        "processing_time": 0.5,
    }
