#!/usr/bin/env python3
"""
Offline diarized transcription for the Réunion Produits / R&D recording.

Pipeline (all local, no SaaS):
  1. Silero VAD            -> voiced segments (vocal_helper.vad.SileroVADStage)
  2. TitaNet embeddings    -> one L2-normalised vector per segment
  3. Agglomerative cluster -> exactly N_SPK=4 speakers (known participants)
  4. pywhispercpp turbo    -> French transcription per merged same-speaker turn

Checkpoints let us re-run the (slow) ASR / naming stages without redoing VAD
and embeddings.

Outputs:
  diar_checkpoint.npz   segment times, embeddings, cluster labels
  transcript.json       [{t0,t1,speaker,text}, ...]  (speaker = S0..S3)
"""
import asyncio
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_TAG = sys.argv[1] if len(sys.argv) > 1 else ""      # "" => full run
WAV = os.path.join(HERE, f"reunion_16k{_TAG}.wav" if _TAG else "reunion_16k.wav")
CKPT = os.path.join(HERE, f"diar_checkpoint{_TAG}.npz")
OUT = os.path.join(HERE, f"transcript{_TAG}.json")
LOG = os.path.join(HERE, f"pipeline_progress{_TAG}.log")

N_SPK = 4
SR = 16000
WHISPER_MODEL = "large-v3-turbo-q5_0"
LANGUAGE = "fr"
INITIAL_PROMPT = (
    "Réunion de travail produit et R&D en français entre Warith Harchaoui, "
    "Benoît Defoug, Vincent Sammiez et Philippe Vivien. Sujets : roadmap "
    "produit, intelligence artificielle, organisation, clients."
)
MERGE_GAP_S = 0.8      # merge same-speaker segments closer than this
MAX_TURN_S = 28.0      # cap a merged turn's length before forcing a cut


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def load_audio():
    import soundfile as sf
    audio, sr = sf.read(WAV, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    assert sr == SR, f"expected {SR}Hz got {sr}"
    return np.ascontiguousarray(audio, dtype=np.float32)


async def run_vad(audio):
    from vocal_helper.types import PcmFrame
    from vocal_helper.vad import SileroVADStage
    stage = SileroVADStage(sample_rate=SR)
    inbox, outbox = asyncio.Queue(maxsize=8), asyncio.Queue()
    FR = SR * 30

    async def feeder():
        for i in range(0, len(audio), FR):
            await inbox.put(PcmFrame(t0=i / SR, sample_rate=SR, pcm=audio[i:i + FR].copy()))
        await inbox.put(None)

    segs = []

    async def collector():
        while True:
            item = await outbox.get()
            if item is None:
                break
            segs.append((float(item["t0"]), float(item["t1"])))
            if len(segs) % 200 == 0:
                log(f"  VAD: {len(segs)} segments so far (t={item['t1']:.0f}s)")

    await asyncio.gather(feeder(), stage.run(inbox, outbox), collector())
    return segs


def embed_segments(audio, segs):
    from vocal_helper.diar import _TitaNetEmbedder
    emb = _TitaNetEmbedder()
    emb.load()
    X = np.zeros((len(segs), 192), dtype=np.float32)
    ok = np.zeros(len(segs), dtype=bool)
    for k, (t0, t1) in enumerate(segs):
        i0, i1 = int(t0 * SR), int(t1 * SR)
        pcm = audio[i0:i1]
        if pcm.shape[0] < int(0.25 * SR):
            continue
        try:
            v = emb.embed(pcm, SR).astype(np.float32)
            n = np.linalg.norm(v)
            if n > 0:
                X[k] = v / n
                ok[k] = True
        except Exception as e:
            log(f"  embed fail seg {k}: {e}")
        if (k + 1) % 200 == 0:
            log(f"  embed: {k + 1}/{len(segs)}")
    return X, ok


def cluster(X, ok):
    from sklearn.cluster import AgglomerativeClustering
    Xok = X[ok].astype(np.float64)
    # L2-normalise, then CENTER (remove the common channel/room component that
    # otherwise dominates cosine similarity on a single-device far-field
    # recording and collapses everyone into one cluster), then renormalise.
    Xn = Xok / (np.linalg.norm(Xok, axis=1, keepdims=True) + 1e-9)
    Xc = Xn - Xn.mean(0)
    Xc = Xc / (np.linalg.norm(Xc, axis=1, keepdims=True) + 1e-9)
    lab = AgglomerativeClustering(n_clusters=N_SPK, metric="cosine",
                                  linkage="average").fit_predict(Xc)
    labels = -np.ones(len(X), dtype=int)
    labels[np.where(ok)[0]] = lab
    # relabel S0..S3 by first appearance for stable ids
    _order, remap, nxt = [], {}, 0
    for l in labels:
        if l == -1:
            continue
        if l not in remap:
            remap[l] = nxt
            nxt += 1
    labels = np.array([remap.get(l, -1) for l in labels], dtype=int)
    return labels


def merge_turns(segs, labels):
    """Merge consecutive same-speaker VAD segments into turns."""
    turns = []
    for (t0, t1), lab in zip(segs, labels, strict=False):
        if lab < 0:
            continue
        if turns and turns[-1]["spk"] == lab and \
           t0 - turns[-1]["t1"] <= MERGE_GAP_S and \
           t1 - turns[-1]["t0"] <= MAX_TURN_S:
            turns[-1]["t1"] = t1
        else:
            turns.append({"t0": t0, "t1": t1, "spk": int(lab)})
    return turns


def transcribe(audio, turns):
    from vocal_helper.asr import WhisperStage
    from vocal_helper.types import DiarizedSegment
    stage = WhisperStage(model=WHISPER_MODEL, language=LANGUAGE,
                         word_timestamps=False, initial_prompt=INITIAL_PROMPT)
    stage._ensure_model()
    out = []
    n = len(turns)
    t_start = time.time()
    for k, tn in enumerate(turns):
        i0, i1 = int(tn["t0"] * SR), int(tn["t1"] * SR)
        pcm = audio[i0:i1]
        seg = DiarizedSegment(t0=tn["t0"], t1=tn["t1"], sample_rate=SR,
                              speaker=f"S{tn['spk']}", pcm=pcm.astype(np.float32, copy=False))
        try:
            utt = stage._transcribe_blocking(seg)
            text = "" if utt is None else utt["text"].strip()
        except Exception as e:
            log(f"  asr fail turn {k}: {e}")
            text = ""
        if text:
            out.append({"t0": round(tn["t0"], 2), "t1": round(tn["t1"], 2),
                        "speaker": f"S{tn['spk']}", "text": text})
        if (k + 1) % 100 == 0:
            el = time.time() - t_start
            eta = el / (k + 1) * (n - k - 1)
            log(f"  asr: {k + 1}/{n}  elapsed={el/60:.1f}m eta={eta/60:.1f}m")
        # periodic flush of partial results
        if (k + 1) % 500 == 0:
            with open(OUT, "w") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main():
    open(LOG, "w").close()
    log("=== pipeline start ===")
    log("loading audio...")
    audio = load_audio()
    log(f"audio: {len(audio)/SR/60:.1f} min")

    if os.path.exists(CKPT):
        log("loading diar checkpoint (reusing VAD + embeddings)...")
        d = np.load(CKPT, allow_pickle=True)
        segs = [tuple(x) for x in d["segs"]]
        log("re-clustering to 4 speakers (centered cosine)...")
        labels = cluster(d["X"], d["ok"])
        np.savez(CKPT, segs=d["segs"], labels=labels, X=d["X"], ok=d["ok"])
    else:
        log("running Silero VAD...")
        segs = asyncio.run(run_vad(audio))
        log(f"VAD done: {len(segs)} voiced segments")
        log("loading TitaNet + embedding segments...")
        X, ok = embed_segments(audio, segs)
        log(f"embeddings done: {int(ok.sum())}/{len(segs)} usable")
        log("clustering to 4 speakers...")
        labels = cluster(X, ok)
        np.savez(CKPT, segs=np.array(segs, dtype=object), labels=labels,
                 X=X, ok=ok)
        log("checkpoint saved")

    from collections import Counter
    log(f"speaker seg counts: {dict(Counter(int(l) for l in labels if l>=0))}")
    turns = merge_turns(segs, labels)
    log(f"merged into {len(turns)} turns; transcribing (turbo)...")
    out = transcribe(audio, turns)
    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"=== done: {len(out)} utterances -> {OUT} ===")


if __name__ == "__main__":
    main()
