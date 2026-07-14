"""``notes-helper`` command-line entry point.

Module summary
--------------
Exposes the ``notes-helper`` CLI, an argparse-based dispatcher over the local-first
audio-to-report pipeline. Subcommands::

    notes-helper run <audio> --out DIR        audio -> transcript (+ diarization + identity)
    notes-helper synth <DIR>                  transcript -> synthese.json (local Ollama)
    notes-helper report <DIR> --format ...    render html / md / docx / pdf / pptx / vault
    notes-helper all <audio> --out DIR        run + synth + report in one go
    notes-helper enroll <ckpt> --cluster S0 --name "..."     name a voice once
    notes-helper identify <ckpt>              match clusters against the store
    notes-helper people list | forget <id>    manage on-device voiceprints
    notes-helper audit <DIR>                  fail if any artifact phones home

Each ``_cmd_*`` handler performs work and then prints its *result* (paths,
mapping tables, "wrote ...", audit outcome) to stdout for the user — that is the
program's user-facing output and is intentionally kept as ``print``. Genuine
diagnostics would go through :mod:`os_helper`; the handlers below emit results
only, so there are none to convert here.

Heavy pipeline modules are imported lazily inside each handler so that
``notes-helper --version`` / ``--help`` stay fast and do not pull in ASR / LLM / DB
dependencies that a given invocation may not need.

Usage example
-------------
    >>> from notes_helper.cli import audit_egress
    >>> print(audit_egress("/nonexistent/dir"))
    0
    # expected output: 0

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections.abc import Sequence

from . import __version__


def _cmd_run(a: argparse.Namespace) -> None:
    """Run the audio -> transcript pipeline and print the result JSON.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``audio``, ``out``, ``speakers``, ``lang``,
        ``prompt`` and ``no_identify``.

    Returns
    -------
    None
        Prints the pipeline result as indented JSON to stdout (user output).
    """
    # Lazy import: the ASR/diarization stack is heavy and only needed here.
    from .pipeline import run

    res = run(
        a.audio,
        a.out,
        n_spk=a.speakers,
        language=a.lang,
        initial_prompt=a.prompt or "",
        identify=not a.no_identify,
    )
    print(json.dumps(res, indent=2))


def _cmd_synth(a: argparse.Namespace) -> None:
    """Synthesize a transcript into ``synthese.json`` via the local LLM.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``dir``, ``title``, ``lieu``, ``model`` and
        ``lang``.

    Returns
    -------
    None
        Writes ``<dir>/synthese.json`` and prints its path to stdout.
    """
    from .synth import load_speakers, synthesize

    tr = json.load(open(os.path.join(a.dir, "transcript.json"), encoding="utf-8"))
    speakers = load_speakers(os.path.join(a.dir, "speaker_mapping.json"), tr)
    # A --context-file (if readable) takes precedence over inline --context.
    context = a.context or ""
    if getattr(a, "context_file", ""):
        context = open(a.context_file, encoding="utf-8").read()
    syn = synthesize(
        tr,
        speakers,
        title=a.title or os.path.basename(a.dir.rstrip("/")),
        lieu=a.lieu or "",
        model=a.model,
        language=a.lang,
        context=context,
    )
    out = os.path.join(a.dir, "synthese.json")
    json.dump(syn, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {out}")  # user-facing result: where the artifact landed


def _cmd_report(a: argparse.Namespace) -> None:
    """Render the requested report formats and print a mapping table.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``dir``, ``format`` (comma-separated) and
        ``vault``.

    Returns
    -------
    None
        Prints one ``format -> path`` line per rendered artifact to stdout.
    """
    from .outputs import render

    # Accept a comma-separated list and drop empty entries from stray commas.
    formats = [f.strip() for f in a.format.split(",") if f.strip()]
    written = render(a.dir, formats, vault_dir=a.vault or "")
    for k, v in written.items():
        print(f"  {k:6s} -> {v}")  # user-facing result: format -> path


def _cmd_all(a: argparse.Namespace) -> None:
    """Run the full ``run`` -> ``synth`` -> ``report`` chain in order.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments carrying the union of fields required by the three
        underlying handlers.

    Returns
    -------
    None
        Delegates to :func:`_cmd_run`, :func:`_cmd_synth` and
        :func:`_cmd_report`, each of which prints its own result.
    """
    _cmd_run(a)
    _cmd_synth(a)
    _cmd_report(a)


def _cmd_enroll(a: argparse.Namespace) -> None:
    """Enroll a diarization cluster as a named person in the voiceprint store.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``checkpoint``, ``db``, ``cluster``, ``name``
        and ``role``.

    Returns
    -------
    None
        Prints the newly assigned person id to stdout.
    """
    from .identity import PeopleStore, _load_ckpt, enroll_cluster

    X, labels = _load_ckpt(a.checkpoint)
    store = PeopleStore(a.db)
    # cluster is passed like "S0"/"s3"; strip the leading S to get the int index.
    pid = enroll_cluster(store, X, labels, int(a.cluster.lstrip("Ss")), a.name, a.role)
    store.close()
    print(f"enrolled {a.name} as '{pid}' from {a.cluster}")


def _cmd_identify(a: argparse.Namespace) -> None:
    """Match a recording's clusters against the store and write a mapping.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``checkpoint``, ``db`` and ``out``.

    Returns
    -------
    None
        Writes a speaker-mapping JSON (defaulting to ``speaker_mapping.json``)
        and prints a per-cluster confidence table to stdout.
    """
    from .identity import PeopleStore, _load_ckpt, identify_recording

    X, labels = _load_ckpt(a.checkpoint)
    store = PeopleStore(a.db)
    mp = identify_recording(X, labels, store)
    store.close()
    out = a.out or "speaker_mapping.json"
    json.dump(
        {"mapping": {k: v["name"] for k, v in mp.items()}, "detail": mp},
        open(out, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    for k, v in mp.items():
        print(f"  {k} -> {v['name']:24s} conf={v['confidence']:.3f} ({v['mode']})")


def _cmd_people(a: argparse.Namespace) -> None:
    """List or forget on-device voiceprints.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``db``, ``action`` (``"list"`` | ``"forget"``)
        and ``person_id`` (for ``forget``).

    Returns
    -------
    None
        For ``list`` prints one line per stored person; for ``forget`` prints a
        confirmation.
    """
    from .identity import PeopleStore

    store = PeopleStore(a.db)
    if a.action == "list":
        for p in store.all_people():
            print(f"  {p['id']:22s} {p['name']:24s} {p['role']:14s} ({p['n_exemplars']} ex.)")
    elif a.action == "forget":
        store.delete(a.person_id)
        print(f"forgot {a.person_id}")
    store.close()


def _cmd_audit(a: argparse.Namespace) -> None:
    """Fail (exit 1) if generated artifacts reference any external URL.

    Parameters
    ----------
    a : argparse.Namespace
        Parsed arguments; uses ``dir`` (the artifact directory to scan).

    Returns
    -------
    None
        Prints the audit outcome. Calls :func:`sys.exit(1)` when egress is
        detected so the command is CI-friendly.

    Raises
    ------
    SystemExit
        With code 1 when one or more external URLs are found.
    """
    n = audit_egress(a.dir)
    if n:
        # Failure summary goes to stderr so it does not pollute piped stdout.
        print(f"EGRESS AUDIT FAILED: {n} external URL(s) found", file=sys.stderr)
        sys.exit(1)
    print("egress audit OK — no external URLs in generated artifacts")


def audit_egress(path: str) -> int:
    """Return the number of external http(s) URLs found in generated artifacts.

    Parameters
    ----------
    path : str
        Root directory to scan recursively for ``*.html`` and ``*.md`` files.

    Returns
    -------
    int
        The count of lines containing an ``http(s)://`` reference across the
        scanned files. Each offending line is also printed to stdout for review.

    Notes
    -----
    Files under a ``assets`` directory are skipped: vendored local assets are
    expected to be self-contained and are not egress. Files are read with
    ``errors="ignore"`` so a stray binary/encoding hiccup cannot abort the audit.
    """
    import re

    pat = re.compile(r"https?://")
    files = glob.glob(os.path.join(path, "**", "*.html"), recursive=True)
    files += glob.glob(os.path.join(path, "**", "*.md"), recursive=True)
    hits = 0
    for f in files:
        if os.sep + "assets" + os.sep in f:
            continue  # vendored local assets are fine
        for i, line in enumerate(open(f, encoding="utf-8", errors="ignore"), 1):
            if pat.search(line):
                # Report the offending location + a trimmed preview (result output).
                print(f"  {f}:{i}: {line.strip()[:100]}")
                hits += 1
    return hits


def main(argv: Sequence[str] | None = None) -> None:
    """Parse arguments and dispatch to the selected subcommand handler.

    Parameters
    ----------
    argv : sequence of str, optional
        Argument vector to parse. Defaults to ``None``, in which case argparse
        reads from ``sys.argv``.

    Returns
    -------
    None
        Executes the matched handler for its side effects (files written,
        results printed). May exit non-zero via a handler (e.g. ``audit``).
    """
    ap = argparse.ArgumentParser(
        prog="notes-helper",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"notes-helper {__version__}")
    # DB path default mirrors config.DB_PATH's env/home fallback so the CLI and
    # library agree on where voiceprints live.
    ap.add_argument(
        "--db",
        default=os.environ.get("NOTES_HELPER_DB", os.path.expanduser("~/.notes-helper/people.db")),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_synth_report_args(p: argparse.ArgumentParser) -> None:
        """Attach the arguments shared by the ``synth``/``report``/``all`` subcommands."""
        p.add_argument("--title", default="")
        p.add_argument("--lieu", default="")
        p.add_argument(
            "--model", default=os.environ.get("NOTES_HELPER_OLLAMA_MODEL", "qwen2.5:32b")
        )
        p.add_argument("--lang", default="fr")
        p.add_argument("--format", default="html")
        p.add_argument("--vault", default="")
        # Meeting context for synth: inline text or a file (file wins). Used to
        # bias proper-noun spelling and framing. Ignored by the report handler.
        p.add_argument("--context", default="")
        p.add_argument("--context-file", default="")

    p = sub.add_parser("run")
    p.add_argument("audio")
    p.add_argument("--out", required=True)
    p.add_argument("--speakers", type=int, default=None)
    p.add_argument("--lang", default="fr")
    p.add_argument("--prompt", default="")
    p.add_argument("--no-identify", action="store_true")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("synth")
    p.add_argument("dir")
    add_synth_report_args(p)
    p.set_defaults(func=_cmd_synth)

    p = sub.add_parser("report")
    p.add_argument("dir")
    add_synth_report_args(p)
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("all")
    p.add_argument("audio")
    p.add_argument("--out", required=True, dest="out")
    p.add_argument("--speakers", type=int, default=None)
    p.add_argument("--prompt", default="")
    p.add_argument("--no-identify", action="store_true")
    add_synth_report_args(p)
    # 'dir' is derived from --out (see set_defaults below); hide it from help.
    p.add_argument("dir", nargs="?", help=argparse.SUPPRESS)
    p.set_defaults(func=lambda a: (setattr(a, "dir", a.out), _cmd_all(a)))

    p = sub.add_parser("enroll")
    p.add_argument("checkpoint")
    p.add_argument("--cluster", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", default="")
    p.set_defaults(func=_cmd_enroll)

    p = sub.add_parser("identify")
    p.add_argument("checkpoint")
    p.add_argument("--out", default="")
    p.set_defaults(func=_cmd_identify)

    p = sub.add_parser("people")
    p.add_argument("action", choices=["list", "forget"])
    p.add_argument("person_id", nargs="?")
    p.set_defaults(func=_cmd_people)

    p = sub.add_parser("audit")
    p.add_argument("dir")
    p.set_defaults(func=_cmd_audit)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
