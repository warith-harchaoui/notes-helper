"""Output renderers package — Markdown is the neutral core, everything else renders it.

Module summary
--------------
This package turns a pair of on-disk artifacts — ``transcript.json`` (the raw
diarized transcript) and ``synthese.json`` (the LLM synthesis produced by
``notes-helper synth``) — into the user-facing deliverables: Markdown, a
self-contained interactive HTML report, an Obsidian vault, and compiled
documents (DOCX / PDF / PPTX).

Markdown is treated as the neutral source of truth: the compiled documents are
generated *from* the Markdown, and the HTML / vault renders share the same
underlying synthesis dictionary. This module exposes :func:`render`, the single
dispatch entry point that reads the two JSON files once and fans out to the
requested formats, plus the individual renderers re-exported for direct use.

Usage example
-------------
>>> # Given an ``out_dir`` that already contains transcript.json + synthese.json
>>> from notes_helper.outputs import render
>>> written = render(out_dir, formats=("md", "html"))  # doctest: +SKIP
>>> print(sorted(written))                              # doctest: +SKIP
['html', 'md']
# expected output: ['html', 'md']

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable

from ..synth import normalize_synthese
from .docs import compile_all
from .html import render_html
from .markdown import render_markdown
from .vault import build_vault

# Public surface of the package — the individual renderers plus the dispatcher.
__all__: list[str] = ["render_html", "render_markdown", "build_vault", "compile_all", "render"]

# Formats that are produced by compiling the intermediate Markdown through
# md2star rather than rendered directly. Kept in one place so the Markdown file
# is generated whenever any of these are requested (they all depend on it).
_DOC_FMTS: set[str] = {"docx", "pdf", "pptx"}


def _load(out_dir: str) -> tuple[list[dict], dict]:
    """Load the transcript and synthesis JSON artifacts from an output directory.

    Parameters
    ----------
    out_dir : str
        Directory produced by an earlier ``notes-helper`` run. It must contain
        ``transcript.json`` and ``synthese.json``.

    Returns
    -------
    tuple[list[dict], dict]
        The parsed transcript (a list of utterance dicts) and the parsed
        synthesis dictionary, in that order.

    Raises
    ------
    FileNotFoundError
        If ``synthese.json`` is missing, i.e. ``notes-helper synth`` has not been run
        yet. The message points the user at the command to run.
    """
    # transcript.json is assumed present — it is the primary output of notes-helper.
    tr = json.load(open(os.path.join(out_dir, "transcript.json"), encoding="utf-8"))
    syn_path = os.path.join(out_dir, "synthese.json")
    # The synthesis is a separate, optional (local Ollama) step; guide the user
    # explicitly instead of failing with an opaque JSON error downstream.
    if not os.path.exists(syn_path):
        raise FileNotFoundError(
            f"{syn_path} missing — run `notes-helper synth {out_dir}` first (local Ollama)."
        )
    # A synthese.json may have been produced by an older notes-helper, another tool,
    # or edited by hand; normalise it to the renderers' expected schema so a
    # drifted field shape cannot crash rendering.
    syn = normalize_synthese(json.load(open(syn_path, encoding="utf-8")))
    return tr, syn


def render(out_dir: str, formats: Iterable[str] = ("html",), *, vault_dir: str = "") -> dict:
    """Render the requested output formats from an output directory's JSON artifacts.

    Parameters
    ----------
    out_dir : str
        Directory containing ``transcript.json`` and ``synthese.json``; also the
        destination for the rendered files.
    formats : Iterable[str], optional
        Formats to produce. Case-insensitive. Recognised values are ``"md"``,
        ``"html"``, the document formats in :data:`_DOC_FMTS`
        (``"docx"``, ``"pdf"``, ``"pptx"``) and ``"vault"``. Defaults to
        ``("html",)``.
    vault_dir : str, keyword-only, optional
        Destination Obsidian vault directory. Required only when ``"vault"`` is
        among the requested formats.

    Returns
    -------
    dict
        Mapping from each produced format name to the absolute-or-relative path
        of the file that was written (for ``"vault"`` this is the meeting note).

    Raises
    ------
    FileNotFoundError
        Propagated from :func:`_load` when the synthesis is missing.
    ValueError
        If ``"vault"`` is requested without a ``vault_dir``.

    Notes
    -----
    The intermediate ``report.md`` is written whenever Markdown itself or any of
    the compiled document formats is requested, because md2star compiles from
    that file. It is only recorded in the returned mapping when ``"md"`` was
    explicitly asked for.
    """
    tr, syn = _load(out_dir)
    # Normalise once so all downstream membership checks are case-insensitive.
    formats = [f.lower() for f in formats]
    written: dict[str, str] = {}

    # Markdown is the compilation source for docx/pdf/pptx, so materialise it
    # whenever Markdown or any document format is requested.
    md_path = os.path.join(out_dir, "report.md")
    if "md" in formats or _DOC_FMTS.intersection(formats):
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_markdown(tr, syn))
        if "md" in formats:
            written["md"] = md_path

    if "html" in formats:
        written["html"] = render_html(tr, syn, os.path.join(out_dir, "report.html"))

    # Compile every requested document format from the Markdown produced above.
    doc_fmts = [f for f in formats if f in _DOC_FMTS]
    if doc_fmts:
        written.update(compile_all(md_path, out_dir, doc_fmts))

    if "vault" in formats:
        if not vault_dir:
            raise ValueError("format 'vault' requires --vault <path>")
        written["vault"] = build_vault(tr, syn, vault_dir)["meeting"]

    return written
