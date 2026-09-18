"""Export meetings to portable formats.

A meeting's value is mostly in what happens after it, which means getting the
summary and action items out of this tool and into an email, a ticket or a
calendar. Everything here is pure: a Meeting in, a string in, so exports are
trivially testable and need no network or filesystem.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from meeting_intelligence.models import Meeting, MeetingListItem

_ICS_ESCAPES = {"\\": "\\\\", ";": r"\;", ",": r"\,", "\n": r"\n"}

_STATUS_MARKS = {"done": "x", "cancelled": "-"}


def _escape_ics(value: str) -> str:
    """Escape a value for an iCalendar property (RFC 5545 §3.3.11)."""
    return "".join(_ICS_ESCAPES.get(ch, ch) for ch in value or "")


def _fold_ics(line: str) -> str:
    """Fold a line to 75 octets, as RFC 5545 requires.

    Unfolded long lines are the most common reason an .ics file is rejected.
    Continuation lines begin with a single space, which consumes one octet of
    the 75, so they carry 74 of content.

    Folding walks characters rather than bytes: splitting a byte sequence
    mid-character produces undecodable output, and backing off byte by byte
    can fail to advance at all.
    """
    if len(line.encode("utf-8")) <= 75:
        return line

    chunks: list[str] = []
    current = ""
    budget = 75
    for char in line:
        char_width = len(char.encode("utf-8"))
        if len(current.encode("utf-8")) + char_width > budget:
            chunks.append(current)
            current = ""
            budget = 74  # a leading space is added to continuation lines
        current += char
    if current:
        chunks.append(current)

    return "\r\n".join(
        chunk if index == 0 else " " + chunk for index, chunk in enumerate(chunks)
    )


def _slugify(value: str, fallback: str = "meeting") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower()
    return slug[:60] or fallback


def export_filename(meeting: Meeting, extension: str) -> str:
    """A descriptive, filesystem-safe download name."""
    day = (meeting.date or "")[:10] or date.today().isoformat()
    return f"{day}-{_slugify(meeting.title)}.{extension}"


# ── Markdown ────────────────────────────────────────────────────────────


def to_markdown(meeting: Meeting) -> str:
    """Render a meeting as Markdown, suitable for pasting into a wiki or email."""
    lines: list[str] = [f"# {meeting.title}", ""]

    meta = [f"**Date:** {meeting.date}"]
    if meeting.participants:
        meta.append(f"**Participants:** {', '.join(meeting.participants)}")
    if meeting.provider:
        model = f" ({meeting.model})" if meeting.model else ""
        meta.append(f"**Processed by:** {meeting.provider}{model}")
    lines.extend(meta)

    if meeting.degraded:
        lines += [
            "",
            f"> **Incomplete:** {meeting.chunk_failures} of {meeting.chunk_total} "
            "transcript segments could not be summarised, so this summary is partial.",
        ]

    if meeting.summary.executive_summary:
        lines += ["", "## Summary", "", meeting.summary.executive_summary]

    if meeting.action_items:
        lines += ["", "## Action Items", ""]
        for item in meeting.action_items:
            mark = _STATUS_MARKS.get(item.status, " ")
            owner = f"**{item.owner}** — " if item.owner else ""
            detail = f" _({item.priority} priority, {item.status})_"
            lines.append(f"- [{mark}] {owner}{item.task}{detail}")

    if meeting.decisions:
        lines += ["", "## Decisions", ""]
        for decision in meeting.decisions:
            lines.append(f"- **{decision.decision}**")
            if decision.rationale:
                lines.append(f"  - _Rationale:_ {decision.rationale}")

    if meeting.deadlines:
        lines += ["", "## Deadlines", ""]
        for deadline in meeting.deadlines:
            when = f" — {deadline.date}" if deadline.date else ""
            lines.append(f"- {deadline.description}{when} _({deadline.type})_")

    lines.append("")
    return "\n".join(lines)


# ── CSV ─────────────────────────────────────────────────────────────────


def action_items_to_csv(meetings: Iterable[Meeting]) -> str:
    """Flatten action items across meetings, for a spreadsheet or tracker import."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["meeting_id", "meeting_title", "meeting_date", "owner", "task", "priority", "status"]
    )
    for meeting in meetings:
        for item in meeting.action_items:
            writer.writerow(
                [
                    meeting.id,
                    meeting.title,
                    meeting.date,
                    item.owner,
                    item.task,
                    item.priority,
                    item.status,
                ]
            )
    return buffer.getvalue()


def meetings_to_csv(items: Iterable[MeetingListItem]) -> str:
    """One row per meeting, for reporting."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["id", "title", "date", "participants", "provider", "action_items", "decisions", "degraded"]
    )
    for item in items:
        writer.writerow(
            [
                item.id,
                item.title,
                item.date,
                "; ".join(item.participants),
                item.provider,
                item.action_item_count,
                item.decision_count,
                "yes" if item.degraded else "no",
            ]
        )
    return buffer.getvalue()


# ── iCalendar ───────────────────────────────────────────────────────────

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_deadline_date(value: str) -> Optional[date]:
    """Best-effort date parsing for a deadline.

    Deadlines are whatever the model produced: an ISO date, or free text like
    "next Thursday". Only genuine dates become calendar events; the rest are
    skipped rather than guessed at.
    """
    if not value:
        return None
    match = _ISO_DATE.search(value)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def deadlines_to_ics(meetings: Iterable[Meeting], calendar_name: str = "Meeting Deadlines") -> str:
    """Render dated deadlines as an iCalendar feed.

    Each deadline becomes an all-day VEVENT. Deadlines whose date could not be
    parsed are omitted: a calendar entry on the wrong day is worse than none.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Meeting Intelligence//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape_ics(calendar_name)}",
    ]
    stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")

    for meeting in meetings:
        for index, deadline in enumerate(meeting.deadlines):
            when = parse_deadline_date(deadline.date)
            if when is None:
                continue
            uid = f"{meeting.id}-{deadline.id or index}@ai-meeting-intelligence"
            description = f"From meeting: {meeting.title}"
            if deadline.type:
                description += f" ({deadline.type} deadline)"
            lines += [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{when.strftime('%Y%m%d')}",
                # DTEND is exclusive for all-day events, so add a day.
                f"DTEND;VALUE=DATE:{(when + timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:{_escape_ics(deadline.description or 'Deadline')}",
                f"DESCRIPTION:{_escape_ics(description)}",
                "END:VEVENT",
            ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_ics(line) for line in lines) + "\r\n"
