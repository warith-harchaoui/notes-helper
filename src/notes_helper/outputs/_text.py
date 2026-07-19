"""Coerce any LLM-emitted value into clean display text — one place, for every renderer.

Module summary
--------------
Local LLMs drift: a field documented as a string sometimes comes back as a dict
(``{"texte": "…"}``), a list, or a number. If a renderer interpolates that value
directly it leaks Python/JSON syntax into the page — e.g. ``{'texte': '…'}`` — which
is exactly what a reader must never see. :func:`as_text` is the single coercion the
Markdown and HTML renderers share so no drifted value is ever shown raw.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

from typing import Any

# Keys a drifted dict may hide its human text under, in priority order. Covers the
# shapes the synthesis prompt can emit for points, quotes, chapters, decisions,
# actions, themes — so a stray ``{"texte": …}`` renders as its text, never as JSON.
_TEXT_KEYS: tuple[str, ...] = (
    "texte",
    "text",
    "point",
    "phrase",
    "contenu",
    "content",
    "titre",
    "title",
    "resume",
    "résumé",
    "action",
    "decision",
    "décision",
    "theme",
    "thème",
    "citation",
    "value",
)


def as_text(x: Any) -> str:
    """Return clean, human-readable text for any value a renderer might receive.

    Parameters
    ----------
    x : Any
        A string, ``None``, a number, a list/tuple, or a drifted dict.

    Returns
    -------
    str
        - ``None`` -> ``""``.
        - ``str`` -> itself, stripped.
        - ``dict`` -> the first present :data:`_TEXT_KEYS` value (recursively
          coerced); failing that, its text-ish values joined — never the raw
          ``{...}`` form.
        - ``list`` / ``tuple`` -> each item coerced and joined with ``", "``.
        - anything else -> ``str(x)``.

    Examples
    --------
    >>> as_text("hello")
    'hello'
    >>> as_text({"texte": "OpenAI ships", "t": 12})
    'OpenAI ships'
    >>> as_text(["a", {"point": "b"}])
    'a, b'
    >>> as_text(None)
    ''
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        for key in _TEXT_KEYS:
            if key in x and x[key] not in (None, ""):
                return as_text(x[key])
        # No known key — join whatever scalar values it has rather than dump JSON.
        vals = [as_text(v) for v in x.values() if isinstance(v, (str, int, float))]
        return ", ".join(v for v in vals if v)
    if isinstance(x, (list, tuple)):
        return ", ".join(t for t in (as_text(i) for i in x) if t)
    return str(x)
