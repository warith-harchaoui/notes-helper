# notes-helper — Build Plan

> **Name:** `notes-helper` (lowercase, matching the `*-helper` suite).
>
> A fully-local, free, open-source meeting/conversation recorder that turns audio
> into a diarized, verifiable, speaker-named report — and **nothing leaves the
> device unless you decide**.

---

## 1. The thesis (locked)

Five constraints, each reinforcing the others. None is negotiable; they are the product.

| Constraint | What it means | How it's guaranteed |
|---|---|---|
| **Local** | All compute on-device: capture, VAD, diarization, ASR, summary | No network calls in the hot path |
| **Sovereign** | *By design*, nothing leaves the machine during use | Egress audit in CI; no analytics SDK; on iOS, no network entitlement |
| **Free** | $0 to the user, forever | Zero server COGS — the device does the work |
| **Open** | Permissive license, auditable, reproducible | Apache-2.0 (app) + BSD-3 (libs), public repo |
| **No-cost-to-run** | $0 to the author (one optional $99/yr Apple fee for App Store) | GitHub + Hugging Face + brew/pip host everything |

**Precise sovereignty claim (the exact wording to ship):**
> "Nothing leaves your device during use. The only network events are one-time
> model downloads at first launch, and any sync *you* explicitly enable."

**Non-negotiable design rule:** the summarization LLM is **local** (Ollama / MLX /
llama.cpp). A single cloud API call breaks the entire thesis.

---

## 2. Architecture — three layers, one seam

```
INPUT  ──────────────►  PROCESS  ──────────────►  OUTPUT
capture-helper          vocal-helper              build_page.py · build_vault.py · md2star
(mic | file | screen)   VAD → diar → ASR → LLM    HTML GUI · Markdown (any target) · DOCX/PDF/PPTX
```

**The seam is the frame contract.** `capture-helper.MicFrame` ≈
`podcast_helper.PcmFrame` = 16 kHz mono float32 — exactly what `vocal_helper.vad`
and the current `diar_pipeline.py` (`SR = 16000`) consume.

- **Desktop (Mac/Win/Linux):** INPUT = `capture-helper` (ffmpeg subprocess).
- **iOS:** `capture-helper` cannot run (no ffmpeg/PortAudio). INPUT = native
  `AVAudioEngine` emitting the **same MicFrame dict**. Everything above the seam
  is shared/ported, not rewritten conceptually.

**Modes covered (all committed) — on both Mac and iPhone:**

| Mode | Mac | iPhone | Engineering note |
|---|:--:|:--:|---|
| Offline — audio file | ✅ | ✅ | v1 baseline; batch pipeline as-is |
| Streaming — live mic, report at end | ✅ | ✅ | live capture → buffer → same batch pipeline |
| Streaming — live mic, real-time transcript | ✅ | ✅ | needs **online diarization** (the hard part) + rolling summary |
| Streaming — application/screen audio | ✅ | ❌ | desktop only; iOS sandbox blocks other-app audio |

Offline + record-live-then-report reuse the existing pipeline. Real-time
transcript is the committed second wave, gated on online-diarization quality
(Phase 6).

