# Notes Helper

[🇫🇷](https://github.com/warith-harchaoui/notes-helper/blob/main/LISEZMOI.md) · [🇬🇧](https://github.com/warith-harchaoui/notes-helper/blob/main/README.md)

[![CI](https://github.com/warith-harchaoui/notes-helper/actions/workflows/ci.yml/badge.svg)](https://github.com/warith-harchaoui/notes-helper/actions/workflows/ci.yml) [![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/warith-harchaoui/notes-helper/blob/main/LICENSE) [![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg)](#) [![Local-first](https://img.shields.io/badge/privacy-local--first-2f6f5e.svg)](#the-promise)

`Notes Helper` belongs to a collection of libraries called **AI Helpers**, developed for building Artificial Intelligence.

**A fully-local, free, open-source recorder that turns any conversation into a diarized, speaker-named, verifiable report — and nothing leaves your device unless you decide.** Record or import audio, and Notes Helper separates the voices, transcribes them, names each speaker **once and forever on your device**, and writes a structured, grounded summary — entirely on your own machine.

By [Warith HARCHAOUI](https://linkedin.com/in/warith-harchaoui) · [🌍 deraison.ai/fr/notes-helper](https://deraison.ai/fr/notes-helper)

## Documentation

[💻 Documentation](https://harchaoui.org/warith/ai-helpers/docs/notes-helper-doc/)

[📋 Examples](https://github.com/warith-harchaoui/notes-helper/blob/main/EXAMPLES.md)

## The promise

> **Nothing leaves your device during use.** The only network events are one-time
> model downloads at first launch, and any sync *you* explicitly enable.

This is not a privacy *policy* ("trust us"). It is an architectural *property*: no
networking in the hot path, no analytics, open source so you can verify it — a
network monitor shows zero egress. The guarantee is even enforced in CI
(`notes-helper audit`).

## Status — v0.1.0

What ships today:

- **`notes-helper run`** — any audio file → diarized transcript (Silero VAD → TitaNet → whisper.cpp), fully local.
- **`notes-helper synth`** — transcript → structured summary via a **local** Ollama LLM (map-reduce, grounded to timestamps).
- **`notes-helper report`** — one report, three renders: interactive **HTML** GUI · **Markdown** (any target — Obsidian is *one* option) · **DOCX/PDF/PPTX** via [`md2star`](https://github.com/warith-harchaoui/md2star).
- **`notes-helper enroll` / `people`** — voiceprint identity in a local SQLite store: *name once, known forever on your device*.
- **`notes-helper audit`** — CI gate that fails if any generated artifact phones home.

## Installation

**Prerequisites** — **Python 3.10–3.13**, **git**, **ffmpeg**, and (for synthesis) a local **[Ollama](https://ollama.com)**, cross-platform:

- 🍎 **macOS** ([Homebrew](https://brew.sh)): `brew install python git ffmpeg ollama`
  (install `brew` thanks to [brew.sh](https://brew.sh/))
- 🐧 **Ubuntu/Debian**: `sudo apt update && sudo apt install -y python3 python3-pip git ffmpeg` — then Ollama via `curl -fsSL https://ollama.com/install.sh | sh`
- 🪟 **Windows** (PowerShell): `winget install Python.Python.3.12 Git.Git Gyan.FFmpeg Ollama.Ollama`

### From source

Notes Helper is not on PyPI yet — install from GitHub, pinned to the release tag:

```bash
pip install "git+https://github.com/warith-harchaoui/notes-helper.git@v0.4.1"
```

Optional extras (pick what you need):

```bash
pip install "notes-helper[process] @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.4.1"   # vocal-helper: VAD/diarization/ASR
pip install "notes-helper[capture] @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.4.1"   # capture-helper: mic/screen capture
pip install "notes-helper[docs]    @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.4.1"   # md2star: DOCX/PDF/PPTX export
pip install "notes-helper[all]     @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.4.1"   # everything
```

> **PyPI release coming soon.**

You still need `ffmpeg` on PATH (audio decode/resample) and `ollama serve` running (local synthesis):

- 🍎 macOS : `brew install ffmpeg` (install `brew` thanks to [brew.sh](https://brew.sh/))
- 🐧 Ubuntu : `sudo apt install ffmpeg`
- 🪟 Windows : `winget install Gyan.FFmpeg`

## Quick start

```bash
# 1) audio -> diarized transcript (+ speaker identity)   [drop files in input/]
notes-helper run input/meeting.m4a --out output/meeting

# 2) transcript -> local summary (needs `ollama serve`)
notes-helper synth output/meeting

# 3) render the report in the formats you want
notes-helper report output/meeting --format html,md,docx,pdf

# name a voice once — every later meeting auto-labels it, on your device
notes-helper enroll output/meeting/diar_checkpoint.npz --cluster S0 --name "Warith Harchaoui"

# prove sovereignty: fails if any artifact references an external URL
notes-helper audit output/meeting
```

As a library:

```python
from notes_helper.pipeline import run
from notes_helper.outputs import render

paths = run("input/meeting.m4a", "output/meeting")
print(paths["transcript"])          # output/meeting/transcript.json
render("output/meeting", ["html", "md"])
```

As an HTTP API or MCP server (aligns with the rest of the `*-helper` suite):

```bash
pip install -e ".[api,mcp]"

# FastAPI: normalize / synth / render over HTTP — OpenAPI docs at /docs
notes-helper-api                      # or: uvicorn notes_helper.api:app --port 8000
curl -F 'transcript=@output/meeting/transcript.json' \
     -F 'synthese=@output/meeting/synthese.json' \
     'http://localhost:8000/render?formats=md,html' -o report.zip

# MCP: expose the same tools (normalize / synth / render) to an MCP client
notes-helper-mcp                      # or: python -m notes_helper.mcp
```

The audio-in `run` stage is intentionally *not* exposed over HTTP — heavy
on-device models belong to a worker surface, not a synchronous request.

For the full catalog of recipes, see [📋 EXAMPLES.md](https://github.com/warith-harchaoui/notes-helper/blob/main/EXAMPLES.md).

## Architecture

Three layers over one seam (16 kHz mono float32 frames):

| Layer | Component | Role |
|---|---|---|
| **INPUT** | [`capture-helper`](https://github.com/warith-harchaoui/capture-helper) | mic / file / screen (iOS: native `AVAudioEngine`, same frame) |
| **PROCESS** | [`vocal-helper`](https://github.com/warith-harchaoui/vocal-helper) | Silero VAD → TitaNet diarization → whisper.cpp ASR → local LLM |
| **OUTPUT** | `build_page` · [`md2star`](https://github.com/warith-harchaoui/md2star) | HTML GUI · Markdown (any target) · DOCX/PDF/PPTX |

See [📄 PRODUCT.md](https://github.com/warith-harchaoui/notes-helper/blob/main/PRODUCT.md), [🗺️ PLAN.md](https://github.com/warith-harchaoui/notes-helper/blob/main/PLAN.md), and [🔭 LANDSCAPE.md](https://github.com/warith-harchaoui/notes-helper/blob/main/LANDSCAPE.md).

## Configuration

Copy the template and fill what you need (all optional — sane local defaults otherwise):

```bash
cp notes_helper_config.json.example notes_helper_config.json   # gitignored; never commit real config
```

Keys: `ollama_url`, `ollama_model`, `whisper_model`, `language`, `db_path`, optional `hf_token`.

## Tests

```bash
pip install -e ".[dev]"
pytest -q                      # fast unit tests
pytest -q --cov=notes_helper         # with coverage
pytest -q -m slow              # integration (needs models / Ollama)
deepeval test run tests/eval/  # AI-eval: summary faithfulness thresholds
python scripts/audit_egress.py output/   # sovereignty gate
```

## Author

- [Warith HARCHAOUI](https://linkedin.com/in/warith-harchaoui).

## Acknowledgements

Special thanks to [Mohamed Chelali](https://mchelali.github.io) and [Bachir Zerroug](https://www.linkedin.com/in/bachirzerroug) and [Alexandre Larmagnac](https://www.linkedin.com/in/alexandre-larmagnac-85b4619b/) for fruitful discussions.
