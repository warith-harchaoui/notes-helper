"""Slide-sync: show the *right* PDF slide as the audio plays — by content, not order.

Module summary
--------------
A talk or meeting often has a slide deck (PDF). This module turns that deck into an
interactive companion for the report's audio player: as the cursor moves, the panel shows
the slide that best matches *what is being said at that moment*. Crucially the match is by
**content, not chronology** — so when a meeting jumps back to an earlier slide, we jump
back with it, and a deck skimmed out of order still lines up.

Three steps, all local:

1. **Render** — the PDF becomes one PNG per page (`slides/slide-001.png`, …) via
   ``pdf2image`` / poppler ``pdftoppm``.
2. **Read** — each page's text is recovered (``pypdf`` text layer; a page with almost no
   text — an image-only slide — is OCR'd from its PNG via ``kreuzberg``).
3. **Align** — for every transcript utterance we pick the slide whose text is most similar
   (TF-IDF cosine, computed here with no heavy dependency), smooth the result so filler
   speech does not flicker the panel, and emit a ``time → slide`` timeline.

The output is ``slides/slidesync.json`` — ``{slides: [png…], timeline: [{t0,t1,slide,
score}]}`` — which the HTML report's player consumes on ``timeupdate``. Nothing leaves the
machine.

The alignment is the interesting, testable part and is deliberately independent of the PDF
tooling: :func:`align_slides` takes plain slide texts and a transcript, so it is unit-
tested on synthetic data (including a back-and-forth deck) with no PDF and no models.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter

import os_helper as osh

# A page with fewer than this many characters of embedded text is treated as image-only
# and OCR'd from its rendered PNG so it still contributes words to the alignment.
_MIN_TEXT_CHARS: int = 20

# Below this cosine similarity we do not trust the best slide and carry the previous one
# forward instead of flickering to a weakly-related page on filler speech.
_MIN_SCORE: float = 0.06

# Tokens shorter than this are dropped (mostly noise for matching).
_MIN_TOKEN_LEN: int = 2

# Very common FR/EN words carry no discriminative signal between slides. IDF already
# down-weights terms shared by all slides; this trims the obvious glue words up front.
_STOPWORDS: frozenset[str] = frozenset(
    """
    le la les un une des du de d au aux et ou mais donc or ni car que qui quoi dont ou
    ce cet cette ces son sa ses leur leurs mon ma mes ton ta tes notre nos votre vos
    je tu il elle on nous vous ils elles se me te lui y en est sont etait etaient sera
    a ont avez avons pas ne plus tres pour par sur dans avec sans sous entre vers chez
    the a an and or but so of to in on at for with without from by as is are was were be
    this that these those it its their his her our your my we you they he she not more very
    """.split()
)


# ---------------------------------------------------------------------------------------
# Text similarity — a tiny, dependency-free TF-IDF cosine over the slides.
# ---------------------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-word characters, drop stopwords and 1-char tokens."""
    toks = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    return [t for t in toks if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS]


