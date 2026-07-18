"""Internationalisation: the single source of truth for output languages.

Module summary
--------------
notes-helper writes back in a chosen language (reports, LLM prompts, GUI labels),
and that set of languages lives in **one** catalog: ``locales/i18n.yaml`` (see the
front-ui i18n convention). This module loads that catalog and exposes three small
helpers over it:

* :func:`supported_languages` — the languages we can produce output in. Adding a
  language is literally adding its column to ``locales/i18n.yaml``; French and
  English are the guaranteed minimum.
* :func:`language_name` — the human-readable name of a code (``"fr" → "Français"``).
* :func:`prompt` — a fully-translated LLM system prompt by id + language.

This module is intentionally tiny and has no side effects beyond a cached read of
the YAML file. It says nothing about the *spoken* language of the input audio: that
is always detected with no a priori, whatever is spoken (see
:mod:`notes_helper.asr`).

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import functools
import os
from typing import Any

import yaml

# The catalog ships inside the package so it is always found, whether installed
# from PyPI or run from a source checkout.
_CATALOG_PATH: str = os.path.join(os.path.dirname(__file__), "locales", "i18n.yaml")

# French and English are the project's hard minimum — always supported, even if a
# hand-edited catalog somehow drops them. Ordered floor: French first (primary).
FLOOR_LANGUAGES: tuple[str, ...] = ("fr", "en")


@functools.lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """Load and cache the ``locales/i18n.yaml`` catalog.

    Returns
    -------
    dict
        The parsed catalog. An empty dict if the file is missing or empty, so
        callers degrade to the :data:`FLOOR_LANGUAGES` / bundled defaults rather
        than crashing.
    """
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def supported_languages() -> tuple[str, ...]:
    """Return the languages notes-helper can produce output in.

    The set is the keys under ``meta.languages`` in the catalog, always unioned
    with :data:`FLOOR_LANGUAGES` (French + English) so the minimum holds no matter
    how the catalog is edited. French leads, then English, then any extra
    languages in catalog order.

    Returns
    -------
    tuple of str
        ISO-639-1 codes, floor first, then extras — each appearing once.
    """
    catalog = load_catalog()
    declared = (catalog.get("meta") or {}).get("languages") or {}
    ordered: list[str] = list(FLOOR_LANGUAGES)
    for code in declared:
        norm = str(code).strip().lower()
        if norm and norm not in ordered:
            ordered.append(norm)
    return tuple(ordered)


def default_language() -> str:
    """Return the default output language (``meta.default``, else ``"fr"``).

    Returns
    -------
    str
        A code guaranteed to be in :func:`supported_languages`.
    """
    catalog = load_catalog()
    code = str((catalog.get("meta") or {}).get("default", "fr")).strip().lower()
    return code if code in supported_languages() else "fr"


def language_name(code: str) -> str:
    """Return the human-readable name of a language code.

    Parameters
    ----------
    code : str
        ISO-639-1 code (e.g. ``"fr"``).

    Returns
    -------
    str
        The catalog's display name, or the upper-cased code as a fallback
        (``"de" → "DE"`` when not declared).
    """
    catalog = load_catalog()
    names = (catalog.get("meta") or {}).get("languages") or {}
    return str(names.get(code, code.upper()))


def prompt(prompt_id: str, lang: str) -> str:
    """Return a fully-translated LLM system prompt by id and language.

    Parameters
    ----------
    prompt_id : str
        Entry under the ``prompts:`` namespace (e.g. ``"map_sys"``).
    lang : str
        Target language code. Falls back to the default language, then French,
        then any available translation, so a partially-translated catalog never
        breaks synthesis.

    Returns
    -------
    str
        The prompt text.

    Raises
    ------
    KeyError
        If ``prompt_id`` is absent from the catalog entirely.
    """
    catalog = load_catalog()
    entry = (catalog.get("prompts") or {}).get(prompt_id)
    if not entry:
        raise KeyError(f"no prompt {prompt_id!r} in {_CATALOG_PATH}")
    for candidate in (lang, default_language(), "fr", "en"):
        if candidate in entry:
            return str(entry[candidate]).strip()
    # Last resort: any translation that exists (catalog guarantees at least one).
    return str(next(iter(entry.values()))).strip()
