# notes-helper — Architecture cible (cross-platform)

> Plan concret dérivé des décisions figées de `TECHNICAL_QUESTIONS.md` (2026-07-18).
> Cibles : **macOS · Ubuntu/Debian · Windows · iOS · Android**. Principe : **cœur
> commun agnostique + adaptateurs par OS/device** (ports-and-adapters). Souverain par
> défaut ; egress uniquement sur décision explicite de l'utilisateur.

---

## 1. Vue d'ensemble en couches

```
┌─────────────────────────────────────────────────────────────────────┐
│  COQUILLES UI (par plateforme)                                        │
│   desktop : Tauri v2 + UI web (skills front-*)   ← rapport HTML       │
│   mobile  : Tauri-mobile OU Flutter/natif (décidé tôt)               │
└───────────────▲───────────────────────────────────────────▲──────────┘
                │ UniFFI (Swift/Kotlin) · ABI C · flutter_rust_bridge   │
┌───────────────┴───────────────────────────────────────────┴──────────┐
│  CŒUR COMMUN — Rust  (nh-core)                                         │
│   Session · SourceGraph(OBS) · Pipeline(offline+online) · Identity ·  │
│   Synthèse · Report model · Export · ModelManager · ShareIntent       │
│                                                                       │
│   Moteurs natifs wrappés :                                            │
│     whisper.cpp (ggml)  ·  sherpa-onnx (ONNX RT)  ·  llama.cpp (GGUF)  │
└───────────────▲───────────────────────────────────────────────────────┘
                │ PORTS (traits Rust)
┌───────────────┴───────────────────────────────────────────────────────┐
│  ADAPTATEURS (par OS/device)                                          │
│   AudioCapture · VideoCapture · RecordingStore · ModelRuntime ·       │
│   FileExport · ShareTarget · IdentityVault                            │
└───────────────────────────────────────────────────────────────────────┘
```

**Règle d'or :** le cœur ne connaît aucun OS. Il parle à des **ports** (traits Rust) ;
chaque plateforme fournit ses **adaptateurs**. « S'ouvrir à un nouvel OS/device » =
écrire ses adaptateurs, pas toucher au cœur.

---

## 2. Le cœur `nh-core` (Rust)

Modules (un crate, ou un workspace de crates) :

| Module | Rôle | S'appuie sur |
|---|---|---|
| `session` | Cycle de vie d'une discussion : sources → capture → traitement → rapport → dossier-par-session | — |
| `source` | **Graphe OBS** (Q13) : N sources typées, online/offline, mixables → un flux PCM (+ vidéo) horodaté | port `AudioCapture`/`VideoCapture` |
| `pipeline` | **Offline** (whole-buffer, qualité max) **et online** (streaming, léger délai) (Q5) : VAD → diarisation → ASR → synthèse roulante | moteurs ci-dessous |
| `asr` | Transcription + timestamps mots + **confiance** (proba des tokens whisper.cpp) | **whisper.cpp** (ggml) |
| `diar` | VAD + diarisation + embeddings locuteur | **sherpa-onnx** (Q4) |
| `synth` | Synthèse LLM locale (résumé/thèmes/décisions/actions/chapitres/citations) + **émotions Plutchik** (LLM-texte, Q7) | **llama.cpp** (GGUF) |
| `emotion` | **SER audio** (Plutchik par le ton, Q7) — global + par locuteur | modèle ONNX via sherpa/ONNX RT |
| `identity` | **Locuteur = entité centrale** : embedding ↔ fiche personne, reconnaissance inter-discussions, enroll/confirm/rename/merge/split | port `IdentityVault` |
| `people` | Fiches (nom, rôle, **photo**, vCard/CSV, PDF LinkedIn, docs, URLs fournies) (Q9) | parse local |
| `report` | **Modèle structuré unique** du rapport → sérialise en HTML (notes-helper-style) + alimente l'export | `front-*` (HTML/Vega) |
| `export` | **DOCX (docx-rs) + PDF (print WebView)** depuis le modèle, **partout** (Q10) | port `FileExport` |
| `models` | **ModelManager** : fetch depuis le FTP de Warith, hash-check, cache, tiers par device (Q11) | port `ModelRuntime` |
| `share` | **ShareIntent** : produit un artefact partageable (fichier auto-suffisant) ; egress vers l'infra **de l'utilisateur** seulement si configuré (Q8) | port `ShareTarget` |

