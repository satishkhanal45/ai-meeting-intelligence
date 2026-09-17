from __future__ import annotations

import asyncio
import json
import traceback
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.schemas import (
    ConfigResponse,
    HealthResponse,
    JobResponse,
    ProcessAcceptedResponse,
    ProcessRequest,
    ProviderInfo,
    ProvidersResponse,
    StatsResponse,
)
from config import settings
from database import (
    delete_meeting,
    get_all_participants,
    get_full_meeting,
    get_meeting_count,
    get_meeting_list,
    search_meetings,
)
from jobs import JobStatus, registry
from logger import get_logger
from pipeline import PROVIDER_REGISTRY, aprocess_transcript, register_provider
from providers.errors import ProviderError
from providers.gemini_provider import AVAILABLE_MODELS as GEMINI_MODELS
from providers.gemini_provider import DEFAULT_MODEL as GEMINI_DEFAULT
from providers.gemini_provider import GeminiProvider
from providers.groq_provider import AVAILABLE_MODELS as GROQ_MODELS
from providers.groq_provider import DEFAULT_MODEL as GROQ_DEFAULT
from providers.groq_provider import GroqProvider
from providers.openrouter_provider import AVAILABLE_MODELS as OPENROUTER_MODELS
from providers.openrouter_provider import DEFAULT_MODEL as OPENROUTER_DEFAULT
from providers.openrouter_provider import OpenRouterProvider

logger = get_logger(__name__)

register_provider("gemini", GeminiProvider)
register_provider("groq", GroqProvider)
register_provider("openrouter", OpenRouterProvider)

#: Providers that require an API key before they can be used.
_KEYED_PROVIDERS = frozenset({"gemini", "groq", "openrouter"})

_PROVIDER_CATALOGUE = [
    ("gemini", GEMINI_DEFAULT, GEMINI_MODELS),
    ("groq", GROQ_DEFAULT, GROQ_MODELS),
    ("openrouter", OPENROUTER_DEFAULT, OPENROUTER_MODELS),
]

#: How long an SSE stream waits for a job change before emitting a keepalive.
_SSE_KEEPALIVE_SECONDS = 15.0

router = APIRouter(prefix="/api")


# ── Health and configuration ────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
def health():
    """Liveness plus readiness: is the database reachable and a provider usable?"""
    db_ok = True
    try:
        get_meeting_count()
    except Exception as exc:
        db_ok = False
        logger.error("Health check: database unreachable", extra={"error": str(exc)})

    configured = settings.get_configured_providers()
    return HealthResponse(
        status="ok" if (db_ok and configured) else "degraded",
        database=db_ok,
        configured_providers=configured,
    )


@router.get("/config", response_model=ConfigResponse)
def get_config():
    return ConfigResponse(
        default_provider=settings.default_provider,
        default_temperature=settings.default_temperature,
        default_chunk_size=settings.default_chunk_size,
        default_chunk_overlap=settings.default_chunk_overlap,
        configured_providers=settings.get_configured_providers(),
    )


@router.get("/providers", response_model=ProvidersResponse)
def list_providers():
    """Expose the selectable providers and their models.

    Models used to be hardcoded in each provider's constructor with no way to
    choose one from the API or the UI.
    """
    return ProvidersResponse(
        providers=[
            ProviderInfo(
                name=name,
                configured=settings.is_provider_configured(name),
                default_model=default,
                available_models=models,
            )
            for name, default, models in _PROVIDER_CATALOGUE
        ],
        default_provider=settings.default_provider,
        fallback_chain=settings.get_fallback_chain(),
    )


# ── Statistics and meetings ─────────────────────────────────────────────


@router.get("/stats", response_model=StatsResponse)
def get_stats():
    meetings = get_meeting_list(limit=100_000)
    return StatsResponse(
        total_meetings=len(meetings),
        unique_participants=len(get_all_participants()),
        total_action_items=sum(m.action_item_count for m in meetings),
        total_decisions=sum(m.decision_count for m in meetings),
    )


@router.get("/meetings")
def list_meetings(
    search: str = "",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    if search.strip():
        return search_meetings(search, limit=limit, offset=offset)
    return get_meeting_list(limit=limit, offset=offset)


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
        graph_data = {}
    # A model can return valid JSON in the wrong shape; the client relies on
    # both keys always being present.
    if not isinstance(graph_data, dict):
        graph_data = {}
    return {
        "entities": graph_data.get("entities") or [],
        "relationships": graph_data.get("relationships") or [],
    }


# ── Processing ──────────────────────────────────────────────────────────


@router.post("/process", response_model=ProcessAcceptedResponse, status_code=202)
async def process(req: ProcessRequest):
    """Enqueue a transcript and return immediately with a job id.

    Processing a long transcript takes minutes. Doing it inline held the
    connection open for the duration, which any reverse proxy would cut, and
    left the client unable to poll or cancel.
    """
    provider_name = req.provider_name or settings.default_provider

    if provider_name not in PROVIDER_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider_name}'. "
            f"Available: {sorted(PROVIDER_REGISTRY)}",
        )
    # Only the built-in hosted providers need a key; a provider registered by
    # other means (a local model, a test double) is usable without one.
    if provider_name in _KEYED_PROVIDERS and not settings.is_provider_configured(provider_name):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider_name}' is not configured. Add its API key to .env",
        )

    job = registry.create()

    async def run() -> None:
        try:
            meeting = await aprocess_transcript(
                text=req.text,
                provider_name=provider_name,
                temperature=req.temperature,
                chunk_size=req.chunk_size,
                chunk_overlap=req.chunk_overlap,
                chunk_mode=req.chunk_mode,
                model=req.model,
                progress_callback=job.apply_progress,
            )
            job.finish_success(meeting.id)
        except asyncio.CancelledError:
            job.finish_cancelled()
            raise
        except ValueError as exc:
            # Caller-supplied problems (unknown provider) are safe to echo.
            job.finish_failure(str(exc))
        except ProviderError as exc:
            error_id = uuid.uuid4().hex[:12]
            logger.error(
                "Processing failed",
                extra={"error_id": error_id, "job_id": job.id, "error": str(exc)},
            )
            job.finish_failure(
                "The language model provider could not complete the request.", error_id
            )
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            logger.error(
                "Processing failed (unexpected)",
                extra={
                    "error_id": error_id,
                    "job_id": job.id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            job.finish_failure("Processing failed.", error_id)

    job._task = asyncio.create_task(run())
    return ProcessAcceptedResponse(job_id=job.id, status=str(job.status))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(**job.as_dict())


@router.delete("/jobs/{job_id}", status_code=204)
async def cancel_job(job_id: str):
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not await registry.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job has already finished")


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    """Stream a job's progress as Server-Sent Events.

    The stream waits on the job's own change signal rather than polling, and
    emits a comment as a keepalive when nothing has happened, so intermediate
    proxies do not close an idle connection.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        yield _sse(job.as_dict())
        while not job.terminal:
            changed = await job.wait_for_change(_SSE_KEEPALIVE_SECONDS)
            if changed:
                yield _sse(job.as_dict())
            else:
                yield ": keepalive\n\n"
        yield _sse(job.as_dict())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell nginx not to buffer, which would defeat streaming.
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


__all__ = ["router", "JobStatus"]
