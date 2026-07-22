# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Unified i18n (GUI + prompts, fr/en/es) with discovered language.** One catalog at the
  repo root, `locales/i18n.yaml`, holds every translatable string — HTML report labels
  under `gui:` and LLM synthesis prompts under `prompts:` — each in `fr`/`en`/`es`. New
  `notes_helper.i18n` loads it (root catalog first, packaged copy as fallback), with
  `gui(id, lang)`, `prompt(id, lang)`, and a `resolve_language` policy: the report/GUI
  language is the **dominant** language, taken from the associated **text** when present
  (langdetect, majority vote) else from the **audio** majority (LID regions). The HTML
  report auto-detects its language from the transcript and renders labels + `<html lang>`
  accordingly (verified fr→French, es→Spanish). Adding a language means editing only the
  catalog. Spoken language stays auto-detected per turn (whisper "auto").
- **Optimized web audio — no more giant WAV in the output.** The report player is served
  small, loudness-normalized **Opus** (~32 kbps mono, primary) + **MP3** (~72 kbps,
  fallback) instead of the pipeline's ~500 MB 16 kHz WAV. New `notes_helper.webaudio`
  applies a speech chain (high-pass 80 Hz → optional denoise → EBU R128 loudnorm) via
  ffmpeg. The pipeline sheds the WAV at the end of `run` (encodes then deletes it), and
  `render()` reclaims any legacy WAV after encoding — out_dir keeps only the compact audio
  (a 4 h meeting drops from ~500 MB to tens of MB). Player playback and ASR-input audio are
  deliberately separate chains.
- **Slide-sync — the player shows the *right* slide, by content not order.** New
  `notes_helper.slides`: renders a deck PDF to one PNG per page (`pdf2image`), reads each
  page's text (`pypdf` text layer, `kreuzberg` OCR for image-only pages), and aligns every
  transcript utterance to the best-matching slide with a dependency-free TF-IDF cosine.
  The match has **no chronological assumption**, so a meeting that jumps around a deck
  (0→14→7→2→25) still shows the slide being discussed; weak matches carry the previous
  slide forward instead of flickering. Output is `slides/slidesync.json` + the PNGs;
  `render_html(slide_sync=…)` inlines the timeline (no `fetch`, works from `file://`) and
  swaps an `<img>` panel on every `timeupdate`. Covered by `tests/test_slides.py`.
- **Associated-document ingestion for context (`notes_helper.context`).** A slug's folder
  of attached documents becomes synth context: Markdown/text read directly, PDFs and other
  rich files extracted with `kreuzberg` (text layer first, OCR on demand). New
  `--context-dir` on `synth`/`all` aggregates a whole dossier (brief + manuscript) in one
  pass; media and generated artifacts are skipped. `kreuzberg` added to the `docs` extra.
  Covered by `tests/test_context.py`.
- **Transcription confidence (offline, Rust core).** Every `Utterance` (and `Word`)
  now carries `confidence: Option<f32>` in `[0,1]`. On the offline whole-buffer path,
  `nh-whisper` computes each whisper segment's confidence as the mean of its content
  tokens' probabilities (`whisper_full_get_token_prob`, skipping special/timestamp
  tokens); `nh_core::model::mean_confidence` folds a turn's segments into one
  duration-weighted score, reused by `nh-run` and both offline pipelines. `nh-run`
  logs the overall mean and flags low-confidence spans (`⚠️ N%`, threshold `0.6`) in
  `report.md`. The **online/streaming** path is untouched — confidence stays `None`
  (unmeasured, never fabricated). `#[serde(default)]` keeps older transcripts loadable.
  This feeds both the *verifiable report* promise and the context-refinement loop.
