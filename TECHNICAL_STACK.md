# notes-helper — Stack technique

> La stack concrète, couche par couche, dérivée des décisions de
> [`TECHNICAL_QUESTIONS.md`](TECHNICAL_QUESTIONS.md) et du design de
> [`ARCHITECTURE.md`](ARCHITECTURE.md). Les exigences par OS/device sont dans
> [`TECHNICAL_REQUIREMENTS.txt`](TECHNICAL_REQUIREMENTS.txt).

---

## 1. Cœur commun — `nh-core`

| Élément | Choix | Pourquoi |
|---|---|---|
| Langage | **Rust** (édition récente) | seule base compilée commune aux 6 cibles ; sûreté mémoire ; FFI propre |
| Organisation | **Cargo workspace** (`core/`) : crate lib `nh-core` + crates de bindings | séparation moteur / liaisons |
| Erreurs | **`thiserror`** (lib) · **`anyhow`** (bords binaires) | erreurs typées, pas de `panic` en lib |
| Logging | **`tracing`** | structuré, pas de `println!` |
| Sérialisation | **`serde`** (+ `serde_json`) | modèle de rapport, config |
| Async | **`tokio`** | pipelines streaming, I/O |
| Hash/intégrité | **`sha2`** | vérif des modèles téléchargés |
| Téléchargement | **`reqwest`** (ou `ureq`) | provisioning modèles depuis le FTP |

## 2. Moteurs on-device (souverains, portables)

| Fonction | Moteur | Binding Rust envisagé | Cibles |
|---|---|---|---|
| **ASR** | **whisper.cpp** (ggml) | `whisper-rs` / bindgen | les 6 |
| **VAD + diarisation + embeddings locuteur** | **sherpa-onnx** (ONNX Runtime) | crate `sherpa-rs` / bindgen | les 6 (Q4 : parité) |
| **Synthèse LLM** | **llama.cpp** (GGUF) | `llama-cpp-2` / bindgen | les 6 |
| **Accélération Apple (option)** | **MLX** | via couche native Swift | iOS/macOS |
| **Émotions (SER audio, Plutchik)** | modèle **ONNX** | via ONNX Runtime (sherpa) | les 6 |

*Diarisation cloud (pyannote.ai payant, Gladia) = **exclue** ; seulement dans
`pyannote-helper`/`diarization-helper`, non utilisés.*

## 3. Liaisons cœur ↔ coquilles

| Cible | Pont |
|---|---|
| iOS / macOS (Swift) | **UniFFI** (bindings générés) |
| Android (Kotlin) | **UniFFI** |
| Desktop (natif/C) | **ABI C** via `cbindgen` |
| Flutter (si retenu mobile) | **flutter_rust_bridge** |

## 4. Coquilles UI  *(aligné doctrine — ADR 0001)*

| Couche | Choix | Notes |
|---|---|---|
| Desktop | **Tauri 2 + React + TypeScript (strict)** (Vite, pnpm) | UI de prod en React ; rapport **notes-helper-style** = HTML auto-suffisant |
| Mobile | **UniFFI + SwiftUI (iOS) / Kotlin+Compose (Android)** | UI **native** ; **Tauri-mobile = exception à ADR** |
| Skills `front-*` | **prototype / audit / figures uniquement** | vanilla-JS = qualité prototype, à **traduire** en composants React ; audits a11y/contraste/UX en CI |
| Figures | **Vega-Lite** (défaut) / **Vega** (bas niveau) via **front-figures**, palette **Good Colors** | camemberts + roue de Plutchik ; boucles PNG en dev ; spec = artefact versionné |
| Diagrammes | **Mermaid** (archi/flux) | pas d'ASCII art ; Vega ≠ diagramme d'archi |

## 5. Entrée (graphe de sources « OBS », Q13)

| Élément | Desktop | Mobile |
|---|---|---|
| Capture live | héritage **capture-helper** (ffmpeg) → porté en adaptateurs Rust ; **multi-micro + multi-audio-système + caméra/écran** | AVAudioEngine (iOS) / AAudio-Oboe (Android) — **micro(s) seulement** |
| Fichiers | décodage **ffmpeg** (héritage audio/video-helper) | idem via natif |
| Liens VOD/live | héritage **youtube-helper/podcast-helper** (yt-dlp) | idem |

## 6. Sortie & export

| Format | Chemin | Portée |
|---|---|---|
| HTML interactif | modèle `Report` → front-ui (auto-suffisant) | les 6 |
| **PDF** | **print WebView** (WKWebView / WebView2 / Android PrintManager) | les 6 |
| **DOCX** | **`docx-rs`** depuis le `Report` | les 6 |
| DOCX/PDF/PPTX « fidélité max » | **md2star** (Pandoc + LibreOffice) | desktop only (option) |
| Composition PDF haute qualité (option) | **Typst** (Rust, embarquable) | les 6 |

