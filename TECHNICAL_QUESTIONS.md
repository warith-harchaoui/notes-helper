# notes-helper — Questions techniques (app cross-platform)

> Document de réflexion. But : figer les décisions structurantes de l'app cible
> (macOS + Ubuntu/Debian + Windows, iOS + Android) **avant** d'écrire du code.
> Lis, réfléchis, réponds-moi en bas (une ligne par question suffit).
>
> Rédigé le 2026-07-18 après relecture complète de notre R&D `~/*-helper`.

---

## ✅ Ce qui est DÉJÀ acquis (ne pas rejouer)

Ces points sont validés, ils encadrent tout le reste :

- **Souveraineté = 100 % local.** Tout tourne on-device, sans service payant externe.
  `vocal-helper` utilise **ses propres poids open** (pyannote 3.1 + NeMo Sortformer +
  embeddings TitaNet, bundle auto-hébergé, zéro token). Gladia et l'API pyannote.ai
  payante n'existent que dans `pyannote-helper`/`diarization-helper` → **on ne les
  utilise pas.**
- **Markdown-first.** Le rapport canonique est du Markdown ; `md2docx`/`md2pdf`/`md2pptx`
  (md2star) en dérivent DOCX/PDF/PPTX. Le HTML *glasspop*-style est l'autre rendu. Il faudra lui donner un nom : notes-helper-style ( plus glasspop style )
- **« Local file first, et toujours récupérable par l'utilisateur ».** Toute capture
  s'écrit d'abord comme vrai fichier standard (m4a/mp4), à un emplacement que
  l'utilisateur peut retrouver/ouvrir/déplacer/supprimer. Jamais un blob privé sauf associé au device. Par exemple iCloud pour les iPhone.
- **Figures dataviz = `front-figures`.** Camemberts temps-de-parole + roue de Plutchik
  en **Vega-Lite** (vivant dans le HTML) et **PNG/SVG** (figé pour DOCX/PDF), palettes
  daltonien-safe via `front-colors`, auditeur anti-fautes (pas de pie 3D, etc.). En dev fais des boucles PNG pour voir et corriger les figures jusqu'à satisfaction.
- **La carte R&D est faite.** Chaque étage input→moteur→output→partage a son helper
  (audio/video/podcast/youtube-helper en entrée ; vocal-helper = moteur ; notes-helper
  = identité + synthèse + rapport ; md2star = export ; bucket/sftp-helper = partage).
- **Principe d'archi retenu : ports-and-adapters.** Un cœur commun agnostique + des
  adaptateurs par OS/device (capture audio, runtime modèle, stockage, partage).

---

## 🔴 Questions STRUCTURANTES (bloquantes, à trancher tôt)

### Q1. Cœur commun : Rust natif, ou hybride Python-desktop + natif-mobile ?

**Contexte.** Toute notre R&D est en **Python** → parfaite sur les 3 desktops
(Ollama/whisper.cpp tournent), **inutilisable telle quelle sur iOS/Android** (pas de
Python). Le seul code qui tourne à l'identique sur les 6 cibles on-device est du
**natif compilé (C++/Rust)**. Les 3 briques souveraines existent déjà, portables :
whisper.cpp (ASR), **sherpa-onnx** (diarisation + VAD + embeddings, offline, mobile
inclus), llama.cpp (synthèse LLM, GGUF, OK sur mobile récent).

| Option | Description | Pour | Contre |
|---|---|---|---|
| **A. Cœur Rust unique** | Orchestration en Rust + UniFFI (→ Swift/Kotlin) + ABI C (desktop) + flutter_rust_bridge. Wrappe whisper.cpp/sherpa-onnx/llama.cpp. | Une base pour les 6 cibles ; pattern éprouvé (Signal, 1Password, Firefox) ; −30–50 % maintenance | +15–25 % au départ ; réécrire l'orchestration Python en Rust |
| **B. Hybride** | Desktop garde **tout le Python** (via API FastAPI locale déjà existante) ; mobile = cœur natif séparé | Réutilise la R&D immédiatement ; desktop livrable très vite | **Deux moteurs à maintenir** (Python desktop + natif mobile) qui divergeront |
| **C. Cœur C++** | Idem A mais en C++ | Proche de ggml/onnx | Bindings + sécurité mémoire moins ergonomiques que Rust |

