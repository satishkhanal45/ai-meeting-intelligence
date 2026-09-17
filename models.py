"""Pydantic models for all data structures in the system.

Every entity that flows between modules is defined here with full type
annotations and validation.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from logger import get_logger

_LOGGER = get_logger(__name__)


class ActionItem(BaseModel):
    owner: str = ""
    task: str = ""
    priority: str = "medium"
    status: str = "open"


class Deadline(BaseModel):
    description: str = ""
    date: str = ""
    type: str = "explicit"


class Decision(BaseModel):
    decision: str = ""
    rationale: str = ""


class Transcript(BaseModel):
    raw_text: str = ""
    cleaned_text: str = ""


class Summary(BaseModel):
    executive_summary: str = ""


class GraphEntity(BaseModel):
    id: str
    label: str
    type: str = "information"
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    source: str
    target: str
    label: str = "related_to"


class GraphData(BaseModel):
    graph_json: str = "{}"

    @property
    def entities(self) -> list[GraphEntity]:
        try:
            data = json.loads(self.graph_json)
            return [GraphEntity(**e) for e in data.get("entities", [])]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    @property
    def relationships(self) -> list[GraphRelationship]:
        try:
            data = json.loads(self.graph_json)
            return [GraphRelationship(**r) for r in data.get("relationships", [])]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []


class ProcessingOutcome(BaseModel):
    """How completely a transcript was processed.

    A pipeline run can partially fail — a chunk's summary call can exhaust its
    retries while the rest succeed — and the result was previously presented as
    if it were complete. These fields let the UI say so.
    """

    chunk_total: int = 0
    chunk_failures: int = 0

    @property
    def degraded(self) -> bool:
        """True when some of the transcript is missing from the summary."""
        return self.chunk_failures > 0

    @property
    def chunk_success_rate(self) -> float:
        if self.chunk_total == 0:
            return 1.0
        return (self.chunk_total - self.chunk_failures) / self.chunk_total


class MeetingMetadata(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    processing_time: float = 0.0
    created_at: str = ""
    updated_at: str = ""
    chunk_total: int = 0
    chunk_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    served_by: list[str] = Field(default_factory=list)


class MeetingListItem(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str] = Field(default_factory=list)
    provider: str = ""
    created_at: str = ""
    action_item_count: int = 0
    decision_count: int = 0
    chunk_total: int = 0
    chunk_failures: int = 0

    @computed_field
    @property
    def degraded(self) -> bool:
        return self.chunk_failures > 0


class Meeting(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    processing_time: float = 0.0
    transcript: Transcript = Field(default_factory=Transcript)
    summary: Summary = Field(default_factory=Summary)
    action_items: list[ActionItem] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    graph_data: GraphData = Field(default_factory=GraphData)

    # Processing outcome
    chunk_total: int = 0
    chunk_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Providers that actually answered. Differs from ``provider`` when the
    #: requested one failed and the fallback chain took over.
    served_by: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def degraded(self) -> bool:
        """True when part of the transcript is missing from the summary."""
        return self.chunk_failures > 0

    @computed_field
    @property
    def used_fallback(self) -> bool:
        """True when a provider other than the requested one did the work."""
        return bool(self.served_by) and any(s != self.provider for s in self.served_by)


class ProviderResponse(BaseModel):
    content: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    processing_time: float = 0.0


class ChunkResult(BaseModel):
    index: int
    text: str
    summary: str = ""
    error: Optional[str] = None


# ── LLM response envelopes ──────────────────────────────────────────────


def _parse_items(raw: Any, model: type[BaseModel], context: str) -> list[Any]:
    """Validate a list of items, discarding only the ones that fail.

    ``Model(**item)`` used to be called directly on LLM output, so a single
    unexpected key raised ``TypeError`` and discarded every other item in the
    batch. One malformed entry should cost one entry.
    """
    if not isinstance(raw, list):
        return []
    items: list[Any] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            items.append(model.model_validate(entry))
        except ValidationError as exc:
            _LOGGER.warning(
                "Discarded malformed item from LLM output",
                extra={"context": context, "error": str(exc), "entry": str(entry)[:200]},
            )
    return items


class StructuredExtraction(BaseModel):
    """The extraction step's expected response.

    ``extra="ignore"`` so that a model volunteering additional fields does not
    fail the whole document.
    """

    model_config = ConfigDict(extra="ignore")

    title: str = "Untitled Meeting"
    participants: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> "StructuredExtraction":
        """Build from an untrusted dict, keeping whatever is well-formed."""
        if not isinstance(data, dict):
            return cls()
        title = data.get("title")
        participants = data.get("participants")
        return cls(
            title=title if isinstance(title, str) and title.strip() else "Untitled Meeting",
            participants=[p for p in participants if isinstance(p, str)]
            if isinstance(participants, list)
            else [],
            action_items=_parse_items(data.get("action_items"), ActionItem, "action_items"),
            deadlines=_parse_items(data.get("deadlines"), Deadline, "deadlines"),
            decisions=_parse_items(data.get("decisions"), Decision, "decisions"),
        )


class KnowledgeGraphPayload(BaseModel):
    """The graph step's expected response."""

    model_config = ConfigDict(extra="ignore")

    entities: list[GraphEntity] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> "KnowledgeGraphPayload":
        if not isinstance(data, dict):
            return cls()
        entities = _parse_items(data.get("entities"), GraphEntity, "entities")
        relationships = _parse_items(
            data.get("relationships"), GraphRelationship, "relationships"
        )
        # Drop edges whose endpoints were not produced, rather than letting the
        # graph builder silently discard them later.
        known = {e.id for e in entities}
        relationships = [r for r in relationships if r.source in known and r.target in known]
        return cls(entities=entities, relationships=relationships)

    def to_json(self) -> str:
        return json.dumps(self.model_dump())
