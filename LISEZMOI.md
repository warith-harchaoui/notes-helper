# Notes Helper

[🇫🇷](https://github.com/warith-harchaoui/notes-helper/blob/main/LISEZMOI.md) · [🇬🇧](https://github.com/warith-harchaoui/notes-helper/blob/main/README.md)

[![CI](https://github.com/warith-harchaoui/notes-helper/actions/workflows/ci.yml/badge.svg)](https://github.com/warith-harchaoui/notes-helper/actions/workflows/ci.yml) [![Licence : Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/warith-harchaoui/notes-helper/blob/main/LICENSE) [![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue.svg)](#) [![Local d'abord](https://img.shields.io/badge/vie%20privée-local%20d'abord-2f6f5e.svg)](#la-promesse)

`Notes Helper` fait partie d'une collection de bibliothèques nommée **AI Helpers**, développée pour construire de l'intelligence artificielle.

**Un enregistreur 100 % local, gratuit et open source qui transforme n'importe quelle conversation en compte-rendu diarisé (voix séparées), nommé et vérifiable — et rien ne quitte votre appareil, sauf si vous le décidez.** Enregistrez ou importez un audio : Notes Helper sépare les voix, les transcrit, nomme chaque interlocuteur **une seule fois et pour toujours, sur votre appareil** et rédige une synthèse structurée et sourcée — entièrement sur votre machine.

Par [Warith HARCHAOUI](https://linkedin.com/in/warith-harchaoui)

## Documentation

[💻 Documentation](https://harchaoui.org/warith/ai-helpers/docs/notes-helper-doc/)

[📋 Exemples](https://github.com/warith-harchaoui/notes-helper/blob/main/EXAMPLES.md)

## La promesse

> **Rien ne quitte votre appareil pendant l'usage.** Les seuls évènements réseau
> sont le téléchargement unique des modèles au premier lancement et toute
> synchronisation que *vous* activez explicitement.

Ce n'est pas une *politique* de confidentialité (« faites-nous confiance »). C'est
une *propriété* d'architecture : aucun réseau dans le chemin critique, aucune
analytique, code ouvert donc vérifiable — un moniteur réseau affiche zéro sortie.
La garantie est même vérifiée en CI (`notes-helper audit`).

## État — v0.1.0

Ce qui fonctionne aujourd'hui :

- **`notes-helper run`** — n'importe quel fichier audio → transcription diarisée (Silero VAD → TitaNet → whisper.cpp), 100 % local.
- **`notes-helper synth`** — transcription → synthèse structurée via un LLM **local** Ollama (map-reduce, ancrée aux timestamps).
- **`notes-helper report`** — un compte-rendu, trois rendus : **HTML** interactif · **Markdown** (n'importe quelle cible — Obsidian n'est qu'*une* option) · **DOCX/PDF/PPTX** via [`md2star`](https://github.com/warith-harchaoui/md2star).
- **`notes-helper enroll` / `people`** — identité par empreinte vocale dans un stockage SQLite local : *nommer une fois, reconnu pour toujours, sur votre appareil*.
- **`notes-helper audit`** — garde-fou CI qui échoue si un artefact généré appelle l'extérieur.

## Installation

**Prérequis** — **Python 3.10–3.13**, **git**, **ffmpeg** et (pour la synthèse) un **[Ollama](https://ollama.com)** local, multiplateforme :

- 🍎 **macOS** ([Homebrew](https://brew.sh)) : `brew install python git ffmpeg ollama`
  (installez `brew` grâce à [brew.sh](https://brew.sh/))
- 🐧 **Ubuntu/Debian** : `sudo apt update && sudo apt install -y python3 python3-pip git ffmpeg` — puis Ollama via `curl -fsSL https://ollama.com/install.sh | sh`
- 🪟 **Windows** (PowerShell) : `winget install Python.Python.3.12 Git.Git Gyan.FFmpeg Ollama.Ollama`

### Depuis les sources

Installez depuis GitHub, épinglé au tag de version :

```bash
pip install "git+https://github.com/warith-harchaoui/notes-helper.git@v0.5.0-alpha.4"
```

Extras optionnels (au choix) :

```bash
pip install "notes-helper[process] @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.5.0-alpha.4"   # vocal-helper : VAD/diarisation/ASR
pip install "notes-helper[capture] @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.5.0-alpha.4"   # capture-helper : capture micro/écran
pip install "notes-helper[docs]    @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.5.0-alpha.4"   # md2star : export DOCX/PDF/PPTX
pip install "notes-helper[all]     @ git+https://github.com/warith-harchaoui/notes-helper.git@v0.5.0-alpha.4"   # tout
```

Il faut toujours `ffmpeg` dans le PATH (décodage/rééchantillonnage audio) et `ollama serve` en marche (synthèse locale) :

- 🍎 macOS : `brew install ffmpeg` (installez `brew` grâce à [brew.sh](https://brew.sh/))
- 🐧 Ubuntu : `sudo apt install ffmpeg`
- 🪟 Windows : `winget install Gyan.FFmpeg`

## Prise en main

```bash
# 1) audio -> transcription diarisée (+ identité des voix)   [déposez vos fichiers dans input/]
notes-helper run input/reunion.m4a --out output/reunion

# 2) transcription -> synthèse locale (nécessite `ollama serve`)
notes-helper synth output/reunion

# 3) rendu du compte-rendu dans les formats voulus
notes-helper report output/reunion --format html,md,docx,pdf

# nommer une voix une seule fois — chaque réunion suivante l'étiquette, sur votre appareil
notes-helper enroll output/reunion/diar_checkpoint.npz --cluster S0 --name "Warith Harchaoui"

# prouver la souveraineté : échoue si un artefact référence une URL externe
notes-helper audit output/reunion
```

En bibliothèque :

```python
from notes_helper.pipeline import run
from notes_helper.outputs import render

paths = run("input/reunion.m4a", "output/reunion")
print(paths["transcript"])          # output/reunion/transcript.json
render("output/reunion", ["html", "md"])
```

Pour le catalogue complet de recettes, voir [📋 EXAMPLES.md](https://github.com/warith-harchaoui/notes-helper/blob/main/EXAMPLES.md).

## Vérité terrain : `notes.yaml`

Déposez un `notes.yaml` à côté de l'enregistrement dans un dossier d'entrée et tout
le compte-rendu s'affine. **Tous les champs sont optionnels** — ne renseignez que
ce que vous connaissez :

```yaml
title: Point produit — feuille de route T3   # titre en tête de rapport
date: 2026-07-23                             # 📅 date ISO (ou toute chaîne)
time: "14:00"                                # 🕘 heure de début
location: Paris, salle B2                    # 📍 texte libre
language: fr                                 # force la langue (omettre = auto-détection)
speakers:                                    # une LISTE DE NOMS — pas indexée par S0/S1
  - Warith Harchaoui
  - Alexandre Larmagnac
slides: presentation.pdf                     # PDF du dossier à utiliser comme diaporama
context_files:                               # documents intégrés au contexte de synthèse
  - brief.md
  - manuscrit.pdf
additional_glossary:                         # mots/noms propres qui COMPLÈTENT le contexte
  - TitaNet
  - Plutchik
```

À propos des deux champs subtils :

- **`speakers` est une liste de noms, pas une table d'identifiants.** La diarisation
  découvre *combien* de voix il y a ; le pipeline **détermine ensuite quelle voix
  enregistrée est quelle personne** à partir de la conversation elle-même
  (attribution par LLM avec repli sur une heuristique de temps de parole). L'ordre
  ne constitue aucune revendication d'identité.
- **`slides`** nomme un PDF du dossier à utiliser comme diaporama (rastérisé et
  synchronisé sur le moment où chaque diapo est discutée). Laissez ce champ vide
  pour auto-détecter un PDF *paysage* ; un PDF *portrait* est traité comme un
  document, pas un diaporama (aucune diapo).
- **`context_files`** sont intégrés au contexte de synthèse (noms propres,
  définitions, cadrage). Un document volumineux est **distillé sur plusieurs passes
  LLM hors-ligne** (découpe → résumé → fusion → récursion) plutôt que tronqué, afin
  que l'intégralité du document informe le compte-rendu.
- **`additional_glossary`** *complète* (ne remplace jamais) le contexte. Le
  `context.md` du dossier reste lu automatiquement ; `context_files` et
  `additional_glossary` viennent l'enrichir.

## Architecture

Trois couches sur une seule couture (trames 16 kHz mono float32) :

| Couche | Composant |
|---|---|
| **ENTRÉE** | [`capture-helper`](https://github.com/warith-harchaoui/capture-helper) |
| **TRAITEMENT** | [`vocal-helper`](https://github.com/warith-harchaoui/vocal-helper) |
| **SORTIE** | `build_page` · [`md2star`](https://github.com/warith-harchaoui/md2star) |

Voir [📄 PRODUCT.md](https://github.com/warith-harchaoui/notes-helper/blob/main/PRODUCT.md), [🗺️ PLAN.md](https://github.com/warith-harchaoui/notes-helper/blob/main/PLAN.md) et [🔭 LANDSCAPE.md](https://github.com/warith-harchaoui/notes-helper/blob/main/LANDSCAPE.md).

## Tests

```bash
pip install -e ".[dev]"
pytest -q                      # tests unitaires rapides
pytest -q --cov=notes_helper         # avec couverture
pytest -q -m slow              # intégration (nécessite modèles / Ollama)
deepeval test run tests/eval/  # éval IA : seuils de fidélité de la synthèse
python scripts/audit_egress.py output/   # garde-fou souveraineté
```

## Auteur

- [Warith HARCHAOUI](https://linkedin.com/in/warith-harchaoui).

## Remerciements

Remerciements chaleureux à [Mohamed Chelali](https://mchelali.github.io) et [Bachir Zerroug](https://www.linkedin.com/in/bachirzerroug) et [Alexandre Larmagnac](https://www.linkedin.com/in/alexandre-larmagnac-85b4619b/) pour nos échanges fructueux.

## Licence

`notes-helper` est distribué sous licence **Apache-2.0**. Voir [LICENSE](LICENSE).
