# ADR 0002 — Portable diarization engine: sherpa-onnx + TitaNet-large

**Status:** Accepted — offline **final** (generalized on held-out IS1008a) · streaming =
periodic offline re-diarization · 2026-07-18
**Scope:** On-device speaker diarization for the cross-platform app (Rust `nh-sherpa`),
and an optional torch-free backend for `vocal-helper` (Python).
**Doctrine:** ADR required for adopting a critical native engine (Engineering Doctrine §23).

## Context

Diarization must run **on every target, including mobile**, fully local. The proven
desktop engines — **pyannote** (DER ~0.122 on AMI) and **NeMo Sortformer** (0.267) — are
**PyTorch** (~5 GB torch+NeMo), **inembeddable on iOS/Android**. We need an ONNX pipeline
(onnxruntime, no torch) that keeps competitive quality **in French AND English** (hard
product requirement).

## Decision

**Portable diarization = sherpa-onnx**, assembled from ONNX parts:
- **Segmentation:** `pyannote/segmentation-community-1`, **exported to ONNX by us**
  (`community1-segmentation.onnx`; HF token used once, no runtime HF dependency).
- **Speaker embedding (the DER lever):** **NeMo TitaNet-large** (quality) or **SpeakerNet**
  (2× faster) — both ONNX, both validated FR+EN.
- **Clustering:** sherpa's agglomerative clustering (offline) / online agglomerative
  clustering over VAD-segment embeddings (streaming).

## Evidence (studies in `~/pasdebonneoudemauvaisesituation`, tech-report §3bis)

- **The embedding dominates the error** (speaker *confusion*). On AMI ES2011a (canonical
  DER, oracle count): **TitaNet-small 0.340 → SpeakerNet 0.226 → TitaNet-large 0.174**
  (RTF 0.17 / 0.28 / 0.58). TitaNet-large **beats NeMo Sortformer (0.267)** and approaches
  pyannote (0.122), while portable.
- **Generalizes (anti-Goodhart):** on a *held-out* meeting **IS1008a** (never used to pick
  the model), TitaNet-large scores **DER 0.148** — *better* than the ES2011a tuning meeting
  (0.174). The choice is not overfit to one recording.
- **community-1 segmentation matches seg-3.0:** TitaNet-large + our ONNX-exported
  `community1-segmentation` = **0.174** on ES2011a, identical DER to `segmentation-3.0`.
  Same quality, but **autonomous from HF** → adopted for sovereignty.
- **FR+EN validated** on clean Common Voice clips: TitaNet-large separates speakers
  perfectly in **French** (same +0.82 / diff ≈0) and **English** (+0.73 / +0.11). Speaker
  embeddings are ~language-agnostic.
- **Rejected embeddings:** CAM++/campplus ONNX (DER 0.36–0.59); ERes2Net(V2) (too slow,
  RTF > 1); **WeSpeaker** (English VoxCeleb only, **no French** → fails the FR+EN rule);
  `ecapa_tdnn` (NeMo→ONNX not consumable by sherpa). No `titanet_medium` exists (NVIDIA
  released S+L only) — SpeakerNet is the intermediate.
- **Per-scenario Pareto (quality × speed):** offline → **TitaNet-large**; streaming →
  benchmark in progress (rolling/hungarian mis-fit sherpa's whole-buffer diarizer; the
  correct path is embed-per-segment + online clustering).

## Alternatives considered

- **pyannote / NeMo (torch):** best quality but not embeddable on mobile — kept as the
  optional desktop "max quality" path only.
- **FluidAudio (CoreML/ANE, MIT/Apache):** strong Apple-only on-device option (pyannote
  seg + WeSpeaker, DER ~22 %, RTF 0.017) → candidate for the iOS/macOS shell.
- **Picovoice Falcon:** rejected — proprietary (not sovereign).

## Consequences

- Rust **`nh-sherpa`** implements the `DiarizationEngine` port over sherpa-onnx via the
  **`sherpa-rs` crate** (0.6.8, `sherpa_rs::diarize::Diarize`), feature-gated like
  `nh-whisper`: default build is a stub, the real binding is opt-in
  (`--features sherpa-onnx`). `sherpa-rs` encapsulates the sherpa-onnx FFI, so the adapter
  keeps `#![forbid(unsafe_code)]`. (Earlier plan assumed hand-written C FFI/bindgen; the
  maintained crate makes that unnecessary.) **Done 2026-07-19** — compiles green in both
  modes; `SherpaDiarizer::new(seg_onnx, emb_onnx)` + threshold / num_clusters knobs.
- Models are app assets, hash-verified, served from the maintainer's FTP via the
  ModelManager (Q11); documented in `models/sherpa/README.md`.
- `vocal-helper` gains an optional torch-free `sherpa` backend; its existing
  `nemo`/TitaNet-large path is already the best desktop embedder (study-confirmed).
- **Reversible:** the embedding and segmentation are config; a better ONNX model swaps in
  without touching the pipeline.
- **Streaming = periodic offline re-diarization** (offline-quality 0.148–0.174 at a light
  delay). The online-per-segment path is a dead end: fast embedders separate poorly,
  strong embedders (TitaNet-large, RTF 0.58) are too slow to cluster per segment. Shipping
  the re-diarization loop gives offline quality in near-real-time — no MVP compromise.
- **community-1-seg × TitaNet-large combo landed** (= seg-3.0, autonomous) and the
  generalization check passed (IS1008a 0.148) → the **offline decision is final**.
