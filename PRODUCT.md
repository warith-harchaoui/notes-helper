# notes-helper — Product Description

> **Name:** `notes-helper` (lowercase, like the `*-helper` suite it composes).
>
> A free, open-source, fully-local recorder that turns any conversation into a
> diarized, speaker-named, verifiable report — **and nothing leaves your device unless you decide.**

See [PLAN.md](PLAN.md) for the build steps and [LANDSCAPE.md](LANDSCAPE.md) for
how it compares to the alternatives.

---

## One line

Record or import a conversation → get a beautiful, structured, searchable report
with each speaker named — computed entirely on your own machine, with no cloud,
no account, and no cost.

## The promise (exact wording)

> **Nothing leaves your device during use.** The only network events are one-time
> model downloads at first launch, and any sync *you* explicitly enable.

This is not a privacy *policy* ("trust us"). It is an architectural *property*:
the app has no networking in the hot path, ships with no analytics, and — on
iPhone — is built with no network entitlement, so the OS itself enforces it. You
can verify it with a network monitor. The source is open; the guarantee is auditable.

---

## Who it's for

- **Anyone who takes meetings** and wants a clean record without a monthly bill
  or a bot joining the call.
- **Confidentiality-bound professionals** for whom cloud note-takers are *not
  allowed*: therapists, lawyers (attorney–client privilege), doctors (HIPAA),
  journalists (source protection), HR/board/M&A conversations. For them, "fully
  local, nothing leaves, you own the file" is the only permissible option.
- **Researchers, teams, and tinkerers** who want an open, hackable, local
  pipeline instead of a walled SaaS.

## What it does

1. **Capture** — record live from any microphone, or import an existing file
   (Voice Memo `.m4a`, mp3, wav, …). Desktop can also capture application/screen
   audio (e.g. a video call).
2. **Understand** — on-device: voice-activity detection → speaker diarization →
   speech-to-text (Whisper) → structured summary by a **local** LLM.
3. **Name once** — you name each speaker a single time; notes-helper remembers their
   voice and recognizes them in every future recording.
