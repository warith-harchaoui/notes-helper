//! Language-region segmentation — the pure posterior-curve → regions core.
//!
//! Translated from the toolbox `vocal_helper.lid`: a language switch has no sharp
//! acoustic cue, so instead of hunting for a boundary the toolbox samples a
//! per-window language **posterior curve** over time, then turns that noisy
//! time-series into stable mono-language spans. This module owns the *pure* half
//! of that method — the part with no model in it:
//!
//! 1. Gaussian-smooth the posterior curve along time (faithful to scipy's
//!    `gaussian_filter1d`, `mode="nearest"`, `truncate=4.0`),
//! 2. take the per-frame argmax language,
//! 3. place change points where it flips,
//! 4. coalesce touching same-language spans,
//! 5. absorb any sub-`min_region_s` span into its longer neighbour.
//!
//! The ML half (reading whisper's language head to build the posterior matrix,
//! and the optional boundary refine/snap-to-silence passes) lives in the engine
//! adapter and feeds [`regions_from_posteriors`] its `(centers, langs, post)`.
//! Language is always *discovered* from the audio, never defaulted: empty or
//! too-short input yields an empty region list rather than a fabricated language.

use std::collections::BTreeMap;

use crate::error::Result;
use crate::model::AudioBuffer;
use crate::ports::LanguageDetector;

/// A contiguous mono-language span of audio, in seconds. `lang` is the ISO-639-1
/// code discovered for `[t0, t1)`.
#[derive(Debug, Clone, PartialEq)]
pub struct LangRegion {
    /// The discovered language code (e.g. `"fr"`, `"en"`).
    pub lang: String,
    /// Span start time in seconds.
    pub t0: f64,
    /// Span end time in seconds.
    pub t1: f64,
}

impl LangRegion {
    /// Span length in seconds.
    pub fn duration(&self) -> f64 {
        self.t1 - self.t0
    }
}

/// scipy's normalized order-0 Gaussian kernel over `[-radius, radius]`, with
/// `radius = int(truncate * sigma + 0.5)` — identical construction to
/// `scipy.ndimage.gaussian_filter1d`.
fn gaussian_kernel_1d(sigma: f64, truncate: f64) -> Vec<f64> {
    // `int(...)` truncates toward zero, matching scipy's radius computation.
    let radius = (truncate * sigma + 0.5) as i64;
    let mut w: Vec<f64> = (-radius..=radius)
        .map(|x| (-0.5 * (x as f64 / sigma).powi(2)).exp())
        .collect();
    let sum: f64 = w.iter().sum();
    for v in &mut w {
        *v /= sum;
    }
    w
}

/// Smooth one column with a Gaussian, `mode="nearest"` (edge values repeated).
///
/// Correlation with a symmetric kernel; index positions past either end clamp to
/// the nearest in-range sample, exactly as scipy's `nearest` boundary does.
fn gaussian_smooth_nearest(col: &[f64], sigma: f64, truncate: f64) -> Vec<f64> {
    if col.is_empty() {
        return Vec::new();
    }
    let weights = gaussian_kernel_1d(sigma, truncate);
    let radius = (weights.len() / 2) as i64;
    let n = col.len() as i64;
    (0..n)
        .map(|i| {
            weights
                .iter()
                .enumerate()
                .map(|(w_idx, w)| {
                    // Kernel tap offset, then clamp to [0, n-1] for nearest-edge.
                    let src = (i + w_idx as i64 - radius).clamp(0, n - 1);
                    w * col[src as usize]
                })
                .sum()
        })
        .collect()
}

/// Smooth a `post[T][L]` posterior matrix along time (axis 0), per language
/// column — the shape [`regions_from_posteriors`] consumes.
fn smooth_posteriors(post: &[Vec<f64>], sigma: f64, truncate: f64) -> Vec<Vec<f64>> {
    if post.is_empty() {
        return Vec::new();
    }
    let t = post.len();
    let l = post[0].len();
    // Pull each column out, smooth it, write it back — keeps the row-major shape.
    let mut out = vec![vec![0.0f64; l]; t];
    for col_idx in 0..l {
        let column: Vec<f64> = post.iter().map(|row| row[col_idx]).collect();
        let smoothed = gaussian_smooth_nearest(&column, sigma, truncate);
        for (row_idx, v) in smoothed.into_iter().enumerate() {
            out[row_idx][col_idx] = v;
        }
    }
    out
}

