//! `blockwise` — O(n) long-form diarization by anchoring speaker identity with
//! in-RAM per-speaker audio references (no embeddings, no centroids).
//!
//! Why
//! ---
//! A whole-buffer diarizer clusters every speech sub-segment against every other, so its
//! cost grows **super-linearly** with the recording length (the affinity/clustering step is
//! ~O(n²) in the number of turns). Measured on this machine (sherpa-onnx, community-1 seg +
//! TitaNet-large): 2 min → 49 s, 10 min → 297 s — the RTF climbs with duration, and a naive
//! whole-buffer pass on a 4 h recording extrapolates to ~13 h. That is unacceptable.
//!
//! Strategy (no embeddings, no centroids on our side)
//! --------------------------------------------------
//! Process the recording in fixed windows of bounded length, so each diarization call is
//! O(1) and the whole run is **O(n)** in the number of windows. The only hard part is
//! keeping speaker identity consistent *across* windows (window k's local `S0` must be the
//! same person as window 0's `S0`). We solve it without computing a single embedding or
//! centroid ourselves: we **fabricate a primer** by concatenating, for each already-known
//! speaker, a few seconds of *their own* audio harvested from an earlier window — pieces
//! separated by **harvested room tone** (real low-level ambient from the same recording, so
//! the segmentation model cuts cleanly between pieces and never sees unnatural digital
//! silence). We prepend that primer to the window and diarize `[primer | window]` in one
//! call. The diarizer's *own* clustering then places each window turn in the same cluster as
//! the matching reference, and we simply **read back** which local label covered each
//! reference to recover the global identity. Anchoring, for free, from the engine we have.
//!
//! Discovering the speaker count (it is part of diarization)
//! --------------------------------------------------------
//! The number of speakers is **discovered as an integer**, never assumed — determining it is
//! part of what diarization does. In discovery mode the reference set **grows**: the first
//! window is diarized alone (inner engine in auto-detect), and each local speaker whose
//! in-window speech exceeds [`min_speaker_s`](BlockwiseDiarizer) is promoted to a global
//! speaker (given a RAM reference). Every later window is diarized as `[primer | window]`;
//! any window cluster that **no known reference covers** is a candidate new speaker and is
//! promoted — again only if it speaks past the duration floor. That floor is the sole knob
//! against sherpa's known auto-detect over-segmentation (it split a 2-min clip into 16): a
//! spurious singleton never lasts long enough to become a speaker, and a genuinely minor
//! voice (a passing waiter, crowd noise) is folded into the dominant nearby speaker rather
//! than inflating the count. The discovered count is the final size of the reference set.
//! A caller that truly knows the count can still pin it with [`BlockwiseDiarizer::new`].
//!
//! This is a pure orchestrator over any [`DiarizationEngine`]: the heavy model stays in the
//! adapter (sherpa-onnx), the identity logic lives here and is unit-tested against a mock.

use std::collections::{HashMap, HashSet};

use crate::error::Result;
use crate::model::{AudioBuffer, DiarizedSegment, SpeakerId};
use crate::ports::DiarizationEngine;

/// Estimate the speaker count from one representative window, discovering it as an integer.
///
/// Diarize `window` with `engine` in auto-detect (`num_clusters = -1`) and count the speakers
/// whose in-window speech clears `min_speaker_s`. The floor is the guard against the auto
/// engine's over-segmentation: a real participant holds the floor a good stretch, a spurious
/// fragment does not. The returned `k` is then used to run the *pinned* block-wise pass, whose
/// forced cluster count makes cross-window anchoring clean — so the count is discovered, never
/// assumed, yet the diarization stays robust. Always returns at least 1.
///
/// # Errors
/// Propagates any engine failure.
pub fn estimate_speaker_count(
    engine: &dyn DiarizationEngine,
    window: &AudioBuffer,
    min_speaker_s: f64,
) -> Result<usize> {
    let segs = engine.diarize(window)?;
    let mut durs: HashMap<String, f64> = HashMap::new();
    for s in &segs {
        *durs.entry(s.speaker.as_str().to_string()).or_insert(0.0) += (s.t1 - s.t0).max(0.0);
    }
    let k = durs.values().filter(|d| **d >= min_speaker_s).count();
    Ok(k.max(1))
}

/// A window's turns projected to window-local time: `(local_label, t0_s, t1_s)` each.
type Region = Vec<(String, f64, f64)>;
/// Map from a diarizer's per-call local label to the stable global speaker it anchors to.
type SpeakerMap = HashMap<String, SpeakerId>;

/// Default window length (seconds). Each block diarizes `window_s + primer` of audio, so a
/// bounded window keeps every call O(1). ~180 s balances per-call clustering cost against
/// the relative overhead of the primer.
pub const DEFAULT_WINDOW_S: f64 = 180.0;

/// Default per-speaker reference budget (seconds) harvested for the primer. Enough audio for
/// a strong embedder (TitaNet-large) to place the reference in its own cluster, small enough
/// to keep the primer cheap.
pub const DEFAULT_REF_BUDGET_S: f64 = 12.0;

