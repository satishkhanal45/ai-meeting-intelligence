"""Tests for the database layer.

Uses an in-memory SQLite database to avoid side effects.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest

from meeting_intelligence.database import (
    SEARCHABLE_COLUMNS,
    delete_meeting,
    get_all_participants,
    get_full_meeting,
    get_meeting_count,
    get_meeting_list,
    get_meeting_metadata,
    init_db,
    insert_meeting,
    search_meetings,
)
from meeting_intelligence.models import (
    ActionItem,
    Deadline,
    Decision,
    GraphData,
    Meeting,
    Summary,
    Transcript,
)


@pytest.fixture(autouse=True)
def _temp_db(monkeypatch, tmp_path):
    """Point the database layer at a throwaway file for the duration of a test.

    ``:memory:`` cannot be used here: the module opens a new connection per
    call, and every ``sqlite3.connect(":memory:")`` returns a *different* empty
    database, so nothing written by one call is visible to the next. A file in
    ``tmp_path`` gives real isolation without touching ``data/meetings.db``.
    """
    db_file = tmp_path / "test_meetings.db"
    monkeypatch.setattr("meeting_intelligence.database.DB_PATH", str(db_file))
    init_db()
    yield db_file


@pytest.fixture
def _override_connection(_temp_db):
    """Yield a factory for raw connections to the test database."""

    @contextmanager
    def _connect():
        conn = sqlite3.connect(str(_temp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    return _connect


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
    def test_tables_created(self, _override_connection):
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


class TestSearchRegressions:
    """Regressions for the search layer.

    ``search_meetings`` used to interpolate a table alias that was never
    declared in the FROM clause, so every non-empty query raised
    ``sqlite3.OperationalError`` and the endpoint returned HTTP 500.
    """

    @pytest.mark.parametrize(
        "query",
        ["Sprint Planning", "Alice", "Fix bug", "Use Stripe", "EOW"],
    )
    def test_every_searchable_column_matches(self, query):
        insert_meeting(_create_test_meeting())
        results = search_meetings(query)
        assert [m.id for m in results] == ["test-001"]

    def test_search_does_not_raise_on_child_tables(self):
        insert_meeting(_create_test_meeting())
        # Exercises each configured (table, column) pair, including the child
        # tables that previously produced "no such column: ai.task".
        for _table, _column in SEARCHABLE_COLUMNS:
            assert isinstance(search_meetings("anything"), list)


class TestReinsertIsIdempotent:
    """Saving the same meeting twice must not duplicate its child rows."""

    def test_children_are_not_duplicated(self):
        meeting = _create_test_meeting()
        insert_meeting(meeting)
        insert_meeting(meeting)

        stored = get_full_meeting("test-001")
        assert len(stored.action_items) == 1
        assert len(stored.deadlines) == 1
        assert len(stored.decisions) == 1

    def test_counts_in_list_view_stay_correct(self):
        meeting = _create_test_meeting()
        insert_meeting(meeting)
        insert_meeting(meeting)

        item = next(m for m in get_meeting_list() if m.id == "test-001")
        assert item.action_item_count == 1
        assert item.decision_count == 1

    def test_reinsert_updates_child_content(self):
        insert_meeting(_create_test_meeting())
        updated = _create_test_meeting(
            {"action_items": [ActionItem(owner="Bob", task="Ship release", priority="low")]}
        )
        insert_meeting(updated)

        stored = get_full_meeting("test-001")
        assert [i.task for i in stored.action_items] == ["Ship release"]
