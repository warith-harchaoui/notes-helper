"""Markdown renderer — the neutral, lock-in-free source of truth for every export.

Module summary
--------------
This module turns a diarized transcript plus its synthesis dictionary into a
single Markdown string. Markdown is deliberately the neutral core of the output
pipeline: it is plain text the user owns, with no vendor lock-in. Obsidian is
merely one consumer of it (see :mod:`notes_helper.outputs.vault`); Logseq, Bear, git,
or a plain folder of files are equally valid destinations.

The public entry point :func:`render_markdown` assembles the document section by
section (title, metadata line, participants, résumé, key points, decisions,
actions table, chapters, themes, quotes, and optionally the full transcript),
building up a list of lines that is joined at the very end. It does not perform
any I/O — callers decide where the returned string is written.

Usage example
-------------
>>> from notes_helper.outputs.markdown import render_markdown
>>> syn = {
...     "meta": {"titre": "Kickoff", "date": "2026-07-10", "lieu": "", "duree": ""},
...     "speakers": {"S1": {"name": "Alice", "role": "PM"}},
...     "resume": ["We aligned on scope."],
... }
>>> md = render_markdown([], syn, include_transcript=False)
>>> print(md.splitlines()[0])
# Kickoff
# expected output: # Kickoff

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

from ._timefmt import seconds as _seconds


def _hhmmss(s: float | int | str | None) -> str:
    """Format a number of seconds as a zero-padded ``H:MM:SS`` timestamp.

    Parameters
    ----------
    s : float | int | None
        Seconds since the start of the recording. ``None`` and falsy values are
        coerced to ``0`` so the function never raises on missing timestamps.

    Returns
    -------
    str
        The timestamp as ``H:MM:SS`` (hours are not zero-padded, minutes and
        seconds are), e.g. ``"1:02:03"``.

    Examples
    --------
    >>> _hhmmss(3723)
    '1:02:03'
    >>> _hhmmss(None)
    '0:00:00'
    >>> _hhmmss("0:00:28")
    '0:00:28'
    """
    # Coerce first: callers may pass None, floats straight from JSON, or a
    # string. Local LLMs emit chapter/quote times inconsistently — as seconds
    # ("28"), as floats, or already formatted ("0:00:28") — so accept all forms
    # rather than raising and aborting the whole render.
    s = _seconds(s)
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def render_markdown(transcript: list[dict], syn: dict, *, include_transcript: bool = True) -> str:
    """Render a full Markdown compte-rendu from a transcript and its synthesis.

    Parameters
    ----------
    transcript : list[dict]
        Ordered utterances. Each dict is expected to carry ``"t0"`` (start time
        in seconds), ``"speaker"`` (a speaker id), and ``"text"``. Only used when
        ``include_transcript`` is true and the list is non-empty.
    syn : dict
        The synthesis dictionary. Required keys are ``"meta"`` (title/date/place/
        duration) and ``"speakers"`` (a mapping of speaker id to ``{"name",
        "role", ...}``). Optional keys — ``"resume"``, ``"points_cles"``,
        ``"decisions"``, ``"actions"``, ``"chapitres"``, ``"themes"``,
        ``"citations"`` — each add their own section only when present.
    include_transcript : bool, keyword-only, optional
        Whether to append the verbatim transcript section at the end. Defaults to
        ``True``.

    Returns
    -------
    str
        The complete Markdown document, terminated by a trailing newline.

    Notes
    -----
    The output is assembled into a list ``L`` of lines that is joined once at the
    end — this is a string-building routine, not an I/O routine, and it must stay
    byte-for-byte stable because compiled documents (DOCX/PDF/PPTX) are produced
    from it.
    """
    meta, speakers = syn["meta"], syn["speakers"]
    # Map every speaker id to a display name, falling back to the id itself so a
    # missing "name" never produces a blank byline.
    names = {sid: info.get("name", sid) for sid, info in speakers.items()}
    L: list[str] = []
    L.append(f"# {meta.get('titre', 'Compte-rendu')}\n")
    # Metadata line: build all bits, then drop the ones whose value is empty or a
    # placeholder dash so we never render "Lieu : —" for meetings with no place.
    bits = [
        f"**Date** : {meta.get('date', '')}",
        f"**Lieu** : {meta.get('lieu', '') or '—'}",
        f"**Durée** : {meta.get('duree', '')}",
    ]
    L.append("  ·  ".join(b for b in bits if b.split(":", 1)[1].strip() not in ("", "—")))
    parts = ", ".join(
        f"{i['name']}" + (f" ({i['role']})" if i.get("role") else "") for i in speakers.values()
    )
    if parts:
        L.append(f"\n**Participants** : {parts}\n")

    # Each of the following sections is emitted only when its key is present, so
    # an empty synthesis yields just the header block above.
    if syn.get("resume"):
        L.append("\n## Résumé\n")
        L += [p for p in syn["resume"]]
    if syn.get("points_cles"):
        L.append("\n## Points clés\n")
        L += [f"- {x}" for x in syn["points_cles"]]
    if syn.get("decisions"):
        L.append("\n## Décisions\n")
        for d in syn["decisions"]:
            L.append(
                f"- ✓ **{d['decision']}**" + (f" — {d['contexte']}" if d.get("contexte") else "")
            )
    if syn.get("actions"):
        # Actions render as a three-column Markdown table with dash placeholders
        # for any missing responsable / échéance.
        L.append("\n## Actions\n")
        L.append("| Action | Responsable | Échéance |")
        L.append("|---|---|---|")
        for a in syn["actions"]:
            L.append(f"| {a['action']} | {a.get('responsable', '—')} | {a.get('echeance', '—')} |")
    if syn.get("chapitres"):
        L.append("\n## Chapitres\n")
        for c in syn["chapitres"]:
            L.append(
                f"- `{_hhmmss(c['t'])}` **{c['titre']}**"
                + (f" — {c['resume']}" if c.get("resume") else "")
            )
    if syn.get("themes"):
        L.append("\n## Thèmes\n")
        for t in syn["themes"]:
            L.append(f"\n### {t['theme']}\n")
            L += [f"- {p}" for p in t.get("points", [])]
    if syn.get("citations"):
        L.append("\n## Citations\n")
        for q in syn["citations"]:
            # Resolve the speaker id to a display name; a timestamp is appended
            # only when the quote carries one.
            who = names.get(q.get("speaker", ""), q.get("speaker", ""))
            ts = f" · {_hhmmss(q['t'])}" if q.get("t") is not None else ""
            L.append(f"> « {q['texte']} »\n> — {who}{ts}\n")
    if include_transcript and transcript:
        L.append("\n## Transcript\n")
        for u in transcript:
            L.append(
                f"`{_hhmmss(u['t0'])}` **{names.get(u['speaker'], u['speaker'])}** : {u['text']}"
            )
    # Join once and guarantee a trailing newline for POSIX-friendly text files.
    return "\n".join(L) + "\n"