/// Default gap (seconds) of harvested room tone inserted between reference pieces and before
/// the window. Must exceed the segmentation model's `min_duration_off` (0.5 s in the study's
/// config) so the model actually cuts a turn boundary at each gap.
pub const DEFAULT_GAP_S: f64 = 0.8;

/// Default minimum in-window speech (seconds) for a new cluster to be promoted to a speaker
/// during discovery. The primary guard against auto-detect over-segmentation (which splits one
/// voice into several short clusters) and against minor non-participants (crowd, waiter): a
/// real participant speaks a good stretch within *some* window, whereas a spurious fragment
/// stays brief. Set high enough (15 s) that fragments never clear it — measured on real audio,
/// the true speakers ran 30–60 s in their first window while every over-segment fragment stayed
/// under 13 s. Keeping the bar here also bounds the reference set (hence the primer), preserving
/// O(n). Tunable via `NH_MIN_SPEAKER_S`.
pub const DEFAULT_MIN_SPEAKER_S: f64 = 15.0;

/// An O(n) long-form wrapper around any whole-buffer [`DiarizationEngine`].
///
/// See the module docs. Build with [`BlockwiseDiarizer::discovering`] (the default: the
/// speaker count is discovered) or [`BlockwiseDiarizer::new`] (pin a known count); tune the
/// window / reference / gap / duration-floor with the builder methods. The inner engine is
/// the real model (`nh_sherpa::SherpaDiarizer`) or, in tests, a mock. In discovery mode the
/// inner engine must be in auto-detect (`num_clusters = -1`); when a count is pinned the
/// inner engine should be fixed to the same count.
pub struct BlockwiseDiarizer<D: DiarizationEngine> {
    /// The whole-buffer engine driven once per block.
    inner: D,
    /// `Some(k)` pins the speaker count; `None` discovers it (grow the reference set).
    target_speakers: Option<usize>,
    /// Window length in seconds.
    window_s: f64,
    /// Per-speaker reference budget in seconds.
    ref_budget_s: f64,
    /// Room-tone gap length in seconds.
    gap_s: f64,
    /// Duration floor (seconds) to promote a cluster to a speaker in discovery mode.
    min_speaker_s: f64,
}

impl<D: DiarizationEngine> BlockwiseDiarizer<D> {
    /// Wrap `inner` and **discover** the speaker count (the default, recommended path).
    ///
    /// The inner engine must be in auto-detect mode (`num_clusters = -1`).
    pub fn discovering(inner: D) -> Self {
        Self {
            inner,
            target_speakers: None,
            window_s: DEFAULT_WINDOW_S,
            ref_budget_s: DEFAULT_REF_BUDGET_S,
            gap_s: DEFAULT_GAP_S,
            min_speaker_s: DEFAULT_MIN_SPEAKER_S,
        }
    }

    /// Wrap `inner` with a **pinned** speaker count (override — use only when the count is
    /// truly known). The inner engine should be fixed to the same count.
    pub fn new(inner: D, num_speakers: usize) -> Self {
        Self {
            target_speakers: Some(num_speakers.max(1)),
            ..Self::discovering(inner)
        }
    }

    /// Override the window length (seconds). Clamped to a sane minimum.
    #[must_use]
    pub fn with_window_s(mut self, window_s: f64) -> Self {
        self.window_s = window_s.max(10.0);
        self
    }

    /// Override the per-speaker reference budget (seconds).
    #[must_use]
    pub fn with_ref_budget_s(mut self, ref_budget_s: f64) -> Self {
        self.ref_budget_s = ref_budget_s.max(1.0);
        self
    }

    /// Override the room-tone gap length (seconds).
    #[must_use]
    pub fn with_gap_s(mut self, gap_s: f64) -> Self {
        self.gap_s = gap_s.max(0.1);
        self
    }

    /// Override the discovery duration floor (seconds).
    #[must_use]
    pub fn with_min_speaker_s(mut self, min_speaker_s: f64) -> Self {
        self.min_speaker_s = min_speaker_s.max(0.0);
        self
    }
}

impl<D: DiarizationEngine> DiarizationEngine for BlockwiseDiarizer<D> {
    fn diarize(&self, audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
        let dur = audio.duration_s();

        // Short enough to diarize whole-buffer directly: the O(n²) term is negligible under
        // ~1.5 windows and a single call avoids all primer overhead. This also keeps the fast
        // case (mock / tiny input) identical to the plain engine.
        if dur <= self.window_s * 1.5 {
            return self.inner.diarize(audio);
        }

        // Harvest one clip of genuine room tone (the quietest span of the recording): real
        // ambient at the recording's own noise level and spectral character, so every gap we
        // splice in reads as natural silence to the segmentation model.
        let room = harvest_room_tone(audio, self.gap_s);

        match self.target_speakers {
            Some(k) => self.diarize_fixed(audio, &room, k),
            None => self.diarize_discovering(audio, &room),
        }
    }
}

