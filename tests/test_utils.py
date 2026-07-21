"""Tests for utility functions."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from utils import (
    chunk_transcript,
    clean_transcript,
    detect_participants,
    estimate_tokens,
    generate_id,
    read_transcript_file,
    truncate,
)


class TestCleanTranscript:
    def test_empty_string(self):
        assert clean_transcript("") == ""
        assert clean_transcript("   ") == ""

    def test_whitespace_normalisation(self):
        result = clean_transcript("Hello    world\n\n\nNext line")
        assert "    " not in result
        assert "\n\n\n" not in result

    def test_encoding_fixes(self):
        result = clean_transcript("Hello\u2018world\u2019")
        assert "'" in result

    def test_deduplicate_lines(self):
        result = clean_transcript("Line one\nLine one\nLine two")
        assert result.count("Line one") == 1


class TestChunkTranscript:
    def test_empty_text(self):
        assert chunk_transcript("") == []

    def test_token_mode_single_chunk(self, sample_cleaned_transcript):
        chunks = chunk_transcript(sample_cleaned_transcript, mode="token", chunk_size=5000, overlap=0)
        assert len(chunks) == 1

    def test_token_mode_multiple_chunks(self, sample_cleaned_transcript):
        chunks = chunk_transcript(sample_cleaned_transcript, mode="token", chunk_size=10, overlap=2)
        assert len(chunks) >= 1

    def test_speaker_mode(self):
        text = """Alice: First turn
Bob: Second turn
Alice: Third turn"""
        chunks = chunk_transcript(text, mode="speaker", chunk_size=1000)
        assert len(chunks) >= 2


class TestDetectParticipants:
    def test_detects_names(self):
        text = """Alice: Hello
Bob: Hi there
Charlie: Hey"""
        participants = detect_participants(text)
        assert "Alice" in participants
        assert "Bob" in participants
        assert "Charlie" in participants

    def test_deduplicates(self):
        text = """Alice: First
Alice: Second"""
        participants = detect_participants(text)
        assert len(participants) == 1

    def test_no_matches(self):
        assert detect_participants("No speakers here") == []


class TestEstimateTokens:
    def test_estimate(self):
        text = "a" * 400
        assert estimate_tokens(text) == 100  # 400 / 4

    def test_minimum_one(self):
        assert estimate_tokens("") == 1
        assert estimate_tokens("a") == 1


class TestGenerateId:
    def test_unique(self):
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100

    def test_hex_string(self):
        id_val = generate_id()
        assert isinstance(id_val, str)
        assert len(id_val) == 32


class TestTruncate:
    def test_short_string(self):
        assert truncate("Hello", 10) == "Hello"

    def test_long_string(self):
        result = truncate("Hello world this is long", 15)
        assert len(result) <= 15
        assert result.endswith("...")

    def test_word_boundary(self):
        result = truncate("Hello world", 10)
        assert result == "Hello..."


class TestReadTranscriptFile:
    def test_utf8_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Hello world")
            path = f.name
        try:
            content = read_transcript_file(path)
            assert content == "Hello world"
        finally:
            Path(path).unlink()

    def test_nonexistent_file(self):
        with pytest.raises((FileNotFoundError, ValueError)):
            read_transcript_file("/nonexistent/file.txt")
