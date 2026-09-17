"""Hierarchical summarisation pipeline.

Orchestrates the end-to-end processing of a meeting transcript:

    Clean → Chunk → Summarise chunks (concurrently) → Merge → Extract
    structured data → Build knowledge graph → Package into Meeting → Persist

Chunk summaries run concurrently behind a semaphore, every provider call is
retried, and a run that loses some chunks is reported as degraded rather than
being presented as complete.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from config import settings
from logger import get_logger
from models import (
    ActionItem,
    ChunkResult,
    Deadline,
    Decision,
    GraphData,
    KnowledgeGraphPayload,
    Meeting,
    ProviderResponse,
    StructuredExtraction,
    Summary,
    Transcript,
)
from prompts import (
    PROMPT_VERSION,
    extract_structured,
    merge_summaries,
)
from prompts import (
    chunk_summary as chunk_summary_prompts,
)
from prompts import (
    knowledge_graph as kg_prompts,
)
from providers.errors import ProviderAuthError, ProviderError
from utils import (
    cache_chunk_summary,
    chunk_transcript,
    clean_transcript,
    detect_participants,
    generate_id,
    get_cached_chunk_summary,
)

logger = get_logger(__name__)

PROVIDER_REGISTRY: dict[str, type] = {}

#: How many summaries are folded together per merge round. Merging every chunk
#: summary in one prompt overflows the context window on long meetings.
MERGE_BATCH_SIZE = 5


# ── Progress reporting ──────────────────────────────────────────────────


@dataclass
class Progress:
    """A pipeline progress update, suitable for streaming to a client."""

    stage: str
    completed: int = 0
    total: int = 0
    message: str = ""

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, self.completed / self.total)

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "completed": self.completed,
            "total": self.total,
            "message": self.message,
            "fraction": round(self.fraction, 3),
        }


ProgressCallback = Optional[Callable[[Progress], None]]

#: Relative cost of each stage, used to turn stage progress into overall
#: progress. Chunk summarisation dominates because it is the only stage whose
#: work grows with transcript length.
_STAGE_WEIGHTS = {
    "preparing": 0.02,
    "summarising": 0.60,
    "merging": 0.18,
    "extracting": 0.10,
    "graphing": 0.08,
    "saving": 0.02,
}


def _emit(callback: ProgressCallback, progress: Progress) -> None:
    """Deliver a progress update, never letting a bad callback break the run."""
    if callback is None:
        return
    try:
        callback(progress)
    except Exception as exc:  # noqa: BLE001 - progress must never fail the pipeline
        logger.warning("Progress callback raised", extra={"error": str(exc)})


def overall_fraction(stage: str, stage_fraction: float) -> float:
    """Map (stage, fraction-within-stage) onto an overall 0..1 fraction."""
    elapsed = 0.0
    for name, weight in _STAGE_WEIGHTS.items():
        if name == stage:
            return round(elapsed + weight * max(0.0, min(1.0, stage_fraction)), 4)
        elapsed += weight
    return 1.0


# ── Provider registry ───────────────────────────────────────────────────


def register_provider(name: str, provider_cls: type) -> None:
    """Register a provider class so the pipeline can instantiate it by name."""
    PROVIDER_REGISTRY[name] = provider_cls
    logger.debug(
        "Provider registered",
        extra={"provider_name": name, "class": getattr(provider_cls, "__name__", str(provider_cls))},
    )


def get_provider(provider_name: str, model: str = "") -> object:
    """Return a configured provider instance by name.

    Raises ``ValueError`` if the provider is not registered.
    """
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        available = list(PROVIDER_REGISTRY.keys())
        raise ValueError(f"Unknown provider '{provider_name}'. Available: {available}")
    if model:
        try:
            return cls(model=model)
        except TypeError:
            # Registered factories (used in tests) may take no arguments.
            return cls()
    return cls()


# ── LLM client with failover ────────────────────────────────────────────


@dataclass
class LLMClient:
    """Calls an ordered list of providers, moving on when one fails outright.

    Each provider already retries its own transient failures; this layer
    handles the case where a provider is exhausted or rejects the request, so
    an outage in one vendor does not end the run.
    """

    providers: list[object]
    temperature: float = 0.3
    #: The provider name the caller asked for. This, not the provider's own
    #: self-reported name, is what gets recorded on the meeting, so it is what
    #: the summary cache must key on: otherwise a meeting can be labelled with
    #: one provider while replaying another's cached output.
    requested_name: str = ""
    _failures: list[str] = field(default_factory=list)
    _served_by: list[str] = field(default_factory=list)

    @property
    def primary(self) -> object:
        return self.providers[0]

    @property
    def name(self) -> str:
        return self.requested_name or getattr(self.primary, "name", "")

    @property
    def primary_name(self) -> str:
        """The self-reported name of the first provider in the chain."""
        return getattr(self.primary, "name", "")

    @property
    def served_by(self) -> list[str]:
        """Providers that actually answered, in first-seen order."""
        return list(dict.fromkeys(self._served_by))

    @property
    def model_name(self) -> str:
        return getattr(self.primary, "model_name", "")

    async def agenerate(
        self, prompt: str, system_prompt: str, json_mode: bool = False
    ) -> tuple[ProviderResponse, str]:
        """Try each provider in turn; raise the last error if all fail.

        Returns the response together with the name of the provider that
        actually served it, which is not always the one that was requested.
        """
        last: Exception | None = None
        for index, provider in enumerate(self.providers):
            served_by = getattr(provider, "name", "")
            try:
                if json_mode:
                    response = await _acall_json(provider, prompt, self.temperature, system_prompt)
                    self._served_by.append(served_by)
                    return response, served_by
                response = await _acall_text(provider, prompt, self.temperature, system_prompt)
                self._served_by.append(served_by)
                return response, served_by
            except ProviderError as exc:
                last = exc
                self._failures.append(f"{getattr(provider, 'name', '?')}: {exc}")
                if index < len(self.providers) - 1:
                    logger.warning(
                        "Provider failed, falling back",
                        extra={
                            "failed_provider": getattr(provider, "name", "?"),
                            "next_provider": getattr(self.providers[index + 1], "name", "?"),
                            "error_type": type(exc).__name__,
                        },
                    )
        assert last is not None
        raise last


async def _acall_text(provider: object, prompt: str, temperature: float, system_prompt: str) -> ProviderResponse:
    """Call a provider's async text method, falling back to its sync one."""
    agenerate = getattr(provider, "agenerate", None)
    if agenerate is not None:
        return await agenerate(prompt=prompt, temperature=temperature, system_prompt=system_prompt)
    return await asyncio.to_thread(
        provider.generate, prompt=prompt, temperature=temperature, system_prompt=system_prompt
    )