- **Selectable diarization embedder (`DIAR_EMBEDDER`).** New config knob
  (`NOTES_HELPER_DIAR_EMBEDDER`, default `"nemo"`) chooses the speaker-embedding
  backend: `"nemo"` keeps the torch/NeMo TitaNet-large (desktop), `"sherpa"` runs the
  same TitaNet-large through onnxruntime — the torch-free, portable path the
  cross-platform app ships (ADR 0002: DER 0.174 on AMI ES2011a, 0.148 on held-out
  IS1008a, FR+EN validated). Both emit the same 192-dim vector, so cross-recording
  identity matching is unchanged. The sherpa path needs `vocal-helper[sherpa]`.
- **Report time cursor.** As the audio plays, the HTML report highlights the
  utterance being spoken and scrolls it into view; timestamp/chapter clicks still
  seek. Works with an MP3 source wired into the report metadata.

### Changed
- **Language is discovered, never assumed.** There is no default language and no
  fixed language set anywhere. The spoken language is auto-detected (`ASR_LANGUAGE`
  / `asr.transcribe` / `pipeline.run` default to `"auto"`, file or stream); the
  report language defaults to `None` in `synth.synthesize` — the model writes in the
  transcript's own language — and an explicit code still forces one. `DEFAULT_LANGUAGE`
  / `SUPPORTED_LANGUAGES` were removed. Prompts live in `locales/i18n.yaml` as single
  templates with a `{lang_clause}` slot filled at call time (`notes_helper.i18n`).
  Covered by `tests/test_i18n.py`.
- **Report renderer no longer leaks raw JSON.** A shared `outputs/_text.as_text`
  coercion (used by both the Markdown and HTML renderers) turns a drifted LLM value
  like `{"texte": "…"}` into its text, so `{'texte': …}` never appears on the page.
  The Actions table dropped its due-date (`Échéance`) column — action + responsable
  only. Covered by `tests/test_outputs_text.py`.
- **Document export tracks the current md2star.** `outputs/docs.compile_doc` now
  drives md2star ≥ 2.6 through its in-process `md2star.cli._convert(fmt, argv)`
  entry point and, in the CLI fallback, the subcommand form
  (`md2star docx in.md -o out.docx`). The previous top-level probes
  (`convert`/`render`/`to_<fmt>`) and the flat `--to` CLI were removed upstream, so
  DOCX/PDF/PPTX export was silently failing on md2star ≥ 2.6; both legacy paths are
  kept as fallbacks so older installs still work. Verified end-to-end producing a
  real `.docx` on md2star 2.8.0.

### Dependencies
- Raised the AI Helpers suite floors to the released, tested baselines:
  `os-helper>=1.7.2`, `vocal-helper>=0.6.0` (flat-layout package; the consumed
  ASR/VAD/diarization symbols are unchanged), `capture-helper>=0.3.0`,
  `md2star>=2.8.0`.

## [0.4.1] - 2026-07-15

### Documentation
- Harmonize README/LISEZMOI to the AI Helpers common structure (single H1,
  source install path pinned to v0.4.1, PyPI-coming-soon note); no code changes.

## [0.4.0] — 2026-07-14

### Added
- Optional meeting-context input for synthesis (inline text or file) via the
  CLI, with unambiguous second-based chunk timestamps, plus a synthesis
  robustness test suite.

### Changed
- Flip optional-extra dependencies (`vocal-helper`, `capture-helper`,
  `md2star`) from `git+https` to PyPI version specifiers so the package is
  installable from PyPI.

### Maintenance
- Apply the project coding standards across `src/` and `tests/` (Numpy
  docstrings, full typing, comment density above the floor); route library
  logging through the os-helper surface and adopt os-helper utilities more
  widely. Refresh the project logo asset.

## [0.3.2] — 2026-07-13

### Changed
- **Project renamed to `notes-helper`** across the whole tree. The
  import package is now `notes_helper`, the console scripts are `notes-helper`,
  `notes-helper-api` and `notes-helper-mcp`, the environment-variable prefix is
  `NOTES_HELPER_*` (e.g. `NOTES_HELPER_OLLAMA_MODEL`), the on-device store lives
  at `~/.notes-helper/people.db`, and the Apple apps ship as **Notes Helper**
  (bundle ids `ai.deraison.noteshelper.*`). Behaviour is unchanged — this is a
  pure rebrand. No PyPI release accompanies it (publishing stays opt-in).

