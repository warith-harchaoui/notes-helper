"""Speaker diarization: Silero VAD, TitaNet embeddings, agglomerative clustering.

Module summary
--------------
This module answers the question "who spoke when?" for a single recording,
entirely with local backends (the ``vocal_helper`` package). The pipeline is:

1. **Voice-activity detection (VAD)** with Silero to slice the waveform into
   voiced segments and drop the silence in between (:func:`run_vad`).
2. **Speaker embedding** with TitaNet: each voiced segment is mapped to a
   192-dimensional L2-normalised vector living in an absolute embedding space
   (:func:`embed_segments`).
3. **Clustering** of those vectors into speakers with agglomerative clustering
   over a *per-recording centered* cosine space (:func:`cluster`).
4. **Turn merging** into contiguous speaker turns (:func:`merge_turns`).

The raw L2-normalised embeddings are returned alongside the labels so that
``notes_helper.identity`` can match the same physical person ACROSS recordings in the
absolute (un-centered) space. The clustering itself deliberately works in a
different, per-recording centered space — see :func:`cluster` for why.

Usage example
-------------
>>> import numpy as np
>>> from notes_helper import diarize
>>> audio = diarize.load_audio("meeting_16k.wav")   # doctest: +SKIP
>>> segs, labels, X, ok, turns = diarize.diarize(audio, n_spk=2)  # doctest: +SKIP
>>> print(turns[0])                                  # doctest: +SKIP
{'t0': 0.0, 't1': 3.2, 'spk': 0}
# expected output: {'t0': 0.0, 't1': 3.2, 'spk': 0}

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import asyncio

import numpy as np
import os_helper as osh

from .config import MAX_TURN_S, MERGE_GAP_S, SR

# TitaNet emits a fixed-width speaker embedding; kept as a module constant so
# the pre-allocated matrix in embed_segments() and any downstream consumer agree
# on the dimensionality without a magic number scattered around.
TITANET_DIM: int = 192


def load_audio(wav_path: str) -> np.ndarray:
    """Load a mono 16 kHz WAV into a contiguous float32 array.

    Parameters
    ----------
    wav_path : str
        Path to a WAV file already at the pipeline sample rate (:data:`SR`).

    Returns
    -------
    numpy.ndarray
        1-D contiguous float32 waveform. Multi-channel input is downmixed by
        averaging the channels.

    Raises
    ------
    AssertionError
        If the file's sample rate does not match :data:`SR`. Resampling is the
        pipeline's responsibility (``pipeline.to_wav16k``), not this loader's,
        so we fail loudly rather than silently returning off-rate audio.
    """
    import soundfile as sf

    audio, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        # Downmix to mono: TitaNet and Silero both expect a single channel, and
        # averaging preserves the dominant voice better than picking one channel.
        audio = audio.mean(axis=1)
    assert sr == SR, f"expected {SR} Hz, got {sr} — resample first (pipeline.to_wav16k)"
    return np.ascontiguousarray(audio, dtype=np.float32)


async def _run_vad(audio: np.ndarray) -> list[tuple[float, float]]:
    """Async driver behind :func:`run_vad`.

    The Silero stage is a producer/consumer pipeline: we feed fixed-size frames
    into an inbox queue and drain voiced ``(t0, t1)`` spans from an outbox queue.
    Running feeder, stage and collector concurrently keeps the model fed while
    results are collected, without materialising every frame up front.

    Parameters
    ----------
    audio : numpy.ndarray
        1-D float32 waveform at :data:`SR`.

    Returns
    -------
    list of tuple of (float, float)
        Voiced ``(start_s, end_s)`` spans in seconds.
    """
    from vocal_helper.types import PcmFrame
    from vocal_helper.vad import SileroVADStage

    stage = SileroVADStage(sample_rate=SR)
    # maxsize=8 bounds memory/back-pressure so the feeder cannot race arbitrarily
    # far ahead of the (slower) VAD stage on long recordings.
    inbox: asyncio.Queue = asyncio.Queue(maxsize=8)
    outbox: asyncio.Queue = asyncio.Queue()
    # Feed 30-second frames: a good trade-off between per-call overhead and the
    # VAD's latency/context window.
    FR = SR * 30

    async def feeder() -> None:
        for i in range(0, len(audio), FR):
            await inbox.put(PcmFrame(t0=i / SR, sample_rate=SR, pcm=audio[i:i + FR].copy()))
        await inbox.put(None)  # sentinel: signals end-of-stream to the stage

    segs: list[tuple[float, float]] = []

    async def collector() -> None:
        while True:
            item = await outbox.get()
            if item is None:  # stage propagates the sentinel when finished
                break
            segs.append((float(item["t0"]), float(item["t1"])))

    await asyncio.gather(feeder(), stage.run(inbox, outbox), collector())
    return segs


def run_vad(audio: np.ndarray) -> list[tuple[float, float]]:
    """Detect voiced spans in a waveform (synchronous wrapper).

    Parameters
    ----------
    audio : numpy.ndarray
        1-D float32 waveform at :data:`SR`.

    Returns
    -------
    list of tuple of (float, float)
        Voiced ``(start_s, end_s)`` spans in seconds.

    Notes
    -----
    Thin ``asyncio.run`` wrapper over :func:`_run_vad` so callers do not need to
    manage an event loop.
    """
    return asyncio.run(_run_vad(audio))


def embed_segments(audio: np.ndarray, segs: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Compute a TitaNet speaker embedding for each voiced segment.

    Each segment is mapped to a 192-dim vector and L2-normalised, so it lives on
    the unit sphere in the raw (absolute) embedding space. That absolute space is
    what lets ``notes_helper.identity`` compare speakers across recordings; the
    clustering step later re-centers a copy of these vectors for a different
    purpose (see :func:`cluster`).

    Parameters
    ----------
    audio : numpy.ndarray
        1-D float32 waveform at :data:`SR`.
    segs : list of tuple of (float, float)
        Voiced ``(start_s, end_s)`` spans, typically from :func:`run_vad`.

    Returns
    -------
    X : numpy.ndarray
        ``(len(segs), TITANET_DIM)`` float32 matrix of embeddings. Rows for
        unusable segments are left as zeros.
    ok : numpy.ndarray
        ``(len(segs),)`` boolean mask marking rows that hold a valid embedding.

    Notes
    -----
    Segments shorter than 0.25 s are skipped: TitaNet needs enough signal to
    produce a stable embedding, and very short blips mostly carry noise. Per-
    segment embedding failures are logged and left as ``ok=False`` rather than
    aborting the whole recording.
    """
    from vocal_helper.diar import _TitaNetEmbedder

    emb = _TitaNetEmbedder()
    emb.load()
    X = np.zeros((len(segs), TITANET_DIM), dtype=np.float32)
    ok = np.zeros(len(segs), dtype=bool)
    for k, (t0, t1) in enumerate(segs):
        pcm = audio[int(t0 * SR):int(t1 * SR)]
        if pcm.shape[0] < int(0.25 * SR):
            continue  # too short to embed reliably; leave ok[k] = False
        try:
            v = emb.embed(pcm, SR).astype(np.float32)
            n = np.linalg.norm(v)
            if n > 0:
                X[k] = v / n  # L2-normalise onto the unit sphere
                ok[k] = True
        except Exception as e:
            # One bad segment must not sink the whole recording.
            osh.warning(f"  embed fail seg {k}: {e}")
        if (k + 1) % 200 == 0:
            osh.info(f"  embed: {k + 1}/{len(segs)}")
    return X, ok


