"""Tests for the export formats."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from meeting_intelligence.exporters import (
    action_items_to_csv,
    deadlines_to_ics,
    export_filename,
    meetings_to_csv,
    parse_deadline_date,
    to_markdown,
)
from meeting_intelligence.models import ActionItem, Deadline, Decision, Meeting, MeetingListItem, Summary


@pytest.fixture
def meeting() -> Meeting:
    return Meeting(
        id="m1",
        title="Sprint Planning — Week 42",
        date="2026-07-20T10:00:00",
        participants=["Alice Chen", "Bob Martinez"],
        provider="gemini",
        model="gemini-flash-latest",
        summary=Summary(executive_summary="The team reviewed the sprint."),
        action_items=[
            ActionItem(id=1, owner="Bob", task="Security review", priority="high", status="open"),
            ActionItem(id=2, owner="Priya", task="Migration", priority="medium", status="done"),
        ],
        decisions=[Decision(id=1, decision="Keep 100 RPM", rationale="Monitor first")],
        deadlines=[
            Deadline(id=1, description="Security review", date="2026-07-24", type="explicit"),
            Deadline(id=2, description="Vague", date="next Thursday", type="relative"),
        ],
    )


class TestMarkdown:
    def test_includes_every_section(self, meeting):
        out = to_markdown(meeting)
        assert out.startswith("# Sprint Planning — Week 42")
        for heading in ("## Summary", "## Action Items", "## Decisions", "## Deadlines"):
            assert heading in out

    def test_status_becomes_a_task_checkbox(self, meeting):
        out = to_markdown(meeting)
        assert "- [ ] **Bob** — Security review" in out
        assert "- [x] **Priya** — Migration" in out

    def test_omits_empty_sections(self):
        bare = Meeting(id="m", title="Bare", date="2026-01-01")
        out = to_markdown(bare)
        assert "## Action Items" not in out
        assert "## Decisions" not in out

    def test_degraded_runs_say_so(self):
        partial = Meeting(
            id="m", title="Partial", date="2026-01-01", chunk_total=5, chunk_failures=2
        )
        assert "Incomplete" in to_markdown(partial)

    def test_complete_runs_carry_no_warning(self, meeting):
        assert "Incomplete" not in to_markdown(meeting)


class TestCsv:
    def test_one_row_per_action_item_plus_header(self, meeting):
        rows = list(csv.reader(io.StringIO(action_items_to_csv([meeting]))))
        assert rows[0][0] == "meeting_id"
        assert len(rows) == 3

    def test_fields_land_in_the_right_columns(self, meeting):
        rows = list(csv.DictReader(io.StringIO(action_items_to_csv([meeting]))))
        assert rows[0]["owner"] == "Bob"
        assert rows[0]["task"] == "Security review"
        assert rows[0]["priority"] == "high"
        assert rows[0]["meeting_title"] == "Sprint Planning — Week 42"

    def test_commas_and_quotes_are_escaped(self):
        tricky = Meeting(
            id="m",
            title='He said "hello", then left',
            date="2026-01-01",
            action_items=[ActionItem(task='Do A, B and "C"')],
        )
        rows = list(csv.DictReader(io.StringIO(action_items_to_csv([tricky]))))
        assert rows[0]["task"] == 'Do A, B and "C"'
        assert rows[0]["meeting_title"] == 'He said "hello", then left'

    def test_meetings_index_marks_degraded_runs(self):
        items = [
            MeetingListItem(id="a", title="Fine", date="2026-01-01"),
            MeetingListItem(id="b", title="Partial", date="2026-01-01", chunk_failures=2, chunk_total=4),
        ]
        rows = list(csv.DictReader(io.StringIO(meetings_to_csv(items))))
        assert rows[0]["degraded"] == "no"
        assert rows[1]["degraded"] == "yes"

    def test_meeting_with_no_action_items_yields_only_a_header(self):
        bare = Meeting(id="m", title="Bare", date="2026-01-01")
        assert len(list(csv.reader(io.StringIO(action_items_to_csv([bare]))))) == 1


class TestIcs:
    def test_structure_is_wellformed(self, meeting):
        out = deadlines_to_ics([meeting])
        assert out.startswith("BEGIN:VCALENDAR")
        assert out.rstrip().endswith("END:VCALENDAR")
        assert out.count("BEGIN:VEVENT") == out.count("END:VEVENT")

    def test_only_parseable_dates_become_events(self, meeting):
        out = deadlines_to_ics([meeting])
        # "next Thursday" cannot be resolved; an event on the wrong day would
        # be worse than no event.
        assert out.count("BEGIN:VEVENT") == 1
        assert "Security review" in out
        assert "Vague" not in out

    def test_all_day_event_end_is_exclusive(self, meeting):
        out = deadlines_to_ics([meeting])
        assert "DTSTART;VALUE=DATE:20260724" in out
        assert "DTEND;VALUE=DATE:20260725" in out

    def test_uses_crlf_line_endings(self, meeting):
        # RFC 5545 requires CRLF; some clients reject bare LF.
        assert "\r\n" in deadlines_to_ics([meeting])

    def test_special_characters_are_escaped(self):
        m = Meeting(
            id="m",
            title="T",
            date="2026-01-01",
            deadlines=[Deadline(id=1, description="Ship A, B; then C", date="2026-03-01")],
        )
        out = deadlines_to_ics([m])
        assert r"Ship A\, B\; then C" in out

    def test_long_lines_are_folded_to_75_octets(self):
        m = Meeting(
            id="m",
            title="T",
            date="2026-01-01",
            deadlines=[Deadline(id=1, description="x" * 300, date="2026-03-01")],
        )
        for line in deadlines_to_ics([m]).split("\r\n"):
            assert len(line.encode("utf-8")) <= 75

    @pytest.mark.parametrize("filler", ["x", "é", "🎯", "日"])
    def test_folded_lines_unfold_back_to_the_original(self, filler):
        """Folding must be reversible, per RFC 5545's unfolding rule.

        An earlier byte-based implementation could split a character across
        the fold, and could fail to advance at all -- an infinite loop.
        """
        from meeting_intelligence.exporters import _fold_ics

        original = "SUMMARY:" + filler * 200
        folded = _fold_ics(original)
        lines = folded.split("\r\n")
        assert all(len(line.encode("utf-8")) <= 75 for line in lines)
        unfolded = lines[0] + "".join(line[1:] for line in lines[1:])
        assert unfolded == original

    def test_folding_preserves_multibyte_characters(self):
        m = Meeting(
            id="m",
            title="T",
            date="2026-01-01",
            deadlines=[Deadline(id=1, description="é" * 200, date="2026-03-01")],
        )
        out = deadlines_to_ics([m])
        # A split mid-character would make this undecodable.
        assert out.encode("utf-8").decode("utf-8")
        assert "é" in out

    def test_empty_input_is_still_a_valid_calendar(self):
        out = deadlines_to_ics([])
        assert "BEGIN:VCALENDAR" in out and "END:VCALENDAR" in out
        assert "BEGIN:VEVENT" not in out


class TestDateParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-07-24", date(2026, 7, 24)),
            ("Due 2026-07-24 EOD", date(2026, 7, 24)),
            ("July 24, 2026", date(2026, 7, 24)),
            ("24 July 2026", date(2026, 7, 24)),
        ],
    )
    def test_parses_real_dates(self, value, expected):
        assert parse_deadline_date(value) == expected

    @pytest.mark.parametrize("value", ["next Thursday", "soon", "", "end of sprint", "2026-13-45"])
    def test_rejects_what_it_cannot_resolve(self, value):
        assert parse_deadline_date(value) is None


class TestFilenames:
    def test_is_descriptive_and_safe(self, meeting):
        assert export_filename(meeting, "md") == "2026-07-20-sprint-planning-week-42.md"

    def test_strips_path_separators(self):
        m = Meeting(id="m", title="../../etc/passwd", date="2026-01-01")
        name = export_filename(m, "csv")
        assert "/" not in name and ".." not in name

    def test_falls_back_when_the_title_has_no_usable_characters(self):
        m = Meeting(id="m", title="!!!", date="2026-01-01")
        assert export_filename(m, "md") == "2026-01-01-meeting.md"
