"""LLM prompt templates for the hierarchical summarisation pipeline.

Each function returns a system prompt and a user prompt for a specific
extraction task. Prompts are designed to produce structured JSON output
that can be parsed reliably.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Bump whenever a prompt below changes semantically. It is part of the chunk
# summary cache key, so editing a prompt invalidates stale cached output.
PROMPT_VERSION = "2026-09-18c"


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _weekday_name(iso_date: str) -> str:
    """Return the weekday for an ISO date, or an empty string if unparseable."""
    try:
        return _WEEKDAYS[datetime.strptime(iso_date[:10], "%Y-%m-%d").weekday()]
    except (ValueError, IndexError):
        return "unknown weekday"


def chunk_summary(chunk_text: str, chunk_index: int, total_chunks: int) -> tuple[str, str]:
    """Return prompts to summarise a single transcript chunk."""
    system = (
        "You are an expert meeting analyst. Summarise the following transcript segment "
        "concisely while preserving all named entities, decisions, action items, and "
        "deadlines mentioned."
    )
    user = (
        f"Transcript segment {chunk_index + 1} of {total_chunks}:\n\n"
        f"{chunk_text}\n\n"
        "Provide a concise summary of this segment. List any action items, deadlines, "
        "or decisions explicitly mentioned."
    )
    return system, user


def merge_summaries(chunk_summaries: list[str]) -> tuple[str, str]:
    """Return prompts to merge chunk summaries into a single coherent summary."""
    system = (
        "You are an expert meeting analyst. Merge the following segment summaries "
        "into one coherent executive summary. Eliminate duplicates, preserve all "
        "named entities and key points, and maintain chronological flow."
    )
    numbered = "\n\n".join(
        f"Segment {i + 1}:\n{s}" for i, s in enumerate(chunk_summaries)
    )
    user = (
        f"The following are summaries of consecutive segments of a meeting transcript:\n\n"
        f"{numbered}\n\n"
        "Produce a unified executive summary that covers the entire meeting."
    )
    return system, user


def extract_structured(summary_text: str, meeting_date: str = "") -> tuple[str, str]:
    """Return prompts to extract action items, deadlines, and decisions.

    *meeting_date* anchors relative language. Without it the model has no way
    to turn "next Thursday" into a date, so those deadlines stay as free text
    and cannot appear on a timeline or in a calendar export.
    """
    reference = (meeting_date or datetime.now(timezone.utc).isoformat())[:10]
    weekday = _weekday_name(reference)

    system = (
        "You are an expert meeting analyst. Extract structured information from "
        "the meeting summary below. Return ONLY valid JSON with no markdown formatting "
        "or code fences.\n\n"
        "Resolve every relative date and output an absolute YYYY-MM-DD date. "
        '"Tomorrow" and "Thursday" are dates you can compute; do not copy them '
        "through as text. Only leave a deadline's date as free text when it names "
        'no resolvable day at all, such as "once the audit finishes".\n'
        # The transcript is the better anchor when it carries its own date: a
        # recording processed months later must not have its deadlines pulled
        # forward to the day it happened to be uploaded.
        "Anchor those calculations on the date the meeting itself states. Only "
        f"if the transcript gives no date, use the processing date, {reference} "
        f"(a {weekday})."
    )
    user = (
        f"Processing date (fallback anchor only): {reference} ({weekday})\n\n"
        f"Meeting summary:\n\n{summary_text}\n\n"
        "Extract and return JSON with this exact structure:\n"
        "{\n"
        '  "title": "Meeting title inferred from content",\n'
        '  "participants": ["Name1", "Name2"],\n'
        '  "action_items": [\n'
        '    {"owner": "Person name or empty string", "task": "Description", '
        '"priority": "high|medium|low", "status": "open|in_progress|done"}\n'
        "  ],\n"
        '  "deadlines": [\n'
        '    {"description": "What is due", "date": "YYYY-MM-DD", '
        '"type": "explicit|relative|milestone"}\n'
        "  ],\n"
        '  "decisions": [\n'
        '    {"decision": "What was decided", "rationale": "Reason if mentioned"}\n'
        "  ]\n"
        "}\n\n"
        "If a field has no items, return an empty array. Every deadline date must "
        "be YYYY-MM-DD unless no day can be determined from the discussion. "
        'Use type "relative" when the date was spoken relatively (\"next Friday\") '
        'and "explicit" when a date was stated outright. '
        "Do not include any text outside the JSON."
    )
    return system, user


def knowledge_graph(summary_text: str, structured_json: str) -> tuple[str, str]:
    """Return prompts to build a knowledge graph from extracted data."""
    system = (
        "You are an expert knowledge graph builder. Convert the meeting data below "
        "into a JSON knowledge graph. Return ONLY valid JSON with no markdown formatting "
        "or code fences."
    )
    user = (
        f"Meeting summary:\n\n{summary_text}\n\n"
        f"Extracted structured data:\n\n{structured_json}\n\n"
        "Generate a knowledge graph as JSON with this exact structure:\n"
        "{\n"
        '  "entities": [\n'
        "    {\n"
        '      "id": "unique-id",\n'
        '      "label": "Display name",\n'
        '      "type": "person|task|deadline|decision|milestone|information|critical",\n'
        '      "properties": {"key": "value"}\n'
        "    }\n"
        "  ],\n"
        '  "relationships": [\n'
        "    {\n"
        '      "source": "entity-id",\n'
        '      "target": "entity-id",\n'
        '      "label": "owns|assigns|depends_on|reviewed_by|due_on|primary_contact|backup_contact|belongs_to|discussed_in"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Create nodes for each person, task, deadline, decision, and milestone mentioned. "
        "Connect them with appropriate relationship labels. Include meaningful properties "
        "on each entity (e.g., priority on tasks, date on deadlines).\n\n"
        "Rules:\n"
        "- Entity types must be lowercase: person, task, deadline, decision, milestone, information, critical\n"
        "- Relationship labels must be one of: owns, assigns, depends_on, reviewed_by, due_on, primary_contact, backup_contact, belongs_to, discussed_in\n"
        "- Each entity id can be a short slug like 'person-alice', 'task-deploy-fix'\n"
        "- Include ALL entities from the structured data\n"
        "- Do not include any text outside the JSON"
    )
    return system, user


def repair_json(broken_json: str) -> tuple[str, str]:
    """Return prompts to repair malformed JSON output."""
    system = (
        "You are a JSON repair utility. Fix the following malformed JSON so it "
        "parses correctly. Return ONLY the repaired JSON with no additional text."
    )
    user = f"Repair this JSON:\n\n{broken_json}"
    return system, user