/// Index of the first maximum in `row` (numpy `argmax` tie-break).
fn argmax(row: &[f64]) -> usize {
    let mut best = 0usize;
    for (i, &v) in row.iter().enumerate().skip(1) {
        if v > row[best] {
            best = i;
        }
    }
    best
}

/// Merge consecutive same-language regions into one span.
pub fn coalesce(regions: &[LangRegion]) -> Vec<LangRegion> {
    let mut out: Vec<LangRegion> = Vec::new();
    for r in regions {
        match out.last_mut() {
            Some(prev) if prev.lang == r.lang => prev.t1 = r.t1,
            _ => out.push(r.clone()),
        }
    }
    out
}

/// Relabel any sub-`min_region_s` region to its longer neighbour, re-coalesce.
///
/// Iteratively relabels the shortest offending region to whichever adjacent
/// region is longer (the more established language) and re-merges touching
/// same-language regions, until every region clears the threshold — or only one
/// remains.
pub fn absorb_short_regions(regions: &[LangRegion], min_region_s: f64) -> Vec<LangRegion> {
    let mut rs = regions.to_vec();
    while rs.len() > 1 {
        // Shortest region; numpy `min(range, key=…)` returns the first on ties.
        let idx = (0..rs.len())
            .min_by(|&a, &b| {
                rs[a]
                    .duration()
                    .partial_cmp(&rs[b].duration())
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .expect("rs is non-empty inside the loop");
        if rs[idx].duration() >= min_region_s {
            break;
        }
        let left = if idx > 0 { Some(&rs[idx - 1]) } else { None };
        let right = if idx < rs.len() - 1 {
            Some(&rs[idx + 1])
        } else {
            None
        };
        // Prefer the longer neighbour; on a tie (or no right) the left wins,
        // matching the Python `left and (not right or left >= right)`.
        let new_lang = match (left, right) {
            (Some(l), r) if r.is_none_or(|r| l.duration() >= r.duration()) => l.lang.clone(),
            (_, Some(r)) => r.lang.clone(),
            (Some(l), None) => l.lang.clone(),
            (None, None) => unreachable!("len > 1 guarantees a neighbour"),
        };
        rs[idx].lang = new_lang;
        rs = coalesce(&rs);
    }
    rs
}

/// Turn a language-posterior curve into stable mono-language regions.
///
/// `centers[T]` are window-centre times (s), `langs[L]` the candidate codes, and
/// each row of `post[T][L]` a posterior over `langs`. `dur` is the audio length,
/// `hop_s` the curve's sampling step (sets the smoothing sigma in frames),
/// `smooth_s` the Gaussian sigma in seconds (`<= 0` disables), and `min_region_s`
/// the shortest span kept before it is absorbed into a neighbour.
///
/// Returns an empty vec when there is nothing to discover (no frames or no
/// candidate languages) — never a fabricated region. This is the pure core; the
/// caller may still refine/snap boundaries with the model and VAD afterwards.
///
/// # Examples
/// ```
/// use nh_core::lid::regions_from_posteriors;
/// // Two clear halves: 'fr' then 'en', sampled every 1 s.
/// let centers = vec![0.0, 1.0, 2.0, 3.0];
/// let langs = vec!["fr".to_string(), "en".to_string()];
/// let post = vec![vec![0.9, 0.1], vec![0.9, 0.1], vec![0.1, 0.9], vec![0.1, 0.9]];
/// let regions = regions_from_posteriors(&centers, &langs, &post, 4.0, 1.0, 0.0, 0.5);
/// assert_eq!(regions.len(), 2);
/// assert_eq!(regions[0].lang, "fr");
/// assert_eq!(regions[1].lang, "en");
/// ```
pub fn regions_from_posteriors(
    centers: &[f64],
    langs: &[String],
    post: &[Vec<f64>],
    dur: f64,
    hop_s: f64,
    smooth_s: f64,
    min_region_s: f64,
) -> Vec<LangRegion> {
    // Nothing to discover — no frames or an empty candidate axis (audio too short
    // to identify anything). Report honestly rather than inventing a language.
    if post.is_empty() || langs.is_empty() || post[0].is_empty() {
        return Vec::new();
    }
    // (1) Gaussian-smooth the curve over time so a single noisy window can't spawn
    // a spurious region; sigma is the smoothing length expressed in frames.
    let smoothed = if post.len() > 1 && smooth_s > 0.0 {
        let sigma = (smooth_s / hop_s).max(1e-6);
        smooth_posteriors(post, sigma, 4.0)
    } else {
        post.to_vec()
    };
    // (2) Per-frame argmax language.
    let idx: Vec<usize> = smoothed.iter().map(|row| argmax(row)).collect();

    // (3) Change points where the argmax flips → raw regions, with each boundary
    // placed midway between the two frames' centres.
    let mut regions: Vec<LangRegion> = Vec::new();
    let mut run = 0usize;
    for k in 1..=idx.len() {
        if k == idx.len() || idx[k] != idx[run] {
            let t0 = if run == 0 {
                0.0
            } else {
                (centers[run - 1] + centers[run]) / 2.0
            };
            let t1 = if k == idx.len() {
                dur
            } else {
                (centers[k - 1] + centers[k]) / 2.0
            };
            regions.push(LangRegion {
                lang: langs[idx[run]].clone(),
                t0,
                t1,
            });
            run = k;
        }
    }

    // (4) + (5) Coalesce touching same-language spans, then absorb sub-threshold ones.
    absorb_short_regions(&coalesce(&regions), min_region_s)
}

/// Move each internal region boundary to the lowest-energy point nearby.
///
/// A code-switch happens at a pause, not mid-word. For each shared boundary this
/// searches ±`snap_s` for the 50 ms frame of minimum RMS and snaps the boundary
/// to that frame's centre — the pure DSP refinement step of the toolbox's lid
/// method, operating on the raw mono `pcm` at `sample_rate`. Fewer than two
/// regions, or a non-positive `snap_s`, leaves the regions untouched.
pub fn snap_boundaries_to_silence(
    pcm: &[f32],
    sample_rate: u32,
    regions: &[LangRegion],
    snap_s: f64,
) -> Vec<LangRegion> {
    if regions.len() < 2 || snap_s <= 0.0 {
        return regions.to_vec();
    }
    let rate = f64::from(sample_rate);
    // A 50 ms analysis frame (at least one sample), matching the toolbox.
    let frame = ((0.05 * rate) as usize).max(1);
    let n = pcm.len();
    let dur = n as f64 / rate;
    let mut out = regions.to_vec();
    for i in 0..out.len() - 1 {
        let b = out[i].t1;
        let lo = (b - snap_s).max(0.0);
        let hi = (b + snap_s).min(dur);
        let a0 = (lo * rate) as usize;
        let a1 = (hi * rate) as usize;
        // range(a0, max(a0+1, a1-frame), frame) — always at least one probe.
        let stop = (a0 + 1).max(a1.saturating_sub(frame));
        let (mut best_t, mut best_rms) = (b, f64::INFINITY);
        let mut s = a0;
        while s < stop {
            let end = (s + frame).min(n);
            let seg = if s < n { &pcm[s..end] } else { &[][..] };
            // RMS over the available samples; an empty tail slice scores infinity.
            let rms = if seg.is_empty() {
                f64::INFINITY
            } else {
                let mean_sq: f64 = seg
                    .iter()
                    .map(|&x| f64::from(x) * f64::from(x))
                    .sum::<f64>()
                    / seg.len() as f64;
                mean_sq.sqrt()
            };
            if rms < best_rms {
                best_rms = rms;
                best_t = (s as f64 + frame as f64 / 2.0) / rate;
            }
            s += frame;
        }
        out[i].t1 = best_t;
        out[i + 1].t0 = best_t;
    }
    out
}

/// A sampled language-posterior curve: `(centers[T], langs[L], post[T][L])` — window
/// centre times, the candidate language axis, and each window's posterior over it.
type PosteriorCurve = (Vec<f64>, Vec<String>, Vec<Vec<f64>>);

/// Sample a per-window language posterior over time, using a [`LanguageDetector`].
///
/// Slides a `window_s` window at `hop_s` steps and reads the detector's posterior
/// where at least one second of audio is present. Returns `(centers, langs, post)`:
/// `centers[T]` window-centre times, `langs[L]` the candidate axis (adopted from
/// the first usable window so nothing is filtered out), and `post[T][L]` each
/// window's posterior projected onto that axis and renormalised. Translated from
/// the toolbox `language_posterior_curve` (the windowing; the model call is the
/// injected detector).
fn language_posterior_curve(
    detector: &dyn LanguageDetector,
    audio: &AudioBuffer,
    window_s: f64,
    hop_s: f64,
) -> Result<PosteriorCurve> {
    let dur = audio.duration_s();
    let sr = audio.sample_rate as usize;
    let half = window_s / 2.0;
    let mut centers: Vec<f64> = Vec::new();
    let mut rows: Vec<Vec<(String, f32)>> = Vec::new();
    // Slide a hop-spaced window, reading the head only where >= 1 s of audio exists
    // (whisper cannot identify a language from less). hop_s <= 0 is guarded by the
    // caller, so this loop always advances.
    let mut t = 0.0;
    while t < dur.max(hop_s) {
        let a = (t - half).max(0.0);
        let b = (t + half).min(dur);
        let seg = audio.slice(a, b);
        if seg.samples.len() >= sr {
            rows.push(detector.detect_language(&seg)?);
            centers.push(t.min(dur));
        }
        t += hop_s;
    }
    // No usable window (audio < 1 s): nothing to discover — an empty axis so callers
    // treat the language as unknown rather than inventing one.
    if rows.is_empty() {
        return Ok((vec![0.0], Vec::new(), vec![Vec::new()]));
    }
    // Adopt the full candidate set from the first window as a stable, sorted axis.
    let mut langs: Vec<String> = rows[0].iter().map(|(c, _)| c.clone()).collect();
    langs.sort();
    langs.dedup();
    // Project each posterior onto that axis and renormalise to a proper distribution
    // (the argmax later must compare like-for-like).
    let post: Vec<Vec<f64>> = rows
        .iter()
        .map(|row| {
            let map: BTreeMap<&str, f64> = row
                .iter()
                .map(|(c, p)| (c.as_str(), f64::from(*p)))
                .collect();
            let mut vals: Vec<f64> = langs
                .iter()
                .map(|c| map.get(c.as_str()).copied().unwrap_or(0.0))
                .collect();
            let s: f64 = vals.iter().sum();
            if s > 0.0 {
                for v in &mut vals {
                    *v /= s;
                }
            } else {
                // Zero-mass window → uniform prior over the axis.
                let u = 1.0 / langs.len() as f64;
                vals.iter_mut().for_each(|v| *v = u);
            }
            vals
        })
        .collect();
    Ok((centers, langs, post))
}

/// Partition `audio` into mono-language regions with a [`LanguageDetector`].
///
/// The posterior-curve method end to end: sample the language curve over windows,
/// then [`regions_from_posteriors`] (smooth → argmax → change-points → coalesce →
/// absorb-short), then [`snap_boundaries_to_silence`]. `window_s`/`hop_s` shape the
/// curve, `smooth_s` the anti-jitter sigma, `min_region_s` the shortest kept span,
/// `snap_s` the pause-snap radius. Empty/too-short audio yields no region — language
/// is discovered, never defaulted. (The toolbox's optional per-boundary posterior
/// *refine* pass is left to a later step; curve → regions → snap already gives
/// stable spans.)
///
/// # Errors
/// Propagates any [`LanguageDetector::detect_language`] failure.
pub fn detect_language_regions(
    detector: &dyn LanguageDetector,
    audio: &AudioBuffer,
    window_s: f64,
    hop_s: f64,
    smooth_s: f64,
    min_region_s: f64,
    snap_s: f64,
) -> Result<Vec<LangRegion>> {
    if audio.samples.is_empty() || hop_s <= 0.0 {
        return Ok(Vec::new());
    }
    let dur = audio.duration_s();
    let (centers, langs, post) = language_posterior_curve(detector, audio, window_s, hop_s)?;
    let regions =
        regions_from_posteriors(&centers, &langs, &post, dur, hop_s, smooth_s, min_region_s);
    Ok(snap_boundaries_to_silence(
        &audio.samples,
        audio.sample_rate,
        &regions,
        snap_s,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r(lang: &str, t0: f64, t1: f64) -> LangRegion {
        LangRegion {
            lang: lang.to_string(),
            t0,
            t1,
        }
    }

    #[test]
    fn gaussian_smoothing_matches_scipy_golden() {
        // Golden values from scipy.ndimage.gaussian_filter1d(mode="nearest").
        let x = [0.0, 0.0, 1.0, 1.0, 0.0, 0.0];
        let cases: [(f64, [f64; 6]); 3] = [
            (
                0.5,
                [0.000264, 0.106715, 0.893021, 0.893021, 0.106715, 0.000264],
            ),
            (
                1.0,
                [0.058423, 0.295963, 0.640915, 0.640915, 0.295963, 0.058423],
            ),
            (
                2.0,
                [0.185747, 0.297023, 0.37551, 0.37551, 0.297023, 0.185747],
            ),
        ];
        for (sigma, expected) in cases {
            let got = gaussian_smooth_nearest(&x, sigma, 4.0);
            for (g, e) in got.iter().zip(expected) {
                assert!((g - e).abs() < 1e-5, "sigma={sigma}: {g} vs {e}");
            }
        }
    }

    #[test]
    fn constant_column_is_unchanged_by_smoothing() {
        let x = [0.4, 0.4, 0.4, 0.4];
        let got = gaussian_smooth_nearest(&x, 1.5, 4.0);
        assert!(got.iter().all(|v| (v - 0.4).abs() < 1e-9));
    }

    #[test]
    fn coalesce_merges_touching_same_language() {
        let regions = [r("fr", 0.0, 1.0), r("fr", 1.0, 2.0), r("en", 2.0, 3.0)];
        let out = coalesce(&regions);
        assert_eq!(out, vec![r("fr", 0.0, 2.0), r("en", 2.0, 3.0)]);
    }

    #[test]
    fn absorb_relabels_short_region_to_longer_neighbour() {
        // A 0.3 s 'en' blip between two long 'fr' spans is absorbed into 'fr'.
        let regions = [r("fr", 0.0, 5.0), r("en", 5.0, 5.3), r("fr", 5.3, 10.0)];
        let out = absorb_short_regions(&regions, 1.0);
        assert_eq!(out, vec![r("fr", 0.0, 10.0)]);
    }

    #[test]
    fn absorb_keeps_a_single_region_even_if_short() {
        // With only one region there is no neighbour to absorb into — keep it.
        let regions = [r("fr", 0.0, 0.2)];
        assert_eq!(absorb_short_regions(&regions, 1.0), vec![r("fr", 0.0, 0.2)]);
    }

    #[test]
    fn empty_or_langless_posteriors_discover_nothing() {
        assert!(regions_from_posteriors(&[], &[], &[], 0.0, 3.0, 6.0, 8.0).is_empty());
        let centers = [0.0, 1.0];
        let post = [vec![], vec![]];
        assert!(regions_from_posteriors(&centers, &[], &post, 2.0, 1.0, 0.0, 0.5).is_empty());
    }

    /// A stand-in detector that "hears" fr in low-amplitude audio and en in high —
    /// so a buffer whose first half is 1.0 and second half is 2.0 reads as fr→en,
    /// letting us exercise the whole orchestration with no model.
    struct MeanAmplitudeDetector;
    impl LanguageDetector for MeanAmplitudeDetector {
        fn detect_language(&self, audio: &AudioBuffer) -> Result<Vec<(String, f32)>> {
            let n = audio.samples.len().max(1) as f32;
            let mean: f32 = audio.samples.iter().sum::<f32>() / n;
            // Closer to 1.0 ⇒ fr, closer to 2.0 ⇒ en; always the same 2-code axis.
            let (fr, en) = if mean < 1.5 { (0.9, 0.1) } else { (0.1, 0.9) };
            Ok(vec![("fr".to_string(), fr), ("en".to_string(), en)])
        }
    }

    #[test]
    fn detect_language_regions_splits_fr_then_en() {
        // 20 s at 1 kHz: first half amplitude 1.0 (fr), second half 2.0 (en).
        let sr = 1_000u32;
        let mut samples = vec![1.0f32; 20_000];
        samples[10_000..].fill(2.0);
        let audio = AudioBuffer::new(sr, samples);
        // 4 s windows every 2 s; no smoothing/snap so the assertion is exact-ish.
        let regions =
            detect_language_regions(&MeanAmplitudeDetector, &audio, 4.0, 2.0, 0.0, 1.0, 0.0)
                .unwrap();
        assert_eq!(regions.len(), 2, "expected fr then en, got {regions:?}");
        assert_eq!(regions[0].lang, "fr");
        assert_eq!(regions[1].lang, "en");
        // The switch lands near the 10 s midpoint (within a window of it).
        assert!(
            (8.0..12.0).contains(&regions[0].t1),
            "boundary at {}",
            regions[0].t1
        );
    }

    #[test]
    fn detect_language_regions_of_empty_audio_is_empty() {
        let audio = AudioBuffer::new(16_000, vec![]);
        let regions =
            detect_language_regions(&MeanAmplitudeDetector, &audio, 4.0, 2.0, 6.0, 1.0, 1.0)
                .unwrap();
        assert!(regions.is_empty());
    }

    #[test]
    fn snap_moves_boundary_into_a_nearby_silent_gap() {
        // 2 s at 1 kHz: loud everywhere except a silent gap in [0.9, 1.1) s.
        let sr = 1_000u32;
        let mut pcm = vec![1.0f32; 2_000];
        for s in pcm.iter_mut().take(1_100).skip(900) {
            *s = 0.0; // carve out the pause
        }
        let regions = [r("fr", 0.0, 1.05), r("en", 1.05, 2.0)];
        let out = snap_boundaries_to_silence(&pcm, sr, &regions, 0.2);
        // The shared boundary snaps to the low-energy frame inside the gap.
        assert!((0.9..1.1).contains(&out[0].t1), "snapped to {}", out[0].t1);
        assert_eq!(out[0].t1, out[1].t0); // still a shared boundary
    }

    #[test]
    fn snap_is_a_noop_below_two_regions_or_nonpositive_radius() {
        let one = [r("fr", 0.0, 1.0)];
        assert_eq!(
            snap_boundaries_to_silence(&[0.0; 100], 1_000, &one, 0.2),
            one
        );
        let two = [r("fr", 0.0, 1.0), r("en", 1.0, 2.0)];
        assert_eq!(
            snap_boundaries_to_silence(&[0.0; 2_000], 1_000, &two, 0.0),
            two
        );
    }

    #[test]
    fn smoothing_removes_a_one_frame_blip() {
        // fr fr EN fr fr — the lone 'en' frame is smoothed away, one 'fr' region.
        let centers: Vec<f64> = (0..5).map(|i| i as f64).collect();
        let langs = vec!["fr".to_string(), "en".to_string()];
        let post = vec![
            vec![0.8, 0.2],
            vec![0.8, 0.2],
            vec![0.45, 0.55],
            vec![0.8, 0.2],
            vec![0.8, 0.2],
        ];
        let regions = regions_from_posteriors(&centers, &langs, &post, 4.0, 1.0, 2.0, 0.5);
        assert_eq!(regions.len(), 1);
        assert_eq!(regions[0].lang, "fr");
    }
}