### Chemins de traitement (Q5)
- **Offline** (fichier / lien VOD / import) : whole-buffer → diarisation qualité max →
  ASR batchée → synthèse → rapport complet.
- **Online** (enregistrement live / lien live) : cadence par segment (VAD→diar en
  ligne→ASR→synthèse roulante) → **rapport rafraîchi avec léger délai**. Point dur
  assumé : **stabilité des étiquettes de locuteurs en ligne** (traité sérieusement,
  pas dérivé du batch).

### Confiance de transcription (offline)
Sur le chemin **offline**, chaque `Utterance` porte une **confiance** dans `[0,1]`
(`Utterance.confidence: Option<f32>`) : la moyenne des probabilités des tokens de
contenu renvoyées par whisper.cpp (`whisper_full_get_token_prob`, tokens spéciaux et
horodatages exclus), agrégée par tour via `mean_confidence` (moyenne pondérée par la
durée). Un tour whisper peut se scinder en plusieurs segments : on les replie en un
seul score. C'est un **vrai plus de l'offline** : il alimente (1) le rapport, qui
signale les passages douteux (« ⚠️ N% », seuil `LOW_CONFIDENCE`) au service de la
promesse *vérifiable*, et (2) la **boucle de raffinement du contexte** (quels noms
propres/termes se transcrivent avec assez de confiance pour être crus). Sur le chemin
**online/streaming** — délibérément préservé — la confiance reste `None` (non mesurée)
plutôt que fabriquée ; `#[serde(default)]` garde les anciens transcripts lisibles.

