#!/usr/bin/env python3
"""Clean common whisper hallucination artifacts from transcript.json.

Writes transcript_clean.json. Conservative: strips leaked tag prefixes,
drops pure subtitle-credit / filler hallucinations, merges consecutive
same-speaker fragments that got split.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
tr = json.load(open(os.path.join(HERE, "transcript.json"), encoding="utf-8"))

# leaked prefixes whisper sometimes injects
PREFIX = re.compile(r"^\s*(Sujets\s*:|Intervenants\s*:|R&D\s*F?\s*:|R&D\b[\s:]*|-\s*Sous-titrage[^\n]*)", re.I)
# whole-utterance junk (subtitle credits / silence hallucinations)
JUNK = re.compile(
    r"(sous-titrage|radio-canada|amara\.org|crayon d.ontario|\bm\.d\.\b|"
    r"société radio|sous-titres réalisés)", re.I)
# pure-filler utterances to drop when alone
FILLER = {"merci.", "merci", "...", "- -", "-", "–", "—", "sous-titrage", ""}


def clean_text(t):
    prev = None
    while prev != t:
        prev = t
        t = PREFIX.sub("", t).strip()
    # collapse immediate word/phrase loops like "C'est De Vos, hein." x6
    t = re.sub(r"(\b[^.!?]{3,40}[.!?])(\s*\1){2,}", r"\1", t)
    return t.strip()


out = []
for u in tr:
    t = clean_text(u["text"])
    low = t.lower().strip(" .!?-–—")
    if not t or low in FILLER:
        continue
    if JUNK.search(t) and len(t) < 60:
        continue
    t = JUNK.sub("", t).strip(" -–—")
    if not t or t.lower().strip(" .!?-") in FILLER:
        continue
    # merge into previous if same speaker and tiny gap
    if out and out[-1]["speaker"] == u["speaker"] and u["t0"] - out[-1]["t1"] <= 1.2:
        out[-1]["text"] = (out[-1]["text"] + " " + t).strip()
        out[-1]["t1"] = u["t1"]
    else:
        out.append({"t0": u["t0"], "t1": u["t1"], "speaker": u["speaker"], "text": t})

json.dump(out, open(os.path.join(HERE, "transcript_clean.json"), "w"),
          ensure_ascii=False, indent=1)
print(f"cleaned {len(tr)} -> {len(out)} utterances")
from collections import Counter

print("per speaker:", dict(Counter(u["speaker"] for u in out)))
