"""
notes-helper — FastAPI HTTP surface.

Module summary
--------------
Exposes the pure-Python, model-light parts of the ``notes-helper`` pipeline as HTTP
endpoints so the toolkit can sit behind any reverse proxy and be consumed by
other services. Kept intentionally aligned with the rest of the ``*-helper``
suite (``os_helper.api`` / ``vocal_helper.api`` / …): **reads** are JSON-in /
JSON-out for cheap, side-effect-free helpers, and **actions** take multipart
uploads and stream a rendered artifact back.

Exposed surface:

- ``GET  /health`` — liveness probe.
- ``POST /normalize`` — coerce a raw ``synthese`` dict into the canonical
  render schema (the :func:`notes_helper.synth.normalize_synthese` boundary).
- ``POST /synth`` — turn a diarized transcript into a structured report
  (``synthese``) via the local Ollama LLM (degrades to the no-LLM heuristic
  when Ollama is unreachable — nothing leaves the device).
- ``POST /render`` — compile an uploaded ``transcript.json`` +
  ``synthese.json`` into ``md`` / ``html`` and stream the result (a single file,
  or a zip when several formats are requested).

The audio-in stages (``run``: decode → VAD → diarization → ASR) are **not**
exposed here: they need heavy on-device models and long-lived jobs, which belong
to a separate worker surface rather than a synchronous HTTP request.

Install the extra to get the runtime dependencies::

    pip install 'notes-helper[api]'

Then run the app with any ASGI server::

    uvicorn notes_helper.api:app --host 0.0.0.0 --port 8000
    # or: notes-helper-api      (see [project.scripts])

Usage example
-------------
>>> # Start the server:
>>> #   uvicorn notes_helper.api:app --reload
>>> # Normalise a drifted synthesis:
>>> #   curl -s -X POST localhost:8000/normalize \\
>>> #        -H 'Content-Type: application/json' \\
>>> #        -d '{"resume": "one para"}'
>>> # Render a report from two JSON artifacts:
>>> #   curl -F 'transcript=@transcript.json' -F 'synthese=@synthese.json' \\
>>> #        'localhost:8000/render?formats=md,html' -o report.zip
>>> # Full OpenAPI docs at http://localhost:8000/docs

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile

try:
    from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
    from fastapi.responses import FileResponse, StreamingResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The FastAPI HTTP surface requires the [api] extra. "
        "Install with: pip install 'notes-helper[api]'"
    ) from exc

# The pipeline imports below are light (no torch / whisper / Ollama pulled at
# import time); the heavy work happens lazily inside the called functions.
from .config import DEFAULT_LANGUAGE, OLLAMA_MODEL
from .outputs import render
from .synth import normalize_synthese, synthesize

app = FastAPI(
    title="notes-helper",
    version="0.3.1",
    description=(
        "Fully-local diarized meeting recorder — HTTP surface for the "
        "model-light stages (normalize / synth / render). Nothing leaves your "
        "device unless you decide."
    ),
)

# Formats the render endpoint can emit synchronously without extra system tools.
# docx / pdf / pptx go through md2star + Pandoc/LibreOffice, which are out of
# scope for a stateless HTTP request, so we keep the surface to md + html.
_RENDER_FORMATS: frozenset[str] = frozenset({"md", "html"})
_FMT_FILE: dict[str, str] = {"md": "report.md", "html": "report.html"}


class Utterance(BaseModel):
    """One diarized, time-stamped utterance."""

    t0: float = Field(..., description="Start time in seconds.")
    t1: float = Field(..., description="End time in seconds.")
    speaker: str = Field(..., description="Speaker id, e.g. ``S0``.")
    text: str = Field(..., description="Transcribed text for this turn.")


class SynthRequest(BaseModel):
    """Input for :func:`notes_helper.synth.synthesize`."""

    transcript: list[Utterance] = Field(..., description="Ordered diarized transcript.")
    speakers: dict[str, dict] = Field(
        default_factory=dict,
        description='Speaker map ``{"S0": {"name": ..., "role": ...}}``.',
    )
    title: str = Field(default="", description="Meeting title.")
    lieu: str = Field(default="", description="Meeting place.")
    language: str = Field(default=DEFAULT_LANGUAGE, description="Synthesis language code.")
    model: str = Field(default=OLLAMA_MODEL, description="Local Ollama model name.")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe.

    Returns
    -------
    dict of str
        ``{"status": "ok"}`` when the service is up.
    """
    return {"status": "ok"}


