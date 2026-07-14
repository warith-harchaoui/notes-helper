"""
Tests for :mod:`notes_helper.clean` — whisper hallucination / filler cleanup.

Module summary
--------------
Checks that leaked tag prefixes are stripped, pure-filler utterances are dropped,
and consecutive same-speaker fragments within a small gap are merged.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

from notes_helper.clean import clean, clean_text


def test_clean_text_strips_leaked_prefix():
    """A leaked ``Sujets :`` prefix is removed from an utterance."""
    assert clean_text("Sujets : Roadmap produit") == "Roadmap produit"


def test_clean_drops_pure_filler():
    """A lone ``merci.`` filler utterance is dropped entirely."""
    tr = [{"t0": 0.0, "t1": 1.0, "speaker": "S0", "text": "merci."}]
    assert clean(tr) == []


def test_clean_merges_same_speaker_within_gap():
    """Two adjacent same-speaker fragments within the gap merge into one turn."""
    tr = [
        {"t0": 0.0, "t1": 2.0, "speaker": "S0", "text": "Bonjour à tous."},
        {"t0": 2.5, "t1": 4.0, "speaker": "S0", "text": "On commence."},
    ]
    out = clean(tr)
    assert len(out) == 1
    assert "Bonjour à tous." in out[0]["text"]
    assert "On commence." in out[0]["text"]
    assert out[0]["t1"] == 4.0
