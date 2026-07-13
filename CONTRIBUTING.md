# Contributing to Notes Helper

Thanks for your interest. Notes Helper is fully local by design — please keep it that
way: no code path may send user audio, transcripts, or summaries off the device.

## Development setup

**Prerequisites** — Python 3.10–3.13, `git`, `ffmpeg`, and a local Ollama:

- 🍎 macOS : `brew install python git ffmpeg ollama`
  (install `brew` thanks to [brew.sh](https://brew.sh/))
- 🐧 Ubuntu : `sudo apt install -y python3 python3-pip git ffmpeg` — then `curl -fsSL https://ollama.com/install.sh | sh`
- 🪟 Windows : `winget install Python.Python.3.12 Git.Git Gyan.FFmpeg Ollama.Ollama`

```bash
git clone https://github.com/warith-harchaoui/notes-helper && cd notes-helper
python -m pip install -e ".[all,dev,eval]"
```

## Running the checks (must be green before a PR)

```bash
ruff check .                       # lint
pytest -q -m "not slow" --cov=notes_helper   # fast unit tests + coverage
python scripts/audit_egress.py output/ # sovereignty gate on any generated report
```

AI-eval (opt-in, local judge — keeps sovereignty):

```bash
deepeval set-ollama qwen2.5:32b
NOTES_HELPER_RUN_EVAL=1 pytest -q -m slow tests/eval/
```

CI (GitHub Actions) runs ruff + the fast suite on Ubuntu/macOS/Windows × Python
3.11 & 3.13 on every push and PR. A red build blocks merge.

## Coding standards (non-negotiable)

Every `.py` (and, for the apps, every Swift file) must ship:

- a **module-level numpy docstring** with `Module summary`, `Usage example`, and an
  `Author` section: `Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui`;
- **numpy-style docstrings** on every function/class (Parameters/Returns/Raises/Examples);
- **full typing** (`from __future__ import annotations`);
- **no bare `print`** in library/script code — use `os_helper` (`osh.info/warning/error/debug`);
  CLI *result* output to stdout is fine;
- generous WHY-comments on non-obvious choices.

Docs are bilingual: keep `README.md` (EN) and `LISEZMOI.md` (FR) in sync, and add
recipes to `EXAMPLES.md`.

## Secrets

Never commit real credentials. Copy `notes_helper_config.json.example` →
`notes_helper_config.json` (git-ignored) and fill locally. `input/` and `output/` are
git-ignored data directories.

## Pull requests

1. Branch from `main`.
2. Keep the change focused; update `CHANGELOG.md`.
3. Make sure `ruff` + `pytest` are green locally.
4. Sign commits under your own name.
