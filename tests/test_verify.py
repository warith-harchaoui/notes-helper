"""
Tests for :mod:`notes_helper.verify` — deterministic summary grounding.

Module summary
--------------
Verifies that grounded citations pass and fabricated ones are flagged, that the
grounding score is computed correctly, and that structural issues (empty
decisions, out-of-range timestamps) are reported. No LLM, fully deterministic.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

from notes_helper.verify import grounding_score, verify_synthesis


def test_grounded_synthesis_passes(sample_transcript, sample_syn):
    """A synthesis whose citations echo the transcript is fully grounded."""
    report = verify_synthesis(sample_transcript, sample_syn)
    assert report["ok"] is True
    assert report["grounding_score"] == 1.0
    assert report["issues"] == []


def test_fabricated_citation_is_flagged(sample_transcript, sample_syn):
    """A quote absent from the transcript drops the score and raises an issue."""
    sample_syn["citations"] = [
        {"speaker": "S0", "texte": "Nous rachetons un concurrent en Australie.", "t": 2}
    ]
    report = verify_synthesis(sample_transcript, sample_syn)
    assert report["ok"] is False
    assert report["grounding_score"] < 1.0
    assert any("not grounded" in i for i in report["issues"])


def test_out_of_range_timestamp_flagged(sample_transcript, sample_syn):
    """A chapter timestamp beyond the recording duration is reported."""
    sample_syn["chapitres"] = [{"t": 99999, "titre": "Bogus", "resume": ""}]
    report = verify_synthesis(sample_transcript, sample_syn)
    assert any("outside" in i for i in report["issues"])


def test_empty_action_flagged(sample_transcript, sample_syn):
    """An action with no text is caught as a structural issue."""
    sample_syn["actions"] = [{"action": "", "responsable": "Bob", "echeance": ""}]
    report = verify_synthesis(sample_transcript, sample_syn)
    assert any("action[0] is empty" in i for i in report["issues"])


def test_no_citations_scores_one(sample_transcript):
    """With nothing to disprove, the grounding score is 1.0."""
    assert grounding_score(sample_transcript, {"citations": []}) == 1.0
