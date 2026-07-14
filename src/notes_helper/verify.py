"""
Deterministic grounding checks for a synthesis — "verifiable summaries".

Module summary
--------------
This module operationalises Notes Helper's core product claim — *every claim in the
summary is grounded in the recording* — as a **deterministic, LLM-free** check
that can run in CI in milliseconds. It does not ask a model whether the summary
is faithful; it mechanically verifies that:

- every citation's text actually appears in the transcript (token overlap),
- every timestamp (chapters, citations) falls within the recording duration,
- every speaker referenced by a citation exists in the ``speakers`` table,
- decisions and actions are non-empty.

The semantic, model-judged faithfulness evaluation lives separately in
``tests/eval/`` (DeepEval, using a *local* judge). This module is the cheap,
always-on gate; DeepEval is the deeper, slower one.

Usage example
-------------
>>> transcript = [{"t0": 0.0, "t1": 4.0, "speaker": "S0", "text": "We ship on Friday."}]
>>> syn = {
...     "meta": {"duree": "0:00:04"}, "speakers": {"S0": {"name": "Alice"}},
...     "citations": [{"speaker": "S0", "texte": "We ship on Friday.", "t": 1}],
...     "chapitres": [], "decisions": [], "actions": [],
... }
>>> report = verify_synthesis(transcript, syn)
>>> print(report["ok"], report["grounding_score"])
True 1.0

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import re
from typing import TypedDict

# Minimum word-level Jaccard overlap between a citation and its best-matching
# transcript utterance for the quote to be considered "grounded". 0.5 tolerates
# light paraphrase / punctuation drift while still catching fabricated quotes.
GROUNDING_THRESHOLD: float = 0.5

_WORD = re.compile(r"\w+", re.UNICODE)


class VerificationReport(TypedDict):
    """Structured result of :func:`verify_synthesis`.

    Attributes
    ----------
    ok : bool
        ``True`` when no issue was found.
    grounding_score : float
        Fraction of citations grounded in the transcript, in ``[0, 1]``.
        ``1.0`` when there are no citations.
    issues : list[str]
        Human-readable descriptions of every problem found.
    """

    ok: bool
    grounding_score: float
    issues: list[str]


def _tokens(text: str) -> set[str]:
    """Return the lowercased word-token set of ``text``.

    Parameters
    ----------
    text : str
        Arbitrary text.

    Returns
    -------
    set[str]
        Lowercased alphanumeric tokens.

    Examples
    --------
    >>> sorted(_tokens("On ship, Friday!"))
    ['friday', 'on', 'ship']
    """
    return {m.group(0).lower() for m in _WORD.finditer(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets.

    Parameters
    ----------
    a, b : set[str]
        Token sets to compare.

    Returns
    -------
    float
        ``|a ∩ b| / |a ∪ b|``; ``0.0`` when both are empty.
    """
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _duration_seconds(transcript: list[dict], syn: dict) -> float:
    """Best-effort recording duration in seconds.

    Prefers the last utterance end time; falls back to parsing ``meta.duree``
    (``"H:MM:SS"``). Returns ``0.0`` when nothing is available (range checks are
    then skipped by the caller).

    Parameters
    ----------
    transcript : list[dict]
        Utterances with a ``t1`` end time.
    syn : dict
        Synthesis dict, possibly carrying ``meta.duree``.

    Returns
    -------
    float
        Duration in seconds (``0.0`` if unknown).
    """
    if transcript:
        return float(transcript[-1].get("t1", 0.0))
    duree = str(syn.get("meta", {}).get("duree", "")).strip()
    if re.fullmatch(r"\d+:\d{2}:\d{2}", duree):
        h, m, s = (int(x) for x in duree.split(":"))
        return float(h * 3600 + m * 60 + s)
    return 0.0


def grounding_score(transcript: list[dict], syn: dict) -> float:
    """Fraction of citations whose text is grounded in the transcript.

    A citation is *grounded* when its best word-overlap (Jaccard) against any
    single transcript utterance is at least :data:`GROUNDING_THRESHOLD`.

    Parameters
    ----------
    transcript : list[dict]
        Utterances with a ``text`` field.
    syn : dict
        Synthesis dict with an optional ``citations`` list.

    Returns
    -------
    float
        Grounded-citation ratio in ``[0, 1]``; ``1.0`` when there are no
        citations (nothing to disprove).

    Examples
    --------
    >>> tr = [{"t0": 0, "t1": 2, "speaker": "S0", "text": "budget doubled"}]
    >>> grounding_score(tr, {"citations": [{"texte": "the budget doubled"}]})
    1.0
    """
    citations = syn.get("citations", []) or []
    if not citations:
        return 1.0
    utt_tokens = [_tokens(u.get("text", "")) for u in transcript]
    grounded = 0
    for q in citations:
        q_tokens = _tokens(q.get("texte", ""))
        best = max((_jaccard(q_tokens, ut) for ut in utt_tokens), default=0.0)
        if best >= GROUNDING_THRESHOLD:
            grounded += 1
    return grounded / len(citations)


def verify_synthesis(transcript: list[dict], syn: dict) -> VerificationReport:
    """Run every deterministic grounding check on a synthesis.

    Parameters
    ----------
    transcript : list[dict]
        The diarized transcript (list of ``{t0, t1, speaker, text}``).
    syn : dict
        The synthesis dict (``meta``, ``speakers``, ``citations``,
        ``chapitres``, ``decisions``, ``actions``, ...).

    Returns
    -------
    VerificationReport
        ``ok`` is ``True`` only when ``issues`` is empty.

    Notes
    -----
    This is intentionally strict-but-cheap: it cannot judge whether a *summary
    paragraph* is faithful (that needs a model — see ``tests/eval/``), but it
    catches fabricated quotes, out-of-range timestamps, dangling speaker
    references, and empty decisions/actions with zero model cost.
    """
    issues: list[str] = []
    speakers = syn.get("speakers", {}) or {}
    duration = _duration_seconds(transcript, syn)

    # 1. Citations must be traceable to the transcript and to a known speaker.
    for i, q in enumerate(syn.get("citations", []) or []):
        q_tokens = _tokens(q.get("texte", ""))
        best = max(
            (_jaccard(q_tokens, _tokens(u.get("text", ""))) for u in transcript), default=0.0
        )
        if best < GROUNDING_THRESHOLD:
            issues.append(f"citation[{i}] not grounded in transcript (overlap={best:.2f})")
        spk = q.get("speaker", "")
        if spk and spk not in speakers and spk not in {v.get("name") for v in speakers.values()}:
            issues.append(f"citation[{i}] references unknown speaker {spk!r}")
        if duration and q.get("t") is not None and not (0 <= float(q["t"]) <= duration + 1):
            issues.append(f"citation[{i}] timestamp {q['t']} outside [0, {duration:.0f}]")

    # 2. Chapter timestamps must fall within the recording.
    for i, c in enumerate(syn.get("chapitres", []) or []):
        if duration and c.get("t") is not None and not (0 <= float(c["t"]) <= duration + 1):
            issues.append(f"chapitre[{i}] timestamp {c['t']} outside [0, {duration:.0f}]")

    # 3. Decisions / actions must carry actual content.
    for i, d in enumerate(syn.get("decisions", []) or []):
        if not str(d.get("decision", "")).strip():
            issues.append(f"decision[{i}] is empty")
    for i, a in enumerate(syn.get("actions", []) or []):
        if not str(a.get("action", "")).strip():
            issues.append(f"action[{i}] is empty")

    return VerificationReport(
        ok=not issues,
        grounding_score=grounding_score(transcript, syn),
        issues=issues,
    )
