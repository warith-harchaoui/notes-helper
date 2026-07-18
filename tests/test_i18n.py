"""
Unit tests for the i18n language catalog and the audio auto-detection defaults.

Module summary
--------------
Covers two connected guarantees:

* :mod:`notes_helper.i18n` — ``locales/i18n.yaml`` is the single source of truth
  for the languages notes-helper can produce output in. French + English are the
  guaranteed minimum, extra languages are just extra columns, and prompts are read
  fully-translated per language (with a safe fallback).
* The spoken language of the audio is **discovered with no a priori**: both
  :func:`notes_helper.asr.transcribe` and :func:`notes_helper.pipeline.run` default
  to ``"auto"`` so whisper detects whatever is spoken, for a file or a stream.

All tests are model-free.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import inspect

import pytest

from notes_helper import i18n


def test_supported_languages_include_fr_en_floor() -> None:
    """French and English are always supported, French first (primary)."""
    langs = i18n.supported_languages()
    assert "fr" in langs
    assert "en" in langs
    assert langs[0] == "fr"


def test_language_name_reads_catalog() -> None:
    """Display names come from the catalog; unknown codes fall back to upper-case."""
    assert i18n.language_name("fr") == "Français"
    assert i18n.language_name("en") == "English"
    assert i18n.language_name("de") == "DE"  # not declared -> code upper-cased


def test_default_language_is_supported() -> None:
    """The default output language is one of the supported languages."""
    assert i18n.default_language() in i18n.supported_languages()


def test_prompts_are_translated_per_language() -> None:
    """``map_sys`` / ``reduce_sys`` return distinct FR and EN text."""
    fr = i18n.prompt("map_sys", "fr")
    en = i18n.prompt("map_sys", "en")
    assert fr and en and fr != en
    assert "secrétaire" in fr  # French wording
    assert "minute-taker" in en  # English wording


def test_prompt_falls_back_for_unknown_language() -> None:
    """An unsupported language degrades to a real prompt, never an error."""
    text = i18n.prompt("reduce_sys", "de")
    assert isinstance(text, str) and text
    # de is not in the catalog, so it falls back to the default (fr).
    assert text == i18n.prompt("reduce_sys", "fr")


def test_unknown_prompt_id_raises() -> None:
    """Asking for a non-existent prompt id is a clear error."""
    with pytest.raises(KeyError):
        i18n.prompt("does_not_exist", "fr")


def test_audio_language_defaults_to_auto_no_a_priori() -> None:
    """ASR + pipeline default to ``"auto"`` so the spoken language is discovered."""
    from notes_helper import asr, config, pipeline

    assert config.ASR_LANGUAGE == "auto"
    assert inspect.signature(asr.transcribe).parameters["language"].default == "auto"
    assert inspect.signature(pipeline.run).parameters["language"].default == "auto"
