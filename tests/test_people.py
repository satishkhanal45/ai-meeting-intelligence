"""Tests for the people projection and cross-meeting views.

Participants live in meetings.participants as a JSON blob, which is what the
pipeline produced. These tables are a queryable projection of it: without
them, "every meeting Alice attended" means deserialising every row.
"""

from __future__ import annotations

import pytest

from meeting_intelligence import database
from meeting_intelligence.database import (
    get_person,
    insert_meeting,
    list_action_items,
    list_deadlines,
    list_people,
)
from meeting_intelligence.models import ActionItem, Deadline, Meeting


@pytest.fixture(autouse=True)
def _temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr("meeting_intelligence.database.DB_PATH", str(tmp_path / "people.db"))
    database.init_db()


def _seed():
    insert_meeting(
        Meeting(
            id="m1",
            title="Sprint 1",
            date="2026-01-01",
            participants=["Alice Chen", "Bob Martinez"],
            action_items=[
                ActionItem(owner="Alice Chen", task="Ship it", status="open", priority="high"),
                ActionItem(owner="Bob Martinez", task="Review", status="done"),
            ],
            deadlines=[Deadline(description="Launch", date="2026-02-01")],
        )
    )
    insert_meeting(
        Meeting(
            id="m2",
            title="Sprint 2",
            date="2026-01-08",
            participants=["Alice Chen", "Carol Diaz"],
            action_items=[
                ActionItem(owner="Alice Chen", task="Deploy", status="in_progress"),
            ],
        )
    )


class TestPeopleProjection:
    def test_people_are_discovered_from_participants(self):
        _seed()
        names = {p.name for p in list_people()}
        assert names == {"Alice Chen", "Bob Martinez", "Carol Diaz"}

    def test_counts_span_meetings(self):
        _seed()
        alice = next(p for p in list_people() if p.name == "Alice Chen")
        assert alice.meeting_count == 2
        assert alice.action_item_count == 2
        assert alice.open_action_item_count == 2

    def test_done_items_are_not_counted_as_open(self):
        _seed()
        bob = next(p for p in list_people() if p.name == "Bob Martinez")
        assert bob.action_item_count == 1
        assert bob.open_action_item_count == 0

    def test_a_person_with_no_items_still_appears(self):
        _seed()
        carol = next(p for p in list_people() if p.name == "Carol Diaz")
        assert carol.meeting_count == 1
        assert carol.action_item_count == 0

    def test_the_same_person_is_not_duplicated_by_case_or_spacing(self):
        insert_meeting(
            Meeting(id="a", title="A", date="2026-01-01", participants=["Alice Chen"])
        )
        insert_meeting(
            Meeting(id="b", title="B", date="2026-01-02", participants=["alice  chen"])
        )
        people = list_people()
        assert len(people) == 1
        assert people[0].meeting_count == 2

    def test_person_detail_links_meetings_and_items(self):
        _seed()
        alice = next(p for p in list_people() if p.name == "Alice Chen")
        detail = get_person(alice.id)
        assert {m.id for m in detail.meetings} == {"m1", "m2"}
        assert {i.task for i in detail.action_items} == {"Ship it", "Deploy"}
        assert all(i.meeting_title for i in detail.action_items)
        assert detail.open_action_items == 2

    def test_unknown_person_is_none(self):
        assert get_person("nope") is None

    def test_reprocessing_a_meeting_does_not_duplicate_links(self):
        _seed()
        _seed()  # same ids, re-saved
        alice = next(p for p in list_people() if p.name == "Alice Chen")
        assert alice.meeting_count == 2

    def test_removing_a_participant_updates_the_projection(self):
        _seed()
        insert_meeting(
            Meeting(id="m2", title="Sprint 2", date="2026-01-08", participants=["Alice Chen"])
        )
        assert "Carol Diaz" not in {p.name for p in list_people()}

    def test_deleting_a_meeting_drops_its_links(self):
        _seed()
        database.delete_meeting("m2")
        alice = next(p for p in list_people() if p.name == "Alice Chen")
        assert alice.meeting_count == 1

    def test_backfill_indexes_meetings_written_before_the_tables_existed(self):
        _seed()
        # Simulate a database written by an older build: drop the projection
        # and confirm startup rebuilds it.
        with database.get_transaction() as conn:
            conn.execute("DELETE FROM meeting_participants")
        database.rebuild_people_index()
        assert {p.name for p in list_people()} >= {"Alice Chen", "Bob Martinez"}


class TestCrossMeetingViews:
    def test_action_items_span_meetings(self):
        _seed()
        items = list_action_items()
        assert {i.task for i in items} == {"Ship it", "Review", "Deploy"}
        assert all(i.meeting_title for i in items)

    def test_filter_by_status(self):
        _seed()
        assert {i.task for i in list_action_items(status="open")} == {"Ship it"}
        assert {i.task for i in list_action_items(status="done")} == {"Review"}

    def test_filter_by_owner_ignores_case(self):
        _seed()
        assert {i.task for i in list_action_items(owner="ALICE CHEN")} == {"Ship it", "Deploy"}

    def test_open_and_high_priority_items_sort_first(self):
        _seed()
        statuses = [i.status for i in list_action_items()]
        assert statuses.index("open") < statuses.index("done")

    def test_deadlines_carry_their_meeting(self):
        _seed()
        deadlines = list_deadlines()
        assert len(deadlines) == 1
        assert deadlines[0].meeting_title == "Sprint 1"

    def test_pagination(self):
        _seed()
        assert len(list_action_items(limit=1)) == 1
        assert len(list_action_items(limit=1, offset=1)) == 1
