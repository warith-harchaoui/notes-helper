# notes-helper — Plan d'implémentation (ce que je vais coder)

> Le plan **du code**. Conçu à partir des décisions figées de
> [`TECHNICAL_QUESTIONS.md`](TECHNICAL_QUESTIONS.md), du design de
> [`ARCHITECTURE.md`](ARCHITECTURE.md), de la stack de
> [`TECHNICAL_STACK.md`](TECHNICAL_STACK.md) et des cibles de
> [`TECHNICAL_REQUIREMENTS.txt`](TECHNICAL_REQUIREMENTS.txt). Tout le code respecte
> [`CODING.md`](CODING.md) **dès le premier jet**.
>
> *(Le plan de l'ère Python — phases 0–7, faites — est archivé dans `old/PLAN.md`.)*

---

## 0. Thèse (verrouillée)

| Contrainte | Sens | Garantie |
|---|---|---|
| **Local** | Tout le calcul on-device (capture, VAD, diar, ASR, synthèse) | Aucun appel réseau dans le hot path |
| **Souverain** | *By design*, rien ne sort pendant l'usage | Audit egress en CI ; egress = opt-in explicite vers l'infra **de l'utilisateur** |
| **Gratuit** | 0 € pour l'utilisateur | L'appareil fait le travail, zéro COGS serveur |
| **Ouvert** | Licence permissive, auditable | Apache-2.0 (app) + BSD-3 (libs) |

**Règle non négociable :** LLM de synthèse **local** (llama.cpp / MLX). Un seul appel
cloud casse la thèse. **Préférence produit :** pas de logique « MVP » — chaque jalon vise
le complet ; l'ordre ne sert qu'à prouver la chaîne tôt.

---

## 1. Le virage

L'ère Python (helpers + `notes-helper` Python) est **prouvée end-to-end** et sert de
**référence de qualité** et de **rampe de transition (option B)**. Le nouveau code est le
**cœur commun `nh-core` en Rust** wrappant whisper.cpp + sherpa-onnx + llama.cpp, exposé
aux coquilles par UniFFI / ABI C / flutter_rust_bridge, sous une frontière
**ports-and-adapters**. On ouvre ensuite OS par OS / device par device en n'écrivant que
des **adaptateurs**.

---

## 2. Jalons (chacun livrable, testé, conforme CODING.md)

> Convention : chaque jalon liste **objectif · périmètre code · tests/éval · fait quand**.
> `slow`/`#[ignore]` pour les tests modèles lourds ; suite rapide en secondes.

### M0 — Bootstrap `nh-core` (Rust) — *squelette + domaine, sans moteurs*
- **Objectif :** un workspace Cargo propre, le **modèle de domaine**, les **ports**
  (traits), les erreurs, le logging, la CI verte — la fondation sur laquelle tout se pose.
- **Périmètre code :**
  - Workspace `core/` : crate lib `nh-core` (+ crates de bindings plus tard).
  - `model` : `Session`, `Source`, `SourceKind`, `PcmFrame`, `VoicedSegment`,
    `DiarizedSegment`, `Utterance`, `Speaker`, `Person`, `Report`, `Emotion` (types
    nommés, `#[deny(missing_docs)]`).
  - `ports` : traits `AudioCapture`, `VideoCapture`, `RecordingStore`, `ModelRuntime`,
    `FileExport`, `ShareTarget`, `IdentityVault`.
  - `error` : enums `thiserror` par sous-système.
  - `tracing` câblé ; `justfile`/`xtask` pour fmt+clippy+test.
  - CI : `rustfmt --check` + `clippy -D warnings` + `cargo test` (unit + doctests).
- **Tests/éval :** doctests sur les types clés ; un test de bout en bout **avec des
  adaptateurs factices** (mock) prouvant le câblage `session → ports`.
- **Fait quand :** `cargo test` vert, clippy clean, doctests OK, un `Session` factice
  produit un `Report` vide via des ports mock.

### M1 — Moteurs + pipeline offline — *la chaîne réelle*  *(en cours)*
- **Objectif :** wav réel → transcript diarisé → `Report`, 100 % local.
- **Périmètre code :**
  - Bindings : **whisper.cpp** (`asr`, crate `nh-whisper`), **sherpa-onnx**
    (`diar` : segmentation + embeddings, crate **`nh-sherpa`** ✅ 2026-07-19 via
    `sherpa-rs` 0.6.8 — stub par défaut / réel sous `--features sherpa-onnx`,
    `#![forbid(unsafe_code)]`), **llama.cpp** (`synth`) — features de build gérées (cmake).
  - `pipeline::offline` : whole-buffer diar (qualité max) → ASR batchée → `synth`
    (résumé/thèmes/décisions/actions/chapitres/citations) → `Report`.
  - `models::ModelManager` : fetch depuis le **FTP de Warith**, **hash-check**, cache
    local, **tiers par device** (Q11) ; adaptateur `ModelRuntime` (Metal sur Mac).
- **Tests/éval :** rejouer l'audio réel `output/reunion_long/` ; **éval WER/DER** vs la
  référence Python ; seuils versionnés gatés en CI (`slow`).
- **Fait quand :** parité de sortie avec le pipeline Python sur l'échantillon réel.

### M2 — Rapport + figures + export — *le livrable visible*
- **Objectif :** `Report` → HTML **notes-helper-style** interactif + figures + exports.
- **Périmètre code :**
  - `report::html` : sérialise le `Report` en **HTML auto-suffisant** (templates
    **front-ui**), sections façon *glasspop*.
  - `report::figures` : camemberts temps-de-parole + **roue de Plutchik** en **Vega-Lite**
    (via **front-figures**), palettes CVD-safe (**front-colors**) ; **boucles PNG en dev**.
  - `export` : **PDF** = print WebView ; **DOCX** = `docx-rs` depuis le `Report` ; md2star
    en option desktop « fidélité max ».
- **Tests/éval :** audit egress (zéro URL externe dans le HTML) ; auditeur dataviz
  front-figures **bloquant** ; snapshot du HTML.
- **Fait quand :** un `Report` réel produit HTML + PDF + DOCX identiques, offline,
  egress propre.

### M3 — Coquille macOS (Tauri v2) + Source « OBS » — *première plateforme (Q3)*
- **Objectif :** vraie app macOS pilotant `nh-core`, avec le graphe de sources OBS.
- **Périmètre code :**
  - Shell **Tauri v2** ; UI web **front-ui** ; commandes Tauri ↔ `nh-core`.
  - Adaptateurs macOS : `AudioCapture` **multi-source/multi-device** (N micros + N audios
    système via loopback) façon **OBS** (Q13, en faisant avancer `capture-helper`),
    `RecordingStore` (« local file first », Finder), `FileExport`, `ShareTarget`.
  - *Rampe B :* possibilité de brancher temporairement le Python existant derrière un port
    le temps que `nh-core` atteigne la parité, puis débrancher.
- **Tests/éval :** test d'intégration capture→rapport ; vérif « fichier récupérable ».
- **Fait quand :** un utilisateur importe/enregistre, voit le rapport interactif, exporte —
  sans terminal, offline.

### M4 — Temps réel (online) — *les deux chemins dès le départ (Q5)*
- **Objectif :** transcript qui grandit + résumé roulant, léger délai.
- **Périmètre code :** `pipeline::online` (cadence par segment) ; **diarisation en ligne
  stable** (assignation aux centroïdes enrôlés, point dur assumé) ; rafraîchissement du
  rapport.
- **Tests/éval :** stabilité des étiquettes en ligne mesurée ; latence bornée.
- **Fait quand :** conversation live → légendes nommées + résumé courant, 100 % local.

### M5 — Identité locuteurs soignée — *fil rouge (Q9+Q12)*
- **Objectif :** locuteur = entité centrale, UX de bout en bout.
- **Périmètre code :** `identity` (embedding ↔ `Person`), enroll/confirm/rename/**merge**/
  **split**, historique inter-discussions ; `people` (vCard/CSV, **PDF LinkedIn**, docs,
  **URLs fournies**, **photo** extraite) ; **pack d'identité chiffré** export/import (Q12),
  adaptateur `IdentityVault`.
- **Tests/éval :** reconnaissance inter-réunions ; round-trip du pack chiffré.
- **Fait quand :** 2ᵉ réunion des mêmes personnes → auto-nommées ; pack déplaçable.

### M6 — Mobile iOS puis Android — *avancé au plus tôt (Q2)*
- **Objectif :** le cœur sur mobile, coquille décidée à ce moment (Tauri-mobile ou
  Flutter/natif).
- **Périmètre code :** **UniFFI** (Swift/Kotlin) ; adaptateurs `AudioCapture`
  (AVAudioEngine / AAudio-Oboe, micros seulement — pas d'audio d'autre app),
  `RecordingStore` (Documents+Fichiers+Photos+**iCloud** / MediaStore-SAF), export via
  **WebView print** ; **tiers modèles légers**.
- **Tests/éval :** parité de sortie mobile↔desktop sur un même fichier ; tailles modèles.
- **Fait quand :** l'app transcrit+diarise+synthétise un fichier entièrement on-device.

### M7 — Linux + Windows — *compléter les 6 cibles (Q3)*
- **Périmètre code :** adaptateurs restants (`AudioCapture` PulseAudio/PipeWire · WASAPI ;
  `RecordingStore` ; `ModelRuntime` CPU/CUDA/Vulkan/DirectML) ; même cœur, même UI web.
- **Fait quand :** app fonctionnelle et testée sur Ubuntu/Debian et Windows.

### M8 — Complétude — *Plutchik, partage, personnes, éval*
- **Périmètre code :** **Plutchik complet** (`synth` LLM-texte **+** `emotion` SER-audio,
  global + par locuteur) ; **partage opt-in** vers l'infra utilisateur (équiv. Rust de
  bucket/sftp-helper) ; enrichissement personnes complet ; **couche d'éval IA** (WER, DER,
  fidélité/hallucination, accord Plutchik) — datasets + seuils versionnés **gatés en CI**.
- **Fait quand :** toutes les features au complet, éval verte, egress propre partout.

---

## 3. Invariants tenus à chaque jalon
- **Audit egress en CI** (zéro URL externe dans le hot path / les artefacts).
- **CODING.md** : doc-comments + typage + commentaires ~25–30 % + tests, dès le 1ᵉʳ jet.
- **Nommage de domaine identique** entre Rust/Swift/Kotlin/TS.
- **Aucun endpoint d'egress livré** ; provisioning modèles = seule sortie « app ».

## 4. Prochaine étape immédiate
**M0** — bootstrap du workspace `core/` (`nh-core`) : modèle de domaine + ports + erreurs
+ CI (fmt/clippy/test/doctests), avec des adaptateurs mock prouvant le câblage. Zéro
moteur lourd encore — la fondation propre d'abord.
