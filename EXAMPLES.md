# EXAMPLES — Notes Helper cookbook

Self-contained, runnable recipes for the main use cases. English throughout
(code is universal); the French [LISEZMOI.md](LISEZMOI.md) links here too.

Convention: drop your audio in `input/`, let Notes Helper write to `output/`. Both
folders are git-ignored — your data never lands in the repo.

> Prerequisites: `pip install -e ".[all]"`, `ffmpeg` on PATH, and `ollama serve`
> running for synthesis. See [README.md](README.md) for cross-platform install.

---

## 1. End-to-end from the shell

```bash
# audio -> transcript -> summary -> report, all local
notes-helper run   input/meeting.m4a --out output/meeting
notes-helper synth output/meeting
notes-helper report output/meeting --format html,md
# open output/meeting/report.html
```

Or in one shot:

```bash
notes-helper all input/meeting.m4a --out output/meeting --format html,md,pdf
```

## 2. Library API — run the pipeline

```python
from notes_helper.pipeline import run

paths = run("input/meeting.m4a", "output/meeting")
print(sorted(paths))
# ['checkpoint', 'out_dir', 'speaker_mapping', 'transcript', 'wav']
print(paths["transcript"])
# output/meeting/transcript.json
```

## 3. Render outputs — Markdown-first, no lock-in

```python
from notes_helper.outputs import render

# Markdown is the neutral source of truth; everything else renders it.
written = render("output/meeting", ["md", "html"])
print(sorted(written))
# ['html', 'md']
```

DOCX / PDF / PPTX via the embeddable, open-source `md2star` (stays on-device):

```python
from notes_helper.outputs import render

written = render("output/meeting", ["docx", "pdf"])
print(sorted(written))
# ['docx', 'pdf']
```

Obsidian is *one* target, not the required one:

```python
from notes_helper.outputs import render

render("output/meeting", ["vault"], vault_dir="/path/to/ObsidianVault")
# writes People/<name>.md + Meetings/<date> <title>.md (wikilinks + Tasks checkboxes)
```

## 4. Speaker identity — "name once, known forever on your device"

```bash
# after a run, name a cluster once
notes-helper enroll output/meeting/diar_checkpoint.npz --cluster S0 --name "Warith Harchaoui"

# a later meeting with the same voice auto-labels it
notes-helper run   input/next-meeting.m4a --out output/next
notes-helper people list
#   warith-harchaoui       Warith Harchaoui         (8 ex.)
```

Under the hood — the store is a local SQLite file of voiceprints (never audio):

```python
from notes_helper.identity import PeopleStore

store = PeopleStore()                     # default ~/.notes-helper/people.db
print([p["name"] for p in store.all_people()])
# ['Warith Harchaoui']
store.close()
```

Forget a person (biometric hygiene — it's your device, your call):

```bash
notes-helper people forget warith-harchaoui
```

## 5. Local summary directly from a transcript

```python
import json
from notes_helper.synth import synthesize, load_speakers

transcript = json.load(open("output/meeting/transcript.json"))
speakers = load_speakers("output/meeting/speaker_mapping.json", transcript)
syn = synthesize(transcript, speakers, title="Product sync", language="en")
print(list(syn))
# ['meta', 'speakers', 'resume', 'points_cles', 'decisions', 'actions', 'chapitres', 'themes', 'citations']
```

> If `ollama serve` is unreachable, `synthesize` emits a clearly-labelled minimal
> summary instead of failing — the transcript and diarization stay complete.

## 6. Prove the sovereignty claim

```bash
notes-helper audit output/meeting
# egress audit OK — no external URLs in generated artifacts
```

```python
from notes_helper.cli import audit_egress

n = audit_egress("output/meeting")
print(n)
# 0
```

## 7. Sharpen the report with `notes.yaml`

Drop a `notes.yaml` (all fields optional) next to the recording, then build the
report from the whole folder — one code path handles a plain conversation and a
slide-backed talk alike:

```yaml
# input/meeting/notes.yaml
title: Product sync — Q3 roadmap
date: 2026-07-23
time: "14:00"
location: Paris, room B2
language: en                    # omit to auto-detect from the transcript
speakers:                       # a LIST OF NAMES, not keyed by S0/S1
  - Warith Harchaoui
  - Alexandre Larmagnac
slides: deck.pdf                # a PDF in the folder (omit → auto-detect a landscape PDF)
context_files:                  # documents distilled into the synthesis context
  - brief.md
  - manuscript.pdf
additional_glossary:            # proper-nouns that COMPLETE (never replace) the context
  - TitaNet
  - Plutchik
```

```python
from notes_helper.report import build_report

index_html = build_report("input/meeting")   # reads notes.yaml if present
print(index_html)
# output/meeting/index.html
```

What each field buys you:

- **`speakers`** — the diarizer discovers *how many* voices there are; the pipeline
  then determines *which recorded voice is which named person* from the conversation
  itself (LLM attribution, talk-time heuristic fallback). Order is not an identity
  claim; an unmatched voice keeps its `S0`/`S1` id.
- **`slides`** — the named PDF is rasterized and content-synced to the moment each
  slide is discussed. A portrait PDF is a document, not a deck, so a folder with
  only a portrait PDF gets no slide panel.
- **`context_files`** — a large document is *distilled* across several offline LLM
  passes (chunk → summarise → merge → recurse) instead of truncated, so the whole
  document informs the report. The folder's `context.md` is still read
  automatically; these augment it.
- **`additional_glossary`** — words/proper-nouns the model must spell right; they
  complete the context, never replace it.

## 8. Configuration

```bash
cp notes_helper_config.json.example notes_helper_config.json   # gitignored
```

```python
from notes_helper import config
print(config.OLLAMA_MODEL)
# qwen2.5:32b
```

Environment variables override the config file, which overrides the defaults:

```bash
NOTES_HELPER_OLLAMA_MODEL=llama3.1:8b NOTES_HELPER_LANG=en notes-helper synth output/meeting
```
