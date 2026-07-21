"""Utility functions for transcript processing.

Provides cleaning, chunking, and text-processing helpers used by the
pipeline and frontend.
"""

import math
import re
import uuid
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

    # Estimate token count per word: assume average word length ~5 chars → ~1.25 tokens
    tokens_per_word = _CHARS_PER_TOKEN / 5.0

    chunk_token_size = max(1, chunk_size)
    overlap_token_size = max(0, min(overlap, chunk_token_size - 1))
    stride = max(1, chunk_token_size - overlap_token_size)

    chunks: list[str] = []
    i = 0
    while i < len(words):
        # Convert token budget back to word count
        word_budget = max(1, int(chunk_token_size / tokens_per_word))
        chunk = " ".join(words[i : i + word_budget])
        if chunk.strip():
            chunks.append(chunk)
        i += max(1, int(stride / tokens_per_word))

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

    chunk_size = chunk_size or settings.default_chunk_size
    overlap = overlap or settings.default_chunk_overlap

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


def detect_participants(text: str) -> list[str]:
    """Extract likely participant names from a transcript.

    Heuristic: lines matching ``Name:`` or ``[Name]`` patterns.
    Returns deduplicated list preserving order of first appearance.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for line in text.splitlines():
        match = _SPEAKER_PATTERN.match(line)
        if match:
            name = match.group("name").strip()
            if name and name.lower() not in seen_set:
                seen.append(name)
                seen_set.add(name.lower())
    return seen


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


_chunk_summary_cache: dict[str, str] = {}


def cache_chunk_summary(chunk_hash: str, summary: str) -> None:
    """Store a chunk summary in memory."""
    _chunk_summary_cache[chunk_hash] = summary


def get_cached_chunk_summary(chunk_hash: str) -> Optional[str]:
    """Retrieve a cached chunk summary, or ``None``."""
    return _chunk_summary_cache.get(chunk_hash)


def clear_chunk_cache() -> None:
    """Empty the in-memory chunk summary cache."""
    _chunk_summary_cache.clear()
