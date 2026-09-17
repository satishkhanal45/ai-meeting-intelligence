"""Hierarchical summarisation pipeline.

Orchestrates the end-to-end processing of a meeting transcript:

    Clean → Chunk → Summarise chunks → Merge → Extract structured data →
    Build knowledge graph → Package into Meeting model → Persist
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Optional

from config import settings
from logger import get_logger
from models import (
    ActionItem,
    ChunkResult,
    Deadline,
    Decision,
    GraphData,
    Meeting,
    ProviderResponse,
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


def register_provider(name: str, provider_cls: type) -> None:
    """Register a provider class so the pipeline can instantiate it by name."""
    PROVIDER_REGISTRY[name] = provider_cls
    logger.debug("Provider registered", extra={"provider_name": name, "class": provider_cls.__name__})


def get_provider(provider_name: str) -> object:
    """Return a configured provider instance by name.

    Raises ``ValueError`` if the provider is not registered.
    """
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        available = list(PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"Unknown provider '{provider_name}'. Available: {available}"
        )
    return cls()


def _chunk_cache_key(
    text: str, provider_name: str, model: str, temperature: float
) -> str:
    """Build a cache key for one chunk summary.

    The key covers everything that can change the summary: the chunk text, the
    provider and model that produced it, the sampling temperature, and the
    prompt revision. Keying on the text alone means switching provider or
    editing a prompt silently returns the previous provider's output.
    """
    payload = "\x00".join(
        [PROMPT_VERSION, provider_name, model, f"{temperature:.4f}", text]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _safe_json_parse(raw: str, default: dict) -> dict:
    """Parse JSON from LLM output, falling back to *default* on failure."""
    if not raw.strip():
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


def process_transcript(
    text: str,
    provider_name: str = "",
    temperature: float = 0.3,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunk_mode: str = "token",
) -> Meeting:
    """Process a raw transcript through the full pipeline.

    Parameters
    ----------
    text : str
        Raw transcript text.
    provider_name : str
        LLM provider to use. Defaults to ``settings.default_provider``.
    temperature : float
        LLM temperature.
    chunk_size : int or None
        Token chunk size.
    chunk_overlap : int or None
        Token overlap.
    chunk_mode : str
        ``"token"`` or ``"speaker"``.

    Returns
    -------
    Meeting
        Fully populated Meeting object (already saved to database).
    """
    from database import insert_meeting

    pipeline_start = time.perf_counter()
    meeting_id = generate_id()

    if not provider_name:
        provider_name = settings.default_provider

    provider = get_provider(provider_name)
    logger.info(
        "Pipeline started",
        extra={
            "meeting_id": meeting_id,
            "provider": provider_name,
            "chunk_mode": chunk_mode,
            "input_length": len(text),
        },
    )

    # ── 1. Clean ─────────────────────────────────────────────────────
    cleaned = clean_transcript(text)
    if not cleaned:
        meeting = Meeting(
            id=meeting_id,
            title="Empty Transcript",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            transcript=Transcript(raw_text=text, cleaned_text=""),
            provider=provider_name,
            processing_time=time.perf_counter() - pipeline_start,
        )
        insert_meeting(meeting)
        logger.info("Pipeline completed (empty transcript)", extra={"meeting_id": meeting_id})
        return meeting

    # ── 2. Detect participants ───────────────────────────────────────
    participants = detect_participants(cleaned)

    # ── 3. Chunk ──────────────────────────────────────────────────────
    chunks = chunk_transcript(cleaned, mode=chunk_mode, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        chunks = [cleaned]

    # ── 4. Summarise each chunk ──────────────────────────────────────
    model_name = getattr(provider, "model_name", "")
    chunk_results: list[ChunkResult] = []
    for i, chunk_text in enumerate(chunks):
        chunk_hash = _chunk_cache_key(chunk_text, provider_name, model_name, temperature)
        cached = get_cached_chunk_summary(chunk_hash)
        if cached:
            chunk_results.append(ChunkResult(index=i, text=chunk_text, summary=cached))
            logger.debug("Chunk summary cache hit", extra={"chunk": i})
            continue

        system, prompt = chunk_summary_prompts(chunk_text, i, len(chunks))
        try:
            response: ProviderResponse = provider.generate(
                prompt=prompt, temperature=temperature, system_prompt=system
            )
            chunk_results.append(
                ChunkResult(index=i, text=chunk_text, summary=response.content)
            )
            cache_chunk_summary(chunk_hash, response.content)
        except RuntimeError as exc:
            logger.error("Chunk summary failed", extra={"chunk": i, "error": str(exc)})
            chunk_results.append(
                ChunkResult(index=i, text=chunk_text, error=str(exc))
            )

    valid_chunk_summaries = [c.summary for c in chunk_results if c.summary]

    # ── 5. Merge summaries ───────────────────────────────────────────
    if not valid_chunk_summaries:
        chunk_errors = [c.error for c in chunk_results if c.error]
        if chunk_errors:
            merged_summary = (
                "No content could be summarised due to API errors:\n"
                + "\n".join(f"  - {e}" for e in chunk_errors[:3])
            )
            if len(chunk_errors) > 3:
                merged_summary += f"\n  ... and {len(chunk_errors) - 3} more errors"
        else:
            merged_summary = "No content could be summarised."
    elif len(valid_chunk_summaries) == 1:
        merged_summary = valid_chunk_summaries[0]
    else:
        system, prompt = merge_summaries(valid_chunk_summaries)
        try:
            response = provider.generate(
                prompt=prompt, temperature=temperature, system_prompt=system
            )
            merged_summary = response.content
        except RuntimeError as exc:
            logger.error("Merge summary failed", extra={"error": str(exc)})
            merged_summary = valid_chunk_summaries[0]

    # ── 6. Extract structured data ────────────────────────────────────
    system, prompt = extract_structured(merged_summary)
    structured_raw = ""
    try:
        response = provider.generate_json(
            prompt=prompt, temperature=temperature, system_prompt=system
        )
        structured_raw = response.content
    except RuntimeError as exc:
        logger.error("Structured extraction failed", extra={"error": str(exc)})

    structured = _safe_json_parse(structured_raw, {})
    title = structured.get("title", "Untitled Meeting")
    extracted_participants = structured.get("participants", [])
    all_participants = list(dict.fromkeys(participants + extracted_participants))

    action_items = [
        ActionItem(**item) for item in structured.get("action_items", [])
    ]
    deadlines = [
        Deadline(**dl) for dl in structured.get("deadlines", [])
    ]
    decisions = [
        Decision(**dec) for dec in structured.get("decisions", [])
    ]

    # ── 7. Build knowledge graph ──────────────────────────────────────
    system, prompt = kg_prompts(merged_summary, structured_raw if structured_raw else "{}")
    graph_raw = ""
    try:
        response = provider.generate_json(
            prompt=prompt, temperature=temperature, system_prompt=system
        )
        graph_raw = response.content
    except RuntimeError as exc:
        logger.error("Knowledge graph generation failed", extra={"error": str(exc)})

    graph_data_raw = _safe_json_parse(graph_raw, {"entities": [], "relationships": []})

    # ── 8. Package meeting ───────────────────────────────────────────
    meeting = Meeting(
        id=meeting_id,
        title=title,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        participants=all_participants,
        provider=provider_name,
        processing_time=round(time.perf_counter() - pipeline_start, 3),
        transcript=Transcript(raw_text=text, cleaned_text=cleaned),
        summary=Summary(executive_summary=merged_summary),
        action_items=action_items,
        deadlines=deadlines,
        decisions=decisions,
        graph_data=GraphData(graph_json=json.dumps(graph_data_raw)),
    )

    # ── 9. Save ───────────────────────────────────────────────────────
    insert_meeting(meeting)

    logger.info(
        "Pipeline completed",
        extra={
            "meeting_id": meeting_id,
            "title": title,
            "duration": meeting.processing_time,
            "participants": len(all_participants),
            "action_items": len(action_items),
            "deadlines": len(deadlines),
            "decisions": len(decisions),
        },
    )

    return meeting