4. **Report** — an interactive offline report (single HTML file), portable
   Markdown for any destination you choose, and compiled DOCX / PDF / PPTX via
   `md2star`. See [Outputs](#outputs).

## Modes

notes-helper covers **two input modes on both platforms**, plus a desktop-only bonus.
The distinction that matters for streaming: *record-live-then-report* (easy — live
capture, process at the end) vs *true real-time* (a transcript that grows as you
speak — harder, because it needs **online** diarization with no future context).

| Mode | Mac Desktop | iPhone / iPad | Notes |
|---|:--:|:--:|---|
| **Offline — audio file** (import Voice Memo / mp3 / wav) | ✅ | ✅ | The v1 baseline; highest quality |
| **Streaming — live mic, report at end** | ✅ | ✅ | Live capture → same pipeline → report when you stop |
| **Streaming — live mic, real-time transcript** | ✅ | ✅ | Near-real-time captions + rolling summary; online diarization is the hard part |
| **Streaming — application / screen audio** (a video call) | ✅ | ❌ | Desktop only — iOS sandbox blocks capturing another app's audio |

- **Desktop input** = `capture-helper` (mic / application / screen).
- **iPhone input** = native `AVAudioEngine`, emitting the same frame contract.
- **Sequencing:** offline + record-live-then-report ship first; real-time
  transcript is the committed second wave (its quality gate is online diarization).

## The report (anatomy)

A single offline HTML file — no CDN, no fonts phoning home — with tabs:

| Tab | Contents |
|---|---|
| **Résumé** | Narrative summary |
| **Points clés** | Key points |
| **Décisions** | Decisions, each with context |
| **Actions** | Action table: task · owner · due date |
| **Chapitres** | Timeline chapters (click a timestamp → audio seeks) |
| **Thèmes** | Thematic groupings |
| **Citations** | Notable quotes, attributed to the named speaker + timestamp |
| **Transcript** | Full diarized transcript, searchable, filterable by speaker |

Speakers are shown as coloured, named chips with roles. An audio player is
embedded (small Opus + universal MP3 fallback). **Every claim in the summary is
grounded**: decisions, actions, and quotes link back to the exact second of
audio that justifies them — click to hear it.

## Speaker identity — "name once, known forever on your device"

- After the first recording, notes-helper asks you to name any unrecognized speaker.
- It stores a compact voiceprint per person **locally** (no biometrics leave the
  device).
- In every later recording, the same people are auto-labelled — and their notes
  link together, so you get a cross-conversation memory: *"what did X commit to
  over the last five meetings, and what's still open?"*

## Outputs — Markdown-first, your choice of destination

notes-helper treats **Markdown as the neutral source of truth** and everything else as
a render of it. No lock-in, no religion — *you* decide where a report goes.

1. **In-app interactive report** (default) — the self-contained HTML/CSS/JS GUI
   already built: tabbed (résumé, décisions, actions, chapitres, citations,
   transcript), speaker-coloured, searchable, with click-to-seek audio. Offline,
   portable, zero external request. Open it, share the single file, or print it.
2. **Markdown** — the portable core. Plain `.md` you own, ready for **any**
   destination you choose: Obsidian (`People/` + `Meetings/` with wikilinks +
   Tasks checkboxes), Logseq, Bear, a git repo, plain folders — or nothing at
   all. **Obsidian is one supported target, not the required one.**
3. **Compiled DOCX / PDF / PPTX** — for sharing, filing, or formal delivery,
   notes-helper compiles the Markdown with **`md2star`** (Markdown → professional DOCX
   / PPTX / PDF). `md2star` is **open source and embeddable**, so document
   generation runs **in-process, on-device** — the sovereignty guarantee extends
   all the way to the exported document; nothing leaves.
4. **Raw artifacts** — `transcript.json`, `synthese.json` — open formats you own,
   the inputs every render is built from.

## Location

- If notes-helper records, it can capture location at record time (with permission).
- For imported files (which rarely carry GPS), you fill the location once; it's
  best-effort from metadata otherwise. Always editable.

## Platforms & price

| | Status | Distribution | Price |
|---|---|---|---|
| **macOS** | first target | brew / GitHub Releases (notarized later) | **Free** |
| **iPhone / iPad** | later | App Store | **Free** |
| Windows / Linux | via the same Python core | pip / GitHub | **Free** |

- **License:** Apache-2.0 (app), BSD-3 (the `vocal-helper` / `capture-helper`
  libraries it composes).
- **Cost to the user:** $0, forever. No account, no subscription, no in-app
  purchase.
- **Why free is sustainable:** the work runs on *your* device, so there are no
  servers and no inference bill to recoup. There is nothing to monetize and,
  therefore, no incentive to ever betray the local guarantee.

## What notes-helper is **not** (scope boundaries)

- **Not a meeting bot.** It does not join Zoom/Meet/Teams as a participant.
- **Not a CRM / workspace.** It writes files you own (HTML, Markdown), not a
  proprietary cloud database.
- **Not cloud-synced by default.** You may point output at iCloud / Obsidian
  Sync, but that is an explicit choice that *does* send bytes off-device (clearly
  labelled). Local folder = fully sovereign.
- **Real-time transcript is the second wave.** Offline files and
  record-live-then-report ship first; a growing-as-you-speak transcript is a
  committed follow-up whose quality gate is online diarization (see [Modes](#modes)).
- **Not magic on-phone summarization.** iPhone uses a smaller local model than a
  Mac; long-meeting summaries are good but not frontier-cloud quality — the
  honest trade for "nothing leaves."

## The built-in tech (all local)

- **VAD:** Silero · **Diarization:** TitaNet embeddings + agglomerative clustering
- **ASR:** whisper.cpp (`large-v3-turbo`), multilingual (FR/EN/…)
- **Summary:** local LLM via Ollama (desktop) / MLX (iPhone)
- **Capture:** `capture-helper` (desktop) / `AVAudioEngine` (iOS)
- **Report:** self-contained HTML with vendored fonts + Tailwind (zero external requests)
