# ADR 0001 — Adopt the Engineering Doctrine

**Status:** Accepted · 2026-07-18
**Scope:** The whole notes-helper cross-platform product (Rust core + apps).

## Context

We are building a local-compute, hardware-aware, multi-platform product (macOS,
Ubuntu/Debian, Windows, iOS, Android) with on-device AI. The maintainer provided an
**Engineering Doctrine** (local-compute, Python-explores / Rust-carries-the-core,
native platform integration, versioned contracts, multi-OS CI, tiered support). Our work
so far already follows its spine; this ADR adopts it formally and records the deltas.

## Decision

Adopt the Engineering Doctrine as the governing reference for architecture, lifecycle,
storage, testing, distribution, and AI-assisted development. The chain of authority is:
**contracts → source + versioned formats → tests/CI/security/perf gates → signed
artifacts**; agents assist but never outrank it.

## Where we already align

- **Python explores, Rust is the product core** (translating the `*-helper` libs to
  Rust). Recorded in `CODING.md` Part III and memory.
- **Ports-and-adapters**, pure/sync domain, async only at boundaries (`nh-core`).
- **High-level product API** (`Session::create/run_offline`, ports) — no vendor handles
  leaked; engines behind adapters (whisper.cpp, sherpa-onnx, llama.cpp, ffmpeg).
- **Model manifest** with hash/version/tier (`nh-core::models`) matches the doctrine's
  model-as-versioned-dependency rule.
- **Feature-gated heavy engines** keep CI fast/green; **sovereign, local-only** (no cloud
  client exists).

## Deltas to apply (this ADR triggers them)

1. **Desktop UI = Tauri 2 + React + TypeScript (strict).** The `front-*` skills are
   prototype/audit/figure tools (vanilla-JS output is prototype-grade), translated into
   the React component system — not the production UI layer. Updated in
   `TECHNICAL_STACK.md` and `ARCHITECTURE.md`.
2. **Storage ownership is tri-partite:** **SQLite** owns durable mutable state (identity
   vault, sessions, settings, model-cache metadata, migrations); **Polars** owns
   analytical computation (speaking-time, Plutchik emotion aggregation, evaluation);
   **Parquet/Arrow** carry large tabular artifacts and golden corpora. A Polars frame is
   never the system of record.
3. **Python→Rust parity via PyO3 + maturin shadow-mode** against **golden fixtures** in
   `contracts/` (inputs, expected outputs, tolerances) — this is how WER/DER parity vs
   the Python pipeline is proven before any switch.
4. **Mobile UI is native** (SwiftUI / Kotlin+Compose) over UniFFI. Tauri-mobile remains
   an explicit exception requiring its own ADR.
5. **Diagrams use Mermaid**, quantitative figures use **Vega-Lite** (Vega for low-level),
   both on the **Good Colors** palette (<https://harchaoui.org/warith/colors/>). Existing
   ASCII diagrams migrate to Mermaid.
6. **Tiered support** (Tier 1/2/3) + capability classes documented in
   `TECHNICAL_REQUIREMENTS.txt`.
7. **ADRs** for irreversible/expensive choices live under `docs/adr/`.

## Consequences

- `ARCHITECTURE.md`, `TECHNICAL_STACK.md`, `TECHNICAL_REQUIREMENTS.txt` updated to match.
- New work items: a `contracts/` tree with golden fixtures; a PyO3 shadow-mode harness
  comparing `nh-core` to the Python `notes_helper` on the real corpus; SQLite/Polars
  adapters behind the existing ports.
- No change to the sovereignty thesis, the no-MVP stance, or the Python-reference /
  Rust-production principle — the doctrine reinforces all three.

## Rollback

These are documentation and planning changes plus additive adapters; each is reversible.
Changing the reference frontend stack (away from React) or making Tauri-mobile the default
would each need a new ADR.
