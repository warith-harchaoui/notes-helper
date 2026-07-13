"""Obsidian vault renderer — People/ and Meetings/ Markdown with wikilinks and Tasks.

Module summary
--------------
This module writes an opt-in Obsidian vault from a transcript and its synthesis.
It is never assumed: it only runs when the caller explicitly asks for the
``vault`` format. In exchange, the cross-meeting *memory graph* comes for free
from Obsidian's backlinks, and action items are emitted as Tasks-plugin
compatible checkboxes (``- [ ]``) with ``[[assignee]]`` wikilinks and ``📅`` due
dates, so they roll up into a single cross-meeting ledger.

Two kinds of note are produced:

* ``Meetings/<date> <title>.md`` — one per meeting, with YAML front matter and
  sections for résumé, decisions, actions and citations.
* ``People/<name>.md`` — one per speaker; backlinks alone build the graph.

Every write goes through :func:`_write_preserving`, which keeps whatever the
user has written below the :data:`MARKER` line intact across re-runs — generated
content is only ever refreshed *above* the marker.

Usage example
-------------
>>> import tempfile, os
>>> from notes_helper.outputs.vault import build_vault
>>> syn = {
...     "meta": {"titre": "Kickoff", "date": "2026-07-10", "lieu": "", "duree": ""},
...     "speakers": {"S1": {"name": "Alice", "role": "PM"}},
...     "resume": ["We aligned on scope."],
... }
>>> with tempfile.TemporaryDirectory() as d:
...     paths = build_vault([], syn, d)
...     print(os.path.basename(paths["meeting"]))
2026-07-10 Kickoff.md
# expected output: 2026-07-10 Kickoff.md

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import os
import re

from ._timefmt import seconds as _seconds

# Sentinel that separates generated content (above) from user annotations
# (below). Everything below this line survives re-runs untouched.
MARKER: str = "<!-- notes-helper:generated — vos notes vont sous cette ligne, jamais écrasées -->"


def _slug(name: str) -> str:
    """Normalise a person's name into a safe, whitespace-collapsed file stem.

    Parameters
    ----------
    name : str
        The raw speaker display name.

    Returns
    -------
    str
        The name with runs of whitespace collapsed to a single space and the
        ends trimmed. Intentionally light-touch: Obsidian tolerates spaces and
        most punctuation in file names, so we only normalise whitespace.

    Examples
    --------
    >>> _slug("  Alice   Martin ")
    'Alice Martin'
    """
    return re.sub(r"\s+", " ", name).strip()


def _hhmmss(s: float | int | str | None) -> str:
    """Format a number of seconds as a zero-padded ``H:MM:SS`` timestamp.

    Parameters
    ----------
    s : float | int | None
        Seconds since the start of the recording. ``None`` and falsy values are
        coerced to ``0``.

    Returns
    -------
    str
        The timestamp as ``H:MM:SS``.

    Examples
    --------
    >>> _hhmmss(3723)
    '1:02:03'
    >>> _hhmmss("0:00:28")
    '0:00:28'
    """
    # Local LLMs emit chapter/quote times as seconds, floats, or already
    # formatted strings; coerce tolerantly so one bad value cannot abort render.
    s = _seconds(s)
    return f"{s//3600:d}:{(s%3600)//60:02d}:{s%60:02d}"


def _yaml_list(items: list[str]) -> str:
    """Render a list of strings as an inline, double-quoted YAML sequence.

    Parameters
    ----------
    items : list[str]
        Values to place in the sequence (typically ``[[wikilinks]]``).

    Returns
    -------
    str
        A YAML flow sequence such as ``["[[Alice]]", "[[Bob]]"]``. Emitting it
        inline keeps the front matter compact and diff-friendly.

    Examples
    --------
    >>> _yaml_list(["[[Alice]]", "[[Bob]]"])
    '["[[Alice]]", "[[Bob]]"]'
    """
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def _write_preserving(path: str, generated: str) -> None:
    """Write ``generated`` above :data:`MARKER`, preserving any user notes below it.

    Parameters
    ----------
    path : str
        Destination file path. Parent directories are created as needed.
    generated : str
        The freshly generated Markdown block to place above the marker.

    Returns
    -------
    None
        The file is written as a side effect.

    Notes
    -----
    On a re-run the existing file is read and everything after the first
    occurrence of :data:`MARKER` (the user's own annotations) is carried over
    verbatim, so hand-written notes are never clobbered. On a first run there is
    no tail to preserve.
    """
    tail = ""
    if os.path.exists(path):
        old = open(path, encoding="utf-8").read()
        # Keep only the portion the user owns — everything after the marker.
        if MARKER in old:
            tail = old.split(MARKER, 1)[1]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        # Normalise trailing whitespace on the generated block, then re-attach
        # the marker and the preserved user tail.
        f.write(generated.rstrip() + "\n\n" + MARKER + tail)


def build_vault(transcript: list[dict], syn: dict, vault_dir: str, *,
                audio_rel: str = "", report_rel: str = "") -> dict:
    """Build an Obsidian vault (meeting note plus one note per speaker).

    Parameters
    ----------
    transcript : list[dict]
        The diarized transcript. Accepted for signature symmetry with the other
        renderers; the vault is built entirely from ``syn``.
    syn : dict
        The synthesis dictionary. Requires ``"meta"`` and ``"speakers"``; the
        optional ``"resume"``, ``"decisions"``, ``"actions"`` and ``"citations"``
        keys each contribute their own section to the meeting note when present.
    vault_dir : str
        Root of the Obsidian vault. ``Meetings/`` and ``People/`` subfolders are
        created underneath it.
    audio_rel : str, keyword-only, optional
        Relative path to the audio file; when given, linked from the meeting
        note's front matter as ``audio: "[[...]]"``.
    report_rel : str, keyword-only, optional
        Relative path to the rendered report; when given, linked from the front
        matter as ``rapport: "[[...]]"``.

    Returns
    -------
    dict
        ``{"meeting": <meeting note path>, "people": [<person note paths>]}``.

    Notes
    -----
    Action items are emitted as Tasks-plugin checkboxes. An assignee is turned
    into a ``[[wikilink]]`` only when it matches a known participant name (so it
    joins the graph); otherwise it is written as plain text. A due date is
    appended with the ``📅`` emoji unless it is empty or the placeholder ``"—"``.
    """
    meta, speakers = syn["meta"], syn["speakers"]
    title = meta.get("titre", "Compte-rendu")
    date = meta.get("date", "")
    people = [i["name"] for i in speakers.values()]
    # Wikilinks to every participant, used both in front matter and in actions.
    links = [f"[[{p}]]" for p in people]

    # --- Meetings/<date> <title>.md ------------------------------------------
    meeting_name = f"{date} {title}".strip()
    # YAML front matter drives Obsidian's Dataview / metadata views.
    fm = [
        "---", "type: meeting", f"date: {date}", f'lieu: "{meta.get("lieu","")}"',
        f'duree: "{meta.get("duree","")}"', f"participants: {_yaml_list(links)}",
    ]
    if audio_rel:
        fm.append(f'audio: "[[{audio_rel}]]"')
    if report_rel:
        fm.append(f'rapport: "[[{report_rel}]]"')
    fm.append("---")
    body = ["\n".join(fm), f"\n# {title}\n"]
    if syn.get("resume"):
        body.append("## Résumé\n" + "\n\n".join(syn["resume"]) + "\n")
    if syn.get("decisions"):
        body.append("## Décisions\n" + "\n".join(
            f"- ✓ **{d['decision']}**" + (f" — {d['contexte']}" if d.get("contexte") else "")
            for d in syn["decisions"]) + "\n")
    if syn.get("actions"):
        # Tasks-plugin checkboxes with [[assignee]] + due date -> cross-meeting ledger.
        lines = []
        for a in syn["actions"]:
            resp = a.get("responsable", "")
            # Link the assignee into the graph only if they are a known speaker;
            # otherwise keep the free-text name as-is.
            who = f" [[{resp}]]" if resp and resp in people else (f" {resp}" if resp else "")
            due = f" 📅 {a['echeance']}" if a.get("echeance") and a["echeance"] != "—" else ""
            lines.append(f"- [ ] {a['action']}{who}{due}")
        body.append("## Actions\n" + "\n".join(lines) + "\n")
    if syn.get("citations"):
        # Resolve speaker ids to display names so quotes backlink to People/ notes.
        names = {sid: i.get("name", sid) for sid, i in speakers.items()}
        qs = []
        for q in syn["citations"]:
            who = names.get(q.get("speaker", ""), q.get("speaker", ""))
            ts = f" · {_hhmmss(q['t'])}" if q.get("t") is not None else ""
            qs.append(f"> « {q['texte']} » — [[{who}]]{ts}")
        body.append("## Citations\n" + "\n".join(qs) + "\n")

    meeting_path = os.path.join(vault_dir, "Meetings", f"{meeting_name}.md")
    _write_preserving(meeting_path, "\n".join(body))

    # --- People/<name>.md (one per speaker; backlinks do the graph) ----------
    written_people = []
    for _sid, info in speakers.items():
        name = info["name"]
        p_fm = ["---", "type: person", f'role: "{info.get("role","")}"']
        # Carry the voiceprint id when known so future runs can re-identify the
        # same speaker across meetings.
        if info.get("person_id"):
            p_fm.append(f'voiceprint_id: {info["person_id"]}')
        p_fm.append("---")
        p_body = "\n".join(p_fm) + f"\n\n# {name}\n"
        p_path = os.path.join(vault_dir, "People", f"{_slug(name)}.md")
        _write_preserving(p_path, p_body)
        written_people.append(p_path)

    return {"meeting": meeting_path, "people": written_people}
