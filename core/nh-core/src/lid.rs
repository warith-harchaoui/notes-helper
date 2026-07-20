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
