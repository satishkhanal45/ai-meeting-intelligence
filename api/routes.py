from __future__ import annotations

import asyncio
import json
import traceback
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from api.schemas import (
    ActionItemPayload,
    ConfigResponse,
    CreatedItemResponse,
    DeadlinePayload,
    DecisionPayload,
    HealthResponse,
    JobResponse,
    MeetingPayload,
    ProcessAcceptedResponse,
    ProcessRequest,
    ProviderInfo,
    ProvidersResponse,
    StatsResponse,
)
from config import settings
from database import (
    create_child,
    delete_child,
    delete_meeting,
    get_all_participants,
    get_child_meeting_id,
    get_full_meeting,
    get_meeting_count,
    get_meeting_list,
    get_meeting_metadata,
    get_person,
    list_action_items,
    list_deadlines,
    list_people,
    search_meetings,
    update_child,
    update_meeting_fields,
)
from exporters import (
    action_items_to_csv,
    deadlines_to_ics,
    export_filename,
    meetings_to_csv,
    to_markdown,
)
from jobs import JobStatus, registry
from logger import get_logger
from models import DatedDeadline, OwnedActionItem, PersonDetail, PersonSummary
from pipeline import (
    PROVIDER_REGISTRY,
    PipelineError,
    aprocess_transcript,
    register_provider,
)
from providers.errors import ProviderAuthError, ProviderError
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


# ── People and cross-meeting views ──────────────────────────────────────


@router.get("/people", response_model=list[PersonSummary])
def get_people():
    """Everyone who has appeared in a meeting, with their workload."""
    return list_people()


@router.get("/people/{person_id}", response_model=PersonDetail)
def get_person_detail(person_id: str):
    """One person: every meeting they attended and every action they own."""
    person = get_person(person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@router.get("/action-items", response_model=list[OwnedActionItem])
def get_action_items(
    status: str = Query(default="", pattern="^(|open|in_progress|done|cancelled)$"),
    owner: str = "",
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Action items across every meeting, for a single cross-meeting view."""
    return list_action_items(status=status, owner=owner, limit=limit, offset=offset)


@router.get("/deadlines", response_model=list[DatedDeadline])
def get_deadlines(limit: int = Query(default=200, ge=1, le=1000)):
    """Every deadline with its meeting, for a timeline."""
    return list_deadlines(limit=limit)


# ── Editing extracted items ─────────────────────────────────────────────
#
# Extraction output used to be write-once, so a wrong owner or a hallucinated
# task was permanent and an item could never be ticked off.

#: URL segment -> (table, payload model). The segment is hyphenated for the
#: URL; the table name is not.
_EDITABLE_KINDS: dict[str, tuple[str, type[BaseModel]]] = {
    "action-items": ("action_items", ActionItemPayload),
    "deadlines": ("deadlines", DeadlinePayload),
    "decisions": ("decisions", DecisionPayload),
}


def _resolve_kind(kind: str) -> tuple[str, type[BaseModel]]:
    entry = _EDITABLE_KINDS.get(kind)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown item type '{kind}'. Expected one of {sorted(_EDITABLE_KINDS)}",
        )
    return entry


async def _read_payload(request: Request, model: type[BaseModel]) -> BaseModel:
    """Parse and validate a request body for a dynamically chosen model.

    These handlers read the body themselves because the payload type depends
    on a path parameter, which means FastAPI's own validation -- and its 422
    response -- does not apply. Without this, a bad value surfaced as a 500.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc


@router.patch("/meetings/{meeting_id}")
def patch_meeting(meeting_id: str, payload: MeetingPayload):
    """Edit the meeting's own fields, currently just the title."""
    if not update_meeting_fields(meeting_id, title=payload.title):
        raise HTTPException(status_code=404, detail="Meeting not found")
    return get_meeting_metadata(meeting_id)


@router.post("/meetings/{meeting_id}/{kind}", response_model=CreatedItemResponse, status_code=201)
async def create_item(meeting_id: str, kind: str, request: Request):
    """Add an action item, deadline or decision the extraction step missed."""
    table, model = _resolve_kind(kind)
    payload = await _read_payload(request, model)

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="At least one field is required")

    item_id = create_child(table, meeting_id, fields)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return CreatedItemResponse(id=item_id)


# Namespaced under /items/ deliberately. A bare "/{kind}/{item_id}" matches the
# shape of /jobs/{job_id} and, being registered first, swallows it: FastAPI
# returns 422 for the failed int conversion rather than falling through.
@router.patch("/items/{kind}/{item_id}")
async def patch_item(kind: str, item_id: int, request: Request):
    """Apply a partial update to one extracted item."""
    table, model = _resolve_kind(kind)
    payload = await _read_payload(request, model)

    fields = payload.model_dump(exclude_none=True)
    if not update_child(table, item_id, fields):
        raise HTTPException(status_code=404, detail="Item not found")

    meeting_id = get_child_meeting_id(table, item_id)
    return get_full_meeting(meeting_id) if meeting_id else {"ok": True}


@router.delete("/items/{kind}/{item_id}", status_code=204)
def delete_item(kind: str, item_id: int):
    """Remove one extracted item, such as a hallucinated action."""
    table, _ = _resolve_kind(kind)
    if not delete_child(table, item_id):
        raise HTTPException(status_code=404, detail="Item not found")


# ── Export ──────────────────────────────────────────────────────────────


_EXPORT_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "ics": "text/calendar; charset=utf-8",
    "json": "application/json",
}


