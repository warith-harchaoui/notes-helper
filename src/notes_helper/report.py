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

import json
import os
import subprocess

from . import config
from .outputs.html import render_html
from .slides import build_slide_sync
from .synth import synthesize

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


def _ensure_transcript(audio: str, output_dir: str) -> list[dict]:
    """Diarize + transcribe ``audio`` into ``output_dir/transcript.json`` (reused if present).

    Shells out to the Rust ``nh-run`` in transcribe-only mode: it discovers the speaker count
    and runs the O(n) block-wise diarization + whisper.cpp ASR, writing ``transcript.json``.
    """
    tj = os.path.join(output_dir, "transcript.json")
    if not os.path.isfile(tj):
        core = os.path.join(config.PROJECT_ROOT, "core", "nh-run") if hasattr(config, "PROJECT_ROOT") else os.environ.get("NH_RUN_DIR", "")
        binary = os.environ.get("NH_RUN_BIN") or os.path.join(core, "target", "release", "nh-run")
        env = dict(os.environ)
        env.setdefault("DYLD_LIBRARY_PATH", os.path.join(os.path.dirname(binary)))
        env["NH_TRANSCRIBE_ONLY"] = "1"
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

    audio = _find_audio(input_dir)
    context = _load_context(input_dir)
    deck = _find_landscape_pdf(input_dir)

    # 1) transcript (Rust O(n) diar + ASR) and 2) the player's MP3.
    transcript = _ensure_transcript(audio, output_dir)
    audio_rel = _ensure_mp3(audio, output_dir)

    # Speakers discovered from the transcript; names stay as ids unless the caller enriches them.
    labels = sorted({u["speaker"] for u in transcript})
    speakers = {sp: {"name": sp, "role": ""} for sp in labels}

    # 3) synthesis (local LLM) — or empty tabs when disabled.
    if synth:
        syn = synthesize(
            transcript,
            speakers,
            title=title or name,
            model=model or config.OLLAMA_MODEL,
            language=language,
            context=context,
        )
    else:
        syn = {
            "meta": {"titre": title or name},
            "speakers": speakers,
            "resume": [],
            "points_cles": [],
            "decisions": [],
            "actions": [],
            "chapitres": [],
            "themes": [],
            "citations": [],
        }
    syn["meta"]["audio"] = audio_rel

    # 4) slides — only for a landscape deck (the sole Le Bench / sev7n difference).
    slide_sync = build_slide_sync(deck, transcript, output_dir) if deck else None

    # 5) polished interactive report.
    out_path = os.path.join(output_dir, "index.html")
    render_html(transcript, syn, out_path=out_path, slide_sync=slide_sync)
    return out_path
