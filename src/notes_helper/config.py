"""Central, layered configuration for the local-first ``notes-helper`` pipeline.

Module summary
--------------
All defaults are local: audio runs on a working sample rate, ASR uses a
bundled whisper model name, diarization estimates or forces a speaker count,
and the only "remote" endpoint is an Ollama LLM assumed to live on localhost.

Configuration values are resolved once, at import time, through a three-layer
precedence chain so that operators can override behaviour without editing code:

    code default  <  ./notes_helper_config.json  <  environment variable (NOTES_HELPER_*)

The environment variable always wins because it is the most explicit and the
easiest to set per-invocation (e.g. in CI or a one-off shell). The JSON file is
a convenient project-local override that can be committed or gitignored. The
code default is the safe local-first fallback that keeps ``notes-helper`` working out
of the box with no configuration at all.

Every string / URL constant below is computed through :func:`_resolve` so the
same precedence applies uniformly. Numeric / structural constants that were
never environment-driven (``SR``, ``DEFAULT_N_SPK``, ``MERGE_GAP_S``,
``MAX_TURN_S``, ``SPK_COLORS``) keep their original literal values verbatim to
avoid changing pipeline behaviour.

Usage example
-------------
    >>> import notes_helper.config as c
    >>> print(c.SR, c.INPUT_DIR, c.OUTPUT_DIR)
    16000 input output
    # expected output: 16000 input output

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import json
import os
from typing import Any

import os_helper as osh

# Path of the optional project-local override file. Kept relative (cwd-based) on
# purpose: it is meant to travel with the working directory / project, not the
# installed package, so different projects can carry different overrides.
CONFIG_PATH: str = "./notes_helper_config.json"


def load_config(path: str = CONFIG_PATH) -> dict[str, Any]:
    """Load the optional project-local JSON override file.

    Parameters
    ----------
    path : str, optional
        Filesystem path to the JSON configuration file. Defaults to
        :data:`CONFIG_PATH` (``./notes_helper_config.json``).

    Returns
    -------
    dict of str to Any
        A mapping of configuration keys to values. Keys whose name starts with
        ``"_"`` are treated as comments/metadata and are silently dropped. If
        the file does not exist, an empty dict is returned.

    Raises
    ------
    None
        This function never raises: a missing file yields ``{}`` and a malformed
        file is reported via :mod:`os_helper` and also yields ``{}``, so import
        of this module can never be broken by a bad config file.

    Notes
    -----
    Ignoring ``_``-prefixed keys lets JSON (which has no comment syntax) carry
    human notes, e.g. ``{"_note": "prod endpoint", "OLLAMA_URL": "..."}``.
    """
    # Absence of the file is the common case (no override) — not an error.
    if not osh.file_exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)
    except (OSError, ValueError) as exc:
        # A broken override must never crash import: warn and fall back to code
        # defaults + environment so the pipeline still starts.
        osh.warning(f"notes-helper: ignoring invalid config file {path!r}: {exc}")
        return {}
    # Drop comment/metadata keys ("_"-prefixed) so they cannot shadow real keys.
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# Read the override file exactly once, at import time, so downstream modules can
# simply `from .config import OLLAMA_URL` and get a plain module-level string.
_FILE_CONFIG: dict[str, Any] = load_config()


def _resolve(key: str, default: str) -> str:
    """Resolve one string constant through the precedence chain.

    Parameters
    ----------
    key : str
        The logical configuration key. It doubles as the JSON key and, once
        prefixed with ``NOTES_HELPER_``, as the environment-variable name (e.g. key
        ``WHISPER_MODEL`` reads env var ``NOTES_HELPER_WHISPER_MODEL``).
    default : str
        The hard-coded, local-first fallback used when neither the JSON file nor
        the environment provides a value.

    Returns
    -------
    str
        The effective value with precedence
        ``env (NOTES_HELPER_<key>) > notes_helper_config.json > default``.

    Notes
    -----
    We start from ``default``, let the JSON file override it, then let the
    environment override that — so the last writer (env) wins, matching the
    documented precedence.
    """
    value: Any = default
    # Layer 2: project-local JSON file overrides the code default.
    if key in _FILE_CONFIG:
        value = _FILE_CONFIG[key]
    # Layer 3: environment variable overrides everything (most explicit).
    env_name: str = f"NOTES_HELPER_{key}"
    if env_name in os.environ:
        value = os.environ[env_name]
    # Constants below are always strings/URLs; coerce so callers get a str.
    return str(value)


# --- audio / ASR ----------------------------------------------------------- #
SR: int = 16000  # working sample rate (mono float32)
# NOTE: legacy env var name preserved via _resolve("WHISPER_MODEL") ==
# NOTES_HELPER_WHISPER_MODEL, so existing overrides keep working.
WHISPER_MODEL: str = _resolve("WHISPER_MODEL", "large-v3-turbo-q5_0")
# Language of the audio: DISCOVERED, never assumed. Default ``"auto"`` lets whisper
# detect the language with no a priori, whatever is spoken — the same for a file or
# a live stream. There is deliberately NO default language and no fixed language
# set anywhere in notes-helper: the report is written in the language discovered
# from the content (or an explicit one the caller passes). Override with
# NOTES_HELPER_ASR_LANG only to force a known spoken language (rarely wanted).
ASR_LANGUAGE: str = _resolve("ASR_LANG", "auto")

# --- diarization ----------------------------------------------------------- #
# Number of speakers: None => estimate; an int forces exactly N (as in the
# original pipeline where the participant count was known). Never env-driven, so
# it keeps its literal default with no precedence layering.
DEFAULT_N_SPK: int | None = None
MERGE_GAP_S: float = 0.8  # merge same-speaker gaps below this
MAX_TURN_S: float = 28.0  # cap a merged turn before forcing a cut
# Speaker-embedding backend for diarization. ``"nemo"`` (default) uses the
# torch/NeMo TitaNet-large — the sharpest embedder, ideal on desktop. ``"sherpa"``
# runs the *same* TitaNet-large through onnxruntime (no torch), the portable path
# the cross-platform app ships (study-selected, ADR 0002 in notes-helper): DER
# 0.174 on AMI ES2011a, 0.148 on held-out IS1008a, FR+EN validated. Both emit the
# same 192-dim vector, so identity matching is unaffected. The sherpa path needs
# ``pip install vocal-helper[sherpa]`` and a TitaNet-large ONNX via
# ``$VH_SHERPA_EMBEDDING`` or the diarization-engines bundle.
DIAR_EMBEDDER: str = _resolve("DIAR_EMBEDDER", "nemo")

# --- local LLM synthesis (Ollama on localhost) ----------------------------- #
# Localhost by default: the "remote" endpoint is only remote if an operator
# points it elsewhere via config/env — the pipeline stays offline out of the box.
OLLAMA_URL: str = _resolve("OLLAMA_URL", "http://127.0.0.1:11434")
# Default kept light so it runs on a laptop: a 32B model pins ~16 GB and is minutes/chunk
# on an M-series GPU; gemma3:4b is fast, JSON-reliable and ~4.7 GB. Override with
# NOTES_HELPER_OLLAMA_MODEL for a heavier model when the hardware allows.
OLLAMA_MODEL: str = _resolve("OLLAMA_MODEL", "gemma3:4b")

# --- on-device identity store --------------------------------------------- #
# Voiceprint DB lives under the user's home so identities are per-user and never
# leave the device. Legacy env var name is NOTES_HELPER_DB.
DB_PATH: str = _resolve("DB", os.path.expanduser("~/.notes-helper/people.db"))

# --- input / output layout ------------------------------------------------- #
# Conventional relative directory names for batch/project layouts. Kept as plain
# strings (not resolved) because they are structural defaults, not endpoints.
INPUT_DIR: str = "input"
OUTPUT_DIR: str = "output"

# --- output ---------------------------------------------------------------- #
# Deterministic per-speaker colour palette (indexed by speaker order) for report
# rendering. Structural constant, never env-driven.
SPK_COLORS: list[str] = ["#2f6f5e", "#b45309", "#1d4ed8", "#9333ea", "#be123c", "#0f766e"]
