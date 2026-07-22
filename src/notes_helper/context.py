"""Associated-document ingestion: turn a slug's attached files into synth context.

Module summary
--------------
A conversation lives in ``input/<slug>/`` next to its audio and any *associated
documents* — briefs, manuscripts, reports, slide exports — that ground the synthesis
(proper-noun spelling, participants, domain framing). This module reads those documents
into one plain-text *context* string that :func:`notes_helper.synth.synthesize` consumes:

- **Text-native** files (``.md``, ``.txt``, ``.rst`` …) are read as UTF-8 directly.
- **Rich** documents (``.pdf``, ``.docx``, ``.pptx``, ``.odt``, ``.html`` …) are
  extracted with `kreuzberg <https://github.com/Goldziher/kreuzberg>`_, which uses the
  document's embedded text layer when present and falls back to OCR only when needed —
  so an attached PDF manuscript becomes usable context with no manual copy-paste.

Audio/video files and notes-helper's own generated artifacts (``transcript.json``,
``report.*``, checkpoints, logs …) are skipped, so pointing the collector at a slug's
folder "just works". Everything runs locally; no document leaves the machine.

The aggregate can be far larger than a local model's context window (a 282-page
manuscript is ~800 k characters). Fitting it is the caller's job: the honest move is to
*distil* it against the transcript — keep what the conversation actually references,
judged with the transcript's own confidence — rather than blindly truncate. This module
provides the raw material for that loop; it does not truncate on its own unless asked.

Usage example
-------------
>>> from notes_helper.context import collect_context
>>> ctx = collect_context("input/Le-Bench-georges-warith-2026-07-18")  # doctest: +SKIP
>>> print(ctx[:40])                                                    # doctest: +SKIP
# Contexte de la conversation — « Le Bench »

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import os

import os_helper as osh

# Files read verbatim as UTF-8 text — no extraction backend needed.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".markdown", ".txt", ".text", ".rst", ".org", ".tex", ".csv", ".json", ".yaml", ".yml"}
)

# Rich documents whose text is recovered with kreuzberg (text layer, else OCR).
RICH_SUFFIXES: frozenset[str] = frozenset(
    {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf", ".epub", ".html", ".htm"}
)

# Audio/video: this is the conversation itself, never its *context*.
MEDIA_SUFFIXES: frozenset[str] = frozenset(
    {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".oga", ".aac", ".opus", ".aif", ".aiff",
     ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
)

# notes-helper's own outputs — never feed a run's artifacts back in as its context.
GENERATED_NAMES: frozenset[str] = frozenset(
    {"transcript.json", "synthese.json", "report.md", "report.html", "index.html",
     "speaker_mapping.json", "run.log", "diar_checkpoint.npz", "audio.mp3", "audio_16k.wav",
     "robots.txt"}
)


def is_context_document(path: str) -> bool:
    """Return whether *path* is an associated document usable as context.

    Skips directories, hidden/dotfiles, media (the conversation's own audio/video),
    notes-helper's generated artifacts, and anything without a recognized text or rich
    suffix.

    Parameters
    ----------
    path : str
        Absolute or relative path to a candidate file.

    Returns
    -------
    bool
        ``True`` if the file should be read into the context, else ``False``.
    """
    if not os.path.isfile(path):
        return False
    name = os.path.basename(path)
    if name.startswith(".") or name in GENERATED_NAMES:
        return False
    suffix = os.path.splitext(name)[1].lower()
    if suffix in MEDIA_SUFFIXES:
        return False
    return suffix in TEXT_SUFFIXES or suffix in RICH_SUFFIXES


def extract_document_text(path: str) -> str:
    """Extract the plain text of one document.

    Text-native files are read as UTF-8 (errors replaced, never raised). Rich documents
    are handed to kreuzberg, which prefers the embedded text layer and only OCRs when a
    page has none — fast and lossless on born-digital PDFs, still functional on scans.

    Parameters
    ----------
    path : str
        Path to the document.

    Returns
    -------
    str
        The extracted text (empty string if nothing could be recovered).

    Raises
    ------
    RuntimeError
        If a rich document is given but ``kreuzberg`` is not installed, with the exact
        install hint — a missing extractor is a setup error, not a silent empty context.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix in TEXT_SUFFIXES:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if suffix in RICH_SUFFIXES:
        return _extract_rich(path)
    # Unknown suffix: best-effort UTF-8 read so we never hard-fail on an odd extension.
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _extract_rich(path: str) -> str:
    """Extract text from a rich document with kreuzberg (lazy import)."""
    try:
        from kreuzberg import extract_file_sync
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            f"reading {os.path.basename(path)} needs kreuzberg; install it with "
            "`pip install kreuzberg` (or the notes-helper[docs] extra)."
        ) from exc
    result = extract_file_sync(path)
    return (result.content or "").strip()


def collect_context(source: str | list[str], *, max_chars: int | None = None) -> str:
    """Aggregate a slug folder's associated documents into one context string.

    Each document is prefixed with a ``## <filename>`` header so the model can tell the
    sources apart. Files are visited in sorted order for a stable, reproducible context.

    Parameters
    ----------
    source : str or list of str
        Either a slug directory (all its context documents are collected, recursively)
        or an explicit list of file paths.
    max_chars : int, optional
        If given, stop once the aggregate reaches this many characters (documents are
        added whole until the budget is hit). ``None`` (default) returns everything —
        the honest raw material for a transcript-aware distillation, not a blind cut.

    Returns
    -------
    str
        The aggregated context. Empty string if no usable document was found.
    """
    paths = _resolve_paths(source)
    chunks: list[str] = []
    total = 0
    for path in paths:
        try:
            text = extract_document_text(path).strip()
        except RuntimeError as exc:
            osh.warning(f"  context: skipping {os.path.basename(path)} — {exc}")
            continue
        if not text:
            continue
        chunk = f"## {os.path.basename(path)}\n\n{text}\n"
        chunks.append(chunk)
        total += len(chunk)
        osh.info(f"  context: +{len(text)} chars from {os.path.basename(path)}")
        if max_chars is not None and total >= max_chars:
            osh.warning(
                f"  context: reached {max_chars}-char budget after "
                f"{len(chunks)} doc(s); remaining documents skipped"
            )
            break
    out = "\n".join(chunks)
    return out[:max_chars] if max_chars is not None else out


def _resolve_paths(source: str | list[str]) -> list[str]:
    """Normalize *source* (a directory or a file list) to a sorted list of documents."""
    if isinstance(source, list):
        return sorted(p for p in source if is_context_document(p))
    if os.path.isdir(source):
        found: list[str] = []
        for root, _dirs, files in os.walk(source):
            for name in files:
                full = os.path.join(root, name)
                if is_context_document(full):
                    found.append(full)
        return sorted(found)
    # A single file path.
    return [source] if is_context_document(source) else []