def _idf(slide_tokens: list[list[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency of each term across the slides."""
    n = len(slide_tokens)
    df: Counter[str] = Counter()
    for toks in slide_tokens:
        for term in set(toks):
            df[term] += 1
    # +1 smoothing so a term on every slide still has a small positive weight.
    return {term: math.log((n + 1) / (freq + 1)) + 1.0 for term, freq in df.items()}


def _vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """L2-normalized TF-IDF vector (sparse dict) for one bag of tokens."""
    tf = Counter(tokens)
    vec = {term: count * idf.get(term, 0.0) for term, count in tf.items()}
    norm = math.sqrt(sum(w * w for w in vec.values()))
    if norm == 0.0:
        return {}
    return {term: w / norm for term, w in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity of two sparse, already-normalized vectors."""
    # Iterate the smaller vector for speed.
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(term, 0.0) for term, w in a.items())


# ---------------------------------------------------------------------------------------
# Alignment — transcript utterances → best slide, smoothed into a timeline.
# ---------------------------------------------------------------------------------------
def align_slides(
    transcript: list[dict],
    slide_texts: list[str],
    *,
    min_score: float = _MIN_SCORE,
) -> list[dict]:
    """Assign each moment of the conversation to the slide it is talking about.

    For every utterance we score all slides by TF-IDF cosine and take the best. Weak
    matches (``< min_score``) inherit the previous slide rather than jumping to a loosely
    related page — this keeps the panel steady during greetings, filler and cross-talk.
    Consecutive utterances on the same slide are merged into one span.

    Because the choice is made from *content*, the result is naturally order-free: a deck
    revisited out of sequence maps back to the earlier slide whenever its material returns.

    Parameters
    ----------
    transcript : list of dict
        Utterances ``{"t0": float, "t1": float, "text": str, ...}`` in time order.
    slide_texts : list of str
        The per-slide text, index 0 = first slide. Empty slides are allowed (they simply
        never win a match).
    min_score : float, optional
        Cosine floor below which the previous slide is carried forward.

    Returns
    -------
    list of dict
        Timeline spans ``{"t0": float, "t1": float, "slide": int, "score": float}`` where
        ``slide`` is the 0-based index of the PNG to show. Empty if there are no slides.
    """
    if not slide_texts:
        return []

    slide_tokens = [_tokenize(t) for t in slide_texts]
    idf = _idf(slide_tokens)
    slide_vecs = [_vector(toks, idf) for toks in slide_tokens]

    spans: list[dict] = []
    prev_slide: int | None = None
    for utt in transcript:
        uvec = _vector(_tokenize(utt.get("text", "")), idf)
        best_slide, best_score = -1, 0.0
        for i, svec in enumerate(slide_vecs):
            score = _cosine(uvec, svec)
            if score > best_score:
                best_slide, best_score = i, score

        if best_score < min_score or best_slide < 0:
            # Not enough signal: stay on the current slide (or the first, at the very start).
            slide = prev_slide if prev_slide is not None else 0
            score = 0.0
        else:
            slide, score = best_slide, best_score
        prev_slide = slide

        t0, t1 = float(utt.get("t0", 0.0)), float(utt.get("t1", 0.0))
        if spans and spans[-1]["slide"] == slide:
            # Extend the current span; keep the strongest score seen for it.
            spans[-1]["t1"] = t1
            spans[-1]["score"] = max(spans[-1]["score"], round(score, 4))
        else:
            spans.append({"t0": t0, "t1": t1, "slide": slide, "score": round(score, 4)})
    return spans


# ---------------------------------------------------------------------------------------
# Asset generation — PDF → PNGs and per-page text.
# ---------------------------------------------------------------------------------------
def render_slides(pdf_path: str, out_dir: str, *, dpi: int = 120) -> list[str]:
    """Render each PDF page to ``out_dir/slide-NNN.png`` and return the paths in order."""
    from pdf2image import convert_from_path

    osh.make_directory(out_dir)
    images = convert_from_path(pdf_path, dpi=dpi)
    paths: list[str] = []
    for i, img in enumerate(images, start=1):
        path = os.path.join(out_dir, f"slide-{i:03d}.png")
        img.save(path, "PNG")
        paths.append(path)
    osh.info(f"  slides: rendered {len(paths)} page(s) at {dpi} dpi")
    return paths


def slide_texts(pdf_path: str, png_paths: list[str] | None = None) -> list[str]:
    """Per-page text: the PDF text layer, OCR'ing image-only pages from their PNG.

    Parameters
    ----------
    pdf_path : str
        The slide deck.
    png_paths : list of str, optional
        Rendered page PNGs (from :func:`render_slides`), used to OCR pages whose embedded
        text is too sparse. If omitted, sparse pages simply stay sparse.

    Returns
    -------
    list of str
        One text string per page, in order.
    """
    import pypdf

    reader = pypdf.PdfReader(pdf_path)
    texts: list[str] = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(text) < _MIN_TEXT_CHARS and png_paths and i < len(png_paths):
            # Image-only slide: recover words by OCR'ing its rendered PNG.
            text = _ocr_png(png_paths[i]) or text
        texts.append(text)
    return texts


def _ocr_png(png_path: str) -> str:
    """OCR one image via kreuzberg (returns empty string if the extra is absent)."""
    try:
        from kreuzberg import extract_file_sync
    except ImportError:  # pragma: no cover - only without the docs extra
        osh.warning("  slides: kreuzberg absent — image-only slides stay text-less")
        return ""
    try:
        return (extract_file_sync(png_path).content or "").strip()
    except Exception as exc:  # pragma: no cover - OCR backend hiccup, non-fatal
        osh.warning(f"  slides: OCR failed on {os.path.basename(png_path)}: {exc}")
        return ""


def build_slide_sync(
    pdf_path: str,
    transcript: list[dict],
    out_dir: str,
    *,
    dpi: int = 120,
) -> dict:
    """Render the deck, align it to the transcript, and write ``slidesync.json``.

    Parameters
    ----------
    pdf_path : str
        The presentation PDF associated with the conversation.
    transcript : list of dict
        The diarized transcript (utterances with ``t0``/``t1``/``text``).
    out_dir : str
        Where the report lives; slides land in ``<out_dir>/slides/``.
    dpi : int, optional
        Render resolution for the page PNGs.

    Returns
    -------
    dict
        ``{"slides": [relative png paths], "timeline": [...]}`` — also written to
        ``<out_dir>/slides/slidesync.json`` for the player to fetch.
    """
    slides_dir = os.path.join(out_dir, "slides")
    png_paths = render_slides(pdf_path, slides_dir, dpi=dpi)
    texts = slide_texts(pdf_path, png_paths)
    timeline = align_slides(transcript, texts)

    payload = {
        # Paths relative to the report root so the artifact stays portable/self-contained.
        "slides": [os.path.relpath(p, out_dir) for p in png_paths],
        "timeline": timeline,
    }
    out_json = os.path.join(slides_dir, "slidesync.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    osh.info(
        f"  slides: {len(png_paths)} page(s), {len(timeline)} timeline span(s) "
        f"→ {os.path.relpath(out_json, out_dir)}"
    )
    return payload