## 7. Partage (opt-in, infra de l'utilisateur — Q8)

| Backend | Équivalent Rust envisagé |
|---|---|
| S3 / MinIO / R2 / B2 (héritage **bucket-helper**) | `aws-sdk-s3` / `rust-s3` |
| SFTP (héritage **sftp-helper**) | `russh` / `ssh2` |

*Aucun endpoint livré : indisponible tant que l'utilisateur n'a pas renseigné le sien.
L'export interactif ne requiert **pas** de serveur (HTML en pièce jointe, audio Opus).*

## 8. Modèles (provisionnés depuis le FTP de Warith — Q11)

| Rôle | Desktop (lourd) | Mobile (léger) |
|---|---|---|
| ASR | whisper **large-v3-turbo** quantizé (q5_0) | whisper small/base quantizé |
| LLM | GGUF plus gros (≥ 3B) | GGUF **1–3B** quantizé |
| Diarisation | sherpa segmentation + embeddings « lourd » | variantes « légères » |

*Gestionnaire uniforme : fetch → hash-check → cache local → tier auto selon l'appareil.*

## 9. Héritage Python (référence + rampe de transition)

`os/audio/video/podcast/youtube/capture-helper` + `vocal-helper` (moteur) +
`notes-helper` (Python) : **référence de qualité** (WER/DER) et **option B** (branchés
derrière un port le temps que `nh-core` atteigne la parité). Outils Python : **ruff**,
**pytest**, **os_helper** (logging).

## 10. Outillage par langage (voir `CODING.md`)

| Langage | Formateur | Linter | Doc | Tests |
|---|---|---|---|---|
| Rust | rustfmt | clippy (`-D warnings`) | rustdoc `///` | cargo test + doctests |
| Python | ruff (format) | ruff | NumPy docstrings | pytest |
| TS/JS | Prettier | ESLint | TSDoc | Vitest |
| Swift | swift-format | SwiftLint | DocC `///` | XCTest |
| Kotlin | ktlint | detekt | KDoc | JUnit |
| Shell | shfmt | shellcheck | header | bats (option) |

## 11. CI / build

- **GitHub Actions** : fmt + linter + tests **bloquants** par langage ; **audit egress** ;
  **auditeur dataviz** (front-figures) ; **éval IA** gatée (WER/DER/fidélité/Plutchik).
- Build natif : **cmake** + **clang** (whisper.cpp/ggml, sherpa-onnx, llama.cpp).
- Matrice OS pour le cœur ; jobs mobiles (Xcode / Android SDK) à M6.

## 12. Stockage — responsabilités séparées  *(doctrine — ADR 0001)*

| Rôle | Choix | Possède |
|---|---|---|
| État durable mutable | **SQLite** | vault d'identité (locuteurs↔personnes), sessions, réglages, **méta du cache modèles**, migrations, index |
| Calcul analytique | **Polars** | temps de parole/locuteur, agrégation **émotions Plutchik**, préparation des tables de figures, éval |
| Artefacts tabulaires | **Parquet / Arrow** | gros datasets, **corpus golden** (parité), résultats intermédiaires, interchange cross-langage |

Règle : un DataFrame Polars **n'est jamais** la source de vérité ; l'état transactionnel = SQLite.

## 13. Évaluation IA  *(doctrine §14 / CODING.md règle 15)*

Côté **Python** (l'éval reste en Python), gatée en CI, seuils versionnés :

| Cible | Outil | Métriques |
|---|---|---|
| **Synthèse LLM** (résumé/décisions/actions) | **DeepEval** (juge = LLM **local** Ollama) | fidélité (faithfulness), hallucination, pertinence |
| **Émotions Plutchik** | **DeepEval** / rubrique | accord vs référence |
| Robustesse LLM/ML | **Giskard** | robustesse, biais, cas limites |
| **ASR** | WER | vs corpus golden |
| **Diarisation** | DER | vs corpus golden |

Datasets golden dans `contracts/` ; harnais dans `contracts/evaluations/` (opt-in, coûts maîtrisés par cache).

## 14. Parité Python→Rust  *(doctrine §3 / phase 3)*

**PyO3 + maturin** exposent `nh-core` à Python ; **shadow-mode** compare Rust vs l'oracle Python `notes_helper` sur les **golden fixtures** (structure, valeurs, tolérances, erreurs, latence, mémoire) avant tout switch. Rollback vers Python toujours possible.
