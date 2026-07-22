"""Web audio: speech-aware preprocessing + optimized encoding for the report player.

Module summary
--------------
The interactive report ships its audio so the reader can listen while the transcript and
slides follow along. Serving the pipeline's ``audio_16k.wav`` there is a bad idea: a
multi-hour 16 kHz WAV is hundreds of megabytes — heavy to load, impossible to share.

This module turns that raw audio into small, clean, web-friendly sources with a little
signal processing up front:

1. **De-rumble** — a high-pass at 80 Hz removes sub-bass rumble/handling noise that wastes
   bitrate and helps nothing in speech.
2. **Denoise (optional)** — ``afftdn`` shaves steady background hiss/hum for cleaner voice.
3. **Loudness-normalize** — EBU R128 ``loudnorm`` to a broadcast-style target so quiet and
   loud recordings play back at a consistent, comfortable level (no more riding the volume).
4. **Encode small** — mono **Opus ~32 kbps** (excellent for voice) as the primary source,
   with a mono **MP3 ~72 kbps** fallback for players that lack Opus. The player is handed
   both via ``audio_sources`` and picks the first it supports.

Everything runs locally through ffmpeg. The result is typically ~10-20× smaller than the
WAV at transparent speech quality.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import os
import shutil
import subprocess

import os_helper as osh

# Speech-tuned filter chain, applied before both encodes:
#   highpass  — drop everything below 80 Hz (rumble, handling, HVAC thrum)
#   loudnorm  — EBU R128 to -16 LUFS / -1.5 dBTP, the comfortable spoken-word target
# Denoise is added conditionally (it can dull very clean speech, so it is opt-in).
_HIGHPASS = "highpass=f=80"
_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
_DENOISE = "afftdn=nr=12"

# Output specs: (filename, ffmpeg args, <source> MIME type). Opus first — the player
# takes the first source it can decode, so modern browsers get the small file and older
# ones fall back to MP3.
_OPUS = ("audio.ogg", ["-c:a", "libopus", "-b:a", "32k", "-application", "voip"], "audio/ogg; codecs=opus")
_MP3 = ("audio.mp3", ["-c:a", "libmp3lame", "-q:a", "5"], "audio/mpeg")


def encode_web_audio(src: str, out_dir: str, *, denoise: bool = False) -> list[dict]:
    """Preprocess and encode *src* into small web sources under *out_dir*.

    Parameters
    ----------
    src : str
        Path to the source audio (e.g. the pipeline's ``audio_16k.wav`` or an original
        recording). Anything ffmpeg can read is accepted.
    out_dir : str
        Where the report lives; ``audio.ogg`` and ``audio.mp3`` are written here.
    denoise : bool, optional
        Insert a light spectral denoise (``afftdn``) in the chain. Off by default because
        it can soften already-clean speech; turn it on for noisy field recordings.

    Returns
    -------
    list of dict
        ``audio_sources`` for the report, e.g.
        ``[{"src": "audio.ogg", "type": "audio/ogg; codecs=opus"},
           {"src": "audio.mp3", "type": "audio/mpeg"}]`` — relative to *out_dir* so the
        report stays self-contained and portable. Empty if ffmpeg is unavailable or the
        source cannot be read.
    """
    if not shutil.which("ffmpeg"):
        osh.warning("  web-audio: ffmpeg not on PATH — the report will have no player")
        return []
    if not os.path.isfile(src):
        osh.warning(f"  web-audio: source not found ({src}) — skipping player audio")
        return []

    os.makedirs(out_dir, exist_ok=True)
    chain = [_HIGHPASS] + ([_DENOISE] if denoise else []) + [_LOUDNORM]
    af = ",".join(chain)

    sources: list[dict] = []
    for name, codec_args, mime in (_OPUS, _MP3):
        dst = os.path.join(out_dir, name)
        cmd = [
            "ffmpeg", "-nostdin", "-y", "-i", src,
            "-af", af, "-ac", "1", "-vn",  # mono, speech-preprocessed, no video
            *codec_args, dst,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            # One failed encode should not sink the others (or the whole report).
            detail = getattr(exc, "stderr", b"")
            tail = detail.decode("utf-8", "replace").strip().splitlines()[-1:] if detail else [str(exc)]
            osh.warning(f"  web-audio: {name} encode failed — {' '.join(tail)}")
            continue
        size_mb = os.path.getsize(dst) / 1e6
        osh.info(f"  web-audio: wrote {name} ({size_mb:.1f} MB, mono, R128-normalized)")
        sources.append({"src": name, "type": mime})
    return sources
