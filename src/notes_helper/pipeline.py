"""Orchestration: any audio file to transcript.json (plus a diarization checkpoint).

Module summary
--------------
This module wires the local stages together into a single entry point,
:func:`run`::

    audio -> (ffmpeg 16k mono) -> VAD/diarize -> identify -> ASR -> clean -> transcript.json

Every stage runs locally. The only subprocess is ffmpeg, used purely to decode
and resample the input audio to the pipeline's canonical 16 kHz mono WAV. A
diarization checkpoint (``.npz``) and an optional speaker-mapping file are also
written so intermediate results survive a crash and can feed identity matching.

Usage example
-------------
>>> from notes_helper import pipeline
>>> artifacts = pipeline.run("meeting.m4a", "out/", n_spk=2)   # doctest: +SKIP
>>> print(sorted(artifacts))                                  # doctest: +SKIP
['checkpoint', 'out_dir', 'speaker_mapping', 'transcript', 'wav']
# expected output: ['checkpoint', 'out_dir', 'speaker_mapping', 'transcript', 'wav']

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import numpy as np
import os_helper as osh

from . import asr as _asr
from . import clean as _clean
from . import diarize as _diar
from .config import DB_PATH, SR


def to_wav16k(src: str, dst: str) -> str:
    """Decode and resample any audio file to 16 kHz mono WAV via ffmpeg.

    Parameters
    ----------
    src : str
        Path to the source audio (any ffmpeg-decodable format).
    dst : str
        Destination WAV path to write.

    Returns
    -------
    str
        The destination path ``dst``.

    Raises
    ------
    RuntimeError
        If ffmpeg is not found on ``PATH``.
    subprocess.CalledProcessError
        If ffmpeg exits non-zero.

    Notes
    -----
    ffmpeg is the pipeline's single external dependency; forcing mono/16 kHz here
    guarantees every downstream stage sees the sample rate it asserts on.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH (needed to decode audio)")
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", str(SR), "-f", "wav", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


def run(audio_path: str, out_dir: str, *, n_spk: int | None = None,
        language: str = "fr", initial_prompt: str = "", identify: bool = True,
        db_path: str = DB_PATH) -> dict:
    """Run the full local pipeline on one audio file.

    Parameters
    ----------
    audio_path : str
        Path to the input audio (any ffmpeg-decodable format, or a ready 16 kHz
        mono WAV which is copied as-is).
    out_dir : str
        Directory to write all artifacts into; created if missing.
    n_spk : int, optional
        Known speaker count passed to diarization; ``None`` estimates it.
    language : str, optional
        ASR language code (default ``"fr"``).
    initial_prompt : str, optional
        Optional ASR priming prompt (e.g. domain vocabulary).
    identify : bool, optional
        If ``True`` (default), attempt cross-recording speaker identification
        against the people store; failures are logged and skipped, never fatal.
    db_path : str, optional
        Path to the people store used for identification. Defaults to
        :data:`DB_PATH`.

    Returns
    -------
    dict
        Paths of the written artifacts: ``{"wav", "checkpoint", "transcript",
        "speaker_mapping", "out_dir"}``. ``"speaker_mapping"`` is ``None`` when
        identification is disabled or skipped.
    """
    os.makedirs(out_dir, exist_ok=True)
    osh.info(f"=== notes-helper pipeline: {os.path.basename(audio_path)} ===")

    wav = os.path.join(out_dir, "audio_16k.wav")
    if not (audio_path.endswith(".wav") and _is_16k_mono(audio_path)):
        # Anything that is not already 16 kHz mono WAV gets normalised by ffmpeg;
        # an already-conforming WAV is copied to keep a self-contained out_dir.
        osh.info("resampling to 16 kHz mono (ffmpeg)...")
        to_wav16k(audio_path, wav)
    else:
        shutil.copyfile(audio_path, wav)

    audio = _diar.load_audio(wav)
    osh.info(f"audio: {len(audio)/SR/60:.1f} min")

    segs, labels, X, ok, turns = _diar.diarize(audio, n_spk=n_spk)
    # Checkpoint the diarization so a later crash (or an identity re-run) does not
    # force recomputing VAD + embeddings, the expensive part of the pipeline.
    ckpt = os.path.join(out_dir, "diar_checkpoint.npz")
    np.savez(ckpt, segs=np.array(segs, dtype=object), labels=labels, X=X, ok=ok)
    osh.info(f"checkpoint -> {ckpt}")

    mapping_path: str | None = None
    if identify:
        try:
            from .identity import PeopleStore, identify_recording
            store = PeopleStore(db_path)
            mapping = identify_recording(X, labels, store)
            store.close()
            mapping_path = os.path.join(out_dir, "speaker_mapping.json")
            with open(mapping_path, "w") as f:
                json.dump({"mapping": {k: v["name"] for k, v in mapping.items()},
                           "detail": mapping}, f, ensure_ascii=False, indent=2)
            dbg = ", ".join(f"{k}->{v['name']}({v['mode']})" for k, v in mapping.items())
            osh.info(f"identity: {dbg}")
        except Exception as e:
            # Identification is best-effort: a missing/empty store must not block
            # producing the transcript.
            osh.warning(f"identity step skipped: {e}")

    osh.info(f"transcribing {len(turns)} turns...")
    raw = _asr.transcribe(audio, turns, language=language, initial_prompt=initial_prompt)
    cleaned = _clean.clean(raw)
    tr_path = os.path.join(out_dir, "transcript.json")
    with open(tr_path, "w") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=1)
    osh.info(f"=== done: {len(cleaned)} utterances -> {tr_path} ===")

    return {"wav": wav, "checkpoint": ckpt, "transcript": tr_path,
            "speaker_mapping": mapping_path, "out_dir": out_dir}


def _is_16k_mono(wav_path: str) -> bool:
    """Report whether a WAV file is already 16 kHz mono.

    Parameters
    ----------
    wav_path : str
        Path to a candidate WAV file.

    Returns
    -------
    bool
        ``True`` iff the file is readable and has sample rate :data:`SR` with a
        single channel. Any read error yields ``False`` (treat as needing
        resampling) rather than raising.
    """
    try:
        import soundfile as sf
        info = sf.info(wav_path)
        return info.samplerate == SR and info.channels == 1
    except Exception:
        return False