impl<D: DiarizationEngine> BlockwiseDiarizer<D> {
    /// Pinned-count path: block 0 fixes `k` global identities, later windows anchor to them.
    fn diarize_fixed(
        &self,
        audio: &AudioBuffer,
        room: &[f32],
        k: usize,
    ) -> Result<Vec<DiarizedSegment>> {
        let dur = audio.duration_s();
        let block0 = audio.slice(0.0, self.window_s);
        let seg0 = self.inner.diarize(&block0)?;
        let mut out: Vec<DiarizedSegment> = seg0.clone();

        let refs = build_references(&block0, &seg0, k, self.ref_budget_s, self.gap_s, room);
        if refs.is_empty() {
            // Nothing to anchor against — a plain pass beats mislabelling everything.
            return self.inner.diarize(audio);
        }

        let n_blocks = (dur / self.window_s).ceil() as usize;
        let mut t = self.window_s;
        let mut block_idx = 1usize;
        while t < dur {
            let end = (t + self.window_s).min(dur);
            let block = audio.slice(t, end);
            tracing::info!(
                block = block_idx,
                blocks = n_blocks,
                at_s = t,
                speakers = refs.len(),
                turns = out.len(),
                "block-wise diarization progress"
            );
            block_idx += 1;

            let assembled = assemble_primed(&refs, &block, room);
            let segs = self.inner.diarize(&assembled.combined)?;
            let map = read_mapping(&segs, &assembled.ref_spans);
            let fallback = refs.first().map(|(g, _)| g.clone());
            for (label, a, b) in block_region(&segs, assembled.primer_len_s) {
                let speaker = map
                    .get(&label)
                    .cloned()
                    .or_else(|| fallback.clone())
                    .unwrap_or_else(|| SpeakerId::new(label));
                out.push(DiarizedSegment {
                    t0: a + t,
                    t1: b + t,
                    speaker,
                });
            }
            t += self.window_s;
        }
        Ok(merge_adjacent_same_speaker(out))
    }

    /// Discovery path: grow the reference set, promoting a cluster to a speaker only once it
    /// speaks past the duration floor. The discovered count is `refs.len()` at the end.
    fn diarize_discovering(
        &self,
        audio: &AudioBuffer,
        room: &[f32],
    ) -> Result<Vec<DiarizedSegment>> {
        let dur = audio.duration_s();
        let n_blocks = (dur / self.window_s).ceil() as usize;
        let mut refs: Vec<(SpeakerId, Vec<f32>)> = Vec::new();
        let mut next_id = 0usize;
        let mut out: Vec<DiarizedSegment> = Vec::new();

        let mut t = 0.0;
        let mut block_idx = 0usize;
        while t < dur {
            let end = (t + self.window_s).min(dur);
            let block = audio.slice(t, end);
            tracing::info!(
                block = block_idx,
                blocks = n_blocks,
                at_s = t,
                speakers = refs.len(),
                turns = out.len(),
                "block-wise diarization progress (discovering)"
            );
            block_idx += 1;

            // Diarize this window and derive, from the SAME result, (a) the window-region turns
            // and (b) the known local→global map. The map must be read from the full combined
            // segments (the references live in the primer, before the window) BEFORE we project
            // to the region — otherwise no known speaker would ever anchor and the count would
            // explode. Bootstrap (no refs yet) diarizes the bare window and maps nothing.
            let (region, mut map): (Region, SpeakerMap) = if refs.is_empty() {
                let segs = self.inner.diarize(&block)?;
                (block_region(&segs, 0.0), HashMap::new())
            } else {
                let assembled = assemble_primed(&refs, &block, room);
                let segs = self.inner.diarize(&assembled.combined)?;
                let map = read_mapping(&segs, &assembled.ref_spans);
                (block_region(&segs, assembled.primer_len_s), map)
            };

            // Total in-window speech per local label, longest first (deterministic).
            let mut durs: Vec<(String, f64)> = label_durations(&region).into_iter().collect();
            durs.sort_by(|a, b| {
                b.1.partial_cmp(&a.1)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then(a.0.cmp(&b.0))
            });

            // Promote each unmapped label that clears the duration floor to a new speaker.
            let mut promoted_any = false;
            for (label, d) in &durs {
                if map.contains_key(label) || *d < self.min_speaker_s {
                    continue;
                }
                let gid = SpeakerId::new(format!("S{next_id}"));
                next_id += 1;
                let spans: Vec<(f64, f64)> = region
                    .iter()
                    .filter(|(l, _, _)| l == label)
                    .map(|(_, a, b)| (*a, *b))
                    .collect();
                let clip = build_one_reference(&block, &spans, self.ref_budget_s, self.gap_s, room);
                if !clip.is_empty() {
                    refs.push((gid.clone(), clip));
                    map.insert(label.clone(), gid);
                    promoted_any = true;
                    tracing::info!(
                        speaker = next_id - 1,
                        at_s = t,
                        secs = *d,
                        "discovered new speaker"
                    );
                }
            }
            // Bootstrap guarantee: if the very first block promoted nobody (everyone spoke
            // under the floor), promote its single longest label so the run has a speaker.
            if refs.is_empty() && !promoted_any {
                if let Some((label, _)) = durs.first() {
                    let gid = SpeakerId::new(format!("S{next_id}"));
                    next_id += 1;
                    let spans: Vec<(f64, f64)> = region
                        .iter()
                        .filter(|(l, _, _)| l == label)
                        .map(|(_, a, b)| (*a, *b))
                        .collect();
                    let clip =
                        build_one_reference(&block, &spans, self.ref_budget_s, self.gap_s, room);
                    refs.push((gid.clone(), clip));
                    map.insert(label.clone(), gid);
                }
            }

            // Sub-threshold unmapped turns are minor voices — fold them into the window's
            // dominant known speaker rather than invent a speaker or drop the text.
            let fallback = dominant_global(&region, &map);
            for (label, a, b) in &region {
                let speaker = map.get(label).cloned().or_else(|| fallback.clone());
                if let Some(speaker) = speaker {
                    out.push(DiarizedSegment {
                        t0: a + t,
                        t1: b + t,
                        speaker,
                    });
                }
            }
            t += self.window_s;
        }

        // Consolidation. Auto-detect over-segmentation can mint duplicate speakers — fragments
        // of one voice promoted as distinct because, in the block they first appeared, sherpa
        // split them off. Lay every reference end to end (room-tone–separated), diarize that
        // primer once, and union any references that land in the SAME cluster: they are the
        // same person. This collapses the duplicates without a single embedding — the engine's
        // own clustering, applied to the references together, decides who is who.
        if refs.len() > 1 {
            let canon = self.consolidate(&refs, audio.sample_rate, room);
            for s in &mut out {
                if let Some(c) = canon.get(s.speaker.as_str()) {
                    s.speaker = c.clone();
                }
            }
        }
        Ok(merge_adjacent_same_speaker(out))
    }