## [0.3.1] — 2026-07-12

### Fixed
- **Map/reduce resilience on long meetings** — verified end-to-end on a real
  **6.1 h** recording (1206 utterances, ~57 synthesis chunks), which exposed
  that one bad chunk could discard the entire synthesis. Two fixes at the
  boundary: `synth._json_loads_lax` now honours its contract and **never
  raises** (the regex-extraction fallback could throw on truncated model output,
  which propagated up and dropped the whole meeting to the no-LLM heuristic); and
  `synth.synthesize` now **isolates each map chunk** — a chunk that errors or
  returns unparseable JSON is skipped while the rest still feed the reduce, and
  the heuristic fallback triggers only when *every* chunk fails or the reduce
  comes back empty. Adds `tests/test_synth_robustness.py` (parser contract +
  chunk-resilience + all-fail fallback).

## [0.3.0] — 2026-07-12

### Added
- **FastAPI HTTP surface** (`notes_helper.api`, `[api]` extra) — the model-light stages
  of the pipeline as HTTP endpoints: `GET /health`, `POST /normalize` (coerce a
  drifted synthesis to the render schema), `POST /synth` (diarized transcript →
  structured report via the local Ollama LLM, degrading to the no-LLM heuristic
  when Ollama is unreachable), and `POST /render` (upload `transcript.json` +
  `synthese.json` → streamed Markdown/HTML, single file or a zip). Run it with
  `notes-helper-api` or `uvicorn notes_helper.api:app`. The audio-in `run` stage is
  deliberately excluded — heavy on-device models belong to a worker surface, not
  a synchronous request.
- **MCP surface** (`notes_helper.mcp`, `[mcp]` extra) — exposes the FastAPI app as MCP
  tools via `fastapi-mcp`, so any MCP client — proprietary (Claude Desktop,
  Cursor, Windsurf) or open-source (Cline, Continue, Goose, Zed), plus agents
  and IDEs — can call `normalize` / `synth` / `render` as first-class tools. Run it with
  `notes-helper-mcp` or `python -m notes_helper.mcp`. This brings notes-helper in line with the
  rest of the `*-helper` suite, which already ships CLI + API + MCP.
- Smoke + round-trip tests for both surfaces (`tests/test_api.py`,
  `tests/test_mcp.py`); CI installs the `api`/`mcp` deps via `[dev]` so they run
  (not skip) on every push. The `/render` round-trip re-asserts the zero-egress
  guarantee on the API path.

## [0.2.1] — 2026-07-12

### Fixed
- **Renderer robustness against LLM drift** — the Python pipeline was verified
  end-to-end on real audio (whisper `large-v3-turbo` on Metal → local Ollama
  synthesis → HTML/Markdown report → egress audit), which surfaced two crashes
  when a small local model drifted from the synthesis schema: a chapter
  timestamp emitted as `"0:00:28"` instead of seconds broke `_hhmmss`, and a
  quote field emitted as a JSON list broke HTML escaping. Both are now handled
  at the boundary: a new `notes_helper.synth.normalize_synthese` coerces the raw
  synthesis into the exact shape the renderers expect (applied both at synth
  time and when a possibly hand-edited `synthese.json` is loaded for rendering),
  a shared `notes_helper.outputs._timefmt.seconds` tolerantly parses any timestamp
  form, and `esc` no longer raises on non-string values.
- **Version string** — `notes-helper --version` now reports the real version
  (`pyproject.toml` and `notes-helper.__version__` were still pinned at `0.1.0`).

### Notes
- The default synthesis model is `qwen2.5:32b`; on a machine without it, set
  `NOTES_HELPER_OLLAMA_MODEL` (e.g. `qwen2.5:3b`) or the synth step falls back to the
  no-LLM heuristic report.

