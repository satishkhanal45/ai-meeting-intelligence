"""Tests for the prompt templates."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from meeting_intelligence.prompts import (
    PROMPT_VERSION,
    _weekday_name,
    chunk_summary,
    extract_structured,
    knowledge_graph,
    merge_summaries,
)


class TestDateGrounding:
    """Relative deadlines are only resolvable if the model is given an anchor.

    Without one, "next Thursday" stays as free text, which cannot be placed on
    a timeline or exported to a calendar. Just over half the deadlines in the
    existing archive were unusable for this reason.
    """

    def test_system_prompt_demands_absolute_dates(self):
        system, _ = extract_structured("summary", "2026-07-20T10:00:00")
        assert "YYYY-MM-DD" in system
        assert "do not copy them" in system

    def test_meeting_date_is_the_primary_anchor(self):
        system, user = extract_structured("summary", "2026-07-20T10:00:00")
        # A recording processed months later must not have its deadlines
        # pulled forward to the upload date.
        assert "date the meeting itself states" in system
        assert "fallback anchor only" in user

    def test_processing_date_is_offered_as_a_fallback(self):
        system, _ = extract_structured("summary", "2026-07-20T10:00:00")
        assert "2026-07-20" in system
        assert "Monday" in system

    def test_defaults_to_today_when_no_date_is_supplied(self):
        system, _ = extract_structured("summary")
        assert datetime.now(timezone.utc).strftime("%Y-%m-%d") in system

    def test_schema_asks_for_an_iso_date(self):
        _, user = extract_structured("summary", "2026-07-20")
        assert '"date": "YYYY-MM-DD"' in user
        # The old schema invited free text, which is what produced "Thursday".
        assert "or relative text" not in user

    @pytest.mark.parametrize(
        ("iso", "expected"),
        [
            ("2026-07-20", "Monday"),
            ("2026-07-24", "Friday"),
            ("2026-07-25T12:00:00", "Saturday"),
        ],
    )
    def test_weekday_names(self, iso, expected):
        assert _weekday_name(iso) == expected

    def test_unparseable_dates_do_not_raise(self):
        assert _weekday_name("not a date") == "unknown weekday"
        assert _weekday_name("") == "unknown weekday"


class TestPromptContracts:
    def test_every_prompt_returns_a_system_and_user_pair(self):
        for system, user in (
            chunk_summary("text", 0, 3),
            merge_summaries(["a", "b"]),
            extract_structured("summary"),
            knowledge_graph("summary", "{}"),
        ):
            assert system and user
            assert isinstance(system, str) and isinstance(user, str)

    def test_chunk_prompt_states_its_position(self):
        _, user = chunk_summary("text", 2, 7)
        assert "3 of 7" in user

    def test_json_prompts_forbid_code_fences(self):
        for system, _ in (extract_structured("s"), knowledge_graph("s", "{}")):
            assert "code fences" in system

    def test_prompt_version_is_set(self):
        # It participates in the summary cache key, so a change here must
        # invalidate previously cached output.
        assert PROMPT_VERSION