    /// Merge over-segmented duplicate speakers. Diarize all references concatenated (with
    /// room-tone gaps); references sharing a cluster are the same speaker. Returns a map from
    /// every speaker id to its canonical (lowest-numbered) id.
    fn consolidate(
        &self,
        refs: &[(SpeakerId, Vec<f32>)],
        rate: u32,
        room: &[f32],
    ) -> HashMap<String, SpeakerId> {
        // Build [gap ref0 gap ref1 … gap] and remember each reference's span.
        let mut samples: Vec<f32> = Vec::new();
        let mut spans: Vec<(usize, f64, f64)> = Vec::with_capacity(refs.len());
        for (i, (_, clip)) in refs.iter().enumerate() {
            samples.extend_from_slice(room);
            let s0 = samples.len() as f64 / f64::from(rate);
            samples.extend_from_slice(clip);
            let s1 = samples.len() as f64 / f64::from(rate);
            spans.push((i, s0, s1));
        }
        samples.extend_from_slice(room);
        let buf = AudioBuffer::new(rate, samples);

        let segs = match self.inner.diarize(&buf) {
            Ok(s) => s,
            // If the consolidation pass fails, keep every speaker as-is (identity map).
            Err(_) => return HashMap::new(),
        };

        // Local cluster label covering each reference (max overlap on its span).
        let local_of: Vec<String> = spans
            .iter()
            .map(|(_, s0, s1)| {
                let mut best = String::new();
                let mut best_ov = 0.0f64;
                for seg in &segs {
                    let ov = (seg.t1.min(*s1) - seg.t0.max(*s0)).max(0.0);
                    if ov > best_ov {
                        best_ov = ov;
                        best = seg.speaker.as_str().to_string();
                    }
                }
                best
            })
            .collect();

        // Union references that share a local cluster; the canonical id is the earliest ref
        // (references are in discovery order, so S0 wins over a later duplicate).
        let mut canon: HashMap<String, SpeakerId> = HashMap::new();
        let mut cluster_owner: HashMap<String, SpeakerId> = HashMap::new();
        for (i, (id, _)) in refs.iter().enumerate() {
            let local = &local_of[i];
            if local.is_empty() {
                canon.insert(id.as_str().to_string(), id.clone());
                continue;
            }
            let owner = cluster_owner
                .entry(local.clone())
                .or_insert_with(|| id.clone());
            canon.insert(id.as_str().to_string(), owner.clone());
        }
        canon
    }
}

/// The audio fed to one block's diarization, plus the bookkeeping needed to read identities
/// back out of the result.
struct Primed {
    /// `[gap ref0 gap ref1 … gap | window]` as one buffer.
    combined: AudioBuffer,
    /// For each global speaker, the `[start_s, end_s]` its reference occupies in `combined`.
    ref_spans: Vec<(SpeakerId, f64, f64)>,
    /// Where the window starts in `combined` (end of the primer), in seconds.
    primer_len_s: f64,
}