## [0.2.0] — 2026-07-11

### Added
- **Native iOS engine** — the iPhone app now runs **without the CLI**. A fully
  on-device Swift pipeline: `AudioDecoder` (AVFoundation resample) → energy VAD →
  speaker embedding (dependency-free DSP fallback + a CoreML/TitaNet path) →
  agglomerative clustering with the per-recording centering trick → whisper.cpp
  ASR (SwiftWhisper, guarded) → on-device JSON voiceprint identity store → MLX or
  heuristic synthesis → self-contained HTML/Markdown report. Both Apple targets
  type-check on the macOS **and** iOS SDKs, and CI gains a **Swift type-check
  job**. Ship a ggml whisper model to enable transcription; a TitaNet CoreML
  model and an MLX model are optional quality upgrades.

## [0.1.1] — 2026-07-10

### Added
- **Apple apps** (`apps/`): native SwiftUI for **macOS + iOS**, sharing one UI
  and one `NotesHelperEngine` contract. macOS drives the local `notes-helper` CLI
  (functional); the iOS native engine (whisper.cpp / CoreML / MLX) is scaffolded
  (WIP). Project defined in text via XcodeGen (`apps/project.yml`); both targets
  type-check cleanly against the macOS and iOS SDKs.

### Fixed
- **Green CI**: pinned ruff and declared `notes-helper` as isort first-party
  (deterministic lint local↔CI); switched CI to a non-editable install so the
  `notes_helper.outputs` subpackage resolves on all runners.
- Attribution: Alexandre Larmagnac moved from authors to Acknowledgements.

[0.2.0]: https://github.com/warith-harchaoui/notes-helper/releases/tag/v0.2.0
[0.1.1]: https://github.com/warith-harchaoui/notes-helper/releases/tag/v0.1.1

## [0.1.0] — 2026-07-10

First public release. Fully-local, free, open-source diarized meeting recorder —
nothing leaves your device unless you decide.

### Added
- **Pipeline** (`notes-helper run`): any audio → 16 kHz mono (ffmpeg) → Silero VAD →
  TitaNet diarization → whisper.cpp ASR → `transcript.json`, all local.
- **On-device identity** (`notes-helper enroll` / `notes-helper people`): SQLite voiceprint
  store; speakers matched across meetings in raw TitaNet space ("name once,
  known forever on your device").
- **Local synthesis** (`notes-helper synth`): transcript → structured `synthese.json`
  via a local Ollama LLM (map-reduce, timestamp-grounded), with a graceful
  heuristic fallback when Ollama is unreachable.
- **Outputs** (`notes-helper report`): Markdown-first, then self-contained interactive
  HTML (zero external requests), and DOCX/PDF/PPTX via `md2star`. Obsidian vault
  target (`People/` + `Meetings/`, wikilinks + Tasks checkboxes) — opt-in.
- **Sovereignty gate** (`notes-helper audit`, `scripts/audit_egress.py`): fails if any
  generated artifact references an external URL.
- **Verifiable summaries** (`notes_helper.verify`): deterministic grounding checks
  (citations traceable to transcript, timestamps in range, non-empty items).
- **Tests**: `pytest` unit suite (identity, clean, markdown, outputs, audit,
  verify) + DeepEval faithfulness eval with a local judge (opt-in, `-m slow`).
- **CI**: GitHub Actions matrix (Ubuntu / macOS / Windows × Python 3.11, 3.13),
  ruff + pytest, blocking.
- **Docs**: bilingual `README.md` / `LISEZMOI.md`, `EXAMPLES.md`, `PRODUCT.md`,
  `PLAN.md`, `LANDSCAPE.md`, timestamped `TODO.md` session journal.

[0.1.0]: https://github.com/warith-harchaoui/notes-helper/releases/tag/v0.1.0
