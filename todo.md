# notes-helper — TODO / reprise (session 2026-07-21)

> Point de reprise après redémarrage machine. On s'est arrêtés à cause d'une
> **contention Ollama** (voir §0) : trop de problèmes inexpliqués → reboot pour
> repartir sur des démons Ollama propres.

---

## 0. LE BLOCAGE (à comprendre avant de reprendre)

**Symptôme** : la synthèse (`notes-helper synth`) rendait des fichiers vides —
overview = « (Synthèse locale indisponible — Ollama non joignable…) », 0 points /
décisions / actions, quelques chapitres heuristiques.

**Causes trouvées** :
1. **Consommateur Ollama externe** = ton script **`python /Volumes/orange-dev/outcome_experiment.py`**
   (tournait ~17 min, détaché PPID 1) qui chargeait **qwen3:8b (11 Go)** en même temps
   que mes synthèses. Avec mes modèles → **3 modèles ~21 Go** en mémoire → contention
   GPU/mémoire → mes appels Ollama échouent → fallback. **C'est la cause la plus
   probable de toutes les synthèses vides** (même mes runs 4b « propres » : ton
   expérience tournait déjà en fond).
2. **Bug réel du `reduce`** (indépendant, à corriger) : voir §1.

**À faire au redémarrage, AVANT tout** :
- `ollama ps` → doit être **vide**. `lsof -nP -i :11434` → **aucun** client (surtout
  pas `outcome_experiment.py`). Un seul consommateur Ollama à la fois.
- Vérifier qu'aucun IDE (Antigravity) ni script n'utilise Ollama en fond.

---

## 1. CORRIGER LE BUG `reduce` (code) — priorité

Fichier : `src/notes_helper/synth.py`, fonction `synthesize()` (~lignes 507-557).

- Le `reduce` reçoit `json.dumps(partials)[:24000]` — sur une longue réunion (Le-Bench =
  39 chunks) les notes sont **tronquées à 24k** et le petit modèle rend un reduce
  vide → bascule heuristique. **Le `map` marche** (JSON valide vérifié à la main).
- **Fix** : `reduce` **hiérarchique / par lots** (fold les partials par paquets qui
  tiennent dans le budget → notes intermédiaires → reduce final récursif), pas de
  troncature brutale. (Le map-reduce lui-même est un bon design — **le garder**.)
- **Corriger le message trompeur** : distinguer « Ollama injoignable » (vraie erreur
  réseau) de « reduce n'a rien produit » (map OK mais reduce vide). Aujourd'hui les deux
  donnent le même texte « Ollama non joignable », ce qui a envoyé sur une fausse piste.
- Après fix : run **1 synth propre AVEC logs** (ne pas rediriger vers /dev/null) sur le
  subset pour confirmer que c'était bien la contention + le reduce.

---

## 2. COMPARAISON LLM (puis JE choisis le défaut du projet)

But (demande utilisateur) : comparer, décider **le LLM définitif du projet**, le fixer en
défaut, puis refaire les 2 outputs de bout en bout avec lui.

- **Sérialisé, UN modèle à la fois, jamais `ollama stop`/kill/swap pendant un run.**
- Modèles (**-mlx pour Mac**) : `gemma3:4b`, `gemma4:e2b-mlx`, `gemma4:e4b-mlx`, `qwen3:8b`.
  **12B abandonné** (trop gros, décision utilisateur). Plafond = 8B.
- Entrée fixe = même subset pour tous : `output/_model-compare/transcript.json`
  (150 tours ≈ 38 min ; déjà créé, + `speaker_mapping.json`).
- Runner : `scratchpad/model_compare.sh` (dans le dossier de session claude) — **À
  CORRIGER** : il redirige la sortie synth vers `/dev/null` → on perd les logs de diag.
  L'enlever. (Le chemin exact du scratchpad change par session ; le recréer si besoin.)
- Commande type par modèle :
  `notes-helper synth output/_model-compare --context-file input/Le-Bench-georges-warith-2026-07-18/context.md --lang fr --model <MODELE>`
  puis préserver `synthese.json` → `synthese.<modele>.json`, et chronométrer.
