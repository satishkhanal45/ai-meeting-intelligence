from __future__ import annotations

from pydantic import BaseModel


class ProcessRequest(BaseModel):
    text: str
    provider_name: str = ""
    temperature: float = 0.3
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    chunk_mode: str = "token"


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


class ErrorResponse(BaseModel):
    detail: str
