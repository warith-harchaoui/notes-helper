//! `nh-run` — a real offline end-to-end run of the Notes Helper native core.
//!
//! Wires the actual engines behind the ports and drives one recording all the way to a
//! [`Report`]: **ffmpeg decode → sherpa multi-speaker diarization → whisper.cpp ASR →
//! local Ollama synthesis**, then writes `report.json`, `transcript.json` and a readable
//! `report.md`. Models and knobs come from the environment so the binary carries no
//! machine paths:
//!
//! ```bash
//! NH_WHISPER_MODEL=/path/ggml-base.bin \
//! NH_SHERPA_SEG=/path/community1-segmentation.onnx \
//! NH_SHERPA_EMB=/path/nemo_en_titanet_large.onnx \
//! NH_OLLAMA_MODEL=gemma3:4b NH_LANG=français NH_SPEAKERS=2 \
//! cargo run --release -- <input-audio> <output-dir>
//! ```

use std::path::PathBuf;
use std::time::Instant;

use std::collections::BTreeMap;

use nh_core::blockwise::BlockwiseDiarizer;
use nh_core::model::{
    mean_confidence, MeetingContext, Report, SessionId, Speaker, SpeakerId, Transcript, Utterance,
};
use nh_core::ports::{AsrEngine, AudioSource, DiarizationEngine, Synthesizer};
use nh_core::router::{select_diarization, DiarizationQuery};
use nh_io::FfmpegSource;
use nh_sherpa::SherpaDiarizer;
use nh_synth::ollama::OllamaClient;
use nh_synth::LocalSynthesizer;
use nh_whisper::WhisperAsr;

mod report_html;
use report_html::{Extras, Slide};

