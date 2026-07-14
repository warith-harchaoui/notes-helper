"""Automatic speech recognition: per-turn transcription with whisper.cpp.

Module summary
--------------
Given a waveform and the speaker turns produced by :mod:`notes_helper.diarize`, this
module transcribes each turn independently with a local whisper.cpp model (via
the ``vocal_helper`` package) and returns time-stamped, speaker-attributed
utterances. Transcribing per turn — rather than the whole file at once — keeps
each ASR call short, bounds memory, and lets us drop empty/failed turns without
losing the rest. Everything runs locally; no audio leaves the machine.

Usage example
-------------
>>> import numpy as np
>>> from notes_helper import asr
>>> audio = np.zeros(16000, dtype=np.float32)
>>> turns = [{"t0": 0.0, "t1": 1.0, "spk": 0}]
>>> utts = asr.transcribe(audio, turns, language="fr")   # doctest: +SKIP
>>> print(utts)                                          # doctest: +SKIP
[{'t0': 0.0, 't1': 1.0, 'speaker': 'S0', 'text': 'bonjour'}]
# expected output: [{'t0': 0.0, 't1': 1.0, 'speaker': 'S0', 'text': 'bonjour'}]

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import time

import numpy as np
import os_helper as osh

from .config import DEFAULT_LANGUAGE, SR, WHISPER_MODEL


def transcribe(
    audio: np.ndarray,
    turns: list[dict],
    *,
    language: str = DEFAULT_LANGUAGE,
    model: str = WHISPER_MODEL,
    initial_prompt: str = "",
) -> list[dict]:
    """Transcribe each speaker turn into a time-stamped utterance.

    Parameters
    ----------
    audio : numpy.ndarray
        1-D float32 waveform at :data:`SR`.
    turns : list of dict
        Speaker turns as ``{"t0": float, "t1": float, "spk": int}`` (from
        :func:`notes_helper.diarize.merge_turns`).
    language : str, optional
        Language code passed to whisper.cpp. Defaults to :data:`DEFAULT_LANGUAGE`.
    model : str, optional
        whisper.cpp model name. Defaults to :data:`WHISPER_MODEL`.
    initial_prompt : str, optional
        Optional priming prompt (e.g. domain vocabulary) to bias decoding.

    Returns
    -------
    list of dict
        Utterances ``{"t0": float, "t1": float, "speaker": str, "text": str}``,
        with times rounded to 2 decimals and the speaker as ``"S{spk}"``. Turns
        that transcribe to empty text are omitted.

    Notes
    -----
    Per-turn ASR failures are logged and treated as empty text rather than
    aborting the batch. Progress (with a running ETA) is emitted every 100 turns.
    """
    from vocal_helper.asr import WhisperStage
    from vocal_helper.types import DiarizedSegment

    stage = WhisperStage(
        model=model, language=language, word_timestamps=False, initial_prompt=initial_prompt
    )
    stage._ensure_model()

    out: list[dict] = []
    n = len(turns)
    t0 = time.time()
    for k, tn in enumerate(turns):
        pcm = audio[int(tn["t0"] * SR) : int(tn["t1"] * SR)]
        seg = DiarizedSegment(
            t0=tn["t0"],
            t1=tn["t1"],
            sample_rate=SR,
            speaker=f"S{tn['spk']}",
            pcm=pcm.astype(np.float32, copy=False),
        )
        try:
            utt = stage._transcribe_blocking(seg)
            text = "" if utt is None else utt["text"].strip()
        except Exception as e:
            # A single failed turn should not abort the whole transcript.
            osh.warning(f"  asr fail turn {k}: {e}")
            text = ""
        if text:  # skip silent/failed turns so the transcript stays clean
            out.append(
                {
                    "t0": round(tn["t0"], 2),
                    "t1": round(tn["t1"], 2),
                    "speaker": f"S{tn['spk']}",
                    "text": text,
                }
            )
        if (k + 1) % 100 == 0:
            # Linear-extrapolation ETA: cheap, and good enough since turns are
            # roughly uniform in cost.
            el = time.time() - t0
            eta = el / (k + 1) * (n - k - 1)
            osh.info(f"  asr: {k + 1}/{n}  elapsed={el / 60:.1f}m eta={eta / 60:.1f}m")
    return out
