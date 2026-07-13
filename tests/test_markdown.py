"""
Tests for :mod:`notes_helper.outputs.markdown` — the neutral Markdown renderer.

Module summary
--------------
Checks that a schema-valid synthesis renders the expected section headings and
that content (title, actions, participants) surfaces in the output string.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

from notes_helper.outputs.markdown import render_markdown


def test_render_markdown_has_sections(sample_transcript, sample_syn):
    """The rendered Markdown carries the title and the main section headings."""
    md = render_markdown(sample_transcript, sample_syn)
    assert md.startswith("# Réunion test")
    for heading in ("## Résumé", "## Décisions", "## Actions", "## Citations", "## Transcript"):
        assert heading in md


def test_render_markdown_includes_content(sample_transcript, sample_syn):
    """Action text and participant names appear in the output."""
    md = render_markdown(sample_transcript, sample_syn)
    assert "Cadrer le périmètre" in md
    assert "Alice" in md and "Bob" in md


def test_render_markdown_can_omit_transcript(sample_transcript, sample_syn):
    """``include_transcript=False`` drops the verbatim transcript section."""
    md = render_markdown(sample_transcript, sample_syn, include_transcript=False)
    assert "## Transcript" not in md
