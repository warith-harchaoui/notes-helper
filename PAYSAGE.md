# Paysage

🇫🇷 Français · [🇬🇧 LANDSCAPE.md](https://github.com/warith-harchaoui/notes-helper/blob/main/LANDSCAPE.md)

Outils voisins et concurrents dans l'espace « enregistrer une conversation →
obtenir un compte rendu diarisé et résumé », comparés à **notes-helper**. La
tâche pour laquelle notes-helper est optimisé est précise : un preneur de notes
**entièrement local, gratuit, open source et souverain**, qui nomme les
intervenants **une seule fois** et s'en souvient, produit un compte rendu
**vérifiable**, et où **rien ne quitte l'appareil**. Les notes vont de ⭐ (1) à
⭐⭐⭐⭐⭐ (5), évaluées sur l'adéquation à *ce* créneau — enregistrement →
notes structurées, diarisées, sourcées, dont vous êtes propriétaire. Un produit
conçu pour un autre usage (un bot de réunion cloud avec intégrations CRM) n'est
pas pénalisé dans l'absolu ; la note reflète seulement l'adéquation ici.

notes-helper est **en cours de développement** — toutes ses fonctionnalités
cibles ne sont pas encore livrées (voir [PLAN.md](PLAN.md)) et il n'est pas sur
PyPI. Sa ligne ci-dessous reflète le comportement visé et conçu du pipeline
complet.

## En un coup d'œil

Lignes = produits (cloud/payant en haut, local/gratuit en dessous). Colonnes =
les critères qui définissent le créneau de notes-helper.

| Comptes rendus | Priorité au local | Ouvert & gratuit | Diarisation | Identité d'intervenant persistante | Synthèse sourcée | Propriétaire des sorties (Markdown/vault) | Multi-surface |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **notes-helper** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| « AI Note Taker » archétype | ⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐ | ⭐ | ⭐⭐⭐⭐⭐ |
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

## Carte de positionnement

Représentation 2D du tableau ci-dessus.

![Carte de positionnement](https://raw.githubusercontent.com/warith-harchaoui/notes-helper/main/assets/paysage.png)

La carte est un résumé en 2D des 7 critères : à lire comme une forme, pas comme un classement. « notes-helper » se situe dans le coin en haut à droite. Les axes se lisent **Horizontal — Surface ↔ Markdown** et **Vertical — Priorit ↔ Synth**.

> Les notes reflètent la configuration typique/par défaut de chaque outil pour
> *cette* tâche à la mi-2026. Les produits cloud n'offrent généralement qu'une
> offre gratuite limitée et exigent un compte, d'où une note basse en **Ouvert &
> gratuit**. Certains outils locaux peuvent greffer un LLM cloud pour les résumés,
> échangeant la garantie de souveraineté contre de la qualité.
>
> La ligne **« AI Note Taker » archétype** représente l'attente grand public
> générique — l'appli cloud de notes vocales typique que les gens désignent par
> « un preneur de notes IA » (freemium, verrouillé derrière un compte,
> transcription + résumé cloud, sans mémoire des intervenants d'une réunion à
> l'autre). C'est la base au-*dessus* de laquelle notes-helper est construit, pas
> un produit unique ; les lignes cloud nommées ci-dessous en sont les instances
> concrètes.

## Les deux familles

**Preneurs de notes cloud** (Otter, Fireflies, Fathom, Granola, tl;dv, Plaud,
Zoom/Teams/Copilot) — soignés, riches en fonctionnalités, intégrés aux agendas et
aux CRM. Mais ils sont **structurellement cloud** : l'audio et les transcriptions
vivent sur leurs serveurs, ils coûtent un abonnement, ils exigent un compte, et
ils *ne peuvent pas* offrir « rien ne quitte votre appareil » sans renoncer à
leur modèle économique. Parfaits pour les équipes commerciales ;
**disqualifiés** pour un travail soumis à la confidentialité.

**Transcripteurs locaux** (Apple Dictaphone, MacWhisper, Aiko, Vibe, Hyprnote,
Meetily, whisper.cpp) — privés et souvent gratuits/open source. Mais la plupart
s'arrêtent à la *transcription* : diarisation faible ou absente, pas de compte
rendu structuré vérifiable, et — surtout — **pas d'identité d'intervenant
persistante d'une réunion à l'autre** et **pas de résumés sourcés**. Plusieurs
sont exclusifs à Mac et aucun n'associe le pipeline complet à une sortie native
Obsidian de type second cerveau.

## En quoi notes-helper est différent

1. **Souverain par architecture, pas par politique.** Le zéro-fuite est une
   propriété vérifiable (open source + aucune autorisation réseau sur iOS + audit
   d'égression en CI), pas une promesse. Seule la famille locale peut le
   revendiquer — et notes-helper le rend *prouvable*.
2. **Nommé une fois, connu pour toujours.** L'identité par empreinte vocale
   persistante d'une conversation à l'autre est, à l'heure où ces lignes sont
   écrites, **absente de tous les outils du tableau** (tous les concurrents ont
   ⭐ sur cette colonne). Elle transforme un tas de transcriptions en mémoire
   inter-réunions. C'est le plus grand différenciateur.
3. **Résumés sourcés.** Chaque décision/action/citation renvoie à la seconde
   audio exacte dont elle provient. Aucune action orpheline ou hallucinée. Les
   outils cloud qui exposent un peu de traçabilité (Fathom, tl;dv, Plaud,
   Hyprnote) obtiennent une note partielle ici ; la plupart n'essaient même pas.
4. **Vous possédez l'artefact.** Un fichier HTML hors ligne autonome *et* un
   graphe Obsidian `People/`+`Meetings/` en Markdown — pas un enregistrement
   cloud propriétaire. Les éditeurs locaux (MacWhisper, Vibe, Hyprnote, Meetily)
   possèdent aussi leurs sorties, d'où leur bonne note sur cette colonne.
5. **Gratuit sans coût de revient.** Le calcul se fait sur l'appareil de
   l'utilisateur, donc la gratuité est soutenable pour toujours — aucune raison de
   jamais trahir la garantie locale.

## Positionnement honnête face aux voisins les plus proches

- **Hyprnote / Meetily / Vibe** sont les plus proches par l'esprit (local, ouvert,
  gratuit) et le méritent — d'où leurs fortes notes en **Priorité au local** et
  **Ouvert & gratuit**. L'avantage de notes-helper sur eux, c'est l'**identité
  d'intervenant persistante**, le **compte rendu sourcé/vérifiable**, le **graphe
  de mémoire natif Obsidian**, et la composabilité avec la suite **AI Helpers**
  plus large (`capture-helper`, `vocal-helper`).
- **MacWhisper / Aiko** sont d'excellents *transcripteurs* locaux, mais pas des
  produits de *compte rendu diarisé*, et pas multi-plateformes vers iOS avec le
  pipeline complet.
- **La transcription intégrée d'Apple** est la base gratuite dont tout le monde
  dispose ; elle est entièrement locale et partout (d'où ses hautes notes en
  **Priorité au local** et **Multi-surface**), mais elle ne fait aucune
  diarisation, aucun nommage d'intervenants, aucun résumé structuré et aucune
  mémoire inter-réunions — ce qui est précisément la raison d'être de
  notes-helper.

## Quand choisir quoi

- **notes-helper** — vous voulez un compte rendu privé, gratuit, diarisé, nommé et
  vérifiable qui ne quitte jamais votre machine, et vous tenez à la mémoire
  inter-réunions + Obsidian.
- **Otter / Fireflies / Fathom** — vous êtes une équipe commerciale/CS qui *veut*
  le cloud, la synchro CRM et un bot dans l'appel, et la confidentialité n'est pas
  une contrainte.
- **Granola** — vous voulez une expérience Mac soignée et acceptez la
  synthèse cloud.
- **MacWhisper / Aiko / Vibe** — vous voulez surtout une transcription locale
  rapide et n'avez pas besoin de diarisation, d'intervenants nommés ou de comptes
  rendus structurés.
- **Hyprnote / Meetily** — vous voulez dès aujourd'hui un preneur de notes de
  réunion local et open source et n'avez pas encore besoin d'identité
  d'intervenant persistante ou de résumés sourcés.
</content>
