"""Unified i18n: one catalog for GUI strings AND LLM prompts, in every language.

Module summary
--------------
notes-helper follows the front-ui convention: every translatable string — interface
labels and model prompts alike — lives in one YAML catalog (``locales/i18n.yaml`` at
the repo root), never inlined in Python/JS. This module reads that catalog and resolves
the right string for a language.

**Language is discovered, never assumed.** There is no fixed default. The report/GUI
language is the DOMINANT language, resolved by :func:`resolve_language`:

1. when associated **text** is present (transcript, context docs), the majority language
   of that text (``langdetect``; if several languages appear, the majority wins);
2. otherwise, when there is only **audio**, the majority language of the audio — the LID
   stage segments it into per-language regions and the longest-lasting one wins.

On a transcript the two agree. The spoken language of the audio is likewise auto-detected
per turn (whisper ``"auto"``). Supported UI languages so far: ``fr``, ``en``, ``es``.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import functools
import os
from collections import Counter
from typing import Any, Iterable

import yaml

# UI languages the catalog ships. Anything detected outside this set falls back to en.
SUPPORTED_LANGS: tuple[str, ...] = ("fr", "en", "es")
# Fallback order applied after the asked language when a key lacks it.
_FALLBACK: tuple[str, ...] = ("en", "fr")


def _find_catalog() -> str:
    """Locate ``locales/i18n.yaml`` — root catalog first, packaged copy as fallback.

    Resolution order: the ``NOTES_HELPER_I18N`` env override; then ``locales/i18n.yaml``
    found by walking up from this module (the repo root in a source checkout); finally the
    copy shipped inside the package (so an installed wheel always finds one).
    """
    env = os.environ.get("NOTES_HELPER_I18N")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    d = here
    for _ in range(6):
        cand = os.path.join(d, "locales", "i18n.yaml")
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.join(here, "locales", "i18n.yaml")  # packaged fallback


@functools.lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """Load and cache the unified i18n catalog (or ``{}`` if missing/empty)."""
    try:
        with open(_find_catalog(), encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _pick(entry: Any, lang: str) -> str:
    """Resolve one catalog entry to a string for *lang*, with fallback.

    An entry is either a ``{lang: text}`` mapping or a plain string (legacy single-language
    catalogs still work). Resolution tries the asked language, then :data:`_FALLBACK`, then
    any available language.
    """
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for candidate in (lang, *_FALLBACK):
            if candidate in entry:
                return str(entry[candidate]).strip()
        for value in entry.values():  # any language rather than an empty string
            return str(value).strip()
    return ""


def gui(msg_id: str, lang: str) -> str:
    """Return the GUI label *msg_id* in *lang* (from the ``gui:`` namespace)."""
    entries = load_catalog().get("gui") or {}
    if msg_id not in entries:
        raise KeyError(f"no gui string {msg_id!r} in {_find_catalog()}")
    return _pick(entries[msg_id], lang)


def prompt(prompt_id: str, lang: str = "fr") -> str:
    """Return the LLM prompt template *prompt_id* in *lang* (``prompts:`` namespace).

    The template may contain a ``{lang_clause}`` placeholder the caller fills with the
    resolved output language.
    """
    entries = load_catalog().get("prompts") or {}
    if prompt_id not in entries:
        raise KeyError(f"no prompt {prompt_id!r} in {_find_catalog()}")
    return _pick(entries[prompt_id], lang)


def to_supported(lang: str | None) -> str:
    """Map any language code (e.g. from langdetect) to a supported UI language."""
    if not lang:
        return "en"
    base = lang.split("-")[0].lower()
    return base if base in SUPPORTED_LANGS else "en"


def detect_lang(text: str) -> str | None:
    """Best-effort language of *text* via langdetect, mapped to a supported UI language.

    Returns ``None`` when the text is too short/empty to detect or langdetect is absent —
    the caller then falls back (e.g. to the audio majority).
    """
    if not text or not text.strip():
        return None
    try:
        from langdetect import detect

        return to_supported(detect(text))
    except Exception:
        return None


def resolve_language(
    *,
    texts: Iterable[str] | None = None,
    audio_langs: Iterable[str] | None = None,
) -> str:
    """Resolve the dominant report/GUI language per the discovery policy.

    Parameters
    ----------
    texts : iterable of str, optional
        Associated text (transcript utterances, context docs). If any is detectable, the
        **majority** detected language wins — this takes precedence over audio.
    audio_langs : iterable of str, optional
        Per-region/per-turn audio language codes (from the LID stage / whisper). Used only
        when there is no usable text: the **majority** audio language wins.

    Returns
    -------
    str
        A supported UI language (``fr`` / ``en`` / ``es``); ``en`` if nothing is decidable.
    """
    # 1) Text present → majority language of the text.
    if texts:
        votes = Counter(filter(None, (detect_lang(t) for t in texts)))
        if votes:
            return votes.most_common(1)[0][0]
    # 2) Otherwise → majority language of the audio.
    if audio_langs:
        votes = Counter(to_supported(a) for a in audio_langs if a)
        if votes:
            return votes.most_common(1)[0][0]
    # 3) Nothing decidable.
    return "en"
