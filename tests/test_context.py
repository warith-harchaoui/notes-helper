"""Unit tests for :mod:`notes_helper.context` (associated-document ingestion).

These exercise the pure filtering/aggregation logic on text-native files and stand-in
artifacts — no kreuzberg, no models, no network — so they run in the fast CI suite. The
rich-document (PDF/DOCX) path is covered separately where kreuzberg is installed.
"""

from __future__ import annotations

import os

from notes_helper.context import (
    collect_context,
    extract_document_text,
    is_context_document,
)


def _write(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_is_context_document_filters(tmp_path) -> None:
    """Text/rich docs are kept; media, generated artifacts and dotfiles are skipped."""
    md = _write(str(tmp_path / "brief.md"), "# hi")
    wav = _write(str(tmp_path / "audio.wav"), "not really audio")
    gen = _write(str(tmp_path / "transcript.json"), "{}")
    dot = _write(str(tmp_path / ".hidden.md"), "secret")

    assert is_context_document(md) is True
    assert is_context_document(wav) is False  # media is the conversation, not context
    assert is_context_document(gen) is False  # notes-helper's own output
    assert is_context_document(dot) is False  # dotfile
    assert is_context_document(str(tmp_path)) is False  # a directory is not a document


def test_extract_document_text_reads_text(tmp_path) -> None:
    """Text-native files are read verbatim as UTF-8."""
    p = _write(str(tmp_path / "note.txt"), "Bonjour Warith")
    assert extract_document_text(p) == "Bonjour Warith"


def test_collect_context_aggregates_with_headers(tmp_path) -> None:
    """A folder collapses to one string: sorted, per-file headers, media excluded."""
    _write(str(tmp_path / "a_brief.md"), "alpha")
    _write(str(tmp_path / "b_report.md"), "beta")
    _write(str(tmp_path / "combined.wav"), "ignored media")

    ctx = collect_context(str(tmp_path))

    assert "## a_brief.md" in ctx
    assert "## b_report.md" in ctx
    assert "alpha" in ctx and "beta" in ctx
    assert "ignored media" not in ctx
    # Sorted order: a_brief precedes b_report.
    assert ctx.index("a_brief.md") < ctx.index("b_report.md")


def test_collect_context_respects_max_chars(tmp_path) -> None:
    """The optional budget caps the aggregate length."""
    _write(str(tmp_path / "big.md"), "x" * 5000)
    ctx = collect_context(str(tmp_path), max_chars=100)
    assert len(ctx) <= 100


def test_collect_context_accepts_file_list(tmp_path) -> None:
    """An explicit file list works and drops non-context entries."""
    keep = _write(str(tmp_path / "keep.md"), "keep me")
    drop = _write(str(tmp_path / "drop.wav"), "media")
    ctx = collect_context([keep, drop])
    assert "keep me" in ctx
    assert "media" not in ctx
