"""Prompt catalog loader — keeps LLM prompts out of code, in ``locales/i18n.yaml``.

Module summary
--------------
notes-helper follows the front-ui i18n convention: LLM prompts live in one catalog
(``locales/i18n.yaml``), not inlined in Python. This module is the tiny reader for
that catalog. It says nothing about *which* language anything is in — notes-helper
has no fixed language set and no default language: the report language is
discovered at call time (see :mod:`notes_helper.synth`), and the spoken language of
the audio is always auto-detected (see :mod:`notes_helper.asr`).

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


@functools.lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """Load and cache the ``locales/i18n.yaml`` catalog.

    Returns
    -------
    dict
        The parsed catalog, or an empty dict if the file is missing/empty.
    """
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def prompt(prompt_id: str) -> str:
    """Return an LLM prompt template by id from the ``prompts:`` namespace.

    The returned string may contain a ``{lang_clause}`` placeholder that the caller
    fills in — with an explicit language, or with an instruction to write in the
    transcript's own language when none is given.

    Parameters
    ----------
    prompt_id : str
        Entry under ``prompts:`` (e.g. ``"map_sys"``).

    Returns
    -------
    str
        The prompt template.

    Raises
    ------
    KeyError
        If ``prompt_id`` is absent from the catalog.
    """
    prompts = load_catalog().get("prompts") or {}
    if prompt_id not in prompts:
        raise KeyError(f"no prompt {prompt_id!r} in {_CATALOG_PATH}")
    return str(prompts[prompt_id]).strip()
