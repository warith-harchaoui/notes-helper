"""
Tests for map/reduce resilience in :mod:`notes_helper.synth`.

Module summary
--------------
A long meeting fans the synthesis map step out into dozens of independent LLM
calls (a 6 h recording exercised ~57). These tests pin the two guarantees that
keep one bad call from discarding the whole synthesis, surfaced by that real
long-audio run: :func:`notes_helper.synth._json_loads_lax` never raises (it degrades
to ``{}`` on truncated / unparseable model output), and
:func:`notes_helper.synth.synthesize` isolates per-chunk failures — a single garbage
or erroring chunk is dropped while the rest still feed the reduce; only when
*every* chunk fails does it fall back to the no-LLM heuristic.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import pytest

from notes_helper import synth

# Marker text that only appears in the no-LLM heuristic fallback resume.
_HEURISTIC_MARK = "Ollama non joignable"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('sure! {"a": 1} hope that helps', {"a": 1}),
        ('{"a": 1, "b":', {}),          # truncated
        ("total garbage, no json", {}),
        ("", {}),
    ],
)
def test_json_loads_lax_never_raises(raw: str, expected: dict) -> None:
    """The lax parser honours its contract: parse what it can, else ``{}``."""
    assert synth._json_loads_lax(raw) == expected


def _long_transcript(n: int = 90) -> list[dict]:
    """Build a transcript long enough to span several map chunks (>6000 chars)."""
    line = "Nous avons discuté du budget produit et des prochaines actions à mener ensemble."
    return [
        {"t0": float(i * 5), "t1": float(i * 5 + 4), "speaker": f"S{i % 2}", "text": line}
        for i in range(n)
    ]


def test_synthesize_survives_some_bad_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mix of good and garbage map chunks still yields a real (non-heuristic) report."""
    good_map = '{"points":["un point"],"decisions":[],"actions":[],"citations":[],"themes":["t"]}'
    reduce_out = (
        '{"resume":["Vrai résumé fusionné."],"points_cles":["ok"],'
        '"decisions":[],"actions":[],"chapitres":[],"themes":[],"citations":[]}'
    )
    calls = {"map": 0}

    def fake_ollama(messages, model, **kw):
        system = messages[0]["content"]
        if "rédacteur de compte-rendu" in system:  # the reduce step
            return reduce_out
        # map step: alternate usable JSON and unparseable garbage
        calls["map"] += 1
        return good_map if calls["map"] % 2 == 0 else "oops {not json"

    monkeypatch.setattr(synth, "_ollama", fake_ollama)
    out = synth.synthesize(_long_transcript(), {"S0": {"name": "S0"}, "S1": {"name": "S1"}},
                           language="fr", model="test")

    assert calls["map"] >= 2, "transcript should span several map chunks"
    assert out["resume"] == ["Vrai résumé fusionné."]
    assert _HEURISTIC_MARK not in " ".join(out["resume"])


def test_synthesize_falls_back_when_llm_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every map call errors, synth degrades to the heuristic — no crash."""
    def boom(messages, model, **kw):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(synth, "_ollama", boom)
    out = synth.synthesize(_long_transcript(), {"S0": {"name": "S0"}, "S1": {"name": "S1"}},
                           language="fr", model="test")

    assert _HEURISTIC_MARK in " ".join(out["resume"])
