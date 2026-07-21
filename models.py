"""Pydantic models for all data structures in the system.

Every entity that flows between modules is defined here with full type
annotations and validation.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field


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


class MeetingMetadata(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str] = Field(default_factory=list)
    provider: str = ""
    processing_time: float = 0.0
    created_at: str = ""


class MeetingListItem(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str] = Field(default_factory=list)
    provider: str = ""
    created_at: str = ""
    action_item_count: int = 0
    decision_count: int = 0


class Meeting(BaseModel):
    id: str
    title: str
    date: str
    participants: list[str] = Field(default_factory=list)
    provider: str = ""
    processing_time: float = 0.0
    transcript: Transcript = Field(default_factory=Transcript)
    summary: Summary = Field(default_factory=Summary)
    action_items: list[ActionItem] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    graph_data: GraphData = Field(default_factory=GraphData)


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
