"""
Unit tests for the prompt catalog and the discover-don't-assume language policy.

Module summary
--------------
notes-helper has no fixed language set and no default language. Two guarantees are
checked, both model-free:

* :mod:`notes_helper.i18n` reads LLM prompt templates from ``locales/i18n.yaml``
  (front-ui convention). The templates carry a ``{lang_clause}`` placeholder the
  caller fills in.
* Language is always discovered, never assumed: the spoken language defaults to
  ``"auto"`` (``config.ASR_LANGUAGE`` / ``asr.transcribe`` / ``pipeline.run``), and
  the report language defaults to ``None`` in ``synth.synthesize`` — meaning the
  model is told to write in the transcript's own language.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import inspect

import pytest

from notes_helper import i18n


def test_prompt_templates_load_with_placeholder() -> None:
    """map_sys / reduce_sys load and carry the {lang_clause} placeholder."""
    for pid in ("map_sys", "reduce_sys"):
        text = i18n.prompt(pid)
        assert text
        assert "{lang_clause}" in text


def test_unknown_prompt_id_raises() -> None:
    """Asking for a non-existent prompt id is a clear error."""
    with pytest.raises(KeyError):
        i18n.prompt("does_not_exist")


def test_spoken_language_defaults_to_auto() -> None:
    """ASR + pipeline default to ``"auto"`` so the spoken language is discovered."""
    from notes_helper import asr, config, pipeline

    assert config.ASR_LANGUAGE == "auto"
    assert inspect.signature(asr.transcribe).parameters["language"].default == "auto"
    assert inspect.signature(pipeline.run).parameters["language"].default == "auto"


def test_report_language_has_no_default() -> None:
    """``synth.synthesize`` defaults ``language`` to None — the report is discovered."""
    from notes_helper import synth

    assert inspect.signature(synth.synthesize).parameters["language"].default is None


def test_no_default_language_constant_remains() -> None:
    """The old fixed-language config surface is gone (no DEFAULT_LANGUAGE / pair)."""
    from notes_helper import config

    assert not hasattr(config, "DEFAULT_LANGUAGE")
    assert not hasattr(config, "SUPPORTED_LANGUAGES")