/// Read an env var or fall back to `default`.
fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// Format seconds as `HH:MM:SS`.
fn hhmmss(t: f64) -> String {
    let s = t.max(0.0) as u64;
    format!("{:02}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}

/// Largest context kept in the synthesis prompt. The `context.md` files are curated and
/// small (a few KB); this caps a stray large file so it never crowds a local model's window.
const CONTEXT_MAX_CHARS: usize = 8_000;

/// Load the curated meeting context for this run: `NH_CONTEXT` if set, otherwise a
/// `context.md` sitting in the input's own directory (the user keeps everything for a
/// recording in one folder). Missing or empty → an empty context. The text is capped at
/// [`CONTEXT_MAX_CHARS`] on a char boundary so it never splits a UTF-8 sequence.
fn load_context(input: &str) -> MeetingContext {
    // Prefer an explicit path; else look next to the input file.
    let path: Option<PathBuf> = match std::env::var("NH_CONTEXT") {
        Ok(p) if !p.trim().is_empty() => Some(PathBuf::from(p)),
        _ => PathBuf::from(input)
            .parent()
            .map(|dir| dir.join("context.md"))
            .filter(|p| p.is_file()),
    };
    let Some(path) = path else {
        eprintln!("[nh-run] context: none (no NH_CONTEXT, no context.md beside the input)");
        return MeetingContext::empty();
    };
    match std::fs::read_to_string(&path) {
        Ok(mut notes) => {
            if notes.chars().count() > CONTEXT_MAX_CHARS {
                // Truncate on a char boundary, not a byte index, to keep valid UTF-8.
                let end = notes
                    .char_indices()
                    .nth(CONTEXT_MAX_CHARS)
                    .map(|(i, _)| i)
                    .unwrap_or(notes.len());
                notes.truncate(end);
                eprintln!(
                    "[nh-run] context: {} (truncated to {CONTEXT_MAX_CHARS} chars)",
                    path.display()
                );
            } else {
                eprintln!("[nh-run] context: {} ({} chars)", path.display(), notes.len());
            }
            let mut c = MeetingContext::empty();
            c.notes = notes;
            c
        }
        Err(e) => {
            eprintln!("[nh-run] context: failed to read {} ({e}) — using none", path.display());
            MeetingContext::empty()
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Print the core's tracing events (router plan, per-block diarization progress). INFO by
    // default; override with RUST_LOG. `try_init` so a double-init never aborts the run.
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_target(false)
        .try_init();

    let args: Vec<String> = std::env::args().collect();
    let input = args.get(1).cloned().ok_or("usage: nh-run <input-audio> <output-dir>")?;
    let outdir = PathBuf::from(args.get(2).cloned().ok_or("usage: nh-run <input-audio> <output-dir>")?);
    std::fs::create_dir_all(&outdir)?;

    // Render-only: re-build the interactive HTML + MP3 + slides for an already-computed run
    // (its `report.json`), without re-doing diarization/ASR/synthesis. Lets a long run's
    // result gain the rich report without paying for it twice.
    if let Ok(from) = std::env::var("NH_RENDER_FROM") {
        if !from.trim().is_empty() {
            let report: Report = serde_json::from_slice(&std::fs::read(&from)?)?;
            finalize(&report, &input, &outdir)?;
            return Ok(());
        }
    }

    let whisper_model = env_or("NH_WHISPER_MODEL", "");
    let seg = env_or("NH_SHERPA_SEG", "");
    let emb = env_or("NH_SHERPA_EMB", "");
    // gemma3:4b is the project's sanctioned synth model: fast on M-series, JSON-reliable, and
    // beefier than the old qwen2.5:3b default. Override with NH_OLLAMA_MODEL=qwen3:8b (the 8B
    // ceiling) for maximum quality when the transcript warrants it.
    let ollama_model = env_or("NH_OLLAMA_MODEL", "gemma3:4b");
    let lang = env_or("NH_LANG", "français");
    let session_id = env_or("NH_SESSION_ID", "session");
    let speakers: Option<i32> = std::env::var("NH_SPEAKERS").ok().and_then(|s| s.parse().ok());

    eprintln!("[nh-run] input={input}");
    eprintln!("[nh-run] outdir={}", outdir.display());
    eprintln!("[nh-run] whisper={whisper_model}");
    eprintln!("[nh-run] sherpa seg={seg}");
    eprintln!("[nh-run] sherpa emb={emb}");
    eprintln!("[nh-run] ollama={ollama_model} lang={lang} speakers={speakers:?}");

    // whisper is pre-loaded eagerly (fail fast on a bad path); sherpa loads its ONNX per call.
    let t_engines = Instant::now();
    // Generalizable tuning knobs (not per-recording): window, reference budget, room-tone gap,
    // discovery duration floor, and the discovery clustering threshold.
    let env_f64 = |k: &str| std::env::var(k).ok().and_then(|s| s.parse::<f64>().ok());
    let window_s = env_f64("NH_WINDOW_S").unwrap_or(180.0);
    let min_speaker_s = env_f64("NH_MIN_SPEAKER_S").unwrap_or(15.0);
    let disc_threshold = env_f64("NH_DISCOVER_THRESHOLD").unwrap_or(0.8) as f32;

    let whisper = WhisperAsr::load(&whisper_model)?;
    eprintln!("[nh-run] engines ready in {:?}", t_engines.elapsed());

    // Local, sovereign synthesis — a single cloud call would break the thesis.
    let synth = LocalSynthesizer::new(OllamaClient::new(&ollama_model), &lang);

    // We orchestrate the offline stages explicitly (rather than Session::run_offline) so a
    // long run prints per-stage timing and per-turn ASR progress: router (logged) → diarize →
    // per-turn ASR → synth.

    // 1) Decode the whole recording to 16 kHz mono.
    let t = Instant::now();
    let audio = FfmpegSource::new(&input).load()?;
    let dur = audio.duration_s();
    eprintln!("[nh-run] decoded {:.0}s of audio in {:?}", dur, t.elapsed());

    // Diarization is always O(n) block-wise: bounded windows anchored across time by in-RAM
    // per-speaker references + harvested room tone (no embeddings). Whole-buffer clustering
    // would be ~O(n²) and blow up on hours of audio.
    //
    // The **speaker count is discovered as an integer** (it is part of diarization): diarize
    // the first window in auto-detect and count the speakers who clear `min_speaker_s`, which
    // filters the auto engine's over-segmentation fragments. We then run every block PINNED to
    // that count — a forced cluster count is what makes cross-window anchoring clean (auto
    // per-block would keep re-minting the same voices as "new"). A caller can still override
    // the count with NH_SPEAKERS.
    let k = match speakers {
        Some(n) => n.max(1) as usize,
        None => {
            // Discover K by probing several windows spread across the recording, not just the
            // first: a talk often opens with one speaker and only later brings in the others, so
            // one window would under-count. Each probe counts the speakers holding the floor in
            // that window; the recording has at least as many speakers as the busiest window, so
            // we take the max. (A window is bounded, so each probe is O(1); a handful is cheap.)
            let probe = SherpaDiarizer::new(&seg, &emb).with_threshold(disc_threshold);
            let n_probes = 6usize;
            let mut k = 1usize;
            for i in 0..n_probes {
                // Spread probe starts across the span, keeping a full window inside the audio.
                let last_start = (dur - window_s).max(0.0);
                let start = if n_probes > 1 {
                    last_start * (i as f64) / (n_probes as f64 - 1.0)
                } else {
                    0.0
                };
                let win = audio.slice(start, (start + window_s).min(dur));
                let ki = nh_core::blockwise::estimate_speaker_count(&probe, &win, min_speaker_s)?;
                if ki > k {
                    k = ki;
                }
                eprintln!("[nh-run]   probe {}/{n_probes} @ {start:.0}s → {ki} speaker(s)", i + 1);
                // Stop the whole slice from being one window on short inputs.
                if last_start == 0.0 {
                    break;
                }
            }
            eprintln!("[nh-run] discovered speaker count = {k} (max over {n_probes} probes, threshold {disc_threshold}, floor {min_speaker_s}s)");
            k
        }
    };
    let sherpa = SherpaDiarizer::new(&seg, &emb).with_num_clusters(k as i32);
    let mut bw = BlockwiseDiarizer::new(sherpa, k).with_window_s(window_s);
    if let Some(v) = env_f64("NH_REF_S") {
        bw = bw.with_ref_budget_s(v);
    }
    if let Some(v) = env_f64("NH_GAP_S") {
        bw = bw.with_gap_s(v);
    }
    let diarizer: Box<dyn DiarizationEngine> = Box::new(bw);

    // Router: log the plan the study justifies for this length (the diarizer is sherpa).
    let plan = select_diarization(DiarizationQuery {
        duration_s: Some(dur),
        max_speakers: speakers.map(|n| n as u32),
        ..Default::default()
    });
    eprintln!(
        "[nh-run] router plan: {}/{} (DER {}, RTF {}) — {}",
        plan.mode.as_str(),
        plan.backend.as_str(),
        plan.expected_der,
        plan.expected_rtf,
        plan.reason
    );

    // 2) Diarize the whole buffer into speaker turns (sherpa, multi-speaker).
    let t = Instant::now();
    let segments = diarizer.diarize(&audio)?;
    eprintln!("[nh-run] diarized {} turns in {:?}", segments.len(), t.elapsed());

    // 3) Transcribe each turn, attaching its speaker; print progress along the way.
    let t = Instant::now();
    let mut utterances: Vec<Utterance> = Vec::new();
    let mut speaking: BTreeMap<String, f64> = BTreeMap::new();
    for (i, seg) in segments.iter().enumerate() {
        let sub = audio.slice(seg.t0, seg.t1);
        // A single turn that fails ASR must never sink a multi-hour run — log it and move on.
        let parts = match whisper.transcribe(&sub) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("[nh-run]   ASR skipped turn {i} ({:.0}-{:.0}s): {e}", seg.t0, seg.t1);
                continue;
            }
        };
        let text = parts
            .iter()
            .map(|u| u.text.trim())
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join(" ");
        if !text.is_empty() {
            *speaking.entry(seg.speaker.as_str().to_string()).or_insert(0.0) +=
                (seg.t1 - seg.t0).max(0.0);
            // Fold this turn's whisper segments into one duration-weighted confidence
            // (offline path). `None` if the backend reported none (keeps online honest).
            let confidence = mean_confidence(&parts);
            utterances.push(Utterance {
                t0: seg.t0,
                t1: seg.t1,
                speaker: seg.speaker.clone(),
                text,
                words: Vec::new(),
                language: None,
                confidence,
            });
        }
        if (i + 1) % 25 == 0 || i + 1 == segments.len() {
            eprintln!(
                "[nh-run]   ASR {}/{} turns ({:.0}s audio) — {:?} elapsed",
                i + 1,
                segments.len(),
                seg.t1,
                t.elapsed()
            );
        }
    }
    eprintln!("[nh-run] ASR: {} utterances in {:?}", utterances.len(), t.elapsed());
    // Report the overall ASR confidence (mean over turns that carry one) as a quick
    // health signal: a low figure warns that the transcript — and any synthesis grounded
    // on it — deserves a closer look before it is trusted.
    if let Some(overall) = mean_confidence(&utterances) {
        eprintln!("[nh-run] mean ASR confidence: {:.1}%", overall * 100.0);
    }

    let speakers = speaking
        .into_iter()
        .map(|(label, secs)| {
            let mut s = Speaker::new(SpeakerId::new(label));
            s.speaking_time_s = secs;
            s
        })
        .collect();
    let transcript = Transcript { utterances, speakers };

    // Checkpoint the transcript to disk NOW, before the (external, fallible) synthesis step —
    // the diarization + ASR that produced it can be hours of work and must survive an Ollama
    // hiccup. If synthesis later fails, this file is enough to re-render or re-synthesize.
    std::fs::write(
        outdir.join("transcript.json"),
        serde_json::to_vec_pretty(&transcript)?,
    )?;
    eprintln!("[nh-run] checkpointed transcript.json ({} utterances)", transcript.utterances.len());

    // Transcribe-only: hand the diarized transcript to a downstream renderer (e.g. the Python
    // `build_report`, which owns synthesis, slides and the polished HTML) and stop here — this
    // crate's job in that pipeline is just the O(n) diarization + ASR.
    if std::env::var("NH_TRANSCRIBE_ONLY").map(|v| v == "1").unwrap_or(false) {
        eprintln!("[nh-run] transcribe-only: stopping after transcript.json");
        return Ok(());
    }

    // 4) Local synthesis over the whole transcript (map/reduce via Ollama).
    // Ground it in the recording's own curated context (proper nouns, framing): an explicit
    // `NH_CONTEXT` file, else a `context.md` next to the input. Guidance only — the prompts
    // still forbid inventing facts. `NH_SKIP_SYNTH=1` skips it (empty summary), so the
    // interactive report ships fast when the local LLM is the slow link.
    let t = Instant::now();
    let context = load_context(&input);
    let summary = if std::env::var("NH_SKIP_SYNTH").map(|v| v == "1").unwrap_or(false) {
        eprintln!("[nh-run] synthesis skipped (NH_SKIP_SYNTH=1)");
        nh_core::model::Summary::empty()
    } else {
        let s = synth.synthesize(&transcript, &context)?;
        eprintln!("[nh-run] synthesized in {:?}", t.elapsed());
        s
    };

    let report = Report {
        session_id: SessionId::new(session_id),
        title: format!("Notes — {}", input),
        context,
        transcript,
        summary,
    };
    eprintln!(
        "[nh-run] report ready: {} utterances, {} speakers",
        report.transcript.utterances.len(),
        report.transcript.speakers.len()
    );

    // Persist: full report + transcript as JSON, plus a readable Markdown report, then the
    // rich interactive artifacts (optimized MP3, HTML player, slides).
    std::fs::write(outdir.join("report.json"), serde_json::to_vec_pretty(&report)?)?;
    std::fs::write(
        outdir.join("transcript.json"),
        serde_json::to_vec_pretty(&report.transcript)?,
    )?;
    std::fs::write(outdir.join("report.md"), render_markdown(&report))?;
    finalize(&report, &input, &outdir)?;
    Ok(())
}

/// Produce the shippable artifacts on top of the JSON/MD: an **optimized MP3** for the player
/// (never a WAV — the pipeline is in-RAM, only the compressed audio is written), the detected
/// **slides** (any PDF in the input's folder → PNGs), and the interactive **HTML** report.
fn finalize(
    report: &Report,
    input: &str,
    outdir: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    // 1) Optimized MP3 next to the report (mono, speech-friendly bitrate).
    let mp3 = outdir.join("audio.mp3");
    match encode_mp3(input, &mp3) {
        Ok(()) => eprintln!("[nh-run] wrote optimized audio.mp3"),
        Err(e) => eprintln!("[nh-run] audio.mp3 skipped ({e})"),
    }
    let audio_rel = if mp3.is_file() { "audio.mp3" } else { input };

    // 2) Slides — generalizable: if the input folder holds a PDF, rasterize it to PNGs and
    //    sync them across the talk's timeline.
    let extras = detect_slides(input, outdir, report).unwrap_or_default();

    // 3) Interactive HTML — named index.html so the output folder opens as a web page.
    std::fs::write(outdir.join("index.html"), report_html::render(report, audio_rel, &extras))?;
    eprintln!(
        "[nh-run] wrote report.json, transcript.json, report.md, index.html to {}",
        outdir.display()
    );
    Ok(())
}

/// Transcode any source media to an optimized MP3 (mono, 64 kbps — plenty for speech, ~1/20th
/// of a 16 kHz WAV). Uses the system ffmpeg; returns an error if it is missing or fails.
fn encode_mp3(input: &str, out: &std::path::Path) -> Result<(), Box<dyn std::error::Error>> {
    let status = std::process::Command::new("ffmpeg")
        .args(["-y", "-i", input, "-ac", "1", "-b:a", "64k", "-vn"])
        .arg(out)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("ffmpeg exited with {status}").into())
    }
}