### Entrée « dossier » et `notes.yaml`
Un dossier d'entrée porte l'enregistrement (le plus gros média l'emporte) et, en
option, un `notes.yaml` de vérité terrain (tous champs optionnels) qui affine tout le
compte-rendu : `title`, `date`, `time`, `location`, `language` (forcée, sinon
auto-détectée). Deux champs méritent attention :
- **`speakers` = liste de NOMS**, jamais indexée par identifiant de diarisation
  (S0/S1). La diarisation découvre *combien* de voix (compteur entier, jamais codé en
  dur) ; l'appariement voix↔personne est **déterminé** depuis la conversation
  (`assign_speaker_names` : attribution LLM, repli sur une heuristique de temps de
  parole). L'ordre n'est pas une revendication d'identité.
- **`slides`** = un PDF du dossier servant de diaporama (rastérisé et synchronisé au
  contenu discuté) ; sinon on auto-détecte un PDF **paysage** (un PDF **portrait** est
  un document, pas un diaporama).
- **`context_files`** = documents repliés dans le contexte de synthèse ; un gros
  document est **distillé** sur plusieurs passes LLM hors-ligne (découpe → résumé →
  fusion → récursion, `distill_context`) au lieu d'être tronqué.
- **`additional_glossary`** = mots/noms propres qui **complètent** le contexte (jamais
  ne le remplacent). Le `context.md` du dossier reste lu automatiquement.

La synthèse (`synth`, Ollama local, `gemma3:4b` par défaut) est en map/reduce, avec un
**reduce hiérarchique** (repli par lots puis re-fusion) pour qu'une réunion de plusieurs
heures atteigne le rapport sans troncature ; les prompts sont exhaustifs sur les thèmes,
découpent toute la conversation en chapitres cohérents et attribuent les citations mot
pour mot aux participants nommés.

> **Prévu (pas encore livré) :** une **boucle contexte↔transcription** — le
> contexte/glossaire distillé alimente l'`initial_prompt` de whisper pour améliorer la
> transcription, et la **confiance** d'ASR arbitre quel contexte compte et quels
> passages peu sûrs réparer.

---

## 3. Les ports (traits Rust) et leurs adaptateurs

| Port | macOS | Linux | Windows | iOS | Android |
|---|---|---|---|---|---|
| **AudioCapture** (N micros + N audios-système, Q6) | AVFoundation + loopback (BlackHole) | PulseAudio/PipeWire + monitor | WASAPI (+ loopback) | AVAudioEngine (micro ; **pas** d'audio d'autre app) | AAudio/Oboe (micro ; idem) |
| **VideoCapture** (caméra/écran) | AVFoundation / ScreenCaptureKit | v4l2 / PipeWire | Media Foundation / DXGI | AVCaptureSession | CameraX / MediaProjection |
| **RecordingStore** (« local file first », Q-acquis) | `~/…` visible Finder | dossier utilisateur | dossier utilisateur | Documents exposé Fichiers + Photos + **iCloud** | MediaStore / SAF |
| **ModelRuntime** | Metal | CPU/CUDA/Vulkan | CPU/CUDA/DirectML | CoreML/Metal (+ MLX option) | NNAPI/GPU |
| **FileExport** (PDF via WebView, DOCX via docx-rs) | WKWebView print | Chromium headless/webkit | WebView2 print | WKWebView `pdf()` | WebView `PrintManager` |
| **ShareTarget** (opt-in, infra utilisateur) | bucket/sftp-helper équiv. Rust | idem | idem | pièce jointe / Files | pièce jointe / SAF |
| **IdentityVault** (pack chiffré exportable, Q12) | fichier local chiffré | idem | idem | Keychain + fichier | Keystore + fichier |

Le graphe OBS (Q13) est implémenté dans `source`, qui consomme `AudioCapture`/
`VideoCapture` ; **capture-helper** sert de référence/prototype desktop et sa logique
multi-source est portée dans les adaptateurs Rust.

---

## 4. UI (Q2 — aligné doctrine, ADR 0001)

- **Desktop** : **Tauri 2 + React + TypeScript (strict)** (Vite, pnpm) — backend =
  `nh-core` (Rust). Les skills **front-\*** servent au **prototypage, aux audits**
  (a11y/contraste/UX) et aux **figures** (front-figures Vega-Lite/Good Colors) ; leur
  sortie vanilla-JS est **traduite** dans le système de composants React, ce n'est pas
  la couche de prod. Le **rapport notes-helper-style** reste un HTML auto-suffisant.
- **Mobile (tôt)** : **UI native** — **SwiftUI** (iOS) / **Kotlin+Compose** (Android)
  sur le **même `nh-core`** via **UniFFI**. **Tauri-mobile = exception à ADR.**

## 4bis. Stockage (doctrine, ADR 0001)

- **SQLite** = état durable mutable : `IdentityVault` (locuteurs↔personnes), sessions,
  réglages, **méta du cache modèles**, migrations. Adaptateur derrière le port.
- **Polars** = calcul analytique : temps de parole, agrégation **émotions Plutchik**,
  préparation des tables de figures, éval.
- **Parquet/Arrow** = artefacts tabulaires + **corpus golden** (parité Python↔Rust).

*(Diagrammes de ce doc à migrer d'ASCII vers **Mermaid** — convention doctrine.)*

---

## 5. Sécurité / souveraineté (invariants)

- **Aucun endpoint d'egress livré.** Partage indisponible tant que l'utilisateur n'a
  pas renseigné *son* infra. Audit egress en CI conservé (zéro réseau dans le hot path).
- **Provisioning modèles** depuis le FTP de Warith = seule connexion sortante « app »,
  ponctuelle, assets seulement (jamais de données utilisateur).
- **Fetch d'URL** (site perso d'un locuteur) = geste explicite utilisateur, inbound.
- **Pack d'identité** chiffré, déplacé par l'utilisateur ; pas de sync automatique.

---

## 6. Ordre de chantier (Q3 : macOS d'abord, tout livré, mobile tôt)

1. **`nh-core` v0 (Rust)** — squelette ports + `session`/`source`/`pipeline`, wrap
   whisper.cpp + sherpa-onnx + llama.cpp ; `ModelManager` (FTP Warith). Chemin
   **offline** de bout en bout prouvé en tests.
2. **macOS via Tauri v2** — adaptateurs macOS (AudioCapture multi-source, RecordingStore,
   FileExport) ; UI front-* ; rapport notes-helper-style + Vega ; export PDF/DOCX.
   *Rampe B tolérée : brancher temporairement le Python existant derrière le port le
   temps que `nh-core` atteigne la parité, puis débrancher.*
3. **Online / temps réel** — diarisation en ligne stable, rapport rafraîchi.
4. **Identité soignée** — entité locuteur↔personne, photo, merge/split, pack chiffré.
5. **Mobile (iOS puis Android)** — UniFFI, adaptateurs mobiles, tiers modèles légers,
   export via WebView. *(Avancé au plus tôt — cf. Q2.)*
6. **Linux + Windows** — adaptateurs restants (même cœur, même UI web).
7. **Plutchik complet** (LLM-texte + SER-audio), **partage opt-in** (infra utilisateur),
   **enrichissement personnes** (vCard/CSV/PDF/URL).

> Rappel préférence : **pas de logique MVP** — chaque étape vise le complet, l'ordre
> ne sert qu'à prouver la chaîne le plus tôt possible, pas à réduire le périmètre.