**Existing assets in this folder (reuse, don't rebuild):**
- `diar_pipeline.py` — VAD → TitaNet embed → agglomerative cluster → whisper.cpp ASR
- `clean_transcript.py` — hallucination/filler cleanup
- `map_speakers.py` — cluster→name Hungarian assignment (to be superseded by enrollment)
- `build_page.py` — self-contained Tailwind HTML report (already offline: local `assets/`)
- `synthese.json` — the summary schema (currently LLM-authored; must become local-LLM-authored)
- `assets/` — vendored `tailwind.js`, `fonts.css`, `fonts/*.woff2` (no CDN)

---

## 3. Repos & licensing

- `vocal-helper` — **BSD-3** (existing). PROCESS.
- `capture-helper` — **BSD-3** (existing). INPUT.
- `md2star` — **open source** (existing). OUTPUT: Markdown → DOCX / PPTX / PDF,
  embeddable in-process (keeps document export on-device).
- `notes-helper` (this repo) — **Apache-2.0** (patent grant for the AI app). Orchestrates
  the three + OUTPUT + apps.

Distribution, all $0 except the optional Apple fee:
- Desktop: `brew` tap / `pip install` / GitHub Releases. **$0.**
- iOS + notarized Mac: Apple Developer Program **$99/yr** — only when the phone earns it.
- Models: users pull from Hugging Face. **$0 bandwidth to us.**

---

## 4. Milestones

Ordered so each step is shippable and the **$0 path comes first**. Mac-first;
iOS is a later, larger lift.

### Phase 0 — Consolidate + sovereignty guardrails  *(days)*
**Goal:** turn the loose scripts into one clean local pipeline with the egress
guarantee enforced from commit one.

Steps:
1. Package the folder: `notes-helper/` with `pyproject.toml`, `LICENSE` (Apache-2.0),
   `README.md` stating the precise sovereignty claim.
2. Refactor `diar_pipeline.py` + `clean_transcript.py` + `build_page.py` into a
   `notes-helper` CLI: `notes-helper run <audio> --out <dir>` → transcript + report.
3. **Egress-audit check** (`scripts/audit_egress.py`): grep every generated
   HTML/vault artifact for `https?://`; fail the build if any external URL is
   found. Wire into CI. (This catches exactly the stale-CDN leak found in the
   old `reunion_rapport.html`.)
4. Regenerate the sample report from the current local `build_page.py`; confirm
   zero external URLs.

**Done when:** `notes-helper run` produces a report from a file, offline, and CI fails
on any egress.

### Phase 1 — Local LLM synthesis  *(days–1 wk)*
**Goal:** the summary (`synthese.json`) is produced **on-device**, not authored in a chat.

Steps:
1. Define a strict JSON schema for `synthese.json` (meta, speakers, resume,
   points_cles, decisions, actions, chapitres, themes, citations) — it already
   exists implicitly in `build_page.py`; formalize it.
2. Implement `notes-helper/synth.py` calling **Ollama** (`vocal_helper.llm` already
   targets Ollama) with a map-reduce over the transcript for long meetings
   (6h+ won't fit a context window).
3. Ground every claim: each decision/action/quote carries the timestamp(s) it
   was derived from → clickable in the report (`build_page.py` already seeks on
   `data-t`).
4. Pick a default local model (Mac: ~20–30B via Ollama; document the quality
   vs. size tradeoff).

**Done when:** `notes-helper run` produces the full report end-to-end with **no human
and no cloud** in the loop.

### Phase 2 — Voice enrollment + cross-meeting identity  *(1–2 wks)*
**Goal:** "name each speaker once," then recognize them forever. This is the
biggest differentiator.

Steps:
1. `notes-helper/enroll.py`: store per-person TitaNet centroid + name/role in a local
   DB (`~/.notes-helper/people.db` or a folder of JSON).
2. Replace the fragile `map_speakers.py` heuristic: label clusters by
   **nearest enrolled centroid** (with a distance threshold → "unknown speaker,
   name me?").
3. First-run flow: after diarization, prompt the user to name any unknown
   speaker once; update the DB.
4. Surface `mapping_confidence` in output (already computed).

**Done when:** a second recording with the same people auto-labels them with no
user input.

### Phase 3 — Outputs: Markdown-first + exports (no lock-in)  *(days)*
**Goal:** one neutral Markdown core, rendered wherever the user wants — the
interactive GUI, Obsidian (one target among many), and compiled documents. No
religion: the user picks the destination.

Steps:
1. **Markdown emitter** (`notes-helper/build_md.py`) — turn `transcript.json` +
   `synthese.json` into portable `.md` (report + optional `People/`+`Meetings/`
   split). This is the source of truth every other output renders from.
2. **Obsidian target** (`notes-helper/build_vault.py`, sibling of `build_page.py`):
   - `People/<Name>.md` — one note per enrolled speaker (backlinks = every
     meeting they were in).
   - `Meetings/<date> <title>.md` — YAML frontmatter (date, lieu, duree,
     participants as `[[wikilinks]]`, audio, rapport, mapping_confidence);
     sections for résumé/décisions/actions/citations.
   - Actions as `- [ ] … 📅 <due> [[Assignee]]` (Tasks-plugin compatible →
     cross-meeting accountability ledger, zero code).
   - One flag; **Obsidian is opt-in, not assumed** — Logseq/Bear/git/plain
     folders are equally valid consumers of the same Markdown.
3. **Compiled documents** (`notes-helper/build_docs.py`) — pipe the Markdown through
   **`md2star`** (embeddable, open source) → DOCX / PDF / PPTX, in-process and
   on-device (nothing leaves; passes the same egress audit as Phase 0).
4. Config: which outputs to emit (GUI / Markdown dir / Obsidian vault /
   DOCX-PDF-PPTX); keep audio as Opus in `attachments/`; link the HTML report for
   rich timestamp seeking; never overwrite user annotations below a marker line.

**Done when:** one `notes-helper run` can emit — per user choice — the interactive HTML
GUI, plain Markdown, an Obsidian `People/`+`Meetings/` pair, and a DOCX/PDF/PPTX,
all from the same core, all offline.

### Phase 4 — Mac app (the free, $0 flagship)  *(2–4 wks)*
**Goal:** a real app, still 100% local, still $0 to ship.

Steps:
1. Front the pipeline with a local server (`vocal-helper` + `capture-helper`
   already ship FastAPI) and a GUI. Options, in order of least effort:
   - Local FastAPI + the existing HTML report in a **WKWebView / Tauri** shell.
   - `capture-helper` device picker → record live **or** import a file.
2. First-run model download UX (whisper, TitaNet, Ollama model) with progress +
   an explicit "this is the only time anything touches the network" notice.
3. Output to a user folder / iCloud Drive / Obsidian vault (user's choice;
   iCloud clearly flagged as "leaves device to Apple").
4. Distribute via **brew tap / GitHub Releases** ($0) for the interim, then
   **notarize** (covered by the committed $99/yr Apple Developer Program) for a
   frictionless Gatekeeper install.

**Done when:** a non-technical user records or imports, names speakers once, and
gets a report — without a terminal, offline, verifiable via a network monitor
showing zero egress.

### Phase 5 — iOS app (the platform where sovereignty is OS-enforced)  *(months)*
**Goal:** the phone build, where zero-egress becomes provable via the App Store
privacy label.

Steps:
1. Native INPUT: `AVAudioEngine` → MicFrame contract.
2. ASR: **whisper.cpp** (compiles for iOS, CoreML/Metal) — solved.
3. Diarization: **port TitaNet → CoreML/ONNX** (the single biggest work item);
   reimplement agglomerative clustering in Swift (small).
4. Synthesis: small local model via **MLX** (3–8B) — document the quality
   regression vs. Mac; consider an explicit optional cloud toggle *off by
   default* if quality is unacceptable (would be a labeled, opt-in break of the
   claim).
5. Ship with **no network entitlement** → OS-enforced zero egress; App Store
   label = "Data Not Collected". Requires the $99/yr program.
6. Long-recording handling: `BGProcessingTask` / foreground; on-demand model
   resources to keep the initial download small.

**Done when:** the app transcribes + diarizes + summarizes a file entirely
on-device, and the App Store privacy label reads "Data Not Collected."

### Phase 6 — Real-time transcript (committed second wave)  *(1–2 mo)*
**Goal:** the growing-as-you-speak mode, on both Mac and iPhone.

`capture-helper.iter_mic_audio` (desktop) / `AVAudioEngine` (iOS) give the live
stream; the hard part is **online diarization** (incremental clustering, no
future context) — materially harder than the current offline centering trick.
`vocal_helper` already has the streaming stages + `eot.py` (end-of-turn) +
rolling-summary `llm.py` scaffolding to build on.

Steps: (1) live captions via streaming ASR; (2) online speaker assignment against
enrolled voiceprints (Phase 2) — enrollment makes live labelling tractable
because you match to known centroids instead of clustering blind; (3) rolling
local-LLM summary updated every N utterances.

**Done when:** a live conversation shows named captions + a running summary, on
both platforms, still fully local. Offline remains the quality reference; live is
"good enough to follow along."

### Phase 7 — Multimodal + agentic  *(v2)*
- `capture-helper` camera/screen frames → whiteboard/slide sync, screen↔speech
  linkage (desktop only; iOS sandbox blocks other-app audio).
- Actions → drafted emails/tickets/calendar via local LLM or the user's own MCP.
- Meeting/talk → content pipeline via the existing `content-repurposer` /
  `youtube-script-builder` skills.

---

## 5. Sovereignty enforcement (not just intent)

- **CI egress audit** (Phase 0): no `http(s)://` in any generated artifact.
- **No analytics/crash SDK** — auditable absence.
- **iOS:** no network entitlement → OS-enforced; privacy label co-signs it.
- **Reproducible builds** (north star): let an auditor rebuild from source and
  verify the shipped binary byte-for-byte (Signal model). Aspirational, not v1.
- **The claim is a *property*, not a promise:** Little Snitch / iOS Privacy
  Report shows zero egress. That's the marketing.

---

## 6. Known hard parts / risks

1. **iOS TitaNet → CoreML** — biggest single work item; consider a lighter
   embedder (ECAPA/pyannote) with existing mobile exports as fallback.
2. **On-device LLM quality on iPhone** — 8B summarizing a 6h transcript regresses
   vs. Mac 30B. Mitigate with map-reduce + good prompting; accept a quality gap.
3. **Location metadata** — Voice Memos / imported files rarely carry GPS. Reliable
   only when *the app itself* records (capture `CLLocation`). Otherwise manual
   fill (`synthese.json.meta.lieu`). Treat "guess from metadata" as best-effort.
4. **Online diarization** (Phase 6) — genuinely hard; don't gate v1 on it.
5. **Mac App Store sandbox** vs. Python/ffmpeg/Ollama subprocess — avoid by
   distributing outside MAS (brew/notarized DMG) until/unless fully native.

---

## 7. Cost ledger

| Item | Cost |
|---|---|
| Runtime, storage, bandwidth, model hosting, code hosting | **$0** |
| Maintenance | your time, no obligation |
| Apple Developer Program (iOS App Store + Mac notarization) | **$99/yr — committed** |

The **only** monetary cost in the entire project is the **$99/yr Apple Developer
Program**, and it's accepted. Phases 0–4 still run at $0 via brew/pip/GitHub; the
$99 buys the iOS App Store listing (Phase 5) and frictionless Mac notarization.
Total lifetime bill: $99/yr, flat, regardless of user count.

---

## 8. Immediate next step

**Phase 0.** Scaffold `notes-helper/` (pyproject + Apache-2.0 LICENSE + README with the
precise claim), fold the existing scripts into a `notes-helper run` CLI, and wire the
egress-audit check into CI. That converts this conversation into a real project
with the sovereignty guarantee enforced from the first commit — at $0.
