"""Turn an input folder into a polished, interactive report — one function, two shapes.

:func:`build_report` generalizes across the project's two reference cases with a single code
path:

- a plain **conversation** (e.g. Le Bench): audio + optional context notes, **no slides**;
- a **slide-backed talk** (e.g. sev7n): audio + a **landscape** presentation PDF, rendered as a
  content-synced slide panel next to the player.

The only branch between them is whether the folder carries a *landscape* PDF (a slide deck);
a portrait PDF is treated as a background document, not slides. Everything else is identical.

Pipeline
--------
1. **diarization + ASR** — the Rust ``nh-run`` binary (O(n) block-wise diarization with an
   integer speaker count discovered from the audio, then whisper.cpp ASR) writes
   ``transcript.json``. Reused if already present.
2. **audio** — an optimized mono MP3 for the player (never a WAV; the heavy signal stays in
   the pipeline, only the compressed track is written).
3. **synthesis** — :func:`notes_helper.synth.synthesize` (local Ollama map/reduce) fills the
   Résumé / Points / Décisions / Actions / Chapitres / Thèmes / Citations tabs.
4. **slides** — if a landscape PDF is present, :func:`notes_helper.slides.build_slide_sync`
   rasterizes it and content-aligns each page to the moment it is discussed (out-of-order
   decks handled). Otherwise no slide panel.
5. **render** — :func:`notes_helper.outputs.html.render_html` writes ``index.html`` (player +
   cursor on every timestamp) with ``assets/`` copied alongside.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess

import yaml

from . import config
from .context import extract_document_text
from .outputs.html import render_html
from .slides import build_slide_sync
from .synth import assign_speaker_names, distill_context, synthesize

# A folder may carry a small YAML of ground truth (title, date, place, real speaker
# names, context, which PDF is the slide deck) that sharpens the whole report.
_NOTES_YAML_NAMES = ("notes.yaml", "notes.yml", "meeting.yaml", "meeting.yml")

# Media containers we accept as the recording; the largest one in the folder wins.
_AUDIO_EXTS = (
    ".m4a", ".mp3", ".wav", ".mp4", ".mov", ".aac", ".flac", ".ogg", ".webm", ".mkv",
)


def _find_audio(input_dir: str) -> str:
    """Return the largest media file in ``input_dir`` (the recording)."""
    cands = [
        os.path.join(input_dir, n)
        for n in os.listdir(input_dir)
        if os.path.splitext(n)[1].lower() in _AUDIO_EXTS
    ]
    if not cands:
        raise FileNotFoundError(f"no audio/video file in {input_dir}")
    return max(cands, key=lambda p: os.path.getsize(p))


def _find_landscape_pdf(input_dir: str) -> str | None:
    """Return the first **landscape** PDF in ``input_dir`` (a slide deck), else ``None``.

    Landscape (width > height) is the signature of a presentation; a portrait PDF is a
    document (manuscript, paper, notes) that only sits in the folder as context.
    """
    for name in sorted(os.listdir(input_dir)):
        if not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(input_dir, name)
        try:
            out = subprocess.run(
                ["pdfinfo", path], capture_output=True, text=True, check=True
            ).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        for line in out.splitlines():
            if line.startswith("Page size:"):
                nums = [
                    float(tok)
                    for tok in line.replace("Page size:", "").replace("x", " ").split()
                    if tok.replace(".", "", 1).isdigit()
                ]
                if len(nums) >= 2 and nums[0] > nums[1]:
                    return path
    return None


def _load_context(input_dir: str) -> str:
    """Read ``context.md`` from the folder (curated proper nouns / framing), or ``""``."""
    path = os.path.join(input_dir, "context.md")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return ""


def _load_notes(input_dir: str) -> dict:
    """Read the folder's ground-truth YAML (``notes.yaml`` …) if present, else ``{}``."""
    for name in _NOTES_YAML_NAMES:
        path = os.path.join(input_dir, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {}


def _as_date_str(value) -> str:
    """Coerce a YAML date (``datetime.date`` or string) to an ISO string, or ``""``."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10]
    return str(value).strip() if value else ""


def _norm_roster(spec) -> list[str]:
    """Normalize the YAML ``speakers`` field to a plain list of participant names.

    Accepts a list of strings (the documented shape) and tolerates a list of
    ``{name: …}`` dicts. The order carries no identity claim — which recorded voice
    is which person is *determined* later, not assumed from this list.
    """
    out: list[str] = []
    for item in spec or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            out.append(str(item["name"]).strip())
    return out


def _resolve_deck(input_dir: str, notes: dict) -> str | None:
    """Pick the slide deck PDF: the YAML ``slides:`` filename wins, else auto-detect.

    ``slides: my.pdf`` forces that file (a PDF in this folder) as the deck; leaving the
    key unset (or empty) falls back to the landscape-PDF heuristic — a portrait document
    is not a deck, so a folder with only a portrait PDF ends up with no slides.
    """
    spec = notes.get("slides")
    if spec:
        path = spec if os.path.isabs(spec) else os.path.join(input_dir, spec)
        return path if os.path.isfile(path) else None
    return _find_landscape_pdf(input_dir)


def _meeting_context(
    input_dir: str, notes: dict, roster: list[str], model: str, language: str | None
) -> str:
    """Assemble the synthesis context, highest-signal first so any cut keeps what matters.

    Order: a header from the YAML (title, date, place, participant roster); then the
    folder's ``context.md``; then each document in ``context_files`` — extracted and, when
    large, DISTILLED across several offline LLM passes (chunked, summarised, merged) rather
    than truncated, so the whole document informs the report; then ``additional_glossary``,
    which *completes* the context (proper nouns to spell right), never replaces it.
    """
    parts: list[str] = []

    header: list[str] = []
    if notes.get("title"):
        header.append(f"Réunion : {notes['title']}")
    if notes.get("date"):
        header.append(f"Date : {_as_date_str(notes['date'])}")
    if notes.get("location"):
        header.append(f"Lieu : {notes['location']}")
    if roster:
        header.append("Participants : " + ", ".join(roster))
    if header:
        parts.append("\n".join(header))

    md = _load_context(input_dir)
    if md:
        parts.append(md)

    focus = str(notes.get("title", ""))
    for name in notes.get("context_files") or []:
        path = name if os.path.isabs(name) else os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            text = extract_document_text(path).strip()
        except RuntimeError:
            continue  # a rich doc without its extractor installed — skip, don't fail the run
        if text:
            distilled = distill_context(text, model, focus=focus)
            parts.append(f"## {os.path.basename(path)}\n\n{distilled}")

    extra = notes.get("additional_glossary") or []
    if extra:
        parts.append("Termes et noms propres à respecter : " + ", ".join(map(str, extra)))

    return "\n\n".join(p for p in parts if p)


def _drop_spurious(transcript: list[dict], names: dict, roster: list[str]) -> tuple[list[dict], dict]:
    """Remove voices that are not persons of interest — a waiter, a neighbouring table.

    A cluster whose assigned name is not in the roster is spurious; its turns are dropped
    from the transcript entirely (and thus from the synthesis). As a safety net, if the
    naming matched *nobody* in the roster we keep everything rather than empty the report.
    """
    roster_set = set(roster)
    kept = {sid for sid, nm in names.items() if nm in roster_set}
    if not kept:
        return transcript, names
    return (
        [u for u in transcript if u["speaker"] in kept],
        {sid: nm for sid, nm in names.items() if sid in kept},
    )


def _ensure_transcript(audio: str, output_dir: str, min_speakers: int | None = None) -> list[dict]:
    """Diarize + transcribe ``audio`` into ``output_dir/transcript.json`` (reused if present).

    Shells out to the Rust ``nh-run`` in transcribe-only mode, which runs the O(n) block-wise
    diarization + whisper.cpp ASR and writes ``transcript.json``.

    The speaker count is DISCOVERED, never capped by the roster: a recording may hold voices
    beyond the persons of interest (a waiter, a neighbouring table) and those must stay
    distinct so they can be marked spurious downstream — not merged into a named person.
    ``min_speakers`` (the roster length) is only a FLOOR passed to the diarizer so discovery
    surfaces at least the people we know spoke; it never forbids finding more.
    """
    tj = os.path.join(output_dir, "transcript.json")
    if not os.path.isfile(tj):
        core = os.path.join(config.PROJECT_ROOT, "core", "nh-run") if hasattr(config, "PROJECT_ROOT") else os.environ.get("NH_RUN_DIR", "")
        binary = os.environ.get("NH_RUN_BIN") or os.path.join(core, "target", "release", "nh-run")
        env = dict(os.environ)
        env.setdefault("DYLD_LIBRARY_PATH", os.path.join(os.path.dirname(binary)))
        env["NH_TRANSCRIBE_ONLY"] = "1"
        if min_speakers is not None:
            env["NH_MIN_SPEAKERS"] = str(min_speakers)  # floor for discovery, not a cap
        subprocess.run([binary, audio, output_dir], env=env, check=True)
    with open(tj, encoding="utf-8") as fh:
        return json.load(fh)["utterances"]


def _ensure_mp3(audio: str, output_dir: str) -> str:
    """Transcode ``audio`` to an optimized mono MP3 for the player (reused if present)."""
    mp3 = os.path.join(output_dir, "audio.mp3")
    if not os.path.isfile(mp3):
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio, "-ac", "1", "-b:a", "64k", "-vn", mp3],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return "audio.mp3"


def build_report(
    input_dir: str,
    output_dir: str | None = None,
    *,
    title: str = "",
    model: str = "",
    language: str | None = None,
    synth: bool = True,
    reuse_synth: bool = False,
) -> str:
    """Build ``<output_dir>/index.html`` from the recording (and slides) in ``input_dir``.

    Parameters
    ----------
    input_dir : str
        Folder holding the recording (largest media file), an optional ``context.md`` and an
        optional presentation PDF (landscape → slides).
    output_dir : str, optional
        Where the report is written. Defaults to ``output/<input-folder-name>/``.
    title : str, optional
        Report title. Defaults to the input folder name.
    model : str, optional
        Ollama model for the synthesis. Defaults to :data:`config.OLLAMA_MODEL`.
    language : str, optional
        Force the report language; ``None`` (default) discovers it from the transcript.
    synth : bool, optional
        Run the local synthesis. ``False`` ships the report with empty summary tabs (useful
        when the local LLM is unavailable); the transcript, player and slides still stand.
    reuse_synth : bool, optional
        Skip the local LLM and reload the previous synthesis from ``synthese.json`` (also
        enabled by the ``NH_REUSE_SYNTH`` env var). This is the *render-only* path: a
        presentational change (labels, date format, layout) or an edited ``notes.yaml``
        header re-renders in seconds instead of re-running the model. Falls back to a full
        synthesis when no ``synthese.json`` exists yet.

    Returns
    -------
    str
        Path to the written ``index.html``.
    """
    input_dir = os.path.abspath(input_dir)
    name = os.path.basename(input_dir.rstrip("/"))
    if output_dir is None:
        output_dir = os.path.join("output", name)
    os.makedirs(output_dir, exist_ok=True)

    # Ground truth the user dropped in the folder (real names, date, place, deck, context).
    notes = _load_notes(input_dir)
    title = title or notes.get("title", "") or name
    if language is None and notes.get("language"):
        language = str(notes["language"])
    audio = _find_audio(input_dir)
    deck = _resolve_deck(input_dir, notes)

    # The roster (names) is the set of PERSONS OF INTEREST — WHO we care to name — not a cap
    # on how many voices exist. Its length is only a floor so discovery surfaces at least the
    # people we know spoke; extra voices (a waiter, another table) stay distinct and are
    # marked spurious when we name speakers, never merged into a named person.
    roster = _norm_roster(notes.get("speakers"))
    min_speakers = len(roster) or None

    # 1) transcript (Rust O(n) diar + ASR) and 2) the player's MP3.
    transcript = _ensure_transcript(audio, output_dir, min_speakers=min_speakers)
    audio_rel = _ensure_mp3(audio, output_dir)

    # The diarizer discovered HOW MANY voices; we then determine which id is which person of
    # interest from the conversation itself — the roster ORDER is not an identity claim, and
    # a voice matching no roster name keeps its id (spurious / external).
    resolved_model = model or config.OLLAMA_MODEL
    labels = sorted({u["speaker"] for u in transcript})

    # 3) synthesis (local LLM) — reused from disk, freshly computed, or empty tabs.
    synth_path = os.path.join(output_dir, "synthese.json")
    reuse = (reuse_synth or bool(os.environ.get("NH_REUSE_SYNTH"))) and os.path.exists(synth_path)
    if reuse:
        # Render-only: reload the saved synthesis, no LLM. Presentational changes and
        # notes.yaml header edits re-render in seconds this way. Re-apply the drop-spurious
        # policy so an edited roster (or a synthesis saved before it) stays consistent.
        with open(synth_path, encoding="utf-8") as fh:
            syn = json.load(fh)
        if roster:
            saved_names = {sid: info.get("name", sid) for sid, info in syn.get("speakers", {}).items()}
            transcript, kept_names = _drop_spurious(transcript, saved_names, roster)
            syn["speakers"] = {sid: syn["speakers"][sid] for sid in kept_names}
    else:
        if synth and roster:
            names = assign_speaker_names(transcript, roster, resolved_model, language)
            # Drop spurious voices (no roster match) before synthesis, so a waiter's words
            # never enter the summary, decisions or citations.
            transcript, names = _drop_spurious(transcript, names, roster)
            labels = sorted(names)
        else:
            names = {lbl: lbl for lbl in labels}
        speakers = {lbl: {"name": names.get(lbl, lbl), "role": ""} for lbl in labels}
        # Context feeds the synthesis (proper-noun spelling, framing): YAML header +
        # context.md + distilled context_files + additional_glossary, highest-signal first.
        context = _meeting_context(input_dir, notes, roster, resolved_model, language) if synth else ""
        if synth:
            syn = synthesize(
                transcript,
                speakers,
                title=title,
                date=_as_date_str(notes.get("date")),
                lieu=str(notes.get("location", "")),
                model=resolved_model,
                language=language,
                context=context,
            )
        else:
            syn = {
                "meta": {"titre": title},
                "speakers": speakers,
                "resume": [],
                "points_cles": [],
                "decisions": [],
                "actions": [],
                "chapitres": [],
                "themes": [],
                "citations": [],
            }

    # Header fields are ground truth from notes.yaml, refreshed on every render (including
    # render-only) so an edited title/date/place/time shows without re-running the model.
    # The date is stored ISO here; the renderer localizes it for display.
    meta = syn.setdefault("meta", {})
    meta["titre"] = title
    meta["date"] = _as_date_str(notes.get("date"))
    meta["lieu"] = str(notes.get("location", ""))
    meta["audio"] = audio_rel
    if notes.get("time"):
        meta["horaire"] = str(notes["time"])

    # Persist the synthesis so the next build can reuse it (render-only, no LLM).
    with open(synth_path, "w", encoding="utf-8") as fh:
        json.dump(syn, fh, ensure_ascii=False, indent=2)

    # 4) slides — a landscape deck (auto-detected) or the YAML's explicit ``slides:`` file.
    slide_sync = build_slide_sync(deck, transcript, output_dir) if deck else None

    # 5) polished interactive report.
    out_path = os.path.join(output_dir, "index.html")
    render_html(transcript, syn, out_path=out_path, slide_sync=slide_sync)
    return out_path
