"""Tests for the database layer.

Uses an in-memory SQLite database to avoid side effects.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest

from database import (
    get_connection,
    get_transaction,
    init_db,
    insert_meeting,
    get_meeting_metadata,
    get_meeting_list,
    get_full_meeting,
    delete_meeting,
    search_meetings,
    get_meeting_count,
    get_all_participants,
)
from models import (
    ActionItem,
    Deadline,
    Decision,
    GraphData,
    Meeting,
    Summary,
    Transcript,
)


@pytest.fixture(autouse=True)
def _in_memory_db(monkeypatch):
    """Override DB_PATH to use an in-memory database for tests."""
    monkeypatch.setattr("database.DB_PATH", ":memory:")
    monkeypatch.setattr("config.DB_PATH", ":memory:")
    init_db()


@contextmanager
def _override_connection():
    """Helper to yield a connection to the in-memory DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _create_test_meeting(overrides: dict = None) -> Meeting:
    data = dict(
        id="test-001",
        title="Sprint Planning",
        date="2026-07-20T10:00:00",
        participants=["Alice", "Bob"],
        provider="gemini",
        processing_time=5.0,
        transcript=Transcript(raw_text="raw", cleaned_text="cleaned"),
        summary=Summary(executive_summary="Summary text"),
        action_items=[ActionItem(owner="Alice", task="Fix bug", priority="high")],
        deadlines=[Deadline(description="EOW", date="2026-07-25")],
        decisions=[Decision(decision="Use Stripe", rationale="Better API")],
        graph_data=GraphData(graph_json=json.dumps({"entities": [], "relationships": []})),
    )
    if overrides:
        data.update(overrides)
    return Meeting(**data)


class TestInitDB:
    def test_tables_created(self):
        with _override_connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [r["name"] for r in tables]
            assert "meetings" in table_names
            assert "transcripts" in table_names
            assert "summaries" in table_names
            assert "action_items" in table_names
            assert "deadlines" in table_names
            assert "decisions" in table_names
            assert "graph_data" in table_names


class TestInsertAndRetrieve:
    def test_insert_and_get_metadata(self):
        meeting = _create_test_meeting()
        insert_meeting(meeting)
        meta = get_meeting_metadata("test-001")
        assert meta is not None
        assert meta.title == "Sprint Planning"
        assert meta.provider == "gemini"

    def test_get_full_meeting(self):
        meeting = _create_test_meeting()
        insert_meeting(meeting)
        retrieved = get_full_meeting("test-001")
        assert retrieved is not None
        assert retrieved.title == "Sprint Planning"
        assert len(retrieved.action_items) == 1
        assert retrieved.action_items[0].owner == "Alice"
        assert len(retrieved.deadlines) == 1
        assert len(retrieved.decisions) == 1

    def test_get_nonexistent(self):
        assert get_meeting_metadata("nonexistent") is None
        assert get_full_meeting("nonexistent") is None

    def test_meeting_list(self):
        m1 = _create_test_meeting({"id": "m1", "title": "Meeting 1"})
        m2 = _create_test_meeting({"id": "m2", "title": "Meeting 2"})
        insert_meeting(m1)
        insert_meeting(m2)
        items = get_meeting_list()
        assert len(items) == 2


class TestDelete:
    def test_delete_existing(self):
        meeting = _create_test_meeting()
        insert_meeting(meeting)
        assert delete_meeting("test-001") is True
        assert get_meeting_metadata("test-001") is None

    def test_delete_nonexistent(self):
        assert delete_meeting("nonexistent") is False

    def test_cascade_delete(self):
        meeting = _create_test_meeting()
        insert_meeting(meeting)
        delete_meeting("test-001")
        retrieved = get_full_meeting("test-001")
        assert retrieved is None


class TestSearch:
    def test_search_by_title(self):
        insert_meeting(_create_test_meeting({"id": "m1", "title": "Sprint Planning"}))
        results = search_meetings("Sprint")
        assert len(results) >= 1

    def test_search_by_participant(self):
        insert_meeting(_create_test_meeting({"id": "m2", "participants": ["Alice", "Bob"]}))
        results = search_meetings("Alice")
        assert len(results) >= 1

    def test_search_by_task(self):
        meeting = _create_test_meeting({
            "id": "m3",
            "action_items": [ActionItem(owner="Alice", task="Deploy to production")],
        })
        insert_meeting(meeting)
        results = search_meetings("production")
        assert len(results) >= 1

    def test_search_empty_query(self):
        insert_meeting(_create_test_meeting({"id": "m4"}))
        results = search_meetings("")
        assert len(results) >= 1

    def test_search_no_matches(self):
        results = search_meetings("nonexistent keyword xyz")
        assert results == []


class TestCountAndParticipants:
    def test_meeting_count(self):
        assert get_meeting_count() == 0
        insert_meeting(_create_test_meeting({"id": "c1"}))
        assert get_meeting_count() == 1

    def test_all_participants(self):
        insert_meeting(_create_test_meeting({"id": "p1", "participants": ["Alice"]}))
        insert_meeting(_create_test_meeting({"id": "p2", "participants": ["Bob", "Alice"]}))
        participants = get_all_participants()
        assert "Alice" in participants
        assert "Bob" in participants
