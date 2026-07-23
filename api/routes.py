from __future__ import annotations

import json
import traceback

from fastapi import APIRouter, HTTPException

from api.schemas import ConfigResponse, ErrorResponse, ProcessRequest, StatsResponse
from config import settings
from database import (
    delete_meeting,
    get_all_participants,
    get_full_meeting,
    get_meeting_count,
    get_meeting_list,
    init_db,
    search_meetings,
)
from logger import get_logger
from pipeline import process_transcript, register_provider
from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.openrouter_provider import OpenRouterProvider

logger = get_logger(__name__)

register_provider("gemini", GeminiProvider)
register_provider("groq", GroqProvider)
register_provider("openrouter", OpenRouterProvider)

init_db()

router = APIRouter(prefix="/api")


@router.get("/stats", response_model=StatsResponse)
def get_stats():
    meetings = get_meeting_list()
    total_meetings = len(meetings)
    unique_participants = len(get_all_participants())
    total_action_items = sum(m.action_item_count for m in meetings)
    total_decisions = sum(m.decision_count for m in meetings)
    return StatsResponse(
        total_meetings=total_meetings,
        unique_participants=unique_participants,
        total_action_items=total_action_items,
        total_decisions=total_decisions,
    )


@router.get("/meetings")
def list_meetings(search: str = ""):
    if search.strip():
        return search_meetings(search)
    return get_meeting_list()


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str):
    meeting = get_full_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting_endpoint(meeting_id: str):
    if not delete_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Meeting not found")


@router.get("/meetings/{meeting_id}/graph")
def get_meeting_graph(meeting_id: str):
    meeting = get_full_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    try:
        graph_data = json.loads(meeting.graph_data.graph_json)
    except (json.JSONDecodeError, ValueError):
        graph_data = {"entities": [], "relationships": []}
    return graph_data


@router.post("/process")
def process(req: ProcessRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Transcript text is required")
    try:
        meeting = process_transcript(
            text=req.text,
            provider_name=req.provider_name or settings.default_provider,
            temperature=req.temperature,
            chunk_size=req.chunk_size,
            chunk_overlap=req.chunk_overlap,
            chunk_mode=req.chunk_mode,
        )
        return meeting
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        logger.error("Processing failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.error("Processing failed (unexpected)", extra={"error": str(exc), "traceback": traceback.format_exc()})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config", response_model=ConfigResponse)
def get_config():
    return ConfigResponse(
        default_provider=settings.default_provider,
        default_temperature=settings.default_temperature,
        default_chunk_size=settings.default_chunk_size,
        default_chunk_overlap=settings.default_chunk_overlap,
        configured_providers=settings.get_configured_providers(),
    )