async def _acall_json(provider: object, prompt: str, temperature: float, system_prompt: str) -> ProviderResponse:
    """Call a provider's async JSON method, falling back to its sync one."""
    agenerate_json = getattr(provider, "agenerate_json", None)
    if agenerate_json is not None:
        return await agenerate_json(prompt=prompt, temperature=temperature, system_prompt=system_prompt)
    return await asyncio.to_thread(
        provider.generate_json, prompt=prompt, temperature=temperature, system_prompt=system_prompt
    )


def build_client(provider_name: str, model: str = "", temperature: float = 0.3) -> LLMClient:
    """Build an :class:`LLMClient` for *provider_name* plus any fallbacks."""
    providers = [get_provider(provider_name, model)]
    for fallback in settings.get_fallback_chain():
        if fallback == provider_name or fallback not in PROVIDER_REGISTRY:
            continue
        if not settings.is_provider_configured(fallback):
            continue
        try:
            providers.append(get_provider(fallback))
        except ValueError:
            continue
    return LLMClient(
        providers=providers, temperature=temperature, requested_name=provider_name
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _chunk_cache_key(text: str, provider_name: str, model: str, temperature: float) -> str:
    """Build a cache key for one chunk summary.

    The key covers everything that can change the summary: the chunk text, the
    provider and model that produced it, the sampling temperature, and the
    prompt revision. Keying on the text alone means switching provider or
    editing a prompt silently returns the previous provider's output.
    """
    payload = "\x00".join([PROMPT_VERSION, provider_name, model, f"{temperature:.4f}", text])
    return hashlib.sha256(payload.encode()).hexdigest()


def _safe_json_parse(raw: str, default: dict) -> dict:
    """Parse JSON from LLM output, falling back to *default* on failure."""
    if not raw or not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.warning("Failed to parse LLM JSON output", extra={"preview": raw[:200]})
            return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ── Stages ──────────────────────────────────────────────────────────────


async def _summarise_chunks(
    client: LLMClient,
    chunks: Sequence[str],
    temperature: float,
    callback: ProgressCallback,
) -> list[ChunkResult]:
    """Summarise every chunk concurrently, bounded by a semaphore."""
    semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
    total = len(chunks)
    done = 0
    lock = asyncio.Lock()
    # Set when a failure makes every remaining call pointless, such as a
    # rejected API key. asyncio.gather does not cancel sibling tasks when one
    # raises, so the tasks have to check this themselves.
    abort: list[ProviderAuthError] = []

    async def one(index: int, text: str) -> ChunkResult | None:
        nonlocal done
        key = _chunk_cache_key(text, client.name, client.model_name, temperature)
        cached = get_cached_chunk_summary(key)
        if cached is not None:
            result = ChunkResult(index=index, text=text, summary=cached)
        else:
            system, prompt = chunk_summary_prompts(text, index, total)
            async with semaphore:
                if abort:
                    return None
                try:
                    response, served_by = await client.agenerate(prompt, system)
                    # The cache key names the requested provider. A response
                    # that came from a fallback must not be stored under it, or
                    # a later healthy run would replay another vendor's output.
                    if served_by == client.primary_name:
                        cache_chunk_summary(key, response.content)
                    result = ChunkResult(index=index, text=text, summary=response.content)
                except ProviderAuthError as exc:
                    # Every remaining call fails identically, so stop the run
                    # rather than walking the whole transcript to the same end.
                    abort.append(exc)
                    return None
                except ProviderError as exc:
                    logger.error(
                        "Chunk summary failed after retries",
                        extra={"chunk": index, "error_type": type(exc).__name__, "error": str(exc)},
                    )
                    result = ChunkResult(index=index, text=text, error=str(exc))

        async with lock:
            done += 1
            _emit(
                callback,
                Progress("summarising", done, total, f"Summarised {done} of {total} segments"),
            )
        return result

    results = await asyncio.gather(*(one(i, c) for i, c in enumerate(chunks)))
    if abort:
        logger.error(
            "Aborting run: provider rejected credentials",
            extra={"error": str(abort[0]), "chunks_attempted": done + 1, "chunks_total": total},
        )
        raise abort[0]
    return sorted((r for r in results if r is not None), key=lambda r: r.index)


async def _merge(
    client: LLMClient,
    summaries: list[str],
    temperature: float,
    callback: ProgressCallback,
) -> str:
    """Fold chunk summaries into one, in rounds, so no prompt grows unbounded."""
    if not summaries:
        return ""
    if len(summaries) == 1:
        return summaries[0]

    # Count every fold across every round up front. Reporting progress per
    # round restarts the count each time, which makes the bar jump backwards
    # when a long meeting needs more than one round.
    total_folds = _count_folds(len(summaries))
    folds_done = 0

    current = summaries
    round_number = 0
    while len(current) > 1:
        round_number += 1
        batches = [
            current[i : i + MERGE_BATCH_SIZE] for i in range(0, len(current), MERGE_BATCH_SIZE)
        ]

        # Batches within a round are independent, so fold them concurrently.
        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        lock = asyncio.Lock()

        async def fold(batch: list[str]) -> str:
            nonlocal folds_done
            if len(batch) == 1:
                result = batch[0]
            else:
                system, prompt = merge_summaries(batch)
                async with semaphore:
                    try:
                        response, _ = await client.agenerate(prompt, system)
                        result = response.content
                    except ProviderError as exc:
                        logger.error(
                            "Merge failed, concatenating instead", extra={"error": str(exc)}
                        )
                        result = "\n\n".join(batch)
            async with lock:
                folds_done += 1
                _emit(
                    callback,
                    Progress("merging", folds_done, total_folds, "Merging summaries"),
                )
            return result

        current = list(await asyncio.gather(*(fold(b) for b in batches)))
        if round_number > 10:  # pathological guard; batching shrinks by 5x a round
            break

    return current[0]


class PipelineError(RuntimeError):
    """The run could not produce a usable result."""

    def __init__(self, message: str, *, failed_chunks: int = 0, total_chunks: int = 0) -> None:
        super().__init__(message)
        self.failed_chunks = failed_chunks
        self.total_chunks = total_chunks


def _count_folds(count: int) -> int:
    """Total merge operations needed to reduce *count* summaries to one."""
    total = 0
    while count > 1:
        batches = -(-count // MERGE_BATCH_SIZE)  # ceil division
        total += batches
        if batches == count:
            break  # no progress possible; guard against a pathological batch size
        count = batches
    return max(1, total)


def _failure_notice(errors: list[str]) -> str:
    head = "\n".join(f"  - {e}" for e in errors[:3])
    notice = f"No content could be summarised due to provider errors:\n{head}"
    if len(errors) > 3:
        notice += f"\n  ... and {len(errors) - 3} more errors"
    return notice


# ── Entry points ────────────────────────────────────────────────────────


async def aprocess_transcript(
    text: str,
    provider_name: str = "",
    temperature: float = 0.3,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunk_mode: str = "token",
    model: str = "",
    progress_callback: ProgressCallback = None,
) -> Meeting:
    """Process a raw transcript through the full pipeline.

    Returns a fully populated ``Meeting``, already saved to the database. If
    some chunks could not be summarised the meeting is still returned, with
    ``chunk_failures`` set and ``degraded`` true.
    """
    from database import insert_meeting

    pipeline_start = time.perf_counter()
    meeting_id = generate_id()
    provider_name = provider_name or settings.default_provider
    client = build_client(provider_name, model, temperature)

    logger.info(
        "Pipeline started",
        extra={
            "meeting_id": meeting_id,
            "provider": provider_name,
            "model": client.model_name,
            "chunk_mode": chunk_mode,
            "input_length": len(text),
            "fallbacks": len(client.providers) - 1,
        },
    )

    # ── 1. Clean ─────────────────────────────────────────────────────
    _emit(progress_callback, Progress("preparing", 0, 1, "Cleaning transcript"))
    cleaned = clean_transcript(text)
    if not cleaned:
        meeting = Meeting(
            id=meeting_id,
            title="Empty Transcript",
            date=_utc_now(),
            transcript=Transcript(raw_text=text, cleaned_text=""),
            provider=provider_name,
            model=client.model_name,
            processing_time=round(time.perf_counter() - pipeline_start, 3),
        )
        insert_meeting(meeting)
        logger.info("Pipeline completed (empty transcript)", extra={"meeting_id": meeting_id})
        return meeting

    # ── 2. Participants and chunks ───────────────────────────────────
    participants = detect_participants(cleaned)
    chunks = chunk_transcript(cleaned, mode=chunk_mode, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        chunks = [cleaned]
    _emit(progress_callback, Progress("preparing", 1, 1, f"Split into {len(chunks)} segments"))

    # ── 3. Summarise chunks concurrently ─────────────────────────────
    chunk_results = await _summarise_chunks(client, chunks, temperature, progress_callback)
    valid = [c.summary for c in chunk_results if c.summary]
    errors = [c.error for c in chunk_results if c.error]

    # ── 4. Merge ─────────────────────────────────────────────────────
    if not valid and errors:
        # Every segment failed. There is nothing to summarise, extract or
        # graph, so surface the failure instead of saving an empty meeting and
        # calling the run a success.
        raise PipelineError(
            "No part of the transcript could be summarised. "
            f"First error: {errors[0]}",
            failed_chunks=len(errors),
            total_chunks=len(chunks),
        )

    merged_summary = (
        await _merge(client, valid, temperature, progress_callback)
        if valid
        else "No content could be summarised."
    )

    # ── 5. Extract structured data ───────────────────────────────────
    _emit(progress_callback, Progress("extracting", 0, 1, "Extracting action items"))
    structured_raw = ""
    if valid:
        system, prompt = extract_structured(merged_summary)
        try:
            response, _ = await client.agenerate(prompt, system, json_mode=True)
            structured_raw = response.content
        except ProviderError as exc:
            logger.error("Structured extraction failed", extra={"error": str(exc)})

    structured = StructuredExtraction.from_raw(_safe_json_parse(structured_raw, {}))
    all_participants = list(dict.fromkeys(participants + structured.participants))
    _emit(progress_callback, Progress("extracting", 1, 1, "Extracted structured data"))

    # ── 6. Knowledge graph ───────────────────────────────────────────
    _emit(progress_callback, Progress("graphing", 0, 1, "Building knowledge graph"))
    graph_raw = ""
    if valid:
        system, prompt = kg_prompts(merged_summary, structured_raw or "{}")
        try:
            response, _ = await client.agenerate(prompt, system, json_mode=True)
            graph_raw = response.content
        except ProviderError as exc:
            logger.error("Knowledge graph generation failed", extra={"error": str(exc)})

    graph = KnowledgeGraphPayload.from_raw(
        _safe_json_parse(graph_raw, {"entities": [], "relationships": []})
    )
    _emit(progress_callback, Progress("graphing", 1, 1, "Knowledge graph built"))

    # ── 7. Package and save ──────────────────────────────────────────
    _emit(progress_callback, Progress("saving", 0, 1, "Saving meeting"))
    meeting = Meeting(
        id=meeting_id,
        title=structured.title,
        date=_utc_now(),
        participants=all_participants,
        provider=provider_name,
        model=client.model_name,
        processing_time=round(time.perf_counter() - pipeline_start, 3),
        transcript=Transcript(raw_text=text, cleaned_text=cleaned),
        summary=Summary(executive_summary=merged_summary),
        action_items=structured.action_items,
        deadlines=structured.deadlines,
        decisions=structured.decisions,
        graph_data=GraphData(graph_json=graph.to_json()),
        chunk_total=len(chunks),
        chunk_failures=len(errors),
        served_by=client.served_by,
    )
    insert_meeting(meeting)
    _emit(progress_callback, Progress("saving", 1, 1, "Saved"))

    logger.info(
        "Pipeline completed",
        extra={
            "meeting_id": meeting_id,
            "title": meeting.title,
            "duration": meeting.processing_time,
            "participants": len(all_participants),
            "action_items": len(meeting.action_items),
            "deadlines": len(meeting.deadlines),
            "decisions": len(meeting.decisions),
            "chunks": len(chunks),
            "chunk_failures": len(errors),
            "degraded": meeting.degraded,
        },
    )
    return meeting


def process_transcript(
    text: str,
    provider_name: str = "",
    temperature: float = 0.3,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunk_mode: str = "token",
    model: str = "",
    progress_callback: ProgressCallback = None,
) -> Meeting:
    """Synchronous wrapper around :func:`aprocess_transcript`."""
    return asyncio.run(
        aprocess_transcript(
            text=text,
            provider_name=provider_name,
            temperature=temperature,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunk_mode=chunk_mode,
            model=model,
            progress_callback=progress_callback,
        )
    )


# Retained for the ActionItem/Deadline/Decision re-exports some callers expect.
__all__ = [
    "PROVIDER_REGISTRY",
    "PipelineError",
    "ActionItem",
    "Deadline",
    "Decision",
    "LLMClient",
    "Progress",
    "aprocess_transcript",
    "build_client",
    "get_provider",
    "overall_fraction",
    "process_transcript",
    "register_provider",
]
