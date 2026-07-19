"""
Unit tests for the shared text-coercion that keeps raw JSON off the page.

Module summary
--------------
Local LLMs sometimes hand a field back as a dict/list instead of a string. These
tests pin :func:`notes_helper.outputs._text.as_text` and the two renderers so a
drifted value like ``{"texte": "…"}`` is shown as its text, never as the literal
``{'texte': …}`` a reader must never see. Also checks the Actions section no longer
carries a due-date ("Échéance") column. Model-free.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

from notes_helper.outputs._text import as_text
from notes_helper.outputs.html import esc
from notes_helper.outputs.markdown import render_markdown


def test_as_text_pulls_text_from_dicts() -> None:
    """A drifted dict resolves to its human text, not its JSON form."""
    assert as_text({"texte": "OpenAI ships", "t": 12}) == "OpenAI ships"
    assert as_text({"point": "scipy is fast"}) == "scipy is fast"
    assert as_text({"titre": "Chapitre 1"}) == "Chapitre 1"


def test_as_text_handles_lists_and_scalars() -> None:
    """Lists join their coerced items; None/str/number behave sensibly."""
    assert as_text(["a", {"point": "b"}]) == "a, b"
    assert as_text(None) == ""
    assert as_text("  hi  ") == "hi"
    assert as_text(3) == "3"


def test_esc_never_leaks_dict_json() -> None:
    """``esc`` coerces a dict through as_text — no ``{'…': …}`` in the output."""
    out = esc({"texte": "hello"})
    assert out == "hello"
    assert "{" not in out and "texte" not in out


def test_markdown_shows_no_raw_json_for_drifted_fields() -> None:
    """A synthesis with dict-shaped points/actions renders clean Markdown."""
    syn = {
        "meta": {"titre": "T", "date": "", "lieu": "", "duree": ""},
        "speakers": {"S1": {"name": "Alice"}},
        "resume": [{"texte": "we shipped"}],
        "points_cles": [{"texte": "point one"}, "point two"],
        "actions": [{"action": "do X", "responsable": "Alice", "echeance": "soon"}],
        "citations": [{"texte": "quoted", "speaker": "S1", "t": 5}],
    }
    md = render_markdown([], syn, include_transcript=False)
    assert "'texte'" not in md and "{'" not in md
    assert "point one" in md and "we shipped" in md and "quoted" in md


def test_markdown_actions_table_has_no_due_date_column() -> None:
    """The Actions table is two columns — no ``Échéance`` header."""
    syn = {
        "meta": {"titre": "T", "date": "", "lieu": "", "duree": ""},
        "speakers": {},
        "actions": [{"action": "do X", "responsable": "Bob", "echeance": "2026-07-13"}],
    }
    md = render_markdown([], syn, include_transcript=False)
    assert "Échéance" not in md
    assert "| Action | Responsable |" in md
