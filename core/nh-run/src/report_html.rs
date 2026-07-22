//! Interactive, self-contained HTML report for a [`Report`] — the "notes-helper-style"
//! artifact: a sticky audio player, the synthesis with clickable timestamps, and the full
//! diarized transcript where **every line is a cursor** — click it (or any timestamp) and the
//! audio seeks there; as the audio plays the current line highlights and scrolls into view.
//!
//! It is deliberately dependency-free (inline CSS + vanilla JS, no build step, no network) so
//! the report opens anywhere by double-click. The audio is referenced by relative path — the
//! caller writes an **optimized MP3** next to this file (never a WAV). When slides are present
//! they render in a panel that follows the same timeline. This renderer is generalizable: it
//! is driven entirely by the report data, with nothing specific to any one recording.

use nh_core::model::{Report, Summary};

/// A slide to feature alongside the talk: a PNG path (relative to the HTML) and the time it
/// starts being presented. Slides are shown in the order the talk reaches them.
pub struct Slide {
    /// Relative path to the slide PNG.
    pub png: String,
    /// When this slide starts being presented, in seconds.
    pub t0: f64,
}

/// Optional extras to weave into the report (all generalizable, driven by what the input
/// folder actually contains).
#[derive(Default)]
pub struct Extras {
    /// Slides (PDF rasterized to PNGs) synced to the timeline, if any were detected.
    pub slides: Vec<Slide>,
    /// Relative path to the original slide deck (PDF) offered for download, if any.
    pub pdf_download: Option<String>,
}

/// HTML-escape a string for safe insertion into text/attribute context.
fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(c),
        }
    }
    out
}

