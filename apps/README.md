# Notes Helper — Apple apps (macOS + iOS)

Native SwiftUI apps for **Mac Desktop** and **iPhone/iPad**, sharing one UI and
one engine contract. Same promise as the CLI: **nothing leaves your device
unless you decide.**

## Architecture

```
ContentView (SwiftUI, shared) ──► NotesHelperEngine (protocol)
                                    ├── CLIEngine    (macOS)  → drives the local `notes-helper` Python CLI
                                    └── NativeEngine (iOS)    → native whisper.cpp / CoreML / MLX (WIP)
```

- **macOS** reuses the exact on-device pipeline by shelling out to the installed
  `notes-helper` CLI (`pip install notes-helper`). Zero extra ML code, every sovereignty
  guarantee inherited.
- **iOS** cannot run Python/ffmpeg, so it ships a **fully native Swift engine**
  (`Sources/NotesHelper/Engine/`) — no CLI required:
  `AudioDecoder` (AVFoundation) → `VoiceActivityDetector` → `SpeakerEmbedder`
  (`DSPEmbedder` fallback + `CoreMLEmbedder` for TitaNet) → `Diarizer`
  (centered-cosine agglomerative clustering) → `SpeechRecognizer` (whisper.cpp via
  SwiftWhisper) → `IdentityStore` (on-device voiceprints) → `Synthesizer`
  (MLX or heuristic) → `ReportRenderer` (self-contained HTML/MD).
  It will ship with **no network entitlement**, making zero-egress OS-enforced.

### iOS models to bundle

The engine runs with built-in fallbacks, but for full quality add model files:

| Capability | File | Where |
|---|---|---|
| Transcription (required) | `ggml-base.bin` (whisper.cpp) | app bundle or `…/Application Support/NotesHelper/` |
| Diarization (optional) | `SpeakerEmbedder.mlmodelc` (TitaNet→CoreML) | app bundle |
| Summary (optional) | MLX model container | app bundle / download |

Without a whisper model the app diarizes + renders but reports that transcription
needs a model. The `#if canImport(SwiftWhisper)` / `#if canImport(MLXLLM)` guards
mean the app still builds without the packages (added in `project.yml`).

## Build

Prerequisites: **Xcode 15+** and [XcodeGen](https://github.com/yonaskolb/XcodeGen).

- 🍎 macOS : `brew install xcodegen`
  (install `brew` thanks to [brew.sh](https://brew.sh/))

The Xcode project is defined in `project.yml` (text, reviewable) and generated:

```bash
cd apps
xcodegen generate          # writes NotesHelper.xcodeproj
open NotesHelper.xcodeproj       # then pick the NotesHelper-macOS or NotesHelper-iOS scheme
```

Command-line build (no signing, for CI / local checks):

```bash
xcodebuild -project apps/NotesHelper.xcodeproj -scheme NotesHelper-macOS \
  -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO build
```

## Verified builds

Both targets build to real `.app` bundles (verified 2026-07-11 on Xcode 26):

- **macOS** — `xcodebuild -scheme NotesHelper-macOS -destination 'platform=macOS'` → `NotesHelper.app` ✅
- **iOS** — `xcodebuild -scheme NotesHelper-iOS -destination 'generic/platform=iOS Simulator'` → `NotesHelper.app` ✅

Both compile the full app + native engine. The `SwiftWhisper` (whisper.cpp)
SwiftPM target is a large C++ build that compiles slowly and can stall on some
Xcode toolchains during the `whisper_cpp` phase; the app builds cleanly without
it (the ASR path is guarded by `#if canImport(SwiftWhisper)` and type-checks
against the iOS SDK regardless — CI runs a Swift type-check job). Add the package
and build once on a healthy Xcode to activate real transcription.

## macOS runtime requirement

The desktop app needs the `notes-helper` CLI on the machine:

```bash
pip install -e ".[all]"    # from the repo root
```

If it is not on the app's `PATH`, set a custom path in `UserDefaults`
(`notesHelperCLIPath`); the app also probes Homebrew, `~/.local/bin`, and common
Python interpreters (`python3 -m notes_helper.cli`).

## Author

- [Warith HARCHAOUI](https://linkedin.com/in/warith-harchaoui)
