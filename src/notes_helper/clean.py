"""Clean common Whisper hallucination artifacts from a diarized transcript.

Module summary
--------------
Whisper-family ASR models tend to emit a small, recurring set of hallucinations
on silence, music, or noisy segments: leaked tag prefixes (``Sujets:`` /
``Intervenants:`` / ``R&D:``), subtitle-credit boilerplate (``Sous-titrage
Radio-Canada``, ``amara.org`` …), lone filler tokens (``merci``, ``...``,
stray dashes), and short phrases looped several times in a row. This module
removes those artifacts conservatively — it only strips patterns known to be
noise, so real speech is preserved — and then merges consecutive fragments
spoken by the same speaker across small time gaps.

Ported from ``legacy/clean_transcript.py``. The two public entry points are
:func:`clean_text` (string-level scrubbing) and :func:`clean` (utterance-list
scrubbing + same-speaker merge); their signatures and behaviour are unchanged.

Usage example
-------------
    >>> from notes_helper.clean import clean
    >>> tr = [
    ...     {"t0": 0.0, "t1": 1.0, "speaker": "S0", "text": "Sujets: bonjour"},
    ...     {"t0": 1.1, "t1": 2.0, "speaker": "S0", "text": "ça va bien"},
    ...     {"t0": 2.1, "t1": 3.0, "speaker": "S1", "text": "merci"},
    ... ]
    >>> print([u["text"] for u in clean(tr)])
    ['bonjour ça va bien']
    # expected output: ['bonjour ça va bien']

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import re
from re import Pattern

# Leaked structural/tag prefixes that Whisper sometimes prepends to a turn.
# Anchored at start (^) and applied repeatedly (see clean_text) because several
# prefixes can stack on a single utterance.
PREFIX: Pattern[str] = re.compile(
    r"^\s*(Sujets\s*:|Intervenants\s*:|R&D\s*F?\s*:|R&D\b[\s:]*|-\s*Sous-titrage[^\n]*)", re.I
)

# Subtitle-credit / broadcaster boilerplate that is never real dialogue. Used
# both to reject short utterances outright and to excise the substring from
# longer ones.
JUNK: Pattern[str] = re.compile(
    r"(sous-titrage|radio-canada|amara\.org|crayon d.ontario|\bm\.d\.\b|"
    r"société radio|sous-titres réalisés)",
    re.I,
)

# Lone tokens that carry no content once isolated. Matched against a normalized
# (lowercased, punctuation-stripped) form of the utterance.
FILLER: set[str] = {"merci.", "merci", "...", "- -", "-", "–", "—", "sous-titrage", ""}


def clean_text(t: str) -> str:
    """Scrub leaked prefixes and immediate phrase loops from one text span.

    Parameters
    ----------
    t : str
        Raw utterance text as produced by the ASR + diarization step.

    Returns
    -------
    str
        The trimmed text with any stacked tag prefixes removed and any
        immediately-repeated short phrase collapsed to a single occurrence.

    Examples
    --------
    >>> clean_text("Sujets: Intervenants: bonjour")
    'bonjour'
    >>> clean_text("C'est de vos, hein. C'est de vos, hein. C'est de vos, hein.")
    "C'est de vos, hein."

    Notes
    -----
    The prefix stripping loops until it reaches a fixed point (``prev != t``)
    because a single utterance may carry more than one leaked prefix and each
    ``sub`` only removes the outermost match.
    """
    # Fixed-point loop: keep stripping leading tag prefixes until stable, since
    # prefixes can be chained (e.g. "Sujets: Intervenants: ...").
    prev: str | None = None
    while prev != t:
        prev = t
        t = PREFIX.sub("", t).strip()
    # Collapse immediate word/phrase loops like "C'est De Vos, hein." x6 down to
    # a single occurrence. We require 3-40 chars ending in .!? and >=3 repeats to
    # avoid touching legitimately repeated short interjections.
    t = re.sub(r"(\b[^.!?]{3,40}[.!?])(\s*\1){2,}", r"\1", t)
    return t.strip()


def clean(transcript: list[dict], merge_gap_s: float = 1.2) -> list[dict]:
    """Clean and same-speaker-merge a diarized transcript.

    Parameters
    ----------
    transcript : list of dict
        Utterances, each a mapping with keys ``t0``, ``t1`` (float seconds),
        ``speaker`` (str) and ``text`` (str).
    merge_gap_s : float, optional
        Maximum silent gap, in seconds, across which two consecutive utterances
        from the *same* speaker are merged into one turn. Defaults to ``1.2``.

    Returns
    -------
    list of dict
        A new list of cleaned utterances in the same ``{t0, t1, speaker, text}``
        shape. Pure-hallucination and empty utterances are dropped; adjacent
        same-speaker fragments within ``merge_gap_s`` are concatenated.

    Notes
    -----
    Each utterance passes several conservative rejection gates before it can be
    kept: after :func:`clean_text`, an utterance is dropped if it normalizes to a
    known filler token, or if it is short (<60 chars) *and* matches the junk
    boilerplate pattern. Longer utterances keep their real content with only the
    junk substring excised, so mixed lines (boilerplate + speech) are salvaged.
    """
    out: list[dict] = []
    for u in transcript:
        t = clean_text(u["text"])
        # Normalize for filler comparison: lowercase and strip surrounding
        # punctuation/dashes so "Merci." and "merci" collapse to the same token.
        low = t.lower().strip(" .!?-–—")
        if not t or low in FILLER:
            continue
        # Short + boilerplate => pure hallucination; drop entirely.
        if JUNK.search(t) and len(t) < 60:
            continue
        # Longer line: excise the boilerplate substring but keep the real speech.
        t = JUNK.sub("", t).strip(" -–—")
        # Re-check: excision may have left only filler behind.
        if not t or t.lower().strip(" .!?-") in FILLER:
            continue
        # Merge into the previous turn when it is the same speaker and the gap is
        # small enough — this reassembles fragmented turns into readable blocks.
        if out and out[-1]["speaker"] == u["speaker"] and u["t0"] - out[-1]["t1"] <= merge_gap_s:
            out[-1]["text"] = (out[-1]["text"] + " " + t).strip()
            out[-1]["t1"] = u["t1"]
        else:
            out.append({"t0": u["t0"], "t1": u["t1"], "speaker": u["speaker"], "text": t})
    return out