def _estimate_n_spk(Xc: np.ndarray, lo: int = 2, hi: int = 8) -> int:
    """Estimate the number of speakers via silhouette score.

    When the speaker count is unknown, sweep candidate ``k`` values and pick the
    one maximising the cosine silhouette score — the split where clusters are
    internally tight and mutually well separated.

    Parameters
    ----------
    Xc : numpy.ndarray
        The centered/normalised embedding matrix to cluster.
    lo : int, optional
        Smallest speaker count to consider (default 2).
    hi : int, optional
        Largest speaker count to consider (default 8), capped at ``len(Xc) - 1``
        because silhouette is undefined once ``k`` reaches the sample count.

    Returns
    -------
    int
        Estimated speaker count. Falls back to the sample count when there are
        too few points to run the sweep.
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    hi = min(hi, len(Xc) - 1)
    if hi < lo:
        # Not enough points to compare candidate splits; treat each point as its
        # own (degenerate) cluster rather than crashing on tiny recordings.
        return max(1, len(Xc))
    best_k, best_s = lo, -1.0
    for k in range(lo, hi + 1):
        lab = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(Xc)
        try:
            s = silhouette_score(Xc, lab, metric="cosine")
        except Exception:
            s = -1.0  # silhouette can fail (e.g. degenerate cluster); disfavour this k
        if s > best_s:
            best_k, best_s = k, s
    return best_k


def cluster(X: np.ndarray, ok: np.ndarray, n_spk: int | None = None) -> np.ndarray:
    """Cluster segment embeddings into speaker labels.

    Parameters
    ----------
    X : numpy.ndarray
        ``(n_segments, TITANET_DIM)`` raw L2-normalised embeddings.
    ok : numpy.ndarray
        Boolean mask of rows in ``X`` that hold valid embeddings.
    n_spk : int, optional
        Known speaker count. If ``None``, it is estimated with
        :func:`_estimate_n_spk`.

    Returns
    -------
    numpy.ndarray
        ``(n_segments,)`` int label array. Unusable segments get ``-1``; valid
        speakers are renumbered ``0, 1, 2, …`` in order of first appearance so
        ids are stable and human-readable.

    Notes
    -----
    WHY a per-recording *centered* cosine space: every segment of one recording
    shares a common component — the microphone, the room acoustics, the codec.
    In the raw space that shared channel component dominates the cosine geometry
    and can swamp the (smaller) between-speaker differences, which is fatal for
    single-device far-field audio where everyone is recorded through the same
    channel. Subtracting the per-recording mean embedding removes that common
    component, so the remaining variation is mostly *speaker* identity; we then
    re-normalise and cluster in that centered space. This centered space is
    intentionally local to this recording and is NOT the space used for
    cross-recording identity matching (that uses the raw ``X`` returned by
    :func:`embed_segments`).
    """
    from sklearn.cluster import AgglomerativeClustering

    Xok = X[ok].astype(np.float64)
    Xn = Xok / (np.linalg.norm(Xok, axis=1, keepdims=True) + 1e-9)
    Xc = Xn - Xn.mean(0)  # remove the shared channel/room component
    Xc = Xc / (np.linalg.norm(Xc, axis=1, keepdims=True) + 1e-9)
    k = n_spk or _estimate_n_spk(Xc)
    lab = AgglomerativeClustering(n_clusters=k, metric="cosine",
                                  linkage="average").fit_predict(Xc)
    labels = -np.ones(len(X), dtype=int)
    labels[np.where(ok)[0]] = lab
    # Relabel S0.. by first appearance so speaker ids are stable across runs and
    # do not depend on sklearn's internal cluster numbering.
    remap: dict[int, int] = {}
    nxt = 0
    for l in labels:
        if l != -1 and l not in remap:
            remap[l] = nxt
            nxt += 1
    return np.array([remap.get(l, -1) for l in labels], dtype=int)


def merge_turns(segs: list[tuple[float, float]], labels: np.ndarray,
                merge_gap_s: float = MERGE_GAP_S, max_turn_s: float = MAX_TURN_S) -> list[dict]:
    """Merge consecutive same-speaker segments into contiguous turns.

    Parameters
    ----------
    segs : list of tuple of (float, float)
        Voiced ``(start_s, end_s)`` spans.
    labels : numpy.ndarray
        Per-segment speaker labels from :func:`cluster` (``-1`` for unusable).
    merge_gap_s : float, optional
        Maximum silence gap (seconds) between two same-speaker segments for them
        to be fused. Defaults to :data:`MERGE_GAP_S`.
    max_turn_s : float, optional
        Maximum total turn length (seconds); a turn stops growing beyond this so
        downstream ASR gets bounded chunks. Defaults to :data:`MAX_TURN_S`.

    Returns
    -------
    list of dict
        Turns as ``{"t0": float, "t1": float, "spk": int}``, in time order.

    Notes
    -----
    Unlabelled segments (``label < 0``) are dropped: they carry no speaker and
    would only fragment the turn stream.
    """
    turns: list[dict] = []
    for (t0, t1), lab in zip(segs, labels, strict=False):
        if lab < 0:
            continue
        if turns and turns[-1]["spk"] == lab and \
           t0 - turns[-1]["t1"] <= merge_gap_s and \
           t1 - turns[-1]["t0"] <= max_turn_s:
            turns[-1]["t1"] = t1  # extend the current turn in place
        else:
            turns.append({"t0": t0, "t1": t1, "spk": int(lab)})
    return turns


def diarize(audio: np.ndarray, n_spk: int | None = None
            ) -> tuple[list[tuple[float, float]], np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    """Run the full diarization pipeline on a waveform.

    Parameters
    ----------
    audio : numpy.ndarray
        1-D float32 waveform at :data:`SR`.
    n_spk : int, optional
        Known speaker count; ``None`` estimates it (see :func:`_estimate_n_spk`).

    Returns
    -------
    segs : list of tuple of (float, float)
        Voiced spans in seconds.
    labels : numpy.ndarray
        Per-segment speaker labels (``-1`` for unusable segments).
    X : numpy.ndarray
        Raw L2-normalised embeddings (absolute space, for cross-recording
        identity matching).
    ok : numpy.ndarray
        Boolean mask of valid embedding rows.
    turns : list of dict
        Merged speaker turns ``{"t0", "t1", "spk"}``.
    """
    osh.info("running Silero VAD...")
    segs = run_vad(audio)
    osh.info(f"VAD: {len(segs)} voiced segments")
    osh.info("embedding segments (TitaNet)...")
    X, ok = embed_segments(audio, segs)
    osh.info(f"embeddings: {int(ok.sum())}/{len(segs)} usable")
    osh.info("clustering speakers...")
    labels = cluster(X, ok, n_spk)
    turns = merge_turns(segs, labels)
    return segs, labels, X, ok, turns
