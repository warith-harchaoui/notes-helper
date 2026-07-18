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
| `asr` | Transcription + timestamps mots | **whisper.cpp** (ggml) |
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

## 4. UI (Q2)

- **Desktop** : **Tauri v2** — backend = `nh-core` (Rust), UI = **web générée par les
  skills `front-*`** (front-ui pour l'app, front-figures pour camemberts/Plutchik,
  front-colors palettes CVD-safe, front-accessibility/ux-laws en garde-fous). Le
  **rapport notes-helper-style** est le même HTML auto-suffisant, ouvrable hors app.
- **Mobile (tôt)** : coquille sur le **même `nh-core`** via UniFFI. Choix Tauri-mobile
  vs Flutter/natif tranché au moment d'attaquer le mobile (réversible : le cœur ne
  bouge pas). Web-UI réutilisée si Tauri-mobile ; sinon UI native + rapport en WebView.

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
