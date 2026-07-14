"""Shared time-coercion for the output renderers.

Module summary
--------------
The Markdown, HTML and vault renderers all format chapter/quote/utterance times
as ``H:MM:SS`` timestamps, and all face the same input problem: a value may
arrive as a float straight from JSON, as an integer, as ``None``, or — because a
local LLM produced it — as a string that is either a bare second count
(``"28"``) or an already-formatted timestamp (``"0:00:28"``). This module
centralises the tolerant coercion so a single malformed time never aborts a
whole render.

Usage example
-------------
>>> from notes_helper.outputs._timefmt import seconds
>>> seconds("0:00:28")
28
>>> seconds(None)
0

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations


def seconds(s: float | int | str | None) -> int:
    """Coerce a heterogeneous time value into a whole number of seconds.

    Parameters
    ----------
    s : float | int | str | None
        A time. Accepted forms: ``None`` / falsy (→ ``0``); an int or float
        number of seconds; a numeric string (``"28"``, ``"28.0"``); or a
        colon-separated timestamp (``"0:00:28"``, ``"1:02:03"``, ``"5:03"``).

    Returns
    -------
    int
        Whole seconds. Any value that cannot be parsed yields ``0`` rather than
        raising — a single bad time must not break the whole report render.

    Examples
    --------
    >>> seconds(3723)
    3723
    >>> seconds("1:02:03")
    3723
    >>> seconds("28")
    28
    >>> seconds(None)
    0
    >>> seconds("garbage")
    0
    """
    if isinstance(s, str):
        try:
            # Fold a colon-separated timestamp into seconds (base-60 per field);
            # a bare number is just the single-field case.
            total = 0
            for part in s.strip().split(":"):
                total = total * 60 + int(float(part or 0))
            return total
        except ValueError:
            return 0
    return int(s or 0)