**Ma reco : A** (cœur Rust), en tolérant B **temporairement** comme rampe de lancement
(desktop Python pendant qu'on bâtit le cœur Rust), puis convergence. Le cœur Rust est
le même quel que soit le choix d'UI plus bas → décision safe à prendre en premier.

**Décision attendue :** A, B, ou C ? (et si A : tolère-t-on B en transition ?)

---

### Q2. Coquille UI : quelle techno, et dans quel ordre ?

**Contexte.** L'app est **document-centrée** (un rapport riche, pas un jeu 120 fps).
L'avantage de Flutter (widgets natifs ultra-fluides) pèse peu ici ; l'avantage d'une
**UI web** (réutiliser **tes skills front-\*** + le rapport HTML déjà web) pèse lourd.

| Option | Partage | Fit avec tes assets | Réserve |
|---|---|---|---|
| **Tauri v2** (Rust + UI web, 6 plateformes) | backend Rust + UI web | ⭐ tes skills front-* **sont** l'UI ; s'aligne sur le cœur Rust | mobile jeune (2024+), WebView ≠ 100 % natif |
| **Flutter** (Dart, Impeller) | UI + logique Dart | UI mobile la plus léchée | UI à refaire en Dart ; skills web relégués au rapport |
| **Kotlin Multiplatform + Compose** | logique Kotlin, UI Compose/native | feel natif | orbite JVM ; moteur ggml/onnx via C++ quand même |
| **Natif par OS** | rien | qualité max | coût ×3–4 UIs |

**Ma reco : séquence, pas dilemme.**
1. Poser le **cœur Rust** (Q1) — commun à toutes les UIs.
2. **Desktop d'abord en UI web (Tauri v2 + front-\*)** — risque minimal, réutilisation max.
3. **Mobile ensuite, même cœur** : Tauri-mobile si assez mûr le jour venu, sinon coquille
   Flutter/native. Le cœur étant déjà écrit, **le choix mobile devient réversible**.

**Décision attendue :** valides-tu la séquence *cœur → desktop-web → mobile-différé* ?
Et une préférence de principe Tauri vs Flutter pour le mobile, ou on décide plus tard ?

---

### Q3. Quelle plateforme livrée en PREMIER ?

**Contexte.** On ne peut pas tout faire à la fois. Le premier livrable prouve la chaîne.

**Ma reco : macOS desktop d'abord** (ta machine de dev, la stack Python y tourne déjà
end-to-end, l'app Swift est commencée), puis Linux/Windows (même UI web), puis mobile.

**Décision attendue :** macOS en premier ? ou une autre cible prioritaire (ex. iPhone
parce que c'est là que les gens enregistrent leurs réunions) ?

---

### Q4. Diarisation : un seul moteur partout, ou deux ?

**Contexte.** Desktop peut utiliser pyannote/NeMo (via vocal-helper, top qualité).
Mobile a besoin de **sherpa-onnx** (ONNX Runtime, portable). Soit on garde deux
moteurs (qualité desktop max), soit on **standardise sherpa-onnx partout** (parité de
comportement, un seul code, un peu moins de qualité desktop potentielle).

**Ma reco :** viser **sherpa-onnx partout** pour l'uniformité et la maintenabilité,
en gardant pyannote/NeMo comme option « qualité max » sur desktop si l'écart se
vérifie. À valider par un test comparatif DER sur un de tes audios réels.

**Décision attendue :** parité (sherpa partout) ou qualité (deux moteurs) ?

---

### Q5. Temps réel dès le MVP, ou offline d'abord ?

**Contexte.** vocal-helper a **les deux** chemins (streaming *et* batch). L'offline
(whole-buffer) est **strictement meilleur** en qualité de diarisation ; le temps réel
« léger délai » est plus dur (diarisation en ligne, stabilité des étiquettes).

**Ma reco :** **offline d'abord** (fichier + lien VOD), rapport de qualité max, pour
prouver la boucle complète ; **temps réel en v2**.

**Décision attendue :** MVP = offline seul ? ou temps réel indispensable dès le départ ?

---

## 🟠 Questions PRODUIT (importantes, moins bloquantes)

### Q6. Capture de l'audio *système* (réunions Zoom/Meet) ?

`capture-helper` capture le **micro**, pas le son système. Enregistrer l'autre côté
d'un Zoom exige un **device virtuel** (BlackHole/loopback) sur desktop ; sur mobile,
l'OS **interdit** de capter l'audio d'une autre app.
**Reco :** micro (+ éventuellement fichier/lien) au début ; audio système = option
desktop avancée documentée. **Décision : on supporte l'audio système, ou pas au MVP ?**

### Q7. Émotions de Plutchik : nice-to-have ou différenciateur ?

Méthodes : **LLM sur le transcript** (peu cher, déjà local) vs **modèle SER audio**
(plus lourd, capte le ton). **Reco :** version LLM-sur-transcript comme option, activable ;
SER audio plus tard si ça devient un argument. **Décision : optionnel discret, ou
différenciateur qu'on soigne dès le début (et par quelle méthode) ?**

### Q8. Partage « 1 lien » : quel backend, et auto-hébergé ?

C'est **le seul point d'egress**. `bucket-helper` (S3/MinIO/R2/B2) ou `sftp-helper`.
Souverain **seulement** si auto-hébergé (ton MinIO / ton SFTP) ; AWS/R2 = la donnée
part chez un tiers. Toujours **opt-in explicite**. **Reco :** défaut = **ton
infrastructure** (MinIO ou SFTP perso), cloud managé en option. **Décision : quel
backend par défaut ? as-tu déjà un MinIO/SFTP à toi ?**

### Q9. Enrichissement « personnes » (LinkedIn, coordonnées) : jusqu'où ?

Les formulaires de contexte (personnes, rôles) sont locaux et simples. Aller chercher
**LinkedIn automatiquement** = réseau + ToS (zone grise). **Reco :** saisie manuelle +
import d'un fichier (vCard/CSV) au début ; scraping LinkedIn = hors périmètre initial.
**Décision : périmètre des « personnes » au MVP ?**

### Q10. Export DOCX/PDF sur mobile ?

`md2star` a besoin de **Pandoc (+ LibreOffice pour le PDF)** → **desktop only**. Sur
mobile : soit **partage MD/HTML** + un desktop/service fait le lourd, soit export léger
seulement. **Reco :** mobile = MD + HTML + « 1 lien » ; DOCX/PDF = capacité desktop.
**Décision : OK pour DOCX/PDF desktop-only ?**

### Q11. Modèles embarqués : bundle ou téléchargement, et quelles tailles ?

Whisper (base/small/large-v3-turbo), LLM 1–3B GGUF, modèles de diar sherpa. Sur mobile,
poids d'app vs qualité : **petits modèles + téléchargement à la demande** est l'usage.
**Reco :** desktop = modèles plus gros bundlés/téléchargés au 1er lancement ; mobile =
petits modèles, download opt-in, choix qualité dans les réglages. **Décision : politique
de modèles par device ?**

### Q12. Identité « name once » : synchronisée entre tes appareils ?

Les embeddings de locuteurs vivent on-device (`~/.notes-helper/people.db`). Les
reconnaître **d'un appareil à l'autre** demanderait une **sync** — or la souveraineté
interdit un cloud tiers. **Reco :** pas de sync au début (identité par appareil) ; plus
tard, sync **chiffrée via ton propre bucket** en option. **Décision : identité locale
par appareil au MVP, sync plus tard ?**

---

### Q13. Couche Source « OBS » (ajout utilisateur)

L'entrée n'est pas « un micro » mais un **graphe de sources façon OBS** : N sources
simultanées et **mixables**, chacune `{type: micro | audio-système | caméra | écran |
fichier | flux-URL, device, online|offline}`. **Multi-device et multi-source**
(plusieurs micros **et** plusieurs audios-système **et** caméras en même temps).
`capture-helper` est l'implémentation desktop — sa logique est **retravaillée/avancée**
(multi-source mixing, énumération multi-device) pour ça. Contrainte mobile documentée :
l'OS interdit de capter l'audio d'une *autre* app.

---

## ✅ DÉCISIONS FIGÉES (2026-07-18)

| Q | Décision |
|---|---|
| **Q1** | **A — cœur unique en Rust** (wrappe whisper.cpp + sherpa-onnx + llama.cpp ; UniFFI→Swift/Kotlin, ABI C desktop, flutter_rust_bridge). B (Python desktop) toléré **en transition** puis convergence. |
| **Q2** | Séquence *cœur→desktop-web→mobile* validée **mais mobile le plus tôt possible**. Desktop = **Tauri v2 + skills front-\***. Choix coquille mobile (Tauri-mobile vs Flutter/natif) **avancé**, pas différé. |
| **Q3** | **macOS d'abord**, ordre à ma main, **mais TOUTES les cibles livrées** (6), mobile tôt. |
| **Q4** | **sherpa-onnx partout** (parité) ; pyannote/NeMo = option « qualité max » desktop si un test DER réel le justifie. |
| **Q5** | **Tout : offline ET temps réel dès le départ.** Pas de logique MVP. Point dur assumé : diarisation en ligne stable. |
| **Q6** | **Pas d'intégration Zoom/Meet.** App = enregistreur+processeur agnostique. **Audio système supporté (même plusieurs) + plusieurs micros** → sources du graphe OBS (Q13). |
| **Q7** | **Plutchik = vraie feature**, global **et** par locuteur, **LLM-texte ET SER-audio**, camemberts front-figures (boucles PNG en dev). |
| **Q8** | **Souverain par défaut ; egress = opt-in explicite** vers l'infra de **l'utilisateur final** (jamais dev). L'**export interactif ne requiert PAS de serveur** : HTML auto-suffisant en pièce jointe (audio en **Opus** si embarqué). Bucket/SFTP = cas de bord « vrai lien ». |
| **Q9** | Manuel + **vCard/CSV** + **PDF LinkedIn** + docs + **fetch d'URL fournies par l'utilisateur** (site perso). **Aucun scraping.** **Photo** extraite des sources fournies (vCard/site/PDF). |
| **Q10** | **DOCX/PDF partout depuis le HTML, chemin unifié, sans md2docx/pdf sur mobile.** PDF = **print WebView** ; DOCX = **docx-rs** depuis le modèle structuré. md2star/Pandoc = option desktop « fidélité max ». |
| **Q11** | **Model manager uniforme, tiers par device**, provisionné depuis le **FTP perso de Warith** (assets app, hash-vérifiés, cache local). whisper large-v3-turbo quantizé · LLM GGUF · sherpa léger mobile / lourd desktop. |
| **Q12** | **Sync identité opt-in explicite** entre les appareils **de l'utilisateur**. Malin = **pack d'identité chiffré léger**, exporté/importé par l'utilisateur via son canal ; **pas de cloud tiers** ; défaut = par appareil. |
| **Q13** | **Couche Source façon OBS** : multi-device/multi-source, online|offline, mixable ; capture-helper avancé en conséquence. |

**Préférence permanente actée :** *pas de langage/logique « MVP » — on vise le complet.*

Plan d'architecture concret dérivé de ces décisions → **`ARCHITECTURE.md`**.
Décisions reportées dans **`.private/todo.md`**.
