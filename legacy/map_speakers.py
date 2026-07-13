#!/usr/bin/env python3
"""
Best-guess mapping of diarization clusters (S0..S3) -> real participant names,
using a direct-address heuristic on the transcript.

Idea: when someone says a participant's first name, that participant is very
often the *neighbouring* speaker — either answering right after being called,
or being thanked right after they spoke. We accumulate votes over the whole
6-hour transcript and solve a global 1-to-1 assignment (Hungarian).

The café far-field audio makes diarization approximate, so this mapping is a
best guess — it prints the full vote matrix so a human can sanity-check /
override in synthese.json.
"""
import json
import os
import re

import numpy as np
from scipy.optimize import linear_sum_assignment

HERE = os.path.dirname(os.path.abspath(__file__))
TR = os.path.join(HERE, "transcript.json")

NAMES = {
    "Warith Harchaoui": r"\b(warith|warit[eh]?|ouarith)\b",
    "Benoît Defoug":    r"\b(beno[iî]t|defoug)\b",
    "Vincent Sammiez":  r"\b(vincent|sammiez|sami?ez)\b",
    "Philippe Vivien":  r"\b(philippe|vivien)\b",
}


def main():
    tr = json.load(open(TR, encoding="utf-8"))
    names = list(NAMES.keys())
    spks = sorted({u["speaker"] for u in tr})
    votes = {n: {s: 0.0 for s in spks} for n in names}

    pats = {n: re.compile(p, re.I) for n, p in NAMES.items()}
    for i, u in enumerate(tr):
        A = u["speaker"]
        for n, pat in pats.items():
            if pat.search(u["text"]):
                # neighbours that are a different speaker get the vote
                for j, w in ((i + 1, 1.0), (i - 1, 0.6), (i + 2, 0.4)):
                    if 0 <= j < len(tr) and tr[j]["speaker"] != A:
                        votes[n][tr[j]["speaker"]] += w

    # matrix names x speakers ; maximise total assigned votes
    M = np.array([[votes[n][s] for s in spks] for n in names], dtype=float)
    ri, ci = linear_sum_assignment(-M)
    mapping = {spks[ci[k]]: names[ri[k]] for k in range(len(ri))}

    print("=== vote matrix (rows=names, cols=speakers) ===")
    print("            " + "  ".join(f"{s:>7}" for s in spks))
    for k, n in enumerate(names):
        print(f"{n:22s} " + "  ".join(f"{M[k,j]:7.1f}" for j in range(len(spks))))

    # confidence = assigned vote share of that speaker's column
    print("\n=== assignment ===")
    conf = {}
    for s in spks:
        colsum = sum(votes[nm][s] for nm in names) or 1.0
        nm = mapping.get(s)
        c = votes[nm][s] / colsum if nm else 0.0
        conf[s] = c
        seg = sum(1 for u in tr if u["speaker"] == s)
        print(f"  {s} -> {nm:22s}  votes={votes[nm][s]:6.1f}  share={c:.0%}  ({seg} interventions)")

    out = {"mapping": mapping, "confidence": {s: round(conf[s], 3) for s in spks}}
    json.dump(out, open(os.path.join(HERE, "speaker_mapping.json"), "w"),
              ensure_ascii=False, indent=2)
    print("\nwrote speaker_mapping.json")


if __name__ == "__main__":
    main()
