#!/usr/bin/env python3
"""Egress audit (CI gate) — fail if any artifact references an external URL.

Module summary
--------------
Self-contained, stdlib-only audit that scans generated ``.html`` / ``.md``
artifacts and fails (exit code 1) if any of them reference an external
``http(s)`` URL. Vendored local ``assets/`` are exempt. This enforces the
pipeline's sovereignty claim (nothing leaves the device) instead of merely
intending it, which makes it suitable as a pre-commit / CI gate.

Usage example
-------------
>>> import os, tempfile
>>> import audit_egress  # module import; run from the scripts/ directory
>>> d = tempfile.mkdtemp()
>>> _ = open(os.path.join(d, "clean.md"), "w").write("no urls here\\n")
>>> print(audit_egress.audit(d))  # 0 hits => the CI gate would pass
0
# expected output: 0

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import glob
import os
import re
import sys

# Any occurrence of an http(s) scheme counts as a potential egress reference.
PAT: re.Pattern[str] = re.compile(r"https?://")


def audit(path: str) -> int:
    """Scan one directory tree and report external-URL hits in artifacts.

    Parameters
    ----------
    path : str
        Root directory to scan recursively for ``.html`` and ``.md`` files.

    Returns
    -------
    int
        The number of lines containing an external ``http(s)`` URL. Each hit is
        also printed to stdout as ``  <file>:<line>: <snippet>`` for the operator.

    Notes
    -----
    Files under a vendored ``assets/`` directory are skipped: local assets are
    allowed to carry URLs (e.g. embedded metadata) without failing the gate.
    Files are read leniently (``errors="ignore"``) so odd encodings never crash
    the audit.
    """
    files = glob.glob(os.path.join(path, "**", "*.html"), recursive=True)
    files += glob.glob(os.path.join(path, "**", "*.md"), recursive=True)
    hits = 0
    for f in files:
        # Vendored local assets are exempt from the egress rule.
        if os.sep + "assets" + os.sep in f:
            continue
        for i, line in enumerate(open(f, encoding="utf-8", errors="ignore"), 1):
            if PAT.search(line):
                # CLI output: point the operator at the offending line.
                print(f"  {f}:{i}: {line.strip()[:100]}")
                hits += 1
    return hits


def main() -> None:
    """Run the egress audit over the CLI target directories and set exit status.

    Returns
    -------
    None

    Raises
    ------
    SystemExit
        With code ``1`` when one or more external URLs are found (the CI gate
        fails). On success the process simply returns (exit code ``0``).

    Notes
    -----
    Targets come from ``sys.argv[1:]`` (defaulting to the current directory).
    The ``print(...)`` calls here are the CLI gate's *result* — the failure
    summary on stderr and the success line on stdout — so they are intentionally
    kept as ``print`` rather than converted to logging.
    """
    targets = sys.argv[1:] or ["."]
    total = sum(audit(t) for t in targets)
    if total:
        # CLI gate result: fail loudly on stderr and set a non-zero exit code.
        print(f"EGRESS AUDIT FAILED: {total} external URL(s) found", file=sys.stderr)
        sys.exit(1)
    # CLI gate result: the all-clear line on stdout.
    print("egress audit OK — no external URLs")


if __name__ == "__main__":
    main()