/// Format seconds as `H:MM:SS` (compact; hours drop to a single digit).
fn hms(t: f64) -> String {
    let s = t.max(0.0) as u64;
    format!("{}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}

/// Render the full interactive report. `audio_rel` is the relative path to the optimized MP3.
pub fn render(report: &Report, audio_rel: &str, extras: &Extras) -> String {
    let mut h = String::new();
    h.push_str("<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">");
    h.push_str("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">");
    h.push_str(&format!("<title>{}</title>", esc(&report.title)));
    h.push_str(STYLE);
    h.push_str("</head><body>");

    // Header + sticky player.
    h.push_str(&format!("<header><h1>{}</h1></header>", esc(&report.title)));
    h.push_str(&format!(
        "<div id=\"player\"><audio id=\"audio\" controls preload=\"metadata\" src=\"{}\"></audio>\
         <span id=\"now\" class=\"muted\">—</span></div>",
        esc(audio_rel)
    ));

    h.push_str("<main>");

    // Speakers.
    if !report.transcript.speakers.is_empty() {
        h.push_str("<p class=\"speakers\"><strong>Locuteurs :</strong> ");
        let mut parts: Vec<String> = report
            .transcript
            .speakers
            .iter()
            .map(|s| format!("{} <span class=\"muted\">({})</span>", esc(s.id.as_str()), hms(s.speaking_time_s)))
            .collect();
        parts.sort();
        h.push_str(&parts.join(" · "));
        h.push_str("</p>");
    }

    // Slides panel (only when detected) — follows the same timeline as the transcript.
    if !extras.slides.is_empty() {
        h.push_str("<section id=\"slides\"><h2>Slides</h2>");
        h.push_str("<figure><img id=\"slide-img\" alt=\"slide\"><figcaption id=\"slide-cap\" class=\"muted\"></figcaption></figure>");
        if let Some(pdf) = &extras.pdf_download {
            h.push_str(&format!(
                "<p><a class=\"dl\" href=\"{}\" download>⬇ Télécharger le PDF original</a></p>",
                esc(pdf)
            ));
        }
        h.push_str("</section>");
    } else if let Some(pdf) = &extras.pdf_download {
        h.push_str(&format!(
            "<p><a class=\"dl\" href=\"{}\" download>⬇ Télécharger le PDF original</a></p>",
            esc(pdf)
        ));
    }

    render_summary(&mut h, &report.summary);

    // Transcript — each line is a clickable cursor into the audio.
    h.push_str("<section id=\"transcript\"><h2>Transcription</h2><div id=\"lines\">");
    for u in &report.transcript.utterances {
        let lang = u
            .language
            .as_deref()
            .map(|l| format!(" <span class=\"lang\">{}</span>", esc(l)))
            .unwrap_or_default();
        h.push_str(&format!(
            "<p class=\"u\" data-t=\"{:.2}\"><button class=\"ts\" data-t=\"{:.2}\">{}</button> \
             <span class=\"sp\">{}</span>{}: <span class=\"tx\">{}</span></p>",
            u.t0,
            u.t0,
            hms(u.t0),
            esc(u.speaker.as_str()),
            lang,
            esc(u.text.trim())
        ));
    }
    h.push_str("</div></section></main>");

    // Data + behaviour.
    h.push_str(&script(extras));
    h.push_str("</body></html>");
    h
}

/// Render the synthesis block: overview, points, decisions, actions, chapters, quotes.
/// Chapters and quotes carry timestamps, rendered as seek buttons.
fn render_summary(h: &mut String, s: &Summary) {
    h.push_str("<section id=\"summary\"><h2>Synthèse</h2>");
    if !s.overview.trim().is_empty() {
        h.push_str(&format!("<p class=\"overview\">{}</p>", esc(s.overview.trim())));
    }
    if !s.key_points.is_empty() {
        h.push_str("<h3>Points clés</h3><ul>");
        for p in &s.key_points {
            h.push_str(&format!("<li>{}</li>", esc(p.trim())));
        }
        h.push_str("</ul>");
    }
    if !s.decisions.is_empty() {
        h.push_str("<h3>Décisions</h3><ul>");
        for d in &s.decisions {
            h.push_str(&format!("<li>{}</li>", esc(d.trim())));
        }
        h.push_str("</ul>");
    }
    if !s.actions.is_empty() {
        h.push_str("<h3>Actions</h3><table><thead><tr><th>Action</th><th>Responsable</th></tr></thead><tbody>");
        for a in &s.actions {
            let who = a.assignee.as_ref().map(|w| w.as_str()).unwrap_or("—");
            h.push_str(&format!(
                "<tr><td>{}</td><td>{}</td></tr>",
                esc(a.description.trim()),
                esc(who)
            ));
        }
        h.push_str("</tbody></table>");
    }
    if !s.chapters.is_empty() {
        h.push_str("<h3>Chapitres</h3><ul class=\"chapters\">");
        for c in &s.chapters {
            h.push_str(&format!(
                "<li><button class=\"ts\" data-t=\"{:.2}\">{}</button> {}</li>",
                c.t0,
                hms(c.t0),
                esc(c.title.trim())
            ));
        }
        h.push_str("</ul>");
    }
    if !s.quotes.is_empty() {
        h.push_str("<h3>Citations</h3><ul class=\"quotes\">");
        for q in &s.quotes {
            h.push_str(&format!(
                "<li>« {} » — <span class=\"sp\">{}</span> <button class=\"ts\" data-t=\"{:.2}\">{}</button></li>",
                esc(q.text.trim()),
                esc(q.speaker.as_str()),
                q.t0,
                hms(q.t0)
            ));
        }
        h.push_str("</ul>");
    }
    h.push_str("</section>");
}

/// The behaviour script: click-to-seek on any `.ts`/`.u`, highlight + autoscroll the current
/// line on playback, and (when present) swap the slide image to the one for the current time.
fn script(extras: &Extras) -> String {
    // Emit the slide timeline as a JSON-ish array literal (path,t0), sorted by t0.
    let mut slides = extras.slides.iter().collect::<Vec<_>>();
    slides.sort_by(|a, b| a.t0.partial_cmp(&b.t0).unwrap_or(std::cmp::Ordering::Equal));
    let slide_js: String = slides
        .iter()
        .map(|s| format!("{{png:\"{}\",t:{:.2}}}", esc(&s.png), s.t0))
        .collect::<Vec<_>>()
        .join(",");

    format!(
        "<script>\n\
        const audio=document.getElementById('audio');\n\
        const now=document.getElementById('now');\n\
        const lines=[...document.querySelectorAll('.u')];\n\
        const slides=[{slide_js}];\n\
        const slideImg=document.getElementById('slide-img');\n\
        const slideCap=document.getElementById('slide-cap');\n\
        const times=lines.map(l=>parseFloat(l.dataset.t));\n\
        function fmt(t){{t=Math.max(0,t|0);const h=(t/3600|0),m=((t%3600)/60|0),s=t%60;return h+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');}}\n\
        let cur=-1,curSlide=-1;\n\
        function refresh(t){{now.textContent=fmt(t);\n\
          let i=-1;for(let k=0;k<times.length;k++){{if(times[k]<=t+0.001)i=k;else break;}}\n\
          if(i!==cur){{if(cur>=0)lines[cur].classList.remove('on');if(i>=0){{lines[i].classList.add('on');lines[i].scrollIntoView({{block:'center',behavior:'smooth'}});}}cur=i;}}\n\
          if(slides.length){{let j=-1;for(let k=0;k<slides.length;k++){{if(slides[k].t<=t+0.001)j=k;else break;}}\n\
            if(j!==curSlide&&j>=0){{slideImg.src=slides[j].png;slideCap.textContent='Slide '+(j+1)+' · '+fmt(slides[j].t);curSlide=j;}}}}\n\
        }}\n\
        function seek(t){{audio.currentTime=t;refresh(t);audio.play().catch(()=>{{}});}}\n\
        document.addEventListener('click',e=>{{const b=e.target.closest('.ts');if(b){{seek(parseFloat(b.dataset.t));return;}}const u=e.target.closest('.u');if(u){{seek(parseFloat(u.dataset.t));}}}});\n\
        audio.addEventListener('timeupdate',()=>refresh(audio.currentTime));\n\
        audio.addEventListener('seeked',()=>refresh(audio.currentTime));\n\
        if(slides.length){{slideImg.src=slides[0].png;slideCap.textContent='Slide 1 · '+fmt(slides[0].t);}}\n\
        </script>"
    )
}

/// Inline stylesheet — Roboto, light/dark aware, sticky player, readable transcript.
const STYLE: &str = "<style>\
:root{--bg:#fff;--fg:#1a1a1a;--muted:#6b7280;--accent:#2563eb;--on:#fef3c7;--card:#f8fafc;--line:#e5e7eb}\
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e5e7eb;--muted:#9ca3af;--accent:#60a5fa;--on:#3b3620;--card:#161a22;--line:#232833}}\
*{box-sizing:border-box}body{margin:0;font-family:Roboto,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg);line-height:1.55}\
header{padding:1.2rem 1.5rem .3rem}h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.15rem;border-bottom:1px solid var(--line);padding-bottom:.3rem;margin-top:2rem}h3{font-size:1rem;margin:1.2rem 0 .4rem}\
#player{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--line);padding:.6rem 1.5rem;display:flex;align-items:center;gap:1rem}\
#player audio{flex:1;max-width:640px}main{padding:0 1.5rem 4rem;max-width:900px}\
.muted{color:var(--muted)}.speakers{color:var(--fg)}.lang{font-size:.75rem;color:var(--muted);border:1px solid var(--line);border-radius:4px;padding:0 .3rem}\
.ts{font-family:Roboto Mono,ui-monospace,monospace;font-size:.8rem;color:var(--accent);background:none;border:1px solid var(--line);border-radius:5px;padding:.05rem .35rem;cursor:pointer}\
.ts:hover{border-color:var(--accent)}\
#lines .u{padding:.25rem .4rem;border-radius:6px;margin:.1rem 0;cursor:pointer}\
#lines .u:hover{background:var(--card)}#lines .u.on{background:var(--on)}\
.sp{font-weight:600}table{border-collapse:collapse;width:100%}th,td{border:1px solid var(--line);padding:.4rem .6rem;text-align:left;font-size:.92rem}\
figure{margin:0}#slide-img{max-width:100%;border:1px solid var(--line);border-radius:8px;background:var(--card)}\
.dl{color:var(--accent)}ul{margin:.3rem 0}li{margin:.15rem 0}\
</style>";