/// Detect a slide deck (a PDF sitting in the input's own folder), rasterize each page to a PNG
/// under `outdir/slides/`, copy the PDF for download, and sync the slides across the recording
/// so each is featured when the talk reaches it. Requires `pdftoppm` (poppler); silently
/// yields no slides if it is absent or there is no PDF — the report still renders.
fn detect_slides(input: &str, outdir: &std::path::Path, report: &Report) -> Option<Extras> {
    let dir = std::path::Path::new(input).parent()?;
    // First PDF in the folder (skip any we might have written ourselves).
    let pdf = std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()).find_map(|e| {
        let p = e.path();
        (p.extension().and_then(|x| x.to_str()).map(|x| x.eq_ignore_ascii_case("pdf")) == Some(true))
            .then_some(p)
    })?;

    // Only a **landscape** PDF is a slide deck. A portrait PDF is a document (a manuscript, a
    // paper, notes) that happens to sit in the folder as context — not slides to feature. This
    // one aspect-ratio test generalizes: presentations are wide (16:9 / 4:3), documents are tall.
    if !pdf_is_landscape(&pdf) {
        eprintln!("[nh-run] slides: {} is portrait — treated as a document, not slides", pdf.display());
        return None;
    }

    let slides_dir = outdir.join("slides");
    std::fs::create_dir_all(&slides_dir).ok()?;
    // Rasterize: pdftoppm -png -r 120 deck.pdf outdir/slides/slide  → slide-1.png, slide-2.png…
    let ok = std::process::Command::new("pdftoppm")
        .args(["-png", "-r", "120"])
        .arg(&pdf)
        .arg(slides_dir.join("slide"))
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if !ok {
        eprintln!("[nh-run] slides: pdftoppm unavailable or failed — no slides featured");
        return None;
    }
    // Collect the produced PNGs in page order.
    let mut pngs: Vec<String> = std::fs::read_dir(&slides_dir)
        .ok()?
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".png"))
        .collect();
    pngs.sort_by(|a, b| natural_cmp(a, b));
    if pngs.is_empty() {
        return None;
    }

    // Copy the PDF for download.
    let pdf_name = pdf.file_name()?.to_string_lossy().into_owned();
    let _ = std::fs::copy(&pdf, outdir.join(&pdf_name));

    // Sync each slide to the moment the talk is actually about it (content-based, so slides
    // follow the discussion — not a rigid even split). Falls back to even spacing if the PDF
    // carries no extractable text.
    let n = pngs.len();
    let times = sync_slides_by_content(&pdf, report, n);
    let slides = pngs
        .into_iter()
        .zip(times)
        .map(|(png, t0)| Slide { png: format!("slides/{png}"), t0 })
        .collect();
    eprintln!("[nh-run] slides: featured {n} PNG(s) from {pdf_name} (content-synced)");
    Some(Extras {
        slides,
        pdf_download: Some(pdf_name),
    })
}

