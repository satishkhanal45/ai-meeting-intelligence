"""Tests for Pydantic data models."""

from __future__ import annotations

import json

from models import (
    ActionItem,
    Deadline,
    Decision,
    GraphData,
    GraphEntity,
    GraphRelationship,
    Meeting,
    MeetingListItem,
    MeetingMetadata,
    Summary,
    Transcript,
)


class TestActionItem:
    def test_defaults(self):
        item = ActionItem()
        assert item.owner == ""
        assert item.task == ""
        assert item.priority == "medium"
        assert item.status == "open"

    def test_full_construction(self):
        item = ActionItem(owner="Alice", task="Deploy fix", priority="high", status="in_progress")
        assert item.owner == "Alice"
        assert item.task == "Deploy fix"


class TestDeadline:
    def test_defaults(self):
        dl = Deadline()
        assert dl.description == ""
        assert dl.date == ""
        assert dl.type == "explicit"

    def test_milestone_type(self):
        dl = Deadline(description="Launch", date="Q4 2026", type="milestone")
        assert dl.type == "milestone"


class TestDecision:
    def test_defaults(self):
        dec = Decision()
        assert dec.decision == ""
        assert dec.rationale == ""

    def test_with_rationale(self):
        dec = Decision(decision="Use Stripe", rationale="Better API")
        assert dec.decision == "Use Stripe"


class TestGraphEntity:
    def test_default_type(self):
        e = GraphEntity(id="e1", label="Test")
        assert e.type == "information"

    def test_person_type(self):
        e = GraphEntity(id="p1", label="Alice", type="person")
        assert e.type == "person"

    def test_properties(self):
        e = GraphEntity(id="t1", label="Task", type="task", properties={"priority": "high"})
        assert e.properties["priority"] == "high"


class TestGraphRelationship:
    def test_default_label(self):
        r = GraphRelationship(source="a", target="b")
        assert r.label == "related_to"

    def test_custom_label(self):
        r = GraphRelationship(source="a", target="b", label="assigns")
        assert r.label == "assigns"


class TestGraphData:
    def test_empty(self):
        gd = GraphData()
        assert gd.entities == []
        assert gd.relationships == []

    def test_from_json(self):
        data = {
            "entities": [{"id": "p1", "label": "Alice", "type": "person"}],
            "relationships": [{"source": "p1", "target": "t1", "label": "owns"}],
        }
        gd = GraphData(graph_json=json.dumps(data))
        assert len(gd.entities) == 1
        assert gd.entities[0].label == "Alice"
        assert len(gd.relationships) == 1

    def test_malformed_json(self):
        gd = GraphData(graph_json="{invalid")
        assert gd.entities == []
        assert gd.relationships == []


class TestMeeting:
    def test_defaults(self):
        m = Meeting(id="m1", title="Test", date="2026-01-01")
        assert m.participants == []
        assert m.action_items == []
        assert m.deadlines == []
        assert m.decisions == []
        assert m.processing_time == 0.0

    def test_full_meeting(self):
        m = Meeting(
            id="m1",
            title="Sprint Planning",
            date="2026-07-20",
            participants=["Alice", "Bob"],
            provider="gemini",
            processing_time=10.0,
            transcript=Transcript(raw_text="hello", cleaned_text="hello"),
            summary=Summary(executive_summary="Summary text"),
            action_items=[ActionItem(owner="Alice", task="Fix bug")],
            deadlines=[Deadline(description="EOW", date="2026-07-25")],
            decisions=[Decision(decision="Go with Stripe")],
        )
        assert m.title == "Sprint Planning"
        assert len(m.action_items) == 1
        assert m.action_items[0].owner == "Alice"


class TestMeetingMetadata:
    def test_construction(self):
        mm = MeetingMetadata(
            id="m1", title="Test", date="2026-01-01",
            participants=["Alice"], provider="gemini",
            processing_time=5.0, created_at="2026-01-01T00:00:00",
        )
        assert mm.id == "m1"
        assert mm.provider == "gemini"


class TestMeetingListItem:
    def test_counts(self):
        mli = MeetingListItem(
            id="m1", title="Test", date="2026-01-01",
            action_item_count=3, decision_count=2,
        )
        assert mli.action_item_count == 3
        assert mli.decision_count == 2
