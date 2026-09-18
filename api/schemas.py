from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# A transcript far larger than this is almost certainly a mistake, and the whole
# body is held in memory before processing starts.
MAX_TRANSCRIPT_CHARS = 2_000_000


class ProcessRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)
    provider_name: str = ""
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    chunk_size: int | None = Field(default=None, ge=100, le=100_000)
    chunk_overlap: int | None = Field(default=None, ge=0)
    chunk_mode: str = Field(default="token", pattern="^(token|speaker)$")
    #: Optional model override. Empty uses the provider's default.
    model: str = Field(default="", max_length=200)


class StatsResponse(BaseModel):
    total_meetings: int
    unique_participants: int
    total_action_items: int
    total_decisions: int


class ConfigResponse(BaseModel):
    default_provider: str
    default_temperature: float
    default_chunk_size: int
    default_chunk_overlap: int
    configured_providers: list[str]


class HealthResponse(BaseModel):
    status: str
    database: bool
    configured_providers: list[str]


class ErrorResponse(BaseModel):
    detail: str
    error_id: str | None = None


class ProcessAcceptedResponse(BaseModel):
    """Returned by POST /api/process, which now enqueues rather than blocks."""

    job_id: str
    status: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    message: str
    completed: int
    total: int
    fraction: float
    meeting_id: str | None = None
    error: str | None = None
    error_id: str | None = None
    created_at: str
    finished_at: str | None = None


class ProviderInfo(BaseModel):
    name: str
    configured: bool
    default_model: str
    available_models: list[str]


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]
    default_provider: str
    fallback_chain: list[str]


# ── Editing extracted items ─────────────────────────────────────────────


class ActionItemPayload(BaseModel):
    """Partial update for an action item. Omitted fields are left alone."""

    owner: str | None = Field(default=None, max_length=200)
    task: str | None = Field(default=None, max_length=2000)
    priority: Literal["high", "medium", "low"] | None = None
    status: Literal["open", "in_progress", "done", "cancelled"] | None = None


class DeadlinePayload(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    date: str | None = Field(default=None, max_length=100)
    type: Literal["explicit", "relative", "milestone"] | None = None


class DecisionPayload(BaseModel):
    decision: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=2000)


class MeetingPayload(BaseModel):
    """Editable fields on the meeting itself. Titles are often mis-inferred."""

    title: str | None = Field(default=None, min_length=1, max_length=500)


class CreatedItemResponse(BaseModel):
    id: int