/// Whether a PDF's pages are landscape (wider than tall) — the signature of a slide deck, as
/// opposed to a portrait document. Reads `pdfinfo`'s "Page size: W x H pts" line; on any doubt
/// (pdfinfo missing / unparseable) it returns `false`, so an unknown PDF is left as a document
/// rather than rasterized wholesale.
fn pdf_is_landscape(pdf: &std::path::Path) -> bool {
    let out = match std::process::Command::new("pdfinfo").arg(pdf).output() {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).into_owned(),
        _ => return false,
    };
    for line in out.lines() {
        if let Some(rest) = line.strip_prefix("Page size:") {
            // e.g. "  720 x 405.014 pts" — take the two numbers around the 'x'.
            let nums: Vec<f64> = rest
                .split(|c: char| !(c.is_ascii_digit() || c == '.'))
                .filter(|s| !s.is_empty())
                .filter_map(|s| s.parse().ok())
                .collect();
            if nums.len() >= 2 {
                return nums[0] > nums[1];
            }
        }
    }
    false
}

/// Content-based slide→time alignment. Extract each page's text (`pdftotext`), tokenize it and
/// the transcript into overlapping time chunks, and place each slide at the chunk whose words
/// overlap it most. Slides with no textual match are interpolated between their matched
/// neighbours (keeping page order monotonic); if `pdftotext` yields nothing, fall back to an
/// even split. Returns one start-time (seconds) per page.
fn sync_slides_by_content(pdf: &std::path::Path, report: &Report, n_pages: usize) -> Vec<f64> {
    let span = report.transcript.utterances.last().map(|u| u.t1).unwrap_or(0.0).max(1.0);
    let even = |i: usize| span * (i as f64) / (n_pages.max(1) as f64);

    // Per-page text via pdftotext (pages separated by form-feed).
    let pages_text: Vec<String> = std::process::Command::new("pdftotext")
        .arg(pdf)
        .arg("-")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).split('\u{0c}').map(str::to_string).collect())
        .unwrap_or_default();
    if pages_text.len() < n_pages {
        return (0..n_pages).map(even).collect();
    }

    // Transcript → ~45 s chunks, each a token set with its start time.
    let chunk_len = 45.0;
    let mut chunks: Vec<(f64, std::collections::HashSet<String>)> = Vec::new();
    for u in &report.transcript.utterances {
        let idx = (u.t0 / chunk_len) as usize;
        while chunks.len() <= idx {
            chunks.push((chunks.len() as f64 * chunk_len, std::collections::HashSet::new()));
        }
        for tok in tokens(&u.text) {
            chunks[idx].1.insert(tok);
        }
    }
    if chunks.is_empty() {
        return (0..n_pages).map(even).collect();
    }

    // Best-matching chunk per page (by token overlap); None when nothing overlaps.
    let mut matched: Vec<Option<f64>> = Vec::with_capacity(n_pages);
    for page in pages_text.iter().take(n_pages) {
        let ptoks = tokens(page);
        let mut best_t = None;
        let mut best_score = 0usize;
        for (t0, ctoks) in &chunks {
            let score = ptoks.iter().filter(|w| ctoks.contains(*w)).count();
            if score > best_score {
                best_score = score;
                best_t = Some(*t0);
            }
        }
        matched.push(best_t);
    }

    // Fill unmatched pages by interpolating between matched neighbours (monotone in page
    // order), anchoring the ends at 0 and the span.
    let mut times = vec![0.0f64; n_pages];
    let mut prev_i = 0usize;
    let mut prev_t = 0.0f64;
    for i in 0..n_pages {
        if let Some(t) = matched[i] {
            // Interpolate the gap (prev_i, i).
            for j in (prev_i + 1)..i {
                let frac = (j - prev_i) as f64 / (i - prev_i) as f64;
                times[j] = prev_t + (t - prev_t) * frac;
            }
            times[i] = t;
            prev_i = i;
            prev_t = t;
        }
    }
    // Tail after the last matched page.
    for j in (prev_i + 1)..n_pages {
        let frac = (j - prev_i) as f64 / (n_pages - prev_i) as f64;
        times[j] = prev_t + (span - prev_t) * frac;
    }
    // Keep non-decreasing (a later page never jumps before an earlier one).
    for i in 1..n_pages {
        if times[i] < times[i - 1] {
            times[i] = times[i - 1];
        }
    }
    times
}

