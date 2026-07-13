#!/usr/bin/env python3
"""
Build a Plaud-style single-file HTML report from:
  - transcript.json   [{t0,t1,speaker,text}]  (speaker = S0..S3)
  - synthese.json     meeting metadata + all synthesis blocks (authored)

Output: reunion_rapport.html  (self-contained, offline, print-friendly)
"""
import datetime
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPT = os.environ.get("TR_PATH", os.path.join(HERE, "transcript.json"))
SYNTHESE = os.environ.get("SYN_PATH", os.path.join(HERE, "synthese.json"))
OUT = os.environ.get("OUT_PATH", os.path.join(HERE, "reunion_rapport.html"))

SPK_COLORS = ["#2f6f5e", "#b45309", "#1d4ed8", "#9333ea", "#be123c", "#0f766e"]


def hhmmss(s):
    s = int(s)
    return f"{s//3600:d}:{(s%3600)//60:02d}:{s%60:02d}"


def esc(t):
    return html.escape(t or "")


def main():
    tr = json.load(open(TRANSCRIPT, encoding="utf-8"))
    syn = json.load(open(SYNTHESE, encoding="utf-8"))
    meta = syn["meta"]
    speakers = syn["speakers"]  # {"S0": {"name":..., "role":...}, ...}

    def spk_name(sid):
        return speakers.get(sid, {}).get("name", sid)

    spk_ids = list(speakers.keys())
    color_of = {sid: SPK_COLORS[i % len(SPK_COLORS)] for i, sid in enumerate(spk_ids)}
    # citations may reference a speaker by S-id OR by canonical name
    name_to_sid = {info["name"]: sid for sid, info in speakers.items()}

    def resolve_spk(ref):
        return ref if ref in speakers else name_to_sid.get(ref, "")

    # ---- speaker chips ----
    part_chips = "".join(
        f'<span class="inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium text-white" '
        f'style="background:{color_of.get(sid, "#555")}">'
        f'<span class="h-2 w-2 rounded-full bg-white/80"></span>{esc(info["name"])}'
        f'<span class="opacity-80 font-normal">· {esc(info.get("role",""))}</span></span>'
        for sid, info in speakers.items()
    )

    # ---- generic list renderer ----
    def ul(items, cls="space-y-2"):
        lis = "".join(f'<li class="flex gap-3"><span class="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span><span>{esc(x)}</span></li>' for x in items)
        return f'<ul class="{cls}">{lis}</ul>'

    # ---- résumé ----
    resume_html = "".join(f'<p class="mb-4 leading-relaxed">{esc(p)}</p>' for p in syn["resume"])

    # ---- key points ----
    keypoints_html = ul(syn["points_cles"])

    # ---- decisions ----
    def dec_ctx(d):
        c = d.get("contexte", "")
        return f'<p class="text-sm text-slate-500 mt-1">{esc(c)}</p>' if c else ""
    dec_html = "".join(
        '<div class="rounded-xl border border-slate-200 dark:border-slate-700 p-4">'
        '<div class="flex items-start gap-3"><span class="mt-0.5 text-emerald-600 dark:text-emerald-400">✓</span>'
        f'<div><p class="font-medium">{esc(d["decision"])}</p>{dec_ctx(d)}</div></div></div>'
        for d in syn["decisions"]
    )

    # ---- actions (table) ----
    act_rows = "".join(
        f'<tr class="border-b border-slate-100 dark:border-slate-800">'
        f'<td class="py-3 pr-4 align-top">{esc(a["action"])}</td>'
        f'<td class="py-3 pr-4 align-top whitespace-nowrap"><span class="rounded-full bg-slate-100 dark:bg-slate-800 px-2.5 py-1 text-sm">{esc(a.get("responsable","—"))}</span></td>'
        f'<td class="py-3 align-top whitespace-nowrap text-sm text-slate-500">{esc(a.get("echeance","—"))}</td>'
        f'</tr>'
        for a in syn["actions"]
    )
    actions_html = (
        '<div class="overflow-x-auto"><table class="w-full text-left"><thead>'
        '<tr class="border-b-2 border-slate-200 dark:border-slate-700 text-sm uppercase tracking-wide text-slate-500">'
        '<th class="py-2 pr-4 font-semibold">Action</th><th class="py-2 pr-4 font-semibold">Responsable</th>'
        '<th class="py-2 font-semibold">Échéance</th></tr></thead>'
        f'<tbody>{act_rows}</tbody></table></div>'
    )

    # ---- chapters / timeline ----
    chap_html = "".join(
        f'<button class="chapter group flex w-full items-start gap-4 rounded-xl border border-slate-200 dark:border-slate-700 p-4 text-left transition hover:border-emerald-400 hover:bg-emerald-50/50 dark:hover:bg-emerald-900/10" data-t="{c["t"]}">'
        f'<span class="mt-0.5 shrink-0 rounded-lg bg-slate-900 dark:bg-slate-700 px-2.5 py-1 font-mono text-xs text-white">{hhmmss(c["t"])}</span>'
        f'<span><span class="block font-medium group-hover:text-emerald-700 dark:group-hover:text-emerald-400">{esc(c["titre"])}</span>'
        f'<span class="mt-1 block text-sm text-slate-500">{esc(c.get("resume",""))}</span></span></button>'
        for c in syn["chapitres"]
    )

    # ---- themes ----
    themes_html = "".join(
        f'<div class="rounded-xl border border-slate-200 dark:border-slate-700 p-5">'
        f'<h3 class="mb-2 font-semibold text-emerald-700 dark:text-emerald-400">{esc(t["theme"])}</h3>'
        f'{ul(t["points"])}</div>'
        for t in syn["themes"]
    )

    # ---- quotes ----
    def quote_html(q):
        sid = resolve_spk(q.get("speaker", ""))
        col = color_of.get(sid, "#555")
        who = spk_name(sid) or q.get("speaker", "")
        ts = f' · {hhmmss(q["t"])}' if q.get("t") is not None else ""
        return (f'<figure class="rounded-xl border-l-4 p-4 bg-slate-50 dark:bg-slate-800/50" style="border-color:{col}">'
                f'<blockquote class="italic">« {esc(q["texte"])} »</blockquote>'
                f'<figcaption class="mt-2 text-sm text-slate-500">— {esc(who)}{esc(ts)}</figcaption></figure>')
    quotes_html = "".join(quote_html(q) for q in syn.get("citations", []))

    # ---- transcript ----
    tr_rows = []
    for u in tr:
        sid = u["speaker"]
        tr_rows.append(
            f'<div class="utt flex gap-3 py-2" data-spk="{sid}" data-text="{esc(u["text"]).lower()}">'
            f'<button class="ts shrink-0 font-mono text-xs text-slate-400 hover:text-emerald-600" data-t="{u["t0"]}">{hhmmss(u["t0"])}</button>'
            f'<div><span class="mr-2 font-semibold" style="color:{color_of.get(sid, "#555")}">{esc(spk_name(sid))}</span>'
            f'<span>{esc(u["text"])}</span></div></div>'
        )
    transcript_html = "".join(tr_rows)
    spk_filter_btns = "".join(
        f'<button class="spk-filter rounded-full border px-3 py-1 text-sm font-medium" data-spk="{sid}" '
        f'style="border-color:{color_of.get(sid,"#555")};color:{color_of.get(sid,"#555")}">{esc(info["name"])}</button>'
        for sid, info in speakers.items()
    )

    # multi-source <audio>: browser picks the first it supports (Opus small,
    # MP3 universal fallback). Falls back to single meta.audio if provided.
    sources = meta.get("audio_sources") or ([{"src": meta["audio"], "type": "audio/mpeg"}] if meta.get("audio") else [])
    src_tags = "".join(f'<source src="{esc(s["src"])}" type="{esc(s["type"])}">' for s in sources)
    audio_block = (
        f'<audio id="player" controls preload="none" class="w-full">{src_tags}'
        f'<a href="{esc(sources[0]["src"])}">Télécharger l\'audio</a></audio>'
        if src_tags else ""
    )

    tabs = [
        ("resume", "Résumé", f'<div class="prose-lg max-w-none">{resume_html}</div>'),
        ("points", "Points clés", keypoints_html),
        ("decisions", "Décisions", f'<div class="space-y-3">{dec_html}</div>'),
        ("actions", "Actions", actions_html),
        ("chapitres", "Chapitres", f'<div class="space-y-3">{chap_html}</div>'),
        ("themes", "Thèmes", f'<div class="grid gap-4 md:grid-cols-2">{themes_html}</div>'),
        ("citations", "Citations", f'<div class="grid gap-4 md:grid-cols-2">{quotes_html}</div>'),
        ("transcript", "Transcript", None),  # special
    ]

    tab_btns = "".join(
        f'<button class="tab-btn whitespace-nowrap border-b-2 px-4 py-3 text-sm font-medium transition '
        f'{"border-emerald-600 text-emerald-700 dark:text-emerald-400" if i==0 else "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}" '
        f'data-tab="{tid}">{esc(label)}</button>'
        for i, (tid, label, _) in enumerate(tabs)
    )

    def panel(tid, inner, first):
        return (f'<section id="tab-{tid}" class="tab-panel {"" if first else "hidden"}">'
                f'{inner}</section>')

    panels = []
    for i, (tid, _label, inner) in enumerate(tabs):
        if tid == "transcript":
            inner = (
                '<div class="mb-4 flex flex-wrap items-center gap-3">'
                '<input id="search" type="search" placeholder="Rechercher dans le transcript…" '
                'class="w-full max-w-xs rounded-lg border border-slate-300 dark:border-slate-600 bg-transparent px-3 py-2 text-sm">'
                f'<div class="flex flex-wrap gap-2">{spk_filter_btns}</div>'
                '<button id="reset-filter" class="text-sm text-slate-400 underline">tout afficher</button>'
                '</div>'
                f'<div id="transcript" class="divide-y divide-slate-100 dark:divide-slate-800">{transcript_html}</div>'
            )
        panels.append(panel(tid, inner, i == 0))
    panels_html = "".join(panels)

    datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = f"""<!DOCTYPE html>
<html lang="fr" class="scroll-smooth">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(meta["titre"])} — Compte-rendu</title>
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
    <p class="mb-2 text-sm font-medium uppercase tracking-wider text-emerald-600 dark:text-emerald-400">Compte-rendu de réunion</p>
    <h1 class="text-3xl font-bold sm:text-4xl">{esc(meta["titre"])}</h1>
    <div class="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-500">
      <span>📅 {esc(meta.get("date",""))}</span>
      <span>🕘 {esc(meta.get("horaire",""))}</span>
      <span>📍 {esc(meta.get("lieu",""))}</span>
      <span>⏱️ {esc(meta.get("duree",""))}</span>
    </div>
    <div class="mt-5 flex flex-wrap gap-2">{part_chips}</div>
    {f'<div class="mt-6 no-print">{audio_block}</div>' if audio_block else ''}
  </div>
</header>

<nav class="no-print sticky top-0 z-10 border-b border-slate-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur">
  <div class="mx-auto max-w-4xl overflow-x-auto px-4"><div class="flex">{tab_btns}</div></div>
</nav>

<main class="mx-auto max-w-4xl px-6 py-8">{panels_html}</main>

<script>
const player=document.getElementById('player');
function seek(t){{if(!player)return;player.currentTime=t;player.play();window.scrollTo({{top:0,behavior:'smooth'}});}}
document.querySelectorAll('.ts,.chapter').forEach(el=>el.addEventListener('click',()=>seek(parseFloat(el.dataset.t))));
// tabs
const btns=document.querySelectorAll('.tab-btn'),panels=document.querySelectorAll('.tab-panel');
btns.forEach(b=>b.addEventListener('click',()=>{{
  btns.forEach(x=>{{x.classList.remove('border-emerald-600','text-emerald-700','dark:text-emerald-400');x.classList.add('border-transparent','text-slate-500');}});
  b.classList.add('border-emerald-600','text-emerald-700','dark:text-emerald-400');b.classList.remove('border-transparent','text-slate-500');
  panels.forEach(p=>p.classList.add('hidden'));
  document.getElementById('tab-'+b.dataset.tab).classList.remove('hidden');
}}));
// transcript search + speaker filter
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

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"wrote {OUT} ({len(doc)//1024} KB, {len(tr)} utterances)")


if __name__ == "__main__":
    main()
