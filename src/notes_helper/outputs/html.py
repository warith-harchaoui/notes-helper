"""Self-contained interactive HTML report renderer (the in-app GUI).

Module summary
--------------
This module renders a single, fully self-contained HTML report from a transcript
and its synthesis. It was ported from the legacy ``build_page.py`` into the
public entry point :func:`render_html`. The report bundles a tabbed interface
(résumé, key points, decisions, actions, chapters, themes, citations and a
searchable/filterable transcript), an optional audio player wired to seek to any
timestamp, and speaker-coloured chips.

Portability is a hard requirement: the vendored assets (a Tailwind build plus the
Roboto font family) are copied next to the report on write, so the page is fully
offline with **zero external requests** — a property enforced by ``notes-helper
audit``.

Most helpers in this file build HTML fragment strings and are joined into one
large template near the end of :func:`render_html`. That template contains
JavaScript (``document...`` handlers) and CSS; it is *template text*, not Python,
and is emitted verbatim into the output file.

Usage example
-------------
>>> from notes_helper.outputs.html import esc
>>> print(esc('<b>Alice & Bob</b>'))
&lt;b&gt;Alice &amp; Bob&lt;/b&gt;
# expected output: &lt;b&gt;Alice &amp; Bob&lt;/b&gt;

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import html
import json
import os
import shutil

from .. import i18n as _i18n
from ..config import SPK_COLORS
from ._text import as_text
from ._timefmt import seconds as _seconds

# Directory holding the vendored offline assets (Tailwind + fonts), resolved
# relative to this file so it works regardless of the current working directory.
_ASSETS: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")


def _hhmmss(s: float | int | str | None) -> str:
    """Format a number of seconds as a zero-padded ``H:MM:SS`` timestamp.

    Parameters
    ----------
    s : float | int
        Seconds since the start of the recording.

    Returns
    -------
    str
        The timestamp as ``H:MM:SS`` (hours unpadded, minutes/seconds padded).

    Examples
    --------
    >>> _hhmmss(3723)
    '1:02:03'
    >>> _hhmmss("0:00:28")
    '0:00:28'
    """
    # Local LLMs emit chapter/quote times as seconds, floats, or already
    # formatted strings; coerce tolerantly so one bad value cannot abort render.
    s = _seconds(s)
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def esc(t: object) -> str:
    """HTML-escape a value for safe interpolation into the report template.

    Parameters
    ----------
    t : object
        Value to escape. ``None`` is treated as the empty string, and non-string
        values (a list or number sometimes emitted by a local LLM) are coerced
        to a string first, so optional or drifted fields never raise.

    Returns
    -------
    str
        The input with ``&``, ``<``, ``>`` and quotes escaped by
        :func:`html.escape`.

    Examples
    --------
    >>> esc('a & b')
    'a &amp; b'
    >>> esc(None)
    ''
    >>> esc(['x', 'y'])
    'x y'
    """
    if not isinstance(t, str):
        # A local LLM may hand a field back as a dict/list/number instead of a
        # string. as_text pulls the human text out (e.g. {"texte": "…"} -> "…") so
        # the page never leaks raw JSON like {'texte': …}. None -> "".
        t = as_text(t)
    return html.escape(t)


def render_html(
    transcript: list[dict], syn: dict, out_path: str, slide_sync: dict | None = None
) -> str:
    """Render a self-contained interactive HTML report and copy its assets.

    Parameters
    ----------
    transcript : list[dict]
        Ordered utterances, each with ``"speaker"``, ``"t0"`` (start seconds) and
        ``"text"``. Used to build the transcript tab and its per-speaker filters.
    slide_sync : dict, optional
        Slide-sync payload from :func:`notes_helper.slides.build_slide_sync`
        (``{"slides": [png…], "timeline": [{t0,t1,slide,score}…]}``). When present, a
        slide panel is rendered next to the player and follows the audio **by content**:
        the timeline maps each moment to the slide being discussed, so an out-of-order
        deck (slides revisited in any order, e.g. 0→14→7→2→25) still shows the right one.
        The timeline is inlined into the page (no ``fetch``, so it works from ``file://``);
        only the PNGs are external. ``None`` (default) renders no slide panel.
    syn : dict
        The synthesis dictionary. Requires ``"meta"`` and ``"speakers"``; the
        optional ``"resume"``, ``"points_cles"``, ``"decisions"``, ``"actions"``,
        ``"chapitres"``, ``"themes"`` and ``"citations"`` keys populate their
        respective tabs. ``meta`` may also carry ``"audio_sources"``/``"audio"``
        to enable the audio player.
    out_path : str
        Destination path for the ``.html`` file. Its parent directory is created
        if needed, and the vendored ``assets/`` directory is copied alongside it.

    Returns
    -------
    str
        ``out_path``, returned unchanged on success.

    Notes
    -----
    All user-supplied text is passed through :func:`esc` before interpolation.
    Speakers are assigned stable colours by index from
    :data:`notes_helper.config.SPK_COLORS`, wrapping around when there are more
    speakers than colours. The large ``doc`` f-string is template text (HTML +
    CSS + JavaScript) and is written to disk verbatim.
    """
    meta = syn["meta"]
    speakers = syn["speakers"]

    # Report language is DISCOVERED, not assumed: the dominant language of the transcript
    # text (majority vote). GUI labels are then pulled from the i18n catalog in that
    # language. An explicit meta["lang"] wins if the caller set one.
    lang = meta.get("lang") or _i18n.resolve_language(
        texts=[u.get("text", "") for u in transcript[:300] if u.get("text")]
    )
    g = lambda mid: _i18n.gui(mid, lang)  # noqa: E731 - terse label accessor for the template

    def spk_name(sid: str) -> str:
        """Return a speaker's display name, falling back to the id itself."""
        return speakers.get(sid, {}).get("name", sid)

    spk_ids = list(speakers.keys())
    # Assign each speaker a stable colour by position, wrapping the palette so
    # more speakers than colours still render distinctly-ish.
    color_of = {sid: SPK_COLORS[i % len(SPK_COLORS)] for i, sid in enumerate(spk_ids)}
    # Reverse index so citations that reference a speaker by name can be mapped
    # back to a speaker id (and thus a colour).
    name_to_sid = {info["name"]: sid for sid, info in speakers.items()}

    def resolve_spk(ref: str) -> str:
        """Resolve a speaker reference (id or name) to a canonical speaker id."""
        return ref if ref in speakers else name_to_sid.get(ref, "")

    # Participant chips shown in the header, coloured per speaker.
    part_chips = "".join(
        f'<span class="inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium text-white" '
        f'style="background:{color_of.get(sid, "#555")}">'
        f'<span class="h-2 w-2 rounded-full bg-white/80"></span>{esc(info["name"])}'
        f'<span class="opacity-80 font-normal">· {esc(info.get("role", ""))}</span></span>'
        for sid, info in speakers.items()
    )

    def ul(items: list, cls: str = "space-y-2") -> str:
        """Render a list of strings as a styled ``<ul>`` of escaped ``<li>`` items."""
        lis = "".join(
            f'<li class="flex gap-3"><span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span><span>{esc(x)}</span></li>'
            for x in items
        )
        return f'<ul class="{cls}">{lis}</ul>'

    resume_html = "".join(
        f'<p class="mb-4 leading-relaxed">{esc(p)}</p>' for p in syn.get("resume", [])
    )
    keypoints_html = ul(syn.get("points_cles", []))

    def dec_ctx(d: dict) -> str:
        """Render a decision's optional context as a muted paragraph (or nothing)."""
        c = d.get("contexte", "")
        return f'<p class="text-sm text-slate-500 mt-1">{esc(c)}</p>' if c else ""

    dec_html = "".join(
        '<div class="rounded-xl border border-slate-200 dark:border-slate-700 p-4">'
        '<div class="flex items-start gap-3"><span class="mt-0.5 text-emerald-600 dark:text-emerald-400">✓</span>'
        f'<div><p class="font-medium">{esc(d.get("decision", ""))}</p>{dec_ctx(d)}</div></div></div>'
        for d in syn.get("decisions", [])
    )

    # Actions render as a two-column table (action + who); the missing responsable
    # falls back to a dash. No due-date column — deadlines are not tracked here.
    act_rows = "".join(
        f'<tr class="border-b border-slate-100 dark:border-slate-800">'
        f'<td class="py-3 pr-4 align-top">{esc(a.get("action", ""))}</td>'
        f'<td class="py-3 align-top whitespace-nowrap"><span class="rounded-full bg-slate-100 dark:bg-slate-800 px-2.5 py-1 text-sm">{esc(a.get("responsable", "—"))}</span></td></tr>'
        for a in syn.get("actions", [])
    )
    actions_html = (
        '<div class="overflow-x-auto"><table class="w-full text-left"><thead>'
        '<tr class="border-b-2 border-slate-200 dark:border-slate-700 text-sm uppercase tracking-wide text-slate-500">'
        f'<th class="py-2 pr-4 font-semibold">{g("col_action")}</th><th class="py-2 font-semibold">{g("col_owner")}</th>'
        "</tr></thead>"
        f"<tbody>{act_rows}</tbody></table></div>"
    )

    # Chapter buttons carry a data-t timestamp so JS can seek the player to them.
    chap_html = "".join(
        f'<button class="chapter group flex w-full items-start gap-4 rounded-xl border border-slate-200 dark:border-slate-700 p-4 text-left transition hover:border-emerald-400 hover:bg-emerald-50/50 dark:hover:bg-emerald-900/10" data-t="{c.get("t", 0)}">'
        f'<span class="mt-0.5 shrink-0 rounded-lg bg-slate-900 dark:bg-slate-700 px-2.5 py-1 font-mono text-xs text-white">{_hhmmss(c.get("t", 0))}</span>'
        f'<span><span class="block font-medium group-hover:text-emerald-700 dark:group-hover:text-emerald-400">{esc(c.get("titre", ""))}</span>'
        f'<span class="mt-1 block text-sm text-slate-500">{esc(c.get("resume", ""))}</span></span></button>'
        for c in syn.get("chapitres", [])
    )

    themes_html = "".join(
        f'<div class="rounded-xl border border-slate-200 dark:border-slate-700 p-5">'
        f'<h3 class="mb-2 font-semibold text-emerald-700 dark:text-emerald-400">{esc(t.get("theme", ""))}</h3>'
        f"{ul(t.get('points', []))}</div>"
        for t in syn.get("themes", [])
    )

    def quote_html(q: dict) -> str:
        """Render one citation as a coloured blockquote with speaker attribution."""
        sid = resolve_spk(q.get("speaker", ""))
        col = color_of.get(sid, "#555")
        who = spk_name(sid) or q.get("speaker", "")
        ts = f" · {_hhmmss(q['t'])}" if q.get("t") is not None else ""
        return (
            f'<figure class="rounded-xl border-l-4 p-4 bg-slate-50 dark:bg-slate-800/50" style="border-color:{col}">'
            f'<blockquote class="italic">« {esc(q.get("texte", ""))} »</blockquote>'
            f'<figcaption class="mt-2 text-sm text-slate-500">— {esc(who)}{esc(ts)}</figcaption></figure>'
        )

    quotes_html = "".join(quote_html(q) for q in syn.get("citations", []))

    # Transcript rows: each utterance carries data-spk and a lowercased data-text
    # so the client-side speaker filter and search can operate without re-parsing.
    tr_rows = []
    for u in transcript:
        sid = u["speaker"]
        tr_rows.append(
            f'<div class="utt flex gap-3 py-2 -mx-2 px-2 rounded-lg scroll-mt-24 transition-colors" data-spk="{sid}" data-t="{u["t0"]}" data-text="{esc(u["text"]).lower()}">'
            f'<button class="ts shrink-0 font-mono text-xs text-slate-400 hover:text-emerald-600" data-t="{u["t0"]}">{_hhmmss(u["t0"])}</button>'
            f'<div><span class="mr-2 font-semibold" style="color:{color_of.get(sid, "#555")}">{esc(spk_name(sid))}</span>'
            f"<span>{esc(u['text'])}</span></div></div>"
        )
    transcript_html = "".join(tr_rows)
    spk_filter_btns = "".join(
        f'<button class="spk-filter rounded-full border px-3 py-1 text-sm font-medium" data-spk="{sid}" '
        f'style="border-color:{color_of.get(sid, "#555")};color:{color_of.get(sid, "#555")}">{esc(info["name"])}</button>'
        for sid, info in speakers.items()
    )

    # Audio sources: prefer an explicit list, else synthesise one from a single
    # audio path; the player block is omitted entirely when there is no audio.
    sources = meta.get("audio_sources") or (
        [{"src": meta["audio"], "type": "audio/mpeg"}] if meta.get("audio") else []
    )
    src_tags = "".join(f'<source src="{esc(s["src"])}" type="{esc(s["type"])}">' for s in sources)
    audio_block = (
        f'<audio id="player" controls preload="none" class="w-full">{src_tags}'
        f'<a href="{esc(sources[0]["src"])}">{esc(g("download_audio"))}</a></audio>'
        if src_tags
        else ""
    )

    # Slide-sync panel: an <img> that the player swaps to the content-matched slide on
    # every timeupdate. The timeline is inlined into the script below; only the PNGs are
    # external files. Rendered only when a slide_sync payload with pages is supplied.
    has_slides = bool(slide_sync and slide_sync.get("slides"))
    first_slide = esc(slide_sync["slides"][0]) if has_slides else ""
    slidesync_js = json.dumps(slide_sync) if has_slides else "null"
    slide_block = (
        '<figure id="slide-panel" class="mt-6 no-print">'
        f'<a id="slide-link" href="{first_slide}" target="_blank" rel="noopener">'
        f'<img id="slide" src="{first_slide}" data-idx="0" alt="{esc(g("slide_alt"))}" '
        'class="w-full rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm bg-white">'
        '</a>'
        f'<figcaption id="slide-cap" class="mt-2 text-center text-xs text-slate-400">{esc(g("slide"))} 1</figcaption>'
        '</figure>'
        if has_slides
        else ""
    )

    # Tab definitions: (id, label, pre-rendered inner HTML). The transcript tab's
    # inner HTML is None here and assembled below because it needs the search UI.
    tabs = [
        ("resume", g("tab_summary"), f'<div class="prose-lg max-w-none">{resume_html}</div>'),
        ("points", g("tab_keypoints"), keypoints_html),
        ("decisions", g("tab_decisions"), f'<div class="space-y-3">{dec_html}</div>'),
        ("actions", g("tab_actions"), actions_html),
        ("chapitres", g("tab_chapters"), f'<div class="space-y-3">{chap_html}</div>'),
        ("themes", g("tab_themes"), f'<div class="grid gap-4 md:grid-cols-2">{themes_html}</div>'),
        ("citations", g("tab_quotes"), f'<div class="grid gap-4 md:grid-cols-2">{quotes_html}</div>'),
        ("transcript", g("tab_transcript"), None),
    ]
    # The first tab is styled active; the rest get the muted/hover treatment.
    tab_btns = "".join(
        f'<button class="tab-btn whitespace-nowrap border-b-2 px-4 py-3 text-sm font-medium transition '
        f'{"border-emerald-600 text-emerald-700 dark:text-emerald-400" if i == 0 else "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}" '
        f'data-tab="{tid}">{esc(label)}</button>'
        for i, (tid, label, _) in enumerate(tabs)
    )

    def panel(tid: str, inner: str, first: bool) -> str:
        """Wrap a tab's inner HTML in its ``<section>``; hide all but the first."""
        return f'<section id="tab-{tid}" class="tab-panel {"" if first else "hidden"}">{inner}</section>'

    panels = []
    for i, (tid, _label, inner) in enumerate(tabs):
        if tid == "transcript":
            # Assemble the transcript panel lazily: search box + speaker filters +
            # the pre-rendered rows.
            inner = (
                '<div class="mb-4 flex flex-wrap items-center gap-3">'
                f'<input id="search" type="search" placeholder="{esc(g("search_placeholder"))}" '
                'class="w-full max-w-xs rounded-lg border border-slate-300 dark:border-slate-600 bg-transparent px-3 py-2 text-sm">'
                f'<div class="flex flex-wrap gap-2">{spk_filter_btns}</div>'
                f'<button id="reset-filter" class="text-sm text-slate-400 underline">{esc(g("show_all"))}</button></div>'
                f'<div id="transcript" class="divide-y divide-slate-100 dark:divide-slate-800">{transcript_html}</div>'
            )
        panels.append(panel(tid, inner, i == 0))
    panels_html = "".join(panels)

    # ------------------------------------------------------------------------
    # TEMPLATE TEXT — the full HTML document, including its CSS and JavaScript.
    # Everything inside this f-string is emitted verbatim to the output file;
    # the ``document...`` calls and CSS below are browser code, not Python.
    # ------------------------------------------------------------------------
    doc = f"""<!DOCTYPE html>
<html lang="{lang}" class="scroll-smooth">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(meta["titre"])} — {esc(g("report_kicker"))}</title>
<script src="assets/tailwind.js"></script>
<script>tailwind.config={{darkMode:'media',theme:{{extend:{{fontFamily:{{sans:['Roboto','system-ui','sans-serif'],serif:['"Roboto Serif"','Georgia','serif'],mono:['"Roboto Mono"','monospace']}}}}}}}}</script>
<link href="assets/fonts.css" rel="stylesheet">
<style>
  body{{font-family:'Roboto',system-ui,sans-serif}}
  h1,h2,h3{{font-family:'Roboto Serif',Georgia,serif}}
  @media print{{.no-print{{display:none!important}}.tab-panel{{display:block!important}}.tab-panel{{page-break-before:always}}}}
  .spk-filter.active{{color:#fff!important}}
</style>
</head>
<body class="bg-slate-50 text-slate-800 dark:bg-slate-900 dark:text-slate-100">
<header class="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
  <div class="mx-auto max-w-4xl px-6 py-8">
    <p class="mb-2 text-sm font-medium uppercase tracking-wider text-emerald-600 dark:text-emerald-400">{esc(g("report_kicker"))}</p>
    <h1 class="text-3xl font-bold sm:text-4xl">{esc(meta["titre"])}</h1>
    <div class="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-500">
      <span>📅 {esc(meta.get("date", ""))}</span>
      <span>🕘 {esc(meta.get("horaire", ""))}</span>
      <span>📍 {esc(meta.get("lieu", ""))}</span>
      <span>⏱️ {esc(meta.get("duree", ""))}</span>
    </div>
    <div class="mt-5 flex flex-wrap gap-2">{part_chips}</div>
    {f'<div class="mt-6 no-print">{audio_block}</div>' if audio_block else ""}
    {slide_block}
  </div>
</header>
<nav class="no-print sticky top-0 z-10 border-b border-slate-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur">
  <div class="mx-auto max-w-4xl overflow-x-auto px-4"><div class="flex">{tab_btns}</div></div>
</nav>
<main class="mx-auto max-w-4xl px-6 py-8">{panels_html}</main>
<script>
const player=document.getElementById('player');
function seek(t){{if(!player)return;player.currentTime=t;player.play();if(player.scrollIntoView)player.scrollIntoView({{behavior:'smooth',block:'start'}});}}
document.querySelectorAll('.ts,.chapter').forEach(el=>el.addEventListener('click',()=>seek(parseFloat(el.dataset.t))));

// Time cursor: as the audio plays, highlight the utterance currently being spoken
// so the transcript scrolls with the sound (kept lightweight — a linear scan of
// the pre-sorted start times on each timeupdate).
const uttEls=Array.from(document.querySelectorAll('.utt'));
const uttTimes=uttEls.map(u=>parseFloat(u.dataset.t)||0);
let curUtt=-1;
const CUR_CLS=['bg-emerald-50','dark:bg-emerald-900/20'];
function highlightAt(t){{
  let i=uttTimes.length-1;
  while(i>0&&uttTimes[i]>t)i--;
  if(i===curUtt)return;
  if(curUtt>=0&&uttEls[curUtt])uttEls[curUtt].classList.remove(...CUR_CLS);
  curUtt=i;
  const el=uttEls[i];
  if(el){{el.classList.add(...CUR_CLS);el.scrollIntoView({{behavior:'smooth',block:'nearest'}});}}
}}
player&&player.addEventListener('timeupdate',()=>highlightAt(player.currentTime));

// Slide-sync: swap the slide panel to whatever slide the current moment is *about*.
// SLIDESYNC.timeline spans are contiguous and content-ordered (not chronological), so
// the current slide is simply the last span whose start is <= t — big out-of-order
// jumps (0→14→7→2→25) just work.
const SLIDESYNC={slidesync_js};
const slideImg=document.getElementById('slide');
const slideLink=document.getElementById('slide-link');
const slideCap=document.getElementById('slide-cap');
function syncSlideAt(t){{
  if(!SLIDESYNC||!slideImg)return;
  const tl=SLIDESYNC.timeline;if(!tl||!tl.length)return;
  let cur=0;for(let i=0;i<tl.length;i++){{if(tl[i].t0<=t)cur=i;else break;}}
  const sp=tl[cur];if(!sp)return;
  if(String(sp.slide)===slideImg.dataset.idx)return;
  slideImg.dataset.idx=sp.slide;
  const src=SLIDESYNC.slides[sp.slide];
  slideImg.src=src;if(slideLink)slideLink.href=src;
  if(slideCap)slideCap.textContent='{esc(g("slide"))} '+(sp.slide+1)+' / '+SLIDESYNC.slides.length;
}}
player&&SLIDESYNC&&player.addEventListener('timeupdate',()=>syncSlideAt(player.currentTime));

const btns=document.querySelectorAll('.tab-btn'),panels=document.querySelectorAll('.tab-panel');
btns.forEach(b=>b.addEventListener('click',()=>{{
  btns.forEach(x=>{{x.classList.remove('border-emerald-600','text-emerald-700','dark:text-emerald-400');x.classList.add('border-transparent','text-slate-500');}});
  b.classList.add('border-emerald-600','text-emerald-700','dark:text-emerald-400');b.classList.remove('border-transparent','text-slate-500');
  panels.forEach(p=>p.classList.add('hidden'));
  document.getElementById('tab-'+b.dataset.tab).classList.remove('hidden');
}}));
const search=document.getElementById('search');
const utts=()=>document.querySelectorAll('.utt');
let activeSpk=new Set();
function applyFilter(){{
  const q=(search?.value||'').toLowerCase();
  utts().forEach(u=>{{
    const okText=!q||u.dataset.text.includes(q);
    const okSpk=activeSpk.size===0||activeSpk.has(u.dataset.spk);
    u.style.display=(okText&&okSpk)?'':'none';
  }});
}}
search&&search.addEventListener('input',applyFilter);
document.querySelectorAll('.spk-filter').forEach(b=>b.addEventListener('click',()=>{{
  const s=b.dataset.spk;
  if(activeSpk.has(s)){{activeSpk.delete(s);b.classList.remove('active');b.style.background='';}}
  else{{activeSpk.add(s);b.classList.add('active');b.style.background=b.style.borderColor;}}
  applyFilter();
}}));
document.getElementById('reset-filter')?.addEventListener('click',()=>{{
  activeSpk.clear();document.querySelectorAll('.spk-filter').forEach(b=>{{b.classList.remove('active');b.style.background='';}});
  if(search)search.value='';applyFilter();
}});
</script>
</body></html>"""

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    # Copy vendored assets next to the report -> fully self-contained, zero egress.
    # Skipped only in the degenerate case where source and destination coincide.
    dst_assets = os.path.join(out_dir, "assets")
    if os.path.abspath(_ASSETS) != os.path.abspath(dst_assets):
        shutil.copytree(_ASSETS, dst_assets, dirs_exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path