/// Concatenate the per-speaker references (each already gap-separated internally) into a
/// primer, prefix/separate them with room tone, then append the window. Records each
/// reference's span and the primer length for the read-back.
fn assemble_primed(refs: &[(SpeakerId, Vec<f32>)], block: &AudioBuffer, room: &[f32]) -> Primed {
    let rate = block.sample_rate;
    let mut samples: Vec<f32> = Vec::new();
    let mut ref_spans: Vec<(SpeakerId, f64, f64)> = Vec::with_capacity(refs.len());

    for (speaker, clip) in refs {
        samples.extend_from_slice(room);
        let start_s = samples.len() as f64 / f64::from(rate);
        samples.extend_from_slice(clip);
        let end_s = samples.len() as f64 / f64::from(rate);
        ref_spans.push((speaker.clone(), start_s, end_s));
    }
    samples.extend_from_slice(room);
    let primer_len_s = samples.len() as f64 / f64::from(rate);

    samples.extend_from_slice(&block.samples);
    Primed {
        combined: AudioBuffer::new(rate, samples),
        ref_spans,
        primer_len_s,
    }
}

/// Project a diarization result onto the window region: keep only turns after the primer and
/// shift them to window-local time. Returns `(label, t0_local, t1_local)`.
fn block_region(segs: &[DiarizedSegment], primer_len_s: f64) -> Vec<(String, f64, f64)> {
    let mut out = Vec::new();
    for s in segs {
        if s.t1 <= primer_len_s {
            continue;
        }
        let a = s.t0.max(primer_len_s) - primer_len_s;
        let b = s.t1 - primer_len_s;
        if b > a {
            out.push((s.speaker.as_str().to_string(), a, b));
        }
    }
    out
}

/// Total speech seconds per local label over a window region.
fn label_durations(region: &[(String, f64, f64)]) -> HashMap<String, f64> {
    let mut durs: HashMap<String, f64> = HashMap::new();
    for (label, a, b) in region {
        *durs.entry(label.clone()).or_insert(0.0) += (b - a).max(0.0);
    }
    durs
}

/// The global speaker with the most mapped speech in this window region (the fallback owner
/// for minor, sub-threshold turns). `None` if nothing is mapped yet.
fn dominant_global(
    region: &[(String, f64, f64)],
    map: &HashMap<String, SpeakerId>,
) -> Option<SpeakerId> {
    let mut by_global: HashMap<String, f64> = HashMap::new();
    for (label, a, b) in region {
        if let Some(g) = map.get(label) {
            *by_global.entry(g.as_str().to_string()).or_insert(0.0) += (b - a).max(0.0);
        }
    }
    by_global
        .into_iter()
        .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(g, _)| SpeakerId::new(g))
}

/// Harvest genuine room tone: the contiguous `gap_s`-long span of `audio` with the lowest
/// energy (RMS). That span is the recording's own quietest ambient — the right level *and*
/// nature of "silence" to splice between reference pieces. Falls back to true zeros only if
/// the buffer is shorter than one gap.
fn harvest_room_tone(audio: &AudioBuffer, gap_s: f64) -> Vec<f32> {
    let rate = audio.sample_rate as usize;
    let win = (gap_s * rate as f64) as usize;
    let n = audio.samples.len();
    if win == 0 || n < win {
        return vec![0.0; win.max(1)];
    }
    let hop = (rate / 100).max(1);
    let mut best_start = 0usize;
    let mut best_energy = f64::INFINITY;
    // Prefix sum of squares → O(1) energy per window, O(n) total (stays linear).
    let mut prefix: Vec<f64> = Vec::with_capacity(n + 1);
    prefix.push(0.0);
    for &s in &audio.samples {
        prefix.push(prefix[prefix.len() - 1] + f64::from(s) * f64::from(s));
    }
    let mut start = 0usize;
    while start + win <= n {
        let energy = prefix[start + win] - prefix[start];
        if energy < best_energy {
            best_energy = energy;
            best_start = start;
        }
        start += hop;
    }
    audio.samples[best_start..best_start + win].to_vec()
}

/// Build one reference clip per speaker present in `seg0`, drawn from `block`'s audio (the
/// pinned-count path). Longest turns first, joined by room tone, up to the per-speaker budget.
fn build_references(
    block: &AudioBuffer,
    seg0: &[DiarizedSegment],
    num_speakers: usize,
    ref_budget_s: f64,
    gap_s: f64,
    room: &[f32],
) -> Vec<(SpeakerId, Vec<f32>)> {
    let mut by_speaker: HashMap<String, Vec<(f64, f64)>> = HashMap::new();
    for s in seg0 {
        by_speaker
            .entry(s.speaker.as_str().to_string())
            .or_default()
            .push((s.t0, s.t1));
    }
    let mut labels: Vec<String> = by_speaker.keys().cloned().collect();
    labels.sort();
    labels.truncate(num_speakers.max(1));

    let mut refs: Vec<(SpeakerId, Vec<f32>)> = Vec::new();
    for label in labels {
        let spans = by_speaker.remove(&label).unwrap_or_default();
        let clip = build_one_reference(block, &spans, ref_budget_s, gap_s, room);
        if !clip.is_empty() {
            refs.push((SpeakerId::new(label), clip));
        }
    }
    refs
}

