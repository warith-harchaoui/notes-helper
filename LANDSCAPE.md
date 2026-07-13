# LANDSCAPE

Related and competing tools in the "record a conversation → get a diarized,
summarized report" space, benchmarked against **notes-helper**. The job notes-helper
optimizes for is specific: a **fully-local, free, open-source, sovereign**
note-taker that names speakers **once** and remembers them, produces a
**verifiable** report, and where **nothing leaves the device**. A product built
for a different job (a cloud meeting-bot with CRM integrations) is not penalized
in the abstract — the scores just reflect fit to *this* niche.

Legend: **✅** yes · **◐** partial / paid-tier / optional · **❌** no ·
**🎯** notes-helper's target (not all shipped yet — see [PLAN.md](PLAN.md)).

---

## At a glance

Rows = products (cloud/paid on top, local/free below). Columns = the features
that define notes-helper's niche.

| Product | Fully local (no cloud) | Open source | Free | Speaker diarization | **Persistent speaker ID across meetings** | Grounded / click-to-audio summary | Works offline | Long-form (hours) | Owns your files (HTML/MD) | Obsidian / Markdown native | No account | iOS | Desktop |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **notes-helper** *(this project)* | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **« AI Note Taker » (mainstream archetype)** | ❌ | ❌ | ◐ | ◐ | ❌ | ❌ | ❌ | ◐ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Otter.ai | ❌ | ❌ | ◐ | ✅ | ❌ | ❌ | ❌ | ◐ | ❌ | ❌ | ❌ | ✅ | ✅ (web) |
| Fireflies.ai | ❌ | ❌ | ◐ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ (web) |
| Fathom | ❌ | ❌ | ◐ | ✅ | ❌ | ◐ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Granola | ◐ (mic local, cloud summary) | ❌ | ◐ | ◐ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ (Mac) |
| tl;dv | ❌ | ❌ | ◐ | ✅ | ❌ | ◐ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Plaud (device + app) | ❌ | ❌ | ❌ | ✅ | ❌ | ◐ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Zoom AI Companion / Teams / Copilot | ❌ | ❌ | ◐ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Apple Voice Memos / Notes (transcribe) | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ◐ | ◐ | ❌ | ✅ | ✅ | ✅ (Mac) |
| MacWhisper | ✅ (transcribe) / ◐ (cloud summary) | ❌ | ◐ | ◐ (pro) | ❌ | ❌ | ✅ | ✅ | ✅ | ◐ | ✅ | ❌ | ✅ (Mac) |
| Aiko | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ◐ | ❌ | ✅ | ✅ | ✅ (Mac) |
| Vibe | ✅ | ✅ | ✅ | ◐ | ❌ | ❌ | ✅ | ✅ | ✅ | ◐ | ✅ | ❌ | ✅ |
| Hyprnote | ✅ | ✅ | ✅ | ◐ | ❌ | ◐ | ✅ | ✅ | ✅ | ◐ | ✅ | ❌ | ✅ (Mac) |
| Meetily | ✅ | ✅ | ✅ | ◐ | ❌ | ❌ | ✅ | ✅ | ✅ | ◐ | ✅ | ❌ | ✅ |
| whisper.cpp (raw) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ◐ | ❌ | ✅ | ◐ | ✅ |

> Ratings reflect each tool's typical/default configuration for *this* job as of
> mid-2026. Cloud products often have a limited free tier (◐ under "Free"). Some
> local tools can bolt on a cloud LLM for summaries (◐ under "Fully local"),
> which trades the sovereignty guarantee for quality.
>
> The **« AI Note Taker » archetype** row is the generic mainstream expectation —
> the typical cloud voice-note app people mean by "an AI note taker" (freemium,
> account-gated, cloud transcription + summary, no cross-meeting speaker memory).
> It is the baseline notes-helper is built *above*, not a single product; the named
> cloud rows below are its concrete instances.

---

## The two clusters

**Cloud note-takers** (Otter, Fireflies, Fathom, Granola, tl;dv, Plaud, Zoom/
Teams/Copilot) — polished, feature-rich, integrated with calendars and CRMs. But
they are **structurally cloud**: audio and transcripts live on their servers,
they cost a subscription, they require an account, and they *cannot* offer
"nothing leaves your device" without abandoning their business model. Great for
sales teams; **disqualified** for confidentiality-bound work.

**Local transcribers** (Apple Voice Memos, MacWhisper, Aiko, Vibe, Hyprnote,
Meetily, whisper.cpp) — private and often free/open. But most stop at
*transcription*: weak or no diarization, no structured verifiable report, and —
critically — **no persistent speaker identity across meetings** and **no
grounded summaries**. Several are Mac-only and none pairs the full pipeline with
an Obsidian-native second-brain output.

---

## Where notes-helper is different

1. **Sovereign by architecture, not policy.** Zero egress is a verifiable
   property (open source + no network entitlement on iOS + CI egress audit), not
   a promise. Only the local cluster can claim this at all — and notes-helper makes it
   *provable*.
2. **Name once, known forever.** Persistent voiceprint identity across
   conversations is, as of this writing, **absent from every tool in the table**.
   It turns a pile of transcripts into a cross-meeting memory. This is the single
   biggest differentiator.
3. **Verifiable summaries.** Every decision/action/quote links to the exact audio
   second it came from. No orphan, hallucinated action items.
4. **You own the artifact.** A self-contained offline HTML file *and* an
   Obsidian `People/`+`Meetings/` graph — not a proprietary cloud record.
5. **Free with no COGS.** The compute is the user's device, so free is
   sustainable forever — no incentive ever to betray the local guarantee.

## Honest positioning vs. the closest neighbours

- **Hyprnote / Meetily / Vibe** are the nearest in spirit (local, open, free) and
  deserve credit for it. notes-helper's edge over them is the **persistent speaker
  identity**, the **grounded/verifiable report**, the **Obsidian-native memory
  graph**, and composability with the wider **AI Helpers** suite
  (`capture-helper`, `vocal-helper`).
- **MacWhisper / Aiko** are excellent local *transcribers* but not *diarized
  report* products, and not cross-platform to iOS with the full pipeline.
- **Apple's built-in transcription** is the free baseline everyone has; notes-helper's
  reason to exist is everything Apple's doesn't do: diarization, named speakers,
  structured verifiable summaries, cross-meeting memory, and portable reports.

## When to pick what

- **notes-helper** — you need a private, free, diarized, named, verifiable report that
  never leaves your machine, and you value cross-meeting memory + Obsidian.
- **Otter / Fireflies / Fathom** — you're a sales/CS team that *wants* cloud,
  CRM sync, and a bot in the call, and confidentiality isn't a constraint.
- **Granola** — you want a slick Mac experience and accept cloud summarization.
- **MacWhisper / Aiko / Vibe** — you mainly want fast local transcription and
  don't need diarization, named speakers, or structured reports.
- **Hyprnote / Meetily** — you want a local open-source meeting notetaker today
  and don't yet need persistent speaker identity or grounded summaries.
