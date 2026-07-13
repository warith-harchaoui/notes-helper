"""
Tests for renderer robustness against drifted LLM synthesis output.

Module summary
--------------
Local models constrained to the synthesis JSON schema still drift: a field the
renderers expect as a string arrives as a list, an object arrives as a bare
string, a list arrives as a scalar, a timestamp arrives already formatted
(``"0:00:28"``) instead of as seconds, or a key is missing. These tests pin the
two defenses that keep such drift from crashing a render:
:func:`notes_helper.synth.normalize_synthese` (schema coercion at the boundary) and
:func:`notes_helper.outputs._timefmt.seconds` (tolerant timestamp parsing). The final
test renders a deliberately pathological synthesis end-to-end and asserts every
format is produced.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import json
import os

from notes_helper.outputs import render
from notes_helper.outputs._timefmt import seconds
from notes_helper.synth import normalize_synthese


def test_seconds_accepts_heterogeneous_forms() -> None:
    """``seconds`` coerces ints, floats, numeric strings and H:MM:SS alike."""
    assert seconds(3723) == 3723
    assert seconds("1:02:03") == 3723
    assert seconds("0:00:28") == 28
    assert seconds("28") == 28
    assert seconds("28.0") == 28
    assert seconds(5.9) == 5
    # Unparseable / empty values degrade to 0 rather than raising.
    assert seconds(None) == 0
    assert seconds("") == 0
    assert seconds("garbage") == 0


def test_normalize_coerces_drifted_shapes() -> None:
    """A synthesis with wrong-typed fields is coerced into the render schema."""
    drifted = {
        "resume": "un seul paragraphe",            # str where a list is expected
        "points_cles": None,                        # missing -> empty list
        "themes": [{"theme": ["A", "B"], "points": "solo"}],  # list title, scalar points
        "citations": ["une phrase orpheline"],      # bare string, not an object
        "chapitres": [{"t": "0:00:28", "titre": "Intro"}],    # formatted timestamp
    }
    out = normalize_synthese(drifted)
    assert out["resume"] == ["un seul paragraphe"]
    assert out["points_cles"] == []
    assert out["themes"] == [{"theme": "A B", "points": ["solo"]}]
    # A bare string citation is parked under the primary field, not dropped.
    assert out["citations"] == [{"texte": "une phrase orpheline"}]
    assert out["chapitres"][0]["titre"] == "Intro"
    # The raw timestamp is preserved untouched (the renderers parse it).
    assert out["chapitres"][0]["t"] == "0:00:28"


def test_normalize_is_idempotent(sample_syn: dict) -> None:
    """Normalising an already-valid synthesis is a no-op on its report keys."""
    once = normalize_synthese(sample_syn)
    twice = normalize_synthese(once)
    assert once == twice


def test_render_survives_pathological_synthese(out_dir: str) -> None:
    """A drifted synthese.json renders to every format without crashing."""
    # Overwrite the fixture synthesis with shapes that previously crashed the
    # Markdown and HTML renderers (formatted timestamp + list-valued fields).
    pathological = {
        "meta": {"titre": "Drift", "date": "2026-07-12", "duree": "0:02:00"},
        "speakers": {"S0": {"name": "S0", "role": ""}},
        "resume": ["Ligne un.", ["fragment", "imbriqué"]],
        "points_cles": "un point",
        "decisions": ["décision orpheline"],
        "chapitres": [{"t": "0:00:28", "titre": ["Titre", "en liste"], "resume": "ok"}],
        "themes": [{"theme": "T", "points": "point unique"}],
        "citations": [{"speaker": "S0", "texte": ["a", "b"], "t": "0:00:05"}],
    }
    with open(os.path.join(out_dir, "synthese.json"), "w", encoding="utf-8") as f:
        json.dump(pathological, f, ensure_ascii=False)

    written = render(out_dir, ["md", "html"])
    assert set(written) == {"md", "html"}
    assert os.path.exists(written["md"])
    assert os.path.exists(written["html"])
    # The formatted chapter timestamp survives round-trip into the rendered text.
    assert "0:00:28" in open(written["md"], encoding="utf-8").read()
