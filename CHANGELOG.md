# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
