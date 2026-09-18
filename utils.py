"""Utility functions for transcript processing.

Provides cleaning, chunking, and text-processing helpers used by the
pipeline and frontend.
"""

import math
import re
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from typing import Optional

from config import settings
from logger import get_logger

logger = get_logger(__name__)

# Rough heuristic: 1 token ≈ 4 characters for English text.
_CHARS_PER_TOKEN = 4.0

_SECTION_HEADERS = re.compile(
    r"^\s*(##?#?#?|AGENDA|NOTES|SUMMARY|ACTION|DECISION|MINUTES)\s*[:.]?\s*$",
    re.IGNORECASE,
)

_SPEAKER_PATTERN = re.compile(
    r"^\s*(?:\[)?(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*[:.](?:\s|$)",
)

# ── ID Generation ───────────────────────────────────────────────────────


def generate_id() -> str:
    """Return a compact UUID4 string."""
    return uuid.uuid4().hex


# ── Transcript Cleaning ─────────────────────────────────────────────────


def _deduplicate_lines(text: str) -> str:
    """Remove consecutive duplicate lines while preserving intentional repeats."""
    lines = text.splitlines()
    deduped: list[str] = []
    prev = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped == prev:
            continue
        deduped.append(line)
        prev = stripped
    return "\n".join(deduped)


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces/newlines into single space/newline groups."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fix_encoding(text: str) -> str:
    """Replace common Unicode encoding artefacts."""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "--",
        "\u2026": "...",
        "\u00a0": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def clean_transcript(text: str) -> str:
    """Clean a raw transcript for downstream processing.

    Steps:
        1. Fix encoding artefacts.
        2. Normalise whitespace.
        3. Deduplicate consecutive identical lines.
        4. Strip leading/trailing whitespace.

    Returns the cleaned text, or an empty string if input is empty/whitespace.
    """
    if not text or not text.strip():
        return ""
    text = _fix_encoding(text)
    text = _normalize_whitespace(text)
    text = _deduplicate_lines(text)
    text = text.strip()
    logger.debug("Transcript cleaned", extra={"length": len(text)})
    return text


# ── Chunking ────────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Rough estimate of token count (4 chars ≈ 1 token)."""
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def _token_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split *text* into fixed-size token windows with overlap."""
    words = text.split()
    if not words:
        return []

    # Derive the tokens-per-word ratio from this text rather than assuming a
    # fixed average, so the resulting chunks actually land near *chunk_size*.
    tokens_per_word = max(estimate_tokens(text) / len(words), 1e-6)

    chunk_token_size = max(1, chunk_size)
    overlap_token_size = max(0, min(overlap, chunk_token_size - 1))
    stride_tokens = chunk_token_size - overlap_token_size

    word_budget = max(1, int(chunk_token_size / tokens_per_word))
    stride_words = max(1, int(stride_tokens / tokens_per_word))

    chunks: list[str] = []
    for i in range(0, len(words), stride_words):
        chunk = " ".join(words[i : i + word_budget])
        if chunk.strip():
            chunks.append(chunk)
        if i + word_budget >= len(words):
            break

    return chunks


def _speaker_chunks(text: str, max_chunk_tokens: int) -> list[str]:
    """Split on speaker boundaries, merging smaller turns to avoid tiny chunks."""
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []

    for line in lines:
        match = _SPEAKER_PATTERN.match(line)
        speaker_turn = match is not None

        if speaker_turn and current:
            current_text = "\n".join(current)
            if estimate_tokens(current_text) >= max_chunk_tokens * 0.5:
                chunks.append(current_text)
                current = []
        current.append(line)

    if current:
        remainder = "\n".join(current).strip()
        if remainder:
            chunks.append(remainder)

    return chunks if chunks else [text]


def chunk_transcript(
    text: str,
    mode: str = "token",
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> list[str]:
    """Split a cleaned transcript into chunks.

    Parameters
    ----------
    text : str
        Cleaned transcript text.
    mode : str
        ``"token"`` for fixed-size token windows, ``"speaker"`` for
        speaker-turn boundaries.
    chunk_size : int or None
        Target chunk size in tokens. Defaults to ``settings.default_chunk_size``.
    overlap : int or None
        Token overlap (only for ``"token"`` mode). Defaults to
        ``settings.default_chunk_overlap``.

    Returns
    -------
    list[str]
        Non-empty text chunks.
    """
    if not text.strip():
        return []

    # Compare against None explicitly: 0 is a meaningful value for both, and
    # ``or`` would silently replace it with the default.
    chunk_size = settings.default_chunk_size if chunk_size is None else chunk_size
    overlap = settings.default_chunk_overlap if overlap is None else overlap

    if mode == "speaker":
        chunks = _speaker_chunks(text, chunk_size)
    else:
        chunks = _token_chunks(text, chunk_size, overlap)

    result = [c for c in chunks if c.strip()]
    logger.info(
        "Transcript chunked",
        extra={"mode": mode, "chunks": len(result), "chunk_size": chunk_size, "overlap": overlap},
    )
    return result


# ── Participant Detection ───────────────────────────────────────────────


#: Words that match the speaker pattern but label a section rather than name a
#: person. Without these, a transcript header like "Date: 2026-07-20" or
#: "Action Items:" is recorded as a meeting participant.
_NON_SPEAKER_LABELS = frozenset(
    {
        "action",
        "action item",
        "action items",
        "agenda",
        "attendees",
        "date",
        "decision",
        "decisions",
        "deadline",
        "deadlines",
        "duration",
        "key decision",
        "key decisions",
        "location",
        "meeting",
        "minutes",
        "next step",
        "next steps",
        "note",
        "notes",
        "participant",
        "participants",
        "present",
        "purpose",
        "recording",
        "subject",
        "summary",
        "time",
        "title",
        "topic",
        "topics",
    }
)


def detect_participants(text: str) -> list[str]:
    """Extract likely participant names from a transcript.

    Heuristic: lines matching ``Name:`` or ``[Name]`` patterns, excluding
    section headings that share that shape. Returns a deduplicated list in
    order of first appearance.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for line in text.splitlines():
        match = _SPEAKER_PATTERN.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        if not name or name.lower() in _NON_SPEAKER_LABELS:
            continue
        if name.lower() in seen_set:
            continue
        seen.append(name)
        seen_set.add(name.lower())
    return seen


