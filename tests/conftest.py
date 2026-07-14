"""
Shared pytest fixtures for the Notes Helper test-suite.

Module summary
--------------
Provides small, deterministic, model-free fixtures — a two-speaker transcript, a
schema-valid synthesis dictionary, and a temporary output directory holding both
as JSON — so unit tests exercise the pure-Python surface (cleaning, rendering,
verification, auditing) without any ML backend, network, or Ollama.

Usage example
-------------
>>> def test_something(sample_syn):
...     assert sample_syn["meta"]["titre"] == "Réunion test"

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def sample_transcript() -> list[dict]:
    """A tiny two-speaker diarized transcript.

    Returns
    -------
    list of dict
        Utterances shaped like the real ``transcript.json``.
    """
    return [
        {"t0": 0.0, "t1": 5.0, "speaker": "S0", "text": "On double le budget produit."},
        {"t0": 5.0, "t1": 9.0, "speaker": "S1", "text": "D'accord, je m'en occupe lundi."},
    ]


@pytest.fixture
def sample_syn() -> dict:
    """A schema-valid synthesis dictionary whose citations are grounded.

    Returns
    -------
    dict
        The structure consumed by every renderer and by
        :func:`notes_helper.verify.verify_synthesis`.
    """
    return {
        "meta": {
            "titre": "Réunion test",
            "date": "2026-07-10",
            "horaire": "",
            "lieu": "Paris",
            "duree": "0:00:09",
            "audio_sources": [],
        },
        "speakers": {"S0": {"name": "Alice", "role": "PM"}, "S1": {"name": "Bob", "role": "Dev"}},
        "resume": ["Le budget produit est doublé."],
        "points_cles": ["Budget doublé"],
        "decisions": [{"decision": "Doubler le budget produit", "contexte": "réunion test"}],
        "actions": [
            {"action": "Cadrer le périmètre", "responsable": "Bob", "echeance": "2026-07-13"}
        ],
        "chapitres": [{"t": 0, "titre": "Budget", "resume": ""}],
        "themes": [{"theme": "Produit", "points": ["budget"]}],
        "citations": [{"speaker": "S0", "texte": "On double le budget produit.", "t": 1}],
    }


@pytest.fixture
def out_dir(tmp_path, sample_transcript, sample_syn) -> str:
    """A temporary output directory pre-populated with the two JSON artifacts.

    Parameters
    ----------
    tmp_path : pathlib.Path
        pytest's per-test temporary directory.
    sample_transcript, sample_syn : fixtures
        Injected content written to ``transcript.json`` / ``synthese.json``.

    Returns
    -------
    str
        Path to the directory, ready for :func:`notes_helper.outputs.render`.
    """
    (tmp_path / "transcript.json").write_text(
        json.dumps(sample_transcript, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "synthese.json").write_text(
        json.dumps(sample_syn, ensure_ascii=False), encoding="utf-8"
    )
    return str(tmp_path)