def _attachment(content: str, filename: str, extension: str) -> Response:
    return Response(
        content=content,
        media_type=_EXPORT_MEDIA_TYPES[extension],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/meetings/{meeting_id}/export")
def export_meeting(meeting_id: str, format: str = Query(default="md", pattern="^(md|csv|ics|json)$")):
    """Download one meeting as Markdown, CSV, iCalendar or JSON.

    A meeting's value is mostly in what happens afterwards, which means getting
    the summary and actions out of here and into an email, tracker or calendar.
    """
    meeting = get_full_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    filename = export_filename(meeting, format)
    if format == "md":
        return _attachment(to_markdown(meeting), filename, "md")
    if format == "csv":
        return _attachment(action_items_to_csv([meeting]), filename, "csv")
    if format == "ics":
        return _attachment(deadlines_to_ics([meeting], meeting.title), filename, "ics")
    return _attachment(meeting.model_dump_json(indent=2), filename, "json")


@router.get("/export/action-items")
def export_all_action_items(search: str = ""):
    """Every action item across every meeting, as one CSV."""
    listing = search_meetings(search, limit=100_000) if search.strip() else get_meeting_list(limit=100_000)
    meetings = [m for m in (get_full_meeting(item.id) for item in listing) if m is not None]
    return _attachment(action_items_to_csv(meetings), "action-items.csv", "csv")


@router.get("/export/meetings")
def export_meeting_index(search: str = ""):
    """One row per meeting, for reporting."""
    listing = search_meetings(search, limit=100_000) if search.strip() else get_meeting_list(limit=100_000)
    return _attachment(meetings_to_csv(listing), "meetings.csv", "csv")


@router.get("/export/deadlines.ics")
def export_all_deadlines():
    """All dated deadlines as a calendar feed that can be subscribed to."""
    listing = get_meeting_list(limit=100_000)
    meetings = [m for m in (get_full_meeting(item.id) for item in listing) if m is not None]
    return _attachment(deadlines_to_ics(meetings), "deadlines.ics", "ics")


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
        except ProviderAuthError as exc:
            # A rejected key is the operator's to fix, and saying so plainly
            # saves them reading the logs. It names no secret.
            logger.error("Processing failed: provider rejected credentials",
                         extra={"job_id": job.id, "error": str(exc)})
            job.finish_failure(
                "The provider rejected the configured API key. "
                "Check the key in .env and restart the server."
            )
        except PipelineError as exc:
            error_id = uuid.uuid4().hex[:12]
            logger.error(
                "Pipeline produced no usable result",
                extra={
                    "error_id": error_id,
                    "job_id": job.id,
                    "failed_chunks": exc.failed_chunks,
                    "total_chunks": exc.total_chunks,
                    "error": str(exc),
                },
            )
            job.finish_failure(
                f"None of the {exc.total_chunks} transcript segments could be "
                "summarised. The provider may be unavailable or rate limited.",
                error_id,
            )
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