/// Build a single speaker's reference clip from its turn spans in `block`: take the longest
/// turns first, join their audio with a room-tone gap, until the budget is filled.
fn build_one_reference(
    block: &AudioBuffer,
    spans: &[(f64, f64)],
    ref_budget_s: f64,
    gap_s: f64,
    room: &[f32],
) -> Vec<f32> {
    let rate = block.sample_rate;
    let gap_samples = (gap_s * f64::from(rate)) as usize;
    let mut turns = spans.to_vec();
    turns.sort_by(|a, b| {
        (b.1 - b.0)
            .partial_cmp(&(a.1 - a.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut clip: Vec<f32> = Vec::new();
    let mut have_s = 0.0f64;
    for (t0, t1) in turns {
        if have_s >= ref_budget_s {
            break;
        }
        let take = (ref_budget_s - have_s).min(t1 - t0).max(0.0);
        if take <= 0.0 {
            continue;
        }
        let piece = block.slice(t0, t0 + take);
        if piece.samples.is_empty() {
            continue;
        }
        if !clip.is_empty() {
            if room.len() >= gap_samples {
                clip.extend_from_slice(&room[..gap_samples]);
            } else {
                clip.resize(clip.len() + gap_samples, 0.0);
            }
        }
        clip.extend_from_slice(&piece.samples);
        have_s += take;
    }
    clip
}

/// For each global reference span, find the diarizer's local label that covers it best (max
/// temporal overlap), yielding a `local-label → global-speaker` map (pinned-count path).
fn read_mapping(
    segs: &[DiarizedSegment],
    ref_spans: &[(SpeakerId, f64, f64)],
) -> HashMap<String, SpeakerId> {
    // Total overlap between each reference span and each local cluster label. Summing over all
    // of a label's segments (not just its single best segment) counts a cluster fully even when
    // the engine fragments it across the ref span.
    let mut overlap: HashMap<(usize, String), f64> = HashMap::new();
    for (ri, (_, s0, s1)) in ref_spans.iter().enumerate() {
        for seg in segs {
            let ov = (seg.t1.min(*s1) - seg.t0.max(*s0)).max(0.0);
            if ov > 0.0 {
                *overlap
                    .entry((ri, seg.speaker.as_str().to_string()))
                    .or_insert(0.0) += ov;
            }
        }
    }

    // Greedy maximum-overlap assignment: settle the strongest (reference, cluster) pair first,
    // then the next, each reference and each cluster used at most once. This is what keeps a
    // minor-but-real speaker from being starved: the old pass walked references in index order
    // and let the FIRST reference claim a cluster (`or_insert`), so a lower-numbered speaker
    // could steal the cluster that a later speaker overlaps far more — leaving that speaker
    // unmapped and all its window turns dumped onto the window's dominant speaker.
    let mut pairs: Vec<(usize, String, f64)> = overlap
        .into_iter()
        .map(|((ri, l), ov)| (ri, l, ov))
        .collect();
    pairs.sort_by(|a, b| {
        b.2.partial_cmp(&a.2)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.0.cmp(&b.0))
            .then(a.1.cmp(&b.1))
    });

    let mut used_ref = vec![false; ref_spans.len()];
    let mut used_label: HashSet<String> = HashSet::new();
    let mut map: HashMap<String, SpeakerId> = HashMap::new();
    for (ri, label, _) in pairs {
        if used_ref[ri] || used_label.contains(&label) {
            continue;
        }
        used_ref[ri] = true;
        used_label.insert(label.clone());
        map.insert(label, ref_spans[ri].0.clone());
    }
    map
}

/// Merge consecutive same-speaker turns (e.g. a turn split across a window boundary) into one
/// span. A small tolerance bridges the hard cut between windows.
fn merge_adjacent_same_speaker(mut segs: Vec<DiarizedSegment>) -> Vec<DiarizedSegment> {
    segs.sort_by(|a, b| a.t0.partial_cmp(&b.t0).unwrap_or(std::cmp::Ordering::Equal));
    let mut out: Vec<DiarizedSegment> = Vec::with_capacity(segs.len());
    for s in segs {
        if let Some(last) = out.last_mut() {
            if last.speaker == s.speaker && s.t0 - last.t1 <= 0.5 {
                if s.t1 > last.t1 {
                    last.t1 = s.t1;
                }
                continue;
            }
        }
        out.push(s);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A deterministic mock diarizer that recovers "speakers" from the sample amplitude.
    ///
    /// Each physical speaker is a distinct constant amplitude; room tone / silence is
    /// near-zero. The mock segments the buffer into voiced runs (|amp| ≥ 0.1) split by quiet
    /// gaps and assigns each run a label by **first-appearance order in this call** (`S0`,
    /// `S1`, …). So the same physical speaker gets a different label number depending on who
    /// appears first — exactly the cross-window identity problem the primer must solve. It
    /// ignores any speaker count, matching an auto-detect engine.
    struct AmpMockDiarizer;

    impl DiarizationEngine for AmpMockDiarizer {
        fn diarize(&self, audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
            let rate = f64::from(audio.sample_rate);
            let mut segs = Vec::new();
            let mut label_of_amp: HashMap<i64, String> = HashMap::new();
            let mut next = 0usize;
            let mut i = 0usize;
            let n = audio.samples.len();
            while i < n {
                let a = audio.samples[i];
                if a.abs() < 0.1 {
                    i += 1;
                    continue;
                }
                let key = (f64::from(a) * 10.0).round() as i64;
                let start = i;
                while i < n && ((f64::from(audio.samples[i]) * 10.0).round() as i64) == key {
                    i += 1;
                }
                let label = label_of_amp
                    .entry(key)
                    .or_insert_with(|| {
                        let l = format!("S{next}");
                        next += 1;
                        l
                    })
                    .clone();
                segs.push(DiarizedSegment {
                    t0: start as f64 / rate,
                    t1: i as f64 / rate,
                    speaker: SpeakerId::new(label),
                });
            }
            Ok(segs)
        }
    }

    fn tone(amp: f32, secs: f64, rate: u32) -> Vec<f32> {
        vec![amp; (secs * f64::from(rate)) as usize]
    }
    fn quiet(secs: f64, rate: u32) -> Vec<f32> {
        vec![0.01; (secs * f64::from(rate)) as usize]
    }

    #[test]
    fn harvest_picks_the_quietest_span() {
        let rate = 16_000;
        let mut samples = tone(0.5, 1.0, rate);
        samples.extend(quiet(1.0, rate));
        samples.extend(tone(0.5, 1.0, rate));
        let audio = AudioBuffer::new(rate, samples);
        let room = harvest_room_tone(&audio, 0.5);
        assert!(room.iter().all(|&s| s.abs() < 0.1));
        assert_eq!(room.len(), (0.5 * f64::from(rate)) as usize);
    }

    #[test]
    fn short_input_bypasses_blockwise() {
        let rate = 16_000;
        let mut samples = tone(0.5, 2.0, rate);
        samples.extend(quiet(0.6, rate));
        samples.extend(tone(0.9, 2.0, rate));
        let audio = AudioBuffer::new(rate, samples);

        let plain = AmpMockDiarizer.diarize(&audio).unwrap();
        let bw = BlockwiseDiarizer::new(AmpMockDiarizer, 2)
            .with_window_s(30.0)
            .diarize(&audio)
            .unwrap();
        assert_eq!(plain.len(), bw.len());
    }

    /// Build a two-speaker, multi-window buffer where the mock's per-call labels flip between
    /// windows (B leads window 1). Returns the buffer and its sample rate.
    fn two_speaker_buffer() -> (AudioBuffer, u32) {
        let rate = 16_000;
        let a = 0.5f32;
        let b = 0.9f32;
        let g = || quiet(0.8, rate);
        let mut s: Vec<f32> = Vec::new();
        // Window 0 (0–30 s): A, B, A.
        s.extend(tone(a, 8.0, rate));
        s.extend(g());
        s.extend(tone(b, 8.0, rate));
        s.extend(g());
        s.extend(tone(a, 8.0, rate));
        s.extend(g());
        // Window 1 (~30–60 s): B first, then A.
        s.extend(tone(b, 12.0, rate));
        s.extend(g());
        s.extend(tone(a, 12.0, rate));
        s.extend(g());
        // Window 2 (~60–90 s): only B.
        s.extend(tone(b, 20.0, rate));
        s.extend(g());
        (AudioBuffer::new(rate, s), rate)
    }

    /// Assert every output turn's global label is a 1:1 function of the physical speaker read
    /// from the audio at the turn midpoint, and that exactly `expected` speakers surfaced.
    fn assert_consistent(audio: &AudioBuffer, segs: &[DiarizedSegment], expected: usize) {
        let rate_f = f64::from(audio.sample_rate);
        let mut label_for_phys: HashMap<i64, String> = HashMap::new();
        for seg in segs {
            let mid = ((seg.t0 + seg.t1) / 2.0 * rate_f) as usize;
            let amp = audio.samples.get(mid).copied().unwrap_or(0.0);
            let phys = (f64::from(amp) * 10.0).round() as i64;
            if phys.abs() < 1 {
                continue;
            }
            let entry = label_for_phys
                .entry(phys)
                .or_insert_with(|| seg.speaker.as_str().to_string());
            assert_eq!(
                entry,
                seg.speaker.as_str(),
                "physical speaker {phys} got two labels"
            );
        }
        let labels: std::collections::HashSet<_> = label_for_phys.values().collect();
        assert_eq!(
            labels.len(),
            expected,
            "expected {expected} distinct global labels"
        );
    }

    #[test]
    fn identity_is_consistent_across_windows_pinned() {
        let (audio, _) = two_speaker_buffer();
        let segs = BlockwiseDiarizer::new(AmpMockDiarizer, 2)
            .with_window_s(30.0)
            .with_ref_budget_s(6.0)
            .with_gap_s(0.8)
            .diarize(&audio)
            .unwrap();
        assert_consistent(&audio, &segs, 2);
    }

    #[test]
    fn discovers_two_speakers() {
        // No count given — the discovery path must find exactly two, and keep identity stable.
        let (audio, _) = two_speaker_buffer();
        let segs = BlockwiseDiarizer::discovering(AmpMockDiarizer)
            .with_window_s(30.0)
            .with_ref_budget_s(6.0)
            .with_gap_s(0.8)
            .with_min_speaker_s(3.0)
            .diarize(&audio)
            .unwrap();
        assert_consistent(&audio, &segs, 2);
    }

    /// A mock that always returns ONE cluster (everything is `S0`), simulating an
    /// over-segmenting engine whose fragments actually belong to a single speaker.
    struct OneClusterMock;
    impl DiarizationEngine for OneClusterMock {
        fn diarize(&self, audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
            Ok(vec![DiarizedSegment {
                t0: 0.0,
                t1: audio.duration_s(),
                speaker: SpeakerId::new("S0"),
            }])
        }
    }

    #[test]
    fn consolidation_merges_duplicate_speakers() {
        // Two references that the engine says share a cluster must collapse to one speaker.
        let bw = BlockwiseDiarizer::discovering(OneClusterMock);
        let refs = vec![
            (SpeakerId::new("S0"), vec![0.5f32; 16_000]),
            (SpeakerId::new("S1"), vec![0.9f32; 16_000]),
        ];
        let canon = bw.consolidate(&refs, 16_000, &[0.01f32; 800]);
        // Both map to the same canonical id (S0, the earliest).
        assert_eq!(canon.get("S0"), canon.get("S1"));
        assert_eq!(
            canon.get("S1").map(|s| s.as_str().to_string()),
            Some("S0".to_string())
        );
    }

    #[test]
    fn mapping_does_not_starve_a_minor_speaker() {
        // Two references and two local clusters. Cluster "big" overlaps ref S1 more than S0
        // (0.9 vs 0.6); cluster "small" overlaps S1 more too (0.8) but S0 by 0.5. The old
        // index-order, first-come pass let S0 claim "big" (its own argmax) and then dropped S1
        // entirely (its argmax "big" was taken), so every "big" turn — the bulk of the audio —
        // was mislabelled S0 and S1 was starved. Greedy max-overlap assignment settles the
        // strongest pair first (S1←big), leaving S0←small: both speakers keep their turns.
        let ref_spans = vec![
            (SpeakerId::new("S0"), 0.0, 1.0),
            (SpeakerId::new("S1"), 2.0, 3.0),
        ];
        let segs = vec![
            // "big": 0.6 s inside S0's span, 0.9 s inside S1's span.
            DiarizedSegment {
                t0: 0.4,
                t1: 1.0,
                speaker: SpeakerId::new("big"),
            },
            DiarizedSegment {
                t0: 2.1,
                t1: 3.0,
                speaker: SpeakerId::new("big"),
            },
            // "small": 0.5 s inside S0's span, 0.8 s inside S1's span.
            DiarizedSegment {
                t0: 0.5,
                t1: 1.0,
                speaker: SpeakerId::new("small"),
            },
            DiarizedSegment {
                t0: 2.2,
                t1: 3.0,
                speaker: SpeakerId::new("small"),
            },
        ];
        let map = read_mapping(&segs, &ref_spans);
        assert_eq!(
            map.get("big").map(|g| g.as_str().to_string()),
            Some("S1".to_string())
        );
        assert_eq!(
            map.get("small").map(|g| g.as_str().to_string()),
            Some("S0".to_string())
        );
        // Both references anchored — neither speaker starved.
        assert_eq!(map.len(), 2);
    }

    #[test]
    fn discovery_ignores_a_minor_voice() {
        // Two main speakers plus a single 1 s interjection from a third amplitude. With a 3 s
        // floor the minor voice must NOT become a speaker — the count stays 2.
        let rate = 16_000;
        let a = 0.5f32;
        let b = 0.9f32;
        let c = 0.3f32; // minor voice, speaks only briefly
        let g = || quiet(0.8, rate);
        let mut s: Vec<f32> = Vec::new();
        s.extend(tone(a, 10.0, rate));
        s.extend(g());
        s.extend(tone(b, 10.0, rate));
        s.extend(g());
        s.extend(tone(a, 10.0, rate));
        s.extend(g());
        // Window 1: main speakers + a 1 s minor interjection.
        s.extend(tone(b, 12.0, rate));
        s.extend(g());
        s.extend(tone(c, 1.0, rate)); // minor — below the 3 s floor
        s.extend(g());
        s.extend(tone(a, 12.0, rate));
        s.extend(g());
        let audio = AudioBuffer::new(rate, s);

        let segs = BlockwiseDiarizer::discovering(AmpMockDiarizer)
            .with_window_s(30.0)
            .with_ref_budget_s(6.0)
            .with_gap_s(0.8)
            .with_min_speaker_s(3.0)
            .diarize(&audio)
            .unwrap();
        // Exactly two speakers discovered; the minor voice was folded in, not promoted.
        let labels: std::collections::HashSet<_> =
            segs.iter().map(|s| s.speaker.as_str()).collect();
        assert_eq!(
            labels.len(),
            2,
            "minor voice must not inflate the speaker count"
        );
    }
}
