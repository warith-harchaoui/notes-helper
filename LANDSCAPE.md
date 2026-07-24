# Landscape

[🇫🇷 PAYSAGE.md](https://github.com/warith-harchaoui/notes-helper/blob/main/PAYSAGE.md) · 🇬🇧 English

Related and competing tools in the "record a conversation → get a diarized,
summarized report" space, benchmarked against **notes-helper**. The job
notes-helper optimizes for is specific: a **fully-local, free, open-source,
sovereign** note-taker that names speakers **once** and remembers them,
produces a **verifiable** report, and where **nothing leaves the device**.
Ratings are ⭐ (1) to ⭐⭐⭐⭐⭐ (5), scored on fit to *this* niche —
recording → structured, diarized, grounded notes you own. A product built for a
different job (a cloud meeting-bot with CRM integrations) is not penalized in the
abstract; the score just reflects fit here.

notes-helper is **work in progress** — not all of its target features are shipped
yet (see [PLAN.md](PLAN.md)), and it is not on PyPI. Its row below reflects the
intended, designed-for behaviour of the full pipeline.

## At a glance

Rows = products (cloud/paid on top, local/free below). Columns = the criteria
that define notes-helper's niche.

<!-- TABLE:START -->
| Meeting Notes | Local-first | Open & free | Diarization | Persistent speaker ID | Grounded synthesis | Owns output (Markdown/vault) | Multi-surface |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **notes-helper** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| « AI Note Taker » archetype | ⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| Otter.ai | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ |
| Fireflies.ai | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ |
| Fathom | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| Granola | ⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ |
| tl;dv | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| Plaud | ⭐ | ⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| Zoom AI Companion / Teams / Copilot | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
| Apple Voice Memos / Notes | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| MacWhisper | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Aiko | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| Vibe | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Hyprnote | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Meetily | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| whisper.cpp | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐ | ⭐⭐⭐ |
<!-- TABLE:END -->

## Positioning map

<!-- FIGURE:START -->
2D representation of the table above.

![Positioning map](https://raw.githubusercontent.com/warith-harchaoui/notes-helper/main/assets/landscape.png)

The map is a 2-D summary of the seven criteria, so read it as a shape, not a scoreboard. `notes-helper` is at the top-right corner. The axes read **Horizontal — Versatile ↔ Openness** and **Vertical — Localism ↔ Authenticity**.
<!-- FIGURE:END -->

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

## Where notes-helper is different

1. **Sovereign by architecture, not policy.** Zero egress is a verifiable
   property (open source + no network entitlement on iOS + CI egress audit), not
   a promise. Only the local cluster can claim this at all — and notes-helper
   makes it *provable*.
2. **Name once, known forever.** Persistent voiceprint identity across
   conversations is, as of this writing, **absent from every tool in the table**
   (every competitor scores ⭐ on that column). It turns a pile of transcripts
   into a cross-meeting memory. This is the single biggest differentiator.
3. **Grounded summaries.** Every decision/action/quote links to the exact audio
   second it came from. No orphan, hallucinated action items. Cloud tools that
   surface some source-linking (Fathom, tl;dv, Plaud, Hyprnote) earn a partial
   score here; most tools do not attempt it at all.
4. **You own the artifact.** A self-contained offline HTML file *and* an
   Obsidian `People/`+`Meetings/` graph in Markdown — not a proprietary cloud
   record. The local editors (MacWhisper, Vibe, Hyprnote, Meetily) own their
   output too, which is why they score well on that column.
5. **Free with no COGS.** The compute is the user's device, so free is
   sustainable forever — no incentive ever to betray the local guarantee.

## Honest positioning vs. the closest neighbours

- **Hyprnote / Meetily / Vibe** are the nearest in spirit (local, open, free) and
  deserve credit for it — hence their strong **Local-first** and **Open & free**
  scores. notes-helper's edge over them is the **persistent speaker identity**,
  the **grounded/verifiable report**, the **Obsidian-native memory graph**, and
  composability with the wider **AI Helpers** suite (`capture-helper`,
  `vocal-helper`).
- **MacWhisper / Aiko** are excellent local *transcribers* but not *diarized
  report* products, and not cross-platform to iOS with the full pipeline.
- **Apple's built-in transcription** is the free baseline everyone has; it is
  fully local and everywhere (hence its high **Local-first** and **Multi-surface**
  scores), but it does no diarization, no named speakers, no structured summary,
  and no cross-meeting memory — which is exactly notes-helper's reason to exist.

## When to pick what

- **notes-helper** — you need a private, free, diarized, named, verifiable report
  that never leaves your machine, and you value cross-meeting memory + Obsidian.
- **Otter / Fireflies / Fathom** — you're a sales/CS team that *wants* cloud,
  CRM sync, and a bot in the call, and confidentiality isn't a constraint.
- **Granola** — you want a slick Mac experience and accept cloud summarization.
- **MacWhisper / Aiko / Vibe** — you mainly want fast local transcription and
  don't need diarization, named speakers, or structured reports.
- **Hyprnote / Meetily** — you want a local open-source meeting notetaker today
  and don't yet need persistent speaker identity or grounded summaries.
</content>
</invoke>
