"""Compiled documents renderer — Markdown to DOCX / PDF / PPTX via md2star.

Module summary
--------------
This module compiles the neutral Markdown produced by
:mod:`notes_helper.outputs.markdown` into binary office documents (DOCX, PDF, PPTX)
using `md2star <https://github.com/warith-harchaoui/md2star>`_. md2star is open
source and embeddable, so generation runs *in-process, on-device*: the
sovereignty guarantee that governs the rest of notes-helper extends all the way to the
exported file — nothing is uploaded to convert a document.

Conversion is attempted in two stages, most-preferred first:

1. The embeddable Python API (:func:`compile_doc` probes several plausible
   function names on the ``md2star`` module), which keeps everything in-process.
2. A ``md2star`` command-line fallback, if the executable is on ``PATH``.

If neither is available a clear :class:`RuntimeError` tells the user how to
install the optional dependency (``pip install 'notes-helper[docs]'``).

Usage example
-------------
>>> from notes_helper.outputs.docs import compile_all
>>> # Compile an existing report.md into every supported format:
>>> written = compile_all("report.md", "out/", ["pdf", "docx"])  # doctest: +SKIP
>>> print(sorted(written))                                        # doctest: +SKIP
['docx', 'pdf']
# expected output: ['docx', 'pdf']

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable

import os_helper as osh

# Document formats md2star can emit. Kept in sync with ``_DOC_FMTS`` in the
# package ``__init__`` (which decides when the intermediate Markdown is built).
_FMTS: set[str] = {"docx", "pdf", "pptx"}


def compile_doc(md_path: str, out_path: str, fmt: str) -> str:
    """Compile a single Markdown file to one document format via md2star.

    Parameters
    ----------
    md_path : str
        Path to the source Markdown file.
    out_path : str
        Destination path for the compiled document.
    fmt : str
        Target format; case-insensitive. Must be one of :data:`_FMTS`
        (``"docx"``, ``"pdf"``, ``"pptx"``).

    Returns
    -------
    str
        ``out_path``, returned unchanged on success so callers can record it.

    Raises
    ------
    ValueError
        If ``fmt`` is not a supported format.
    RuntimeError
        If neither the md2star Python API nor the ``md2star`` CLI is available.
    subprocess.CalledProcessError
        If the CLI fallback runs but exits non-zero.

    Notes
    -----
    The conversion is resolved defensively across md2star versions. md2star
    >= 2.6 exposes ``md2star.cli._convert(fmt, argv)`` (the in-process entry its
    own HTTP ``/convert`` uses) and a subcommand CLI (``md2star docx in.md``);
    md2star <= 2.4 exposed top-level callables (``convert`` / ``render`` /
    ``to_<fmt>``) and a flat CLI (``in.md --to docx``). We try the modern
    in-process path, then the legacy callables, then the CLI (modern form
    first), so both old and new installs keep working.
    """
    fmt = fmt.lower()
    if fmt not in _FMTS:
        raise ValueError(f"unsupported format {fmt!r} (md2star: {sorted(_FMTS)})")

    # 1) Embeddable Python API (preferred — stays in-process, no subprocess).
    #    md2star >= 2.6 drives every format through ``md2star.cli._convert(fmt,
    #    argv)`` (the same entry point its own FastAPI ``/convert`` calls); it
    #    returns a process-style exit code and takes the post-subcommand argv.
    try:
        from md2star.cli import _convert  # type: ignore

        rc = _convert(fmt, [md_path, "-o", out_path])
        if rc == 0:
            return out_path
        osh.debug(f"md2star _convert({fmt!r}) exit={rc}; trying other paths")
    except ImportError:
        # Older md2star (<= 2.4) exposed the conversion as top-level callables.
        try:
            import md2star  # type: ignore

            for fn in ("convert", "render", "to_" + fmt, "md_to_" + fmt):
                f = getattr(md2star, fn, None)
                if callable(f):
                    try:
                        # convert/render take the format explicitly; the
                        # specialised to_<fmt>/md_to_<fmt> helpers imply it.
                        f(md_path, out_path, fmt) if fn in ("convert", "render") else f(
                            md_path, out_path
                        )
                        return out_path
                    except TypeError:
                        # Wrong arity for this md2star version — try the next.
                        continue
        except ImportError:
            osh.debug("md2star Python API unavailable; trying the CLI fallback")

    # 2) CLI fallback — only if an md2star executable is on PATH. md2star >= 2.6
    #    is subcommand-first (``md2star docx in.md -o out.docx``); older builds
    #    took a flat ``in.md --to docx -o out.docx``. Try the modern form first.
    exe = shutil.which("md2star")
    if exe:
        modern = subprocess.run([exe, fmt, md_path, "-o", out_path])
        if modern.returncode == 0:
            return out_path
        subprocess.run([exe, md_path, "--to", fmt, "-o", out_path], check=True)
        return out_path

    raise RuntimeError(
        "md2star not available — install with:  pip install 'notes-helper[docs]'  "
        "(https://github.com/warith-harchaoui/md2star)"
    )


def compile_all(md_path: str, out_dir: str, formats: Iterable[str], stem: str = "report") -> dict:
    """Compile a Markdown file into several document formats at once.

    Parameters
    ----------
    md_path : str
        Path to the source Markdown file.
    out_dir : str
        Directory where the compiled documents are written.
    formats : Iterable[str]
        Requested formats. Any value not in :data:`_FMTS` is silently ignored,
        so a mixed list (e.g. including ``"html"``) can be passed through safely.
    stem : str, optional
        Base file name (without extension) for each output. Defaults to
        ``"report"``, yielding ``report.docx``, ``report.pdf``, etc.

    Returns
    -------
    dict
        Mapping from each successfully produced format to its output path.

    Raises
    ------
    RuntimeError
        Propagated from :func:`compile_doc` if md2star is unavailable.
    """
    written: dict[str, str] = {}
    for fmt in formats:
        # Only compile recognised document formats; ignore anything else so the
        # caller can hand us the full requested-format list unfiltered.
        if fmt in _FMTS:
            out = os.path.join(out_dir, f"{stem}.{fmt}")
            written[fmt] = compile_doc(md_path, out, fmt)
    return written