def merge_participant_names(names: Iterable[str]) -> list[str]:
    """Collapse the same person appearing under a short and a full name.

    Speaker labels give first names ("Alice"), while the extraction step
    returns full names ("Alice Chen"), so a naive union lists everyone twice
    and doubles the unique-participant count. When one name is the leading
    part of another, keep the longer, more specific form.
    """
    ordered = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    kept: list[str] = []

    for name in sorted(ordered, key=lambda n: (-len(n.split()), -len(n))):
        tokens = name.lower().split()
        # Skip if an already-kept name starts with this one's tokens.
        if any(k.lower().split()[: len(tokens)] == tokens for k in kept):
            continue
        kept.append(name)

    # Restore first-appearance order from the input.
    position = {n: i for i, n in enumerate(ordered)}
    return sorted(kept, key=lambda n: position[n])


# ── Text Helpers ────────────────────────────────────────────────────────


def truncate(text: str, max_chars: int = 200) -> str:
    """Truncate *text* to *max_chars* with ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rsplit(" ", 1)[0] + "..."


# ── File Reading ────────────────────────────────────────────────────────


def read_transcript_file(filepath: str) -> str:
    """Read a transcript file, trying common encodings.

    Raises ``ValueError`` if the file cannot be decoded.
    """
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(
        f"Could not decode file {filepath} with any of {encodings}. "
        "Please ensure the file is UTF-8 encoded."
    )


# ── Chunk Summary Cache ─────────────────────────────────────────────────


# Bounded LRU cache. An unbounded dict leaks memory for the lifetime of a
# long-running API process, which summarises a new chunk on almost every request.
CHUNK_CACHE_MAX_ENTRIES = 512

_chunk_summary_cache: "OrderedDict[str, str]" = OrderedDict()


def cache_chunk_summary(chunk_hash: str, summary: str) -> None:
    """Store a chunk summary, evicting the least recently used entry when full."""
    if chunk_hash in _chunk_summary_cache:
        _chunk_summary_cache.move_to_end(chunk_hash)
    _chunk_summary_cache[chunk_hash] = summary
    while len(_chunk_summary_cache) > CHUNK_CACHE_MAX_ENTRIES:
        evicted, _ = _chunk_summary_cache.popitem(last=False)
        logger.debug("Chunk cache eviction", extra={"key": evicted})


def get_cached_chunk_summary(chunk_hash: str) -> Optional[str]:
    """Retrieve a cached chunk summary, or ``None``."""
    summary = _chunk_summary_cache.get(chunk_hash)
    if summary is not None:
        _chunk_summary_cache.move_to_end(chunk_hash)
    return summary


def clear_chunk_cache() -> None:
    """Empty the in-memory chunk summary cache."""
    _chunk_summary_cache.clear()