- Comparer : **vitesse** (s, s/chunk depuis `~/.ollama/logs/server.log`) + **qualité**
  (nb points/décisions/actions/thèmes/citations, finesse de l'overview, JSON propre,
  orthographe des noms propres du contexte). ⚠️ `qwen3:8b` = modèle « raisonneur » →
  peut émettre des `<think>` (avec `format:json` c'est normalement contraint ; à vérifier).
- **Décision → défaut** : `src/notes_helper/config.py` `OLLAMA_MODEL` et
  `src/notes_helper/cli.py` `--model` (déjà passés de `qwen2.5:32b` → `gemma3:4b`).
  Mettre le gagnant. + noter en mémoire.

---

## 3. REFAIRE LES 2 OUTPUTS DE BOUT EN BOUT (avec le LLM choisi)

### 3a. Le-Bench (`input/Le-Bench-georges-warith-2026-07-18/`)
- ASR **déjà fait** : `output/Le-Bench-.../transcript.json` (1180 tours, 4,2 h, 2 loc.).
  Locuteurs : **S0 = Warith Harchaoui** (auto-reconnu), **S1 = Georges Oppenheim**
  (enrôlé cette session → reconnu à l'avenir). `speaker_mapping.json` déjà édité.
- Reste : `notes-helper synth output/Le-Bench-... --context-file input/Le-Bench-.../context.md --lang fr --title "Le Bench — Georges Oppenheim & Warith Harchaoui" --model <CHOISI>`
  puis `notes-helper report output/Le-Bench-... --format html,md`.
- Note : le `context.md` distillé (manuscrit du livre « Que gouverner de l'intelligence
  artificielle ») est LE bon contexte sous le plafond de 8000 car. Le manuscrit brut
  complet est extractible via Kreuzberg (`ai_dangers-manuscrit-fr.pdf`) mais inutile
  tronqué. Boucle de raffinement du contexte = amélioration future (non bloquante).
- Au report : le **WAV 488 Mo** (`audio_16k.wav`, legacy de l'ancien run) sera encodé en
  Opus/MP3 puis **supprimé** automatiquement. (Sinon `rm` manuel.)

### 3b. sev7n (`input/sev7n-2026-07-21-documentaire-text2sql-intention/`)
- **Rien fait encore.** Pipeline complet :
  `notes-helper run input/sev7n-.../audio.m4a --out output/sev7n-... --lang fr` (ASR ~17 min)
  → `notes-helper synth output/sev7n-... --context-file input/sev7n-.../context.md --lang fr --title "Des outils pour sev7n" --model <CHOISI>`
  → `notes-helper report output/sev7n-... --format html,md`.
- **Slide-sync** : la présentation `sev7n-...-intention.pdf` → PNG + alignement par
  contenu → panneau slide synchronisé au player (voir §Code `slides.py`). Câbler l'option
  `--slides <pdf>` dans le CLI/report + passer `slide_sync` à `render_html` (le moteur +
  le rendu HTML sont faits et testés ; reste le fil CLI → render).
- **PDF téléchargeable** : porter la présentation PDF dans `output/sev7n-...` comme
  pièce jointe téléchargeable + lien dans le rapport (feature « attachments », tâche #4).
- `context.md` sev7n déjà distillé (orateur Warith → équipe sev7n, 3 parties, 6 projets
  + liens GitHub, glossaire).

---

## 4. CODE FAIT CETTE SESSION (dans le working tree, NON commité)

> `git status` : ~18 fichiers modifiés + 6 nouveaux. Rien n'est commité — à relire/committer.

**Python (`src/notes_helper/`)**
- `webaudio.py` (NOUVEAU) : encodage web voix — high-pass + loudnorm EBU R128 → **Opus
  ~32k + MP3 ~72k**, mono. Le player ne sert **jamais** de WAV.
- `context.py` (NOUVEAU) : ingestion des documents associés d'un slug (Markdown lu direct,
  **PDF via Kreuzberg**), agrégation → contexte. `--context-dir`. Tests `tests/test_context.py` (5 verts).
- `slides.py` (NOUVEAU) : slide-sync — PDF→PNG (pdf2image), texte par page (pypdf +
  OCR Kreuzberg), **alignement par contenu** (TF-IDF cosine, **sans ordre chronologique**,
  gère 0→14→7→2→25). Tests `tests/test_slides.py` (6 verts).
- `i18n.py` (RÉÉCRIT) + `locales/i18n.yaml` (NOUVEAU, racine) + copie packagée synchro :
  **catalogue unifié GUI + prompts, fr/en/es**. `gui(id,lang)`, `prompt(id,lang)`,
  `detect_lang` (langdetect), `resolve_language` (policy : **texte majoritaire** sinon
  **audio majoritaire**). `outputs/html.py` câblé → langue auto-détectée du transcript,
  labels + `<html lang>` (testé FR→français, ES→espagnol).
- `pipeline.py` : **plus de WAV disque** — `decode_16k_mono` (ffmpeg → f32le → numpy en
  RAM) ; web audio encodé depuis l'original ; garde-fou `makedirs` avant écritures ;
  ⚠️ **encore whole-buffer en RAM** (voir tâche #7 bounded-memory).
- `outputs/__init__.py` : `render()` encode l'audio web (Opus/MP3) et **supprime le WAV** ;
  réutilise le compressé si présent.
- `config.py` + `cli.py` : défaut LLM `qwen2.5:32b` → **`gemma3:4b`** (à finaliser §2).
- `pyproject.toml` : `kreuzberg` ajouté à l'extra `[docs]`.

**Rust (`core/`)** — port de prod (branche `app`)
- `nh-core/src/model.rs` : `Utterance.confidence` / `Word.confidence` = `Option<f32>` +
  `mean_confidence()`. `nh-whisper` : confiance = moyenne des probas tokens (offline).
  Propagé nh-run + pipelines ; `report.md` flag `⚠️ N%` bas. **cargo check nh-core/nh-synth
  verts** ; **build featuré whisper-cpp PAS encore lancé** (lourd) — à faire (tâche #3).
- `nh-io/src/ffmpeg.rs` : **`load_window(start,dur)`** (décode 1 fenêtre, `-ss/-t`) +
  **`stream_blocks`** (streaming par blocs, mémoire bornée). 2 tests verts. Base de la
  tâche #7 (offline O(fenêtre) + plus vite que le temps réel).

---

## 5. TÂCHES OUVERTES (tracker)

- #3 Rust confidence : build featuré `cargo check -p nh-run -p nh-whisper --features whisper-cpp` (lourd) à valider quand CPU libre.
- #4 Attachments (PDF téléchargeable) dans l'output web — driver = présentation sev7n.
- #6 Slide-sync : câbler `--slides` CLI + passer `slide_sync` à `render()` (moteur + HTML faits).
- #7 Offline mémoire-bornée : câbler `stream_blocks`/`load_window` dans le pipeline
  (VAD/embeddings sur blocs → garder les features pas les samples → cluster global → ASR
  par fenêtre, parallélisé). Rust nh-core + miroir vocal-helper.
- #9 (ce document) : comparaison LLM → choix → 2 outputs.
- Archi consignée en mémoire : 3 tiers (online / offline standard / offline deep-refine),
  capture multi-source (micro + loopback visio), scénarios (podcast / visio / resto),
  « ne pas débruiter avant l'ASR » (recherche), DSP réutilisable → **audio-helper**
  (consommé par vocal-/podcast-/capture-helper), notes-helper = app Rust.

---

## 6. RÈGLES OLLAMA (apprises à la dure)
- **Un seul job Ollama à la fois.** Jamais `ollama stop`/kill/swap pendant qu'un
  consommateur tourne. Surveiller les consommateurs **externes** (IDE, scripts).
- Modèles : **gemma3:4b par défaut, 8B plafond, 12B abandonné** (trop gros pour la machine).
- Santé Ollama : lecture passive de `~/.ollama/logs/server.log`, pas de génération de test
  pendant un run.