@app.post("/normalize")
def normalize(syn: dict) -> dict:
    """Coerce a raw synthesis dict into the canonical render schema.

    Parameters
    ----------
    syn : dict
        A possibly drifted synthesis (fields as lists where strings are
        expected, missing keys, timestamps as strings, …).

    Returns
    -------
    dict
        The normalised synthesis, exactly as the renderers consume it.
    """
    return normalize_synthese(syn)


@app.post("/synth")
def synth(req: SynthRequest) -> dict:
    """Turn a diarized transcript into a structured report via the local LLM.

    Parameters
    ----------
    req : SynthRequest
        Transcript, speaker map, and synthesis options.

    Returns
    -------
    dict
        The ``synthese`` dictionary (already normalised). If the local Ollama
        server is unreachable, a minimal no-LLM heuristic report is returned so
        the call still succeeds — no audio or text ever leaves the device.
    """
    transcript = [u.model_dump() for u in req.transcript]
    return synthesize(
        transcript,
        req.speakers,
        title=req.title,
        lieu=req.lieu,
        model=req.model,
        language=req.language,
    )


@app.post("/render", response_model=None)
async def render_report(
    background: BackgroundTasks,
    transcript: UploadFile = File(..., description="transcript.json"),
    synthese: UploadFile = File(..., description="synthese.json"),
    formats: str = Query("html", description="Comma-separated: md, html."),
) -> FileResponse | StreamingResponse:
    """Compile an uploaded transcript + synthesis into rendered report files.

    Parameters
    ----------
    background : fastapi.BackgroundTasks
        Used to delete the temp working directory after the response streams.
    transcript : fastapi.UploadFile
        The ``transcript.json`` artifact (a list of utterances).
    synthese : fastapi.UploadFile
        The ``synthese.json`` artifact (the structured report).
    formats : str, optional
        Comma-separated formats to emit; each of ``md`` / ``html``. Defaults to
        ``html``.

    Returns
    -------
    fastapi.responses.FileResponse or fastapi.responses.StreamingResponse
        A single rendered file when one format is requested, or an in-memory
        zip of every rendered file (report + copied assets) when several are.

    Raises
    ------
    fastapi.HTTPException
        400 if an unknown format is requested or the uploaded JSON is invalid.
    """
    wanted = [f.strip().lower() for f in formats.split(",") if f.strip()]
    bad = [f for f in wanted if f not in _RENDER_FORMATS]
    if not wanted or bad:
        raise HTTPException(
            status_code=400,
            detail=f"formats must be a subset of {sorted(_RENDER_FORMATS)}; got {bad or wanted}",
        )

    work = tempfile.mkdtemp(prefix="notes_helper_render_")
    background.add_task(_rmtree_quiet, work)
    try:
        _dump_upload(await transcript.read(), os.path.join(work, "transcript.json"))
        _dump_upload(await synthese.read(), os.path.join(work, "synthese.json"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON upload: {exc}") from exc

    written = render(work, wanted)

    # One format → return the file directly; several → zip the whole output dir
    # (rendered reports plus the self-contained HTML assets copied alongside).
    if len(wanted) == 1:
        path = written[wanted[0]]
        return FileResponse(path, filename=os.path.basename(path))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(work):
            for name in files:
                if name in {"transcript.json", "synthese.json"}:
                    continue  # inputs, not outputs
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, work))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=report.zip"},
    )


def _dump_upload(raw: bytes, path: str) -> None:
    """Validate that ``raw`` is JSON, then write it verbatim to ``path``.

    Parameters
    ----------
    raw : bytes
        The uploaded file body.
    path : str
        Destination path.

    Raises
    ------
    json.JSONDecodeError
        If the body is not valid JSON (surfaced as a 400 by the caller).
    """
    json.loads(raw)  # validate — the renderers assume well-formed JSON on disk
    with open(path, "wb") as f:
        f.write(raw)


def _rmtree_quiet(path: str) -> None:
    """Best-effort recursive delete of a temp directory (no error on failure)."""
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def main() -> None:
    """Entry point for the ``notes-helper-api`` console script.

    Boots the FastAPI app with ``uvicorn`` in single-worker mode. Meant for
    local / container usage; behind a real load balancer, run ``uvicorn`` /
    ``gunicorn`` directly against :data:`notes_helper.api.app`.
    """
    import uvicorn

    host = os.environ.get("NOTES_HELPER_HOST", "0.0.0.0")
    port = int(os.environ.get("NOTES_HELPER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, workers=1)


if __name__ == "__main__":  # pragma: no cover
    main()
