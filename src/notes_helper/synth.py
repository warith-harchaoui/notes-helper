"""Local LLM synthesis: transcript.json to a structured report (synthese).

Module summary
--------------
This module turns a diarized transcript into a structured meeting report
(summary, key points, decisions, actions, chapters, themes, quotes). It runs
entirely against a **local** Ollama server (localhost). A single cloud call
would break the sovereignty thesis of the project, so there is deliberately no
remote fallback: if Ollama is unreachable we emit a minimal heuristic synthese
and say so explicitly.

Long meetings are handled map-reduce style: the transcript is split into
character-bounded chunks, each chunk is summarised into partial notes (the
"map"), and the partials are folded into one final structured report (the
"reduce"). Every extracted decision, action and quote carries the timestamp(s)
it came from, so each claim in the report can be traced back to the exact second
of audio.

Usage example
-------------
>>> from notes_helper import synth
>>> transcript = [{"t0": 0, "t1": 5, "speaker": "S0", "text": "on valide le budget"}]
>>> speakers = {"S0": {"name": "Alice", "role": ""}}
>>> report = synth.synthesize(transcript, speakers, title="Reunion")   # doctest: +SKIP
>>> print(sorted(report))                                             # doctest: +SKIP
['actions', 'chapitres', 'citations', 'decisions', 'meta', 'points_cles', 'resume', 'speakers', 'themes']
# expected output: ['actions', 'chapitres', 'citations', 'decisions', 'meta', 'points_cles', 'resume', 'speakers', 'themes']

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.request
from collections.abc import Iterator

import os_helper as osh

from . import i18n as _i18n
from .config import OLLAMA_MODEL, OLLAMA_URL

# Context window (tokens) requested from Ollama on every call. The default (2048) is far
# too small for our inputs — a transcript chunk plus context on the map step, a large batch
# of partial notes on the reduce step — and a small window silently truncates them. 32k
# comfortably fits input + output for gemma3 (128k-capable) without over-reserving VRAM.
_OLLAMA_NUM_CTX: int = 32768


def _hhmmss(s: float | int | None) -> str:
    """Format a duration in seconds as ``H:MM:SS``.

    Parameters
    ----------
    s : float or int or None
        Number of seconds. ``None`` is treated as 0.

    Returns
    -------
    str
        The duration as ``H:MM:SS`` (hours not zero-padded).
    """
    s = int(s or 0)
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _ollama(messages: list[dict], model: str, fmt_json: bool = True, timeout: int = 600) -> str:
    """Call the local Ollama chat endpoint and return the raw content string.

    Parameters
    ----------
    messages : list of dict
        Chat messages in the ``{"role", "content"}`` format Ollama expects.
    model : str
        Ollama model name to run.
    fmt_json : bool, optional
        If ``True`` (default), ask Ollama to constrain output to JSON.
    timeout : int, optional
        Socket timeout in seconds (default 600) — generous because local models
        can be slow on long chunks.

    Returns
    -------
    str
        The assistant message content (still a string; parse with
        :func:`_json_loads_lax`).

    Raises
    ------
    urllib.error.URLError
        If the local Ollama server is unreachable (handled by the caller as the
        signal to fall back to the heuristic synthese).

    Notes
    -----
    Uses the stdlib ``urllib`` on purpose: no third-party HTTP client, and the
    request never leaves localhost.
    """
    # Ollama defaults num_ctx to 2048 tokens — far too small here: a map call carries the
    # transcript chunk plus the (possibly distilled) context, and the reduce call carries a
    # large batch of partial notes, so a small window silently TRUNCATES the input and the
    # model returns garbage that fails to parse (every chunk failing looks like "Ollama
    # unreachable" and drops the whole synthesis to the heuristic). gemma3 handles 128k, so
    # we ask for a generous window that comfortably fits input + output.
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": _OLLAMA_NUM_CTX},
    }
    if fmt_json:
        body["format"] = "json"
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["message"]["content"]


def _json_loads_lax(s: str) -> dict:
    """Parse JSON, tolerating surrounding prose from the model.

    Parameters
    ----------
    s : str
        Candidate JSON string, possibly wrapped in explanatory text.

    Returns
    -------
    dict
        The parsed object, or ``{}`` if nothing parseable is found.

    Notes
    -----
    Local models occasionally wrap JSON in commentary despite the ``format=json``
    hint, so as a fallback we extract the widest ``{...}`` span and parse that.
    Crucially this function **never raises**: on a long meeting the map step runs
    dozens of times, and a single chunk whose output is truncated or malformed
    (so even the extracted span won't parse) must degrade to ``{}`` rather than
    abort the whole synthesis. That guarantee is what the caller relies on.

    Examples
    --------
    >>> _json_loads_lax('{"a": 1}')
    {'a': 1}
    >>> _json_loads_lax('sure! {"a": 1} hope that helps')
    {'a': 1}
    >>> _json_loads_lax('{"a": 1, "b":')   # truncated -> not parseable
    {}
    """
    try:
        return json.loads(s)
    except Exception:
        pass
    # Fallback: some models wrap JSON in prose — grab the widest {...} span.
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


# --- schema normalisation -------------------------------------------------- #
# The synthesis is produced by a local LLM constrained to a JSON schema, but
# small models drift: a field the renderers expect as a string arrives as a
# list, an object arrives as a bare string, a list arrives as a scalar, or a key
# is missing entirely. Rather than defend against every shape in each of the
# three renderers (Markdown / HTML / vault), we coerce once — here at synth time
# AND again when a possibly hand-edited ``synthese.json`` is loaded for
# rendering — into the exact shapes the renderers rely on. ``t`` timestamps are
# left untouched: the renderers' ``_hhmmss`` already tolerates any form.

# The seven LLM-owned report keys, in the canonical output order.
_REPORT_KEYS: tuple[str, ...] = (
    "resume",
    "points_cles",
    "decisions",
    "actions",
    "chapitres",
    "themes",
    "citations",
)


def _as_str(x: object) -> str:
    """Coerce any value into a display string.

    Parameters
    ----------
    x : object
        A value from the raw synthesis — typically a string, but possibly a
        list (joined with spaces), ``None`` (→ empty), or something else.

    Returns
    -------
    str
        A stripped string representation suitable for direct interpolation.

    Examples
    --------
    >>> _as_str(["a", "b"])
    'a b'
    >>> _as_str(None)
    ''
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    # LLM drift: a point/theme item sometimes comes back as an object like
    # ``{"texte": "...", "timestamp": 307}`` instead of a plain string. Extract the
    # human text and drop metadata (timestamps) so the report never shows raw JSON.
    if isinstance(x, dict):
        for key in ("texte", "text", "point", "phrase", "contenu", "titre", "resume"):
            v = x.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # No known key: take the first string value, else recurse into a nested list.
        for v in x.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in x.values():
            if isinstance(v, (list, tuple)):
                nested = _as_str(v)
                if nested:
                    return nested
        return ""
    if isinstance(x, (list, tuple)):
        return " ".join(_as_str(i) for i in x if i is not None).strip()
    return str(x).strip()


def _as_str_list(x: object) -> list[str]:
    """Coerce any value into a list of non-empty display strings.

    Parameters
    ----------
    x : object
        A value expected to be a list of strings but possibly a bare string,
        ``None``, or a list containing nested lists / objects.

    Returns
    -------
    list of str
        Non-empty strings; a scalar becomes a one-element list, ``None`` an
        empty list.

    Examples
    --------
    >>> _as_str_list("solo")
    ['solo']
    >>> _as_str_list(None)
    []
    """
    if x is None:
        return []
    items = x if isinstance(x, (list, tuple)) else [x]
    return [s for s in (_as_str(i) for i in items) if s]


def _as_dict_list(
    x: object,
    str_keys: tuple[str, ...],
    *,
    list_keys: tuple[str, ...] = (),
    keep_keys: tuple[str, ...] = (),
    fallback_key: str = "",
) -> list[dict]:
    """Coerce a value into a list of dicts with well-typed, expected keys.

    Parameters
    ----------
    x : object
        A value expected to be a list of objects (decisions, actions, chapters,
        themes, citations). Non-list values are wrapped; non-dict items are
        placed under ``fallback_key`` so nothing is silently dropped.
    str_keys : tuple of str
        Keys whose values are coerced to strings via :func:`_as_str`.
    list_keys : tuple of str, keyword-only, optional
        Keys whose values are coerced to string lists via :func:`_as_str_list`.
    keep_keys : tuple of str, keyword-only, optional
        Keys copied through untouched when present (e.g. ``"t"`` timestamps).
    fallback_key : str, keyword-only, optional
        Key under which a non-dict item's string form is stored.

    Returns
    -------
    list of dict
        One normalised dict per input item, each carrying every ``str_keys`` and
        ``list_keys`` entry (defaulting to ``""`` / ``[]``) plus any present
        ``keep_keys``.

    Examples
    --------
    >>> _as_dict_list([{"theme": "T", "points": "p"}], ("theme",), list_keys=("points",))
    [{'theme': 'T', 'points': ['p']}]
    >>> _as_dict_list("plain", ("action",), fallback_key="action")
    [{'action': 'plain'}]
    """
    items = x if isinstance(x, (list, tuple)) else ([x] if x else [])
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            # A bare string where an object was expected: keep the text rather
            # than drop it, parking it under the caller's primary field.
            row: dict = {fallback_key: _as_str(item)} if fallback_key else {}
            out.append(row)
            continue
        row = {k: _as_str(item.get(k, "")) for k in str_keys}
        for k in list_keys:
            row[k] = _as_str_list(item.get(k))
        for k in keep_keys:
            if k in item:
                row[k] = item[k]
        out.append(row)
    return out


def normalize_synthese(syn: dict) -> dict:
    """Coerce a raw synthesis dict into the exact schema the renderers expect.

    Parameters
    ----------
    syn : dict
        A synthesis dictionary as produced by the LLM (or loaded from a possibly
        hand-edited ``synthese.json``). May carry ``"meta"`` / ``"speakers"``
        and any subset of the seven report keys, in any drifted shape.

    Returns
    -------
    dict
        A new dict with ``"meta"`` and ``"speakers"`` passed through and every
        report key present in its canonical shape: ``resume`` / ``points_cles``
        as string lists; ``decisions`` / ``actions`` / ``chapitres`` / ``themes``
        / ``citations`` as lists of well-typed dicts.

    Examples
    --------
    >>> out = normalize_synthese({"resume": "one para", "themes": [{"theme": "T"}]})
    >>> out["resume"], out["themes"]
    (['one para'], [{'theme': 'T', 'points': []}])

    Notes
    -----
    Idempotent: normalising an already-normalised dict returns the same shape,
    so it is safe to apply both at synth time and again at render time.
    """
    return {
        "meta": syn.get("meta", {}),
        "speakers": syn.get("speakers", {}),
        "resume": _as_str_list(syn.get("resume")),
        "points_cles": _as_str_list(syn.get("points_cles")),
        "decisions": _as_dict_list(
            syn.get("decisions"),
            ("decision", "contexte"),
            keep_keys=("t",),
            fallback_key="decision",
        ),
        "actions": _as_dict_list(
            syn.get("actions"), ("action", "responsable", "echeance"), fallback_key="action"
        ),
        "chapitres": _as_dict_list(
            syn.get("chapitres"), ("titre", "resume"), keep_keys=("t",), fallback_key="titre"
        ),
        "themes": _as_dict_list(
            syn.get("themes"), ("theme",), list_keys=("points",), fallback_key="theme"
        ),
        "citations": _as_dict_list(
            syn.get("citations"), ("speaker", "texte"), keep_keys=("t",), fallback_key="texte"
        ),
    }


def _chunks(transcript: list[dict], names: dict, max_chars: int = 6000) -> Iterator[str]:
    """Yield character-bounded, speaker-labelled transcript chunks.

    Parameters
    ----------
    transcript : list of dict
        Utterances ``{"t0", "t1", "speaker", "text"}``.
    names : dict
        Map from speaker id to display name.
    max_chars : int, optional
        Soft upper bound on chunk size in characters (default 6000), chosen to
        stay within a typical local model's practical context window while
        keeping the "map" calls large enough to be efficient.

    Yields
    ------
    str
        A block of newline-joined ``[<seconds>s] Name: text`` lines. Timestamps
        are emitted in whole seconds — the exact unit the map prompt asks the
        model to echo back — so ``H:MM:SS`` can't be flattened into a bogus
        integer (e.g. ``32:59`` mis-copied as ``3259``).

    Notes
    -----
    A line is never split across chunks: the boundary is placed *before* the line
    that would overflow ``max_chars``, so timestamps and utterances stay intact.
    """
    buf: list[str] = []
    size = 0
    for u in transcript:
        line = f"[{int(round(u['t0']))}s] {names.get(u['speaker'], u['speaker'])}: {u['text']}"
        if size + len(line) > max_chars and buf:
            yield "\n".join(buf)
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        yield "\n".join(buf)


# Upper bound on how much user context is appended to each system prompt. Large
# enough to carry a real meeting brief (participants, proper nouns, an attached
# note), small enough to leave room for the transcript chunk in the model's window.
# NOTE: a blind cap is the crude move — the right fix for a big brief is to *distil*
# it against the transcript (keep what the conversation references) rather than cut;
# that loop is future work. gemma3's 128k window lets us carry a generous slice now.
_CONTEXT_MAX_CHARS: int = 16000

# Char budget for one reduce call's partial-notes payload. gemma3 handles a 128k
# context, so we fold far more than the old 24k cut — but a long meeting still
# overflows a single call, so the reduce is HIERARCHICAL: batch the notes to this
# budget, fold each batch into an intermediate report, then fold those together.
_REDUCE_MAX_CHARS: int = 48000


def _batch_by_chars(items: list[dict], budget: int) -> list[list[dict]]:
    """Group ``items`` into batches whose serialized JSON stays within ``budget``.

    Packs as many whole items as fit per batch; an item that alone exceeds the
    budget still gets its own (later truncated) batch, so nothing is dropped.
    """
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 2  # the enclosing "[]"
    for it in items:
        span = len(json.dumps(it, ensure_ascii=False)) + 1
        if cur and cur_len + span > budget:
            batches.append(cur)
            cur, cur_len = [], 2
        cur.append(it)
        cur_len += span
    if cur:
        batches.append(cur)
    return batches


def _reduce(partials: list[dict], reduce_sys: str, model: str, _depth: int = 0) -> dict:
    """Fold partial notes into one final report, hierarchically when they overflow.

    Notes that fit one reduce call are folded directly. Otherwise they are split
    into budget-sized batches, each folded into an intermediate report, and those
    reports are folded again — so a four-hour meeting reaches the final report
    without silently dropping most of its notes to a fixed truncation. A depth cap
    guarantees termination on pathological inputs (fall back to a truncated fold).
    """
    blob = json.dumps(partials, ensure_ascii=False)
    if len(blob) <= _REDUCE_MAX_CHARS or _depth >= 3:
        payload = blob if len(blob) <= _REDUCE_MAX_CHARS else blob[:_REDUCE_MAX_CHARS]
        return _json_loads_lax(
            _ollama(
                [{"role": "system", "content": reduce_sys}, {"role": "user", "content": payload}],
                model,
            )
        )
    batches = _batch_by_chars(partials, _REDUCE_MAX_CHARS)
    intermediates: list[dict] = []
    for i, batch in enumerate(batches):
        osh.info(f"  synth reduce (batch {i + 1}/{len(batches)}, depth {_depth})...")
        part = _json_loads_lax(
            _ollama(
                [
                    {"role": "system", "content": reduce_sys},
                    {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
                ],
                model,
            )
        )
        if part:
            intermediates.append(part)
    if not intermediates:
        return {}
    # Fold the intermediate reports together — they are report-shaped but still valid
    # "notes" to the reducer. Recursion collapses toward a single final report.
    return _reduce(intermediates, reduce_sys, model, _depth + 1)


def distill_context(text: str, model: str, *, budget: int = _CONTEXT_MAX_CHARS, focus: str = "") -> str:
    """Compress a large reference document into compact, faithful context notes.

    A document that already fits ``budget`` is returned unchanged. A larger one is
    split into ``budget``-sized chunks, each condensed by its own offline LLM call
    (keeping proper nouns, definitions, facts, framing; dropping filler), and the
    summaries are concatenated; if the result still overflows, the pass repeats — so
    the WHOLE document informs the report through several small calls rather than one
    truncated blob. Falls back to a plain cut only if the model is unreachable.
    """
    text = text.strip()
    if len(text) <= budget:
        return text
    focus_clause = f" en lien avec : {focus}" if focus else ""
    sys = (
        "Tu condenses un extrait de document en notes de contexte FIDÈLES et concises "
        "pour préparer un compte-rendu de réunion" + focus_clause + ". Garde les NOMS "
        "PROPRES, sigles, définitions et faits importants ; enlève le remplissage. "
        "N'invente rien. Réponds uniquement par les notes condensées."
    )
    chunks = [text[i : i + budget] for i in range(0, len(text), budget)]
    summaries: list[str] = []
    for i, ch in enumerate(chunks):
        osh.info(f"  context: distilling document chunk {i + 1}/{len(chunks)}...")
        try:
            s = _ollama(
                [{"role": "system", "content": sys}, {"role": "user", "content": ch}],
                model,
                fmt_json=False,
            ).strip()
        except Exception as e:  # noqa: BLE001 - a flaky chunk must not sink the whole doc
            osh.warning(f"  context: distill chunk {i + 1} failed: {e}")
            continue
        if s:
            summaries.append(s)
    merged = "\n\n".join(summaries).strip()
    if not merged:
        return text[:budget]  # model unavailable → honest fallback to a single cut
    if len(merged) > budget and len(merged) < len(text):
        return distill_context(merged, model, budget=budget, focus=focus)
    return merged[:budget] if len(merged) > budget else merged


def _labels_by_talktime(transcript: list[dict]) -> list[str]:
    """Diarizer ids ordered by total speech, longest first (deterministic tie-break)."""
    secs: dict[str, float] = {}
    for u in transcript:
        secs[u["speaker"]] = secs.get(u["speaker"], 0.0) + max(0.0, u.get("t1", 0) - u.get("t0", 0))
    return [lbl for lbl, _ in sorted(secs.items(), key=lambda kv: (-kv[1], kv[0]))]


def _speaker_sample(transcript: list[dict], labels: list[str], per: int = 8, maxlen: int = 220) -> str:
    """A few representative lines per diarizer id, for name attribution."""
    by: dict[str, list[str]] = {lbl: [] for lbl in labels}
    for u in transcript:
        lbl = u["speaker"]
        if lbl in by and len(by[lbl]) < per and u.get("text"):
            by[lbl].append(u["text"].strip()[:maxlen])
    lines: list[str] = []
    for lbl in labels:
        lines.append(f"{lbl}:")
        lines.extend(f"  - {t}" for t in by[lbl])
    return "\n".join(lines)


def assign_speaker_names(
    transcript: list[dict], roster: list[str], model: str, language: str | None = None
) -> dict:
    """Determine which diarizer id (S0, S1 …) is which named participant.

    The diarizer discovers *how many* voices there are but not *who* they are; the
    folder's ``notes.yaml`` supplies the roster of names but cannot know, up front,
    which recorded voice is which person. This bridges the two: from a sample of each
    id's speech and the roster, the model maps every id to its most likely name. A
    talk-time heuristic (most-talkative id → first-listed name, by rank) is the
    fallback when the model is unavailable; an id with no confident match keeps its
    id (a minor / audience voice). One named person may cover several ids when there
    is a single speaker.

    Parameters
    ----------
    transcript : list of dict
        Diarized utterances ``{"t0", "t1", "speaker", "text"}``.
    roster : list of str
        Participant names from ``notes.yaml`` (order is not an identity claim).
    model : str
        Ollama model used for the attribution call.
    language : str, optional
        Report language, only to steer the sample interpretation.

    Returns
    -------
    dict
        Map from every diarizer id present to a display name.
    """
    labels = _labels_by_talktime(transcript)
    if not roster:
        return {lbl: lbl for lbl in labels}

    lang_clause = f"en {language}" if language else "dans la langue de la transcription"
    sys = (
        "Tu associes des identifiants de locuteurs (S0, S1, …) à des personnes nommées, "
        f"d'après des échantillons de leurs propos {lang_clause}. Participants connus : "
        + ", ".join(roster)
        + ". Réponds en JSON STRICT {\"S0\": \"Nom\", \"S1\": \"Nom\", ...} : pour chaque "
        "identifiant, la personne la plus probable parmi les participants, ou l'identifiant "
        "lui-même si aucune ne correspond (public, voix mineure). Un même participant peut "
        "couvrir plusieurs identifiants s'il n'y a qu'un seul orateur."
    )
    try:
        mapping = _json_loads_lax(
            _ollama(
                [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": _speaker_sample(transcript, labels)},
                ],
                model,
            )
        )
    except Exception:  # noqa: BLE001 - attribution is best-effort; heuristic covers failure
        mapping = {}

    out: dict[str, str] = {}
    for i, lbl in enumerate(labels):
        name = mapping.get(lbl) if isinstance(mapping.get(lbl), str) else None
        if not name:
            name = roster[i] if i < len(roster) else lbl  # rank heuristic, else keep the id
        out[lbl] = name
    return out

# The map + reduce system prompts live, fully translated per language, in
# locales/i18n.yaml under `prompts.map_sys` / `prompts.reduce_sys`. They are read
# at call time via `_i18n.prompt(id, language)` so a report can be produced in any
# supported language, and adding a language means only editing that catalog.


def synthesize(
    transcript: list[dict],
    speakers: dict,
    *,
    title: str = "",
    date: str = "",
    lieu: str = "",
    model: str = OLLAMA_MODEL,
    language: str | None = None,
    audio_sources: list | None = None,
    context: str = "",
) -> dict:
    """Produce a structured meeting report from a diarized transcript.

    Parameters
    ----------
    transcript : list of dict
        Utterances ``{"t0", "t1", "speaker", "text"}`` in time order.
    speakers : dict
        Speaker metadata keyed by speaker id (e.g. ``{"S0": {"name": ...}}``);
        carried through into the report and used for display names.
    title : str, optional
        Report title; defaults to ``"Compte-rendu"`` when empty.
    date : str, optional
        ISO date string; defaults to today when empty.
    lieu : str, optional
        Meeting location (free text).
    model : str, optional
        Ollama model name. Defaults to :data:`OLLAMA_MODEL`.
    language : str, optional
        Output language for the report. ``None`` (default) means **discover** —
        the model is told to write in the transcript's own language, so nothing is
        assumed. Pass an explicit code (e.g. ``"fr"``, ``"en"``) only to force one.
    audio_sources : list, optional
        Source descriptors recorded in the report metadata.
    context : str, optional
        Free-text meeting context (participants and roles, proper nouns, domain
        vocabulary, acronyms, stakes) appended to both the map and reduce system
        prompts. Speech-to-text and summarisation both improve sharply when the
        model knows the domain: it fixes proper-noun spellings and frames the
        notes correctly. It is guidance only — the model is still told never to
        invent facts. Truncated defensively to keep the local context window
        safe. Stays fully local: this is plain text the caller already holds.

    Returns
    -------
    dict
        The synthese: ``meta`` and ``speakers`` plus the seven content keys
        ``resume``, ``points_cles``, ``decisions``, ``actions``, ``chapitres``,
        ``themes``, ``citations`` (all lists; ``resume`` normalised to a list).

    Notes
    -----
    If the local Ollama server is unreachable at any point, the whole map-reduce
    is abandoned and :func:`_heuristic` supplies a minimal, clearly-labelled
    fallback so the report is still produced (never blocks on a missing LLM).
    """
    names = {sid: info.get("name", sid) for sid, info in speakers.items()}
    # Optional user context, appended to every system prompt (map + reduce). It
    # biases proper-noun spelling and framing without licensing invention, and is
    # capped so a long brief cannot crowd out the transcript in the context window.
    ctx = context.strip()
    if len(ctx) > _CONTEXT_MAX_CHARS:
        osh.warning(f"  synth: context is {len(ctx)} chars, truncating to {_CONTEXT_MAX_CHARS}")
        ctx = ctx[:_CONTEXT_MAX_CHARS]
    ctx_block = (
        (
            "\n\nContexte fourni par l'utilisateur (participants et rôles, noms propres, "
            "sigles, enjeux). Sers-t'en pour orthographier correctement les noms propres "
            "et bien cadrer les notes, mais N'INVENTE AUCUN fait absent de la transcription:\n"
            + ctx
        )
        if ctx
        else ""
    )
    # Prompts live in locales/i18n.yaml (front-ui i18n convention). No default
    # language: when the caller passes none, the model is told to write in the
    # transcript's own language — the report language is discovered, never assumed.
    lang_clause = f"en {language}" if language else "dans la langue d'origine de la transcription"
    map_sys = _i18n.prompt("map_sys").replace("{lang_clause}", lang_clause) + ctx_block
    reduce_sys = _i18n.prompt("reduce_sys").replace("{lang_clause}", lang_clause) + ctx_block
    duree = _hhmmss(transcript[-1]["t1"]) if transcript else "0:00:00"
    meta = {
        "titre": title or "Compte-rendu",
        "date": date or datetime.date.today().isoformat(),
        "horaire": "",
        "lieu": lieu,
        "duree": duree,
        "audio_sources": audio_sources or [],
    }

    # Map step. Each chunk is isolated: a long meeting fans out into dozens of
    # map calls, and a single chunk that errors (transport hiccup) or returns
    # unparseable JSON must not discard the notes gathered from every other
    # chunk. We count usable partials so we can tell "one bad chunk" apart from
    # "the LLM is unreachable" (all chunks failed) further down.
    partials: list[dict] = []
    map_ok = 0
    chunks = list(_chunks(transcript, names))
    for i, chunk in enumerate(chunks):
        osh.info(f"  synth map {i + 1}/{len(chunks)}...")
        try:
            c = _ollama(
                [{"role": "system", "content": map_sys}, {"role": "user", "content": chunk}], model
            )
        except Exception as e:
            osh.warning(f"  synth map {i + 1} failed: {e}")
            continue
        part = _json_loads_lax(c)  # never raises; {} when unparseable
        if part:
            map_ok += 1
        partials.append(part)

    if map_ok == 0:
        # Not a single chunk produced usable notes — the local LLM is effectively
        # unavailable (server down, or every response unparseable). Fall back to
        # the no-LLM heuristic so the report still exists.
        osh.warning("  synth: local LLM produced no usable notes -> minimal heuristic synthese")
        final = _heuristic(transcript, names)
    else:
        osh.info("  synth reduce...")
        # Hierarchical fold: the whole meeting's notes reach the report (batched and
        # re-folded when they overflow one call) instead of being cut at a fixed size.
        try:
            final = _reduce(partials, reduce_sys, model)
        except Exception as e:
            osh.warning(f"  synth reduce failed ({e}) -> minimal heuristic synthese")
            final = {}
        if not final:
            # Reduce came back empty/unreachable but we do have per-chunk notes;
            # the heuristic at least preserves structure and timestamps.
            final = _heuristic(transcript, names)

    # Coerce the LLM's (or heuristic's) raw output into the exact schema the
    # renderers rely on — small local models drift on field shapes.
    return normalize_synthese({"meta": meta, "speakers": speakers, **final})


def _heuristic(transcript: list[dict], names: dict) -> dict:
    """Build a minimal no-LLM fallback report.

    Parameters
    ----------
    transcript : list of dict
        Utterances ``{"t0", "t1", "speaker", "text"}``.
    names : dict
        Map from speaker id to display name (unused here but kept for a uniform
        signature with the LLM path).

    Returns
    -------
    dict
        A report shell with a clear "Ollama unreachable" resume and a handful of
        evenly-spaced chapters derived from the transcript, so the downstream
        report never blocks on a missing LLM.

    Notes
    -----
    Chapters are sampled at ~8 evenly-spaced utterances; ``max(1, ...)`` guards
    against a zero step on very short transcripts.
    """
    resume = [
        "(Synthèse locale indisponible — Ollama non joignable. "
        "Transcription et diarisation restent complètes ci-dessous.)"
    ]
    chapters: list[dict] = []
    step = max(1, len(transcript) // 8)
    for i in range(0, len(transcript), step):
        u = transcript[i]
        chapters.append({"t": u["t0"], "titre": u["text"][:60], "resume": ""})
    return {
        "resume": resume,
        "points_cles": [],
        "decisions": [],
        "actions": [],
        "chapitres": chapters,
        "themes": [],
        "citations": [],
    }


def load_speakers(mapping_path: str, transcript: list[dict]) -> dict:
    """Build the speakers dict from a speaker-mapping file (or bare S-ids).

    Parameters
    ----------
    mapping_path : str
        Path to ``speaker_mapping.json`` (as written by :func:`notes_helper.pipeline.run`).
        May be falsy or missing, in which case only bare ids are used.
    transcript : list of dict
        Utterances; their distinct ``speaker`` ids define the speaker set.

    Returns
    -------
    dict
        ``{speaker_id: {"name": <name or id>, "role": ""}}`` for every speaker id
        present in the transcript, with names filled in from the mapping when
        available.
    """
    ids = sorted({u["speaker"] for u in transcript})
    names: dict = {}
    if mapping_path and osh.file_exists(mapping_path):
        names = json.load(open(mapping_path)).get("mapping", {})
    return {sid: {"name": names.get(sid, sid), "role": ""} for sid in ids}