/// Tokenize text into lower-case content words (length ≥ 4, dropping the commonest French and
/// English function words) for cheap content matching.
fn tokens(s: &str) -> std::collections::HashSet<String> {
    const STOP: &[&str] = &[
        "dans", "pour", "avec", "cette", "cette", "être", "etre", "plus", "mais", "nous", "vous",
        "elle", "ils", "elles", "leur", "leurs", "sont", "fait", "faire", "tout", "tous", "toute",
        "comme", "aussi", "donc", "alors", "cela", "quand", "notre", "votre", "that", "this", "with",
        "from", "have", "they", "there", "which", "about", "would", "these", "their", "quelque",
    ];
    s.split(|c: char| !c.is_alphanumeric())
        .map(|w| w.to_lowercase())
        .filter(|w| w.chars().count() >= 4 && !STOP.contains(&w.as_str()))
        .collect()
}

/// Compare filenames so `slide-2.png` sorts before `slide-10.png` (numeric-aware).
fn natural_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    fn key(s: &str) -> (usize, String) {
        let digits: String = s.chars().filter(|c| c.is_ascii_digit()).collect();
        (digits.parse::<usize>().unwrap_or(0), s.to_string())
    }
    key(a).cmp(&key(b))
}

/// Render a [`Report`] as a readable Markdown document (the Rust core's HTML renderer is
/// milestone M2; this keeps the offline run's output human-readable today).
fn render_markdown(report: &Report) -> String {
    let mut m = String::new();
    m.push_str(&format!("# {}\n\n", report.title));

    // Speakers with their floor time.
    if !report.transcript.speakers.is_empty() {
        m.push_str("**Locuteurs :** ");
        let mut parts: Vec<String> = report
            .transcript
            .speakers
            .iter()
            .map(|s| format!("{} ({})", s.id.as_str(), hhmmss(s.speaking_time_s)))
            .collect();
        parts.sort();
        m.push_str(&parts.join(" · "));
        m.push_str("\n\n");
    }

    let s = &report.summary;
    m.push_str("## Synthèse\n\n");
    if !s.overview.trim().is_empty() {
        m.push_str(s.overview.trim());
        m.push_str("\n\n");
    }
    if !s.key_points.is_empty() {
        m.push_str("### Points clés\n\n");
        for p in &s.key_points {
            m.push_str(&format!("- {}\n", p.trim()));
        }
        m.push('\n');
    }
    if !s.decisions.is_empty() {
        m.push_str("### Décisions\n\n");
        for d in &s.decisions {
            m.push_str(&format!("- {}\n", d.trim()));
        }
        m.push('\n');
    }
    if !s.actions.is_empty() {
        m.push_str("### Actions\n\n");
        for a in &s.actions {
            let who = a.assignee.as_ref().map(|w| w.as_str()).unwrap_or("—");
            let due = a.due.as_deref().unwrap_or("—");
            m.push_str(&format!("- {} — responsable : {who} — échéance : {due}\n", a.description.trim()));
        }
        m.push('\n');
    }
    if !s.chapters.is_empty() {
        m.push_str("### Chapitres\n\n");
        for c in &s.chapters {
            m.push_str(&format!("- `{}` {}\n", hhmmss(c.t0), c.title.trim()));
        }
        m.push('\n');
    }
    if !s.quotes.is_empty() {
        m.push_str("### Citations\n\n");
        for q in &s.quotes {
            m.push_str(&format!("- « {} » — {} `{}`\n", q.text.trim(), q.speaker.as_str(), hhmmss(q.t0)));
        }
        m.push('\n');
    }

    // The full diarized transcript.
    m.push_str("## Transcription\n\n");
    for u in &report.transcript.utterances {
        let lang = u
            .language
            .as_deref()
            .map(|l| format!(" _{l}_"))
            .unwrap_or_default();
        // Flag shaky spans so a reader (and the synthesis) knows what to double-check.
        // Only turns the offline ASR actually scored below the threshold are marked; an
        // unmeasured turn (online/streaming) carries no marker rather than a false alarm.
        let flag = match u.confidence {
            Some(c) if c < LOW_CONFIDENCE => format!(" ⚠️{:.0}%", c * 100.0),
            _ => String::new(),
        };
        m.push_str(&format!(
            "- `{}` **{}**{}{}: {}\n",
            hhmmss(u.t0),
            u.speaker.as_str(),
            lang,
            flag,
            u.text.trim()
        ));
    }
    m
}

/// Confidence below which a transcript span is flagged as worth verifying in the readable
/// report. Chosen conservatively: whisper's mean token probability sits well above this on
/// clean speech, so the marker fires on genuinely doubtful spans (crosstalk, noise, rare
/// proper nouns), not on ordinary text.
const LOW_CONFIDENCE: f32 = 0.6;
