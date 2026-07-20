//! Diarization backend router — the study-grounded *aiguilleur*.
//!
//! Translated faithfully from the Notes Helper toolbox, `vocal_helper.router`
//! (the proven Python reference): the app's Rust core owns the same decision so
//! the shell never hard-codes a diarizer, re-derives the trade-off, or hides the
//! cost. Diarization is the one pipeline stage with a genuine backend fork, and
//! there is **no single best backend**: the right one depends on whether the
//! audio is a live stream or a batch file, on its duration, on the speaker count,
//! and on whether the deployment can afford a PyTorch install.
//!
//! Quality × speed per scenario (the whole point)
//! ----------------------------------------------
//! Numbers were re-validated on Warith's machine (2026-07-19,
//! `studies/router_profile_validation.py`, `pyannote.metrics` collar 0.25, median
//! DER + RTF) against ground truth — bagarre (30 short mixes) + AMI dev-slice. The
//! `sherpa` DER is from ADR 0002 (torch-free ONNX pyannote-3.0 segmentation +
//! TitaNet-large embedding). DER = quality (lower is better); RTF = speed
//! (`< 1` = faster than real time):
//!
//! | mode    | backend  | DER   | RTF   | when the router picks it                         |
//! |---------|----------|-------|-------|-------------------------------------------------|
//! | offline | nemo     | 0.142 | 0.051 | short (≤300 s), ≤4 speakers — dense interleaved  |
//! | offline | pyannote | 0.122 | 0.067 | long / unknown / >4 speakers — robust default    |
//! | offline | sherpa   | 0.174 | 0.58  | torch-free deployment (no PyTorch) — ADR 0002    |
//! | online  | nemo     | 0.586 | 0.030 | any live stream (the default online embedder)    |
//! | online  | sherpa   | 0.174 | 0.58  | torch-free streaming = periodic offline re-diar  |
//!
//! Why a router, not a default: on short dense turns NeMo Sortformer wins by
//! ~2.3× (offline DER 0.142 vs pyannote 0.330), but on long meetings the verdict
//! reverses — pyannote median 0.122 and Sortformer *hangs* past ~25 min (its 90 s
//! / 4-speaker cap puts long form out of distribution). Streaming always routes to
//! nemo: the online path is a latency-bound cosine-clustering approximation and the
//! NeMo TitaNet embedder is the best online backend at every length measured.
//!
//! Scope: this module decides the **diarization** backend only — the other stages
//! are single-backend by study verdict (VAD = Silero v5, ASR = whisper large-v3-turbo,
//! analyst = Gemma via Ollama; language is *discovered*, never routed).

use serde::{Deserialize, Serialize};

/// Offline NeMo Sortformer stays reliable up to ~300 s (it chunks internally at
/// its 60 s ideal duration) but *hangs* on very long meetings — the study saw no
/// output on a 27-min AMI file. Past this ceiling, only pyannote is safe.
pub const NEMO_MAX_DURATION_S: f64 = 300.0;

/// Sortformer is trained/capped at 4 speakers. Beyond that it silently mislabels,
/// so a short clip with a known >4 speaker count must still go to pyannote.
pub const SORTFORMER_MAX_SPEAKERS: u32 = 4;

/// Diarizer execution mode — the pair of stages the app can run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DiarMode {
    /// Streaming, whole-buffer-refined online diarizer for a live source.
    Online,
    /// Whole-buffer offline diarizer — the reliable default for files.
    Offline,
}

impl DiarMode {
    /// Wire string matching the toolbox's `mode` field (`"online"` / `"offline"`).
    ///
    /// ```
    /// # use nh_core::router::DiarMode;
    /// assert_eq!(DiarMode::Offline.as_str(), "offline");
    /// ```
    pub fn as_str(self) -> &'static str {
        match self {
            DiarMode::Online => "online",
            DiarMode::Offline => "offline",
        }
    }
}

/// Diarization backend — the one stage with a real fork.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DiarBackend {
    /// pyannote 3.1 — the robust long-form default (needs PyTorch).
    Pyannote,
    /// NeMo Sortformer — end-to-end, best on short dense turns (needs PyTorch).
    Nemo,
    /// onnxruntime TitaNet-large — torch-free, portable (ADR 0002).
    Sherpa,
}

impl DiarBackend {
    /// Wire string to pass straight to the stage's `diar={"backend": ...}` config.
    ///
    /// ```
    /// # use nh_core::router::DiarBackend;
    /// assert_eq!(DiarBackend::Sherpa.as_str(), "sherpa");
    /// ```
    pub fn as_str(self) -> &'static str {
        match self {
            DiarBackend::Pyannote => "pyannote",
            DiarBackend::Nemo => "nemo",
            DiarBackend::Sherpa => "sherpa",
        }
    }
}

/// Representative `(DER, RTF)` for a `(mode, backend)` pair, re-validated on
/// Warith's machine (2026-07-19; median DER + RTF). Keyed so a decision only
/// names `(mode, backend)` and the quality + speed numbers follow — they can
/// never drift from the reason. Only the pairs the router can emit are listed
/// (online never routes to pyannote: it loses to nemo online at every length).
///
/// Returns `None` for a pair the router never produces, mirroring the Python
/// `_PROFILE` dict's key set.
fn profile(mode: DiarMode, backend: DiarBackend) -> Option<(f64, f64)> {
    match (mode, backend) {
        (DiarMode::Offline, DiarBackend::Nemo) => Some((0.142, 0.051)), // bagarre n=30, offline Sortformer
        (DiarMode::Offline, DiarBackend::Pyannote) => Some((0.122, 0.067)), // AMI dev-slice n=2 median
        (DiarMode::Offline, DiarBackend::Sherpa) => Some((0.174, 0.58)), // ADR 0002, ES2011a, TitaNet-large ONNX
        (DiarMode::Online, DiarBackend::Nemo) => Some((0.586, 0.030)), // latency-bound ~4× offline
        (DiarMode::Online, DiarBackend::Sherpa) => Some((0.174, 0.58)), // periodic offline re-diarization
        _ => None,
    }
}

/// One routing decision: which diarizer, and its quality + speed.
///
/// Mirrors the toolbox's frozen `BackendPlan` dataclass: `expected_der` /
/// `expected_rtf` are looked up from [`profile`] for `(mode, backend)` so the
/// numbers can never contradict the choice, and `reason` names the deciding
/// measurement so the pick is never a black box.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BackendPlan {
    /// Online (streaming) or offline (whole-buffer) diarizer.
    pub mode: DiarMode,
    /// The chosen diarization backend.
    pub backend: DiarBackend,
    /// Representative diarization error rate (quality — lower is better).
    pub expected_der: f64,
    /// Representative real-time factor (speed — `< 1` is faster than real time).
    pub expected_rtf: f64,
    /// Human-readable justification citing the deciding measurement.
    pub reason: String,
}

impl BackendPlan {
    /// Assemble a plan, attaching quality + speed from [`profile`].
    ///
    /// # Panics
    /// Panics if `(mode, backend)` is not a pair the router emits — an internal
    /// invariant, unreachable from [`select_diarization`], which only ever names
    /// pairs present in [`profile`].
    fn new(mode: DiarMode, backend: DiarBackend, reason: impl Into<String>) -> Self {
        // Single source of truth for the scenario's quality/speed — no hand-typed
        // numbers at the call sites that could drift from the reason.
        let (der, rtf) = profile(mode, backend)
            .expect("router only names (mode, backend) pairs present in the profile table");
        BackendPlan {
            mode,
            backend,
            expected_der: der,
            expected_rtf: rtf,
            reason: reason.into(),
        }
    }
}

/// The scenario conditions that move diarization DER (quality) and RTF (speed).
///
/// A parameter struct stands in for the toolbox's keyword-only arguments; build
/// from [`DiarizationQuery::default`] and set the fields the caller knows. The
/// defaults match the Python signature's defaults (batch file, unknown length,
/// PyTorch available, both backends installed).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DiarizationQuery {
    /// `true` for a live stream (streaming diarizer), `false` for a batch file.
    pub live: bool,
    /// Audio duration in seconds when known. `None` = unknown, treated as
    /// long-form (the safe, robust branch).
    pub duration_s: Option<f64>,
    /// Known upper bound on the speaker count, to keep >4-speaker audio off the
    /// Sortformer (NeMo) 4-speaker cap. `None` = unknown.
    pub max_speakers: Option<u32>,
    /// `true` when the deployment cannot install PyTorch — forces the `sherpa`
    /// onnxruntime backend regardless of length.
    pub torch_free: bool,
    /// Whether the pyannote backend can actually run (extra installed + bundle
    /// present). When a rule would pick pyannote but it is unavailable, the
    /// router falls back rather than choosing an unrunnable backend.
    pub pyannote_available: bool,
    /// Whether the NeMo Sortformer backend can actually run. When the short/dense
    /// rule would pick nemo but it is not installed, the router falls through to
    /// the robust pyannote branch.
    pub nemo_available: bool,
}

impl Default for DiarizationQuery {
    fn default() -> Self {
        // Matches the Python defaults: offline batch file, unknown duration and
        // speaker count, PyTorch available, both heavy backends installed.
        DiarizationQuery {
            live: false,
            duration_s: None,
            max_speakers: None,
            torch_free: false,
            pyannote_available: true,
            nemo_available: true,
        }
    }
}

/// Route to the diarization backend that the experiments justify.
///
/// Encodes the toolbox study's crossover (see the module docs) as a single,
/// testable decision over the conditions that actually move DER (quality) and RTF
/// (speed): live-vs-batch, duration, speaker count, and torch-availability. The
/// returned [`BackendPlan`] carries both axes explicitly.
///
/// # Examples
/// ```
/// use nh_core::router::{select_diarization, DiarBackend, DiarizationQuery};
///
/// // Short, few-speaker file → NeMo Sortformer (confusion ~0).
/// let plan = select_diarization(DiarizationQuery {
///     duration_s: Some(45.0),
///     max_speakers: Some(3),
///     ..Default::default()
/// });
/// assert_eq!(plan.backend, DiarBackend::Nemo);
/// assert_eq!((plan.expected_der, plan.expected_rtf), (0.142, 0.051));
///
/// // Long / unknown length → pyannote, the robust default.
/// let long = select_diarization(DiarizationQuery { duration_s: Some(1800.0), ..Default::default() });
/// assert_eq!(long.backend, DiarBackend::Pyannote);
///
/// // No PyTorch → the torch-free sherpa path.
/// let portable = select_diarization(DiarizationQuery { live: true, torch_free: true, ..Default::default() });
/// assert_eq!(portable.backend, DiarBackend::Sherpa);
/// ```
pub fn select_diarization(q: DiarizationQuery) -> BackendPlan {
    let mode = if q.live {
        DiarMode::Online
    } else {
        DiarMode::Offline
    };

    // 1. Portability override — no PyTorch available. The torch-free ONNX
    //    TitaNet (sherpa) is the only runnable backend; quality beats NeMo
    //    Sortformer, so it is a safe forced pick. For streaming, sherpa runs as
    //    periodic offline re-diarization (ADR 0002: per-segment online sherpa is
    //    a dead end), which is why online sherpa carries the offline DER 0.174.
    if q.torch_free {
        let mut reason = String::from(
            "torch-free deployment → sherpa (onnxruntime TitaNet-large, no PyTorch); \
             DER 0.174 ES2011a / 0.148 held-out IS1008a, beats NeMo Sortformer 0.267, \
             FR+EN validated (ADR 0002)",
        );
        if q.live {
            reason.push_str("; streaming = periodic offline re-diarization");
        }
        return BackendPlan::new(mode, DiarBackend::Sherpa, reason);
    }

    // 2. STREAMING → nemo, always. The OnlineDiarStage is a latency-bound
    //    cosine-clustering approximation (~3-4× the offline DER); the NeMo TitaNet
    //    embedder is the best online backend at *every* length measured here
    //    (bagarre 0.586, AMI 0.497) and beats pyannote/embedding online. There is
    //    no online length crossover, and the 4-speaker cap is Sortformer/offline
    //    only (the online path uses TitaNet embeddings, uncapped).
    if q.live {
        let (der, rtf) =
            profile(DiarMode::Online, DiarBackend::Nemo).expect("online/nemo profiled");
        return BackendPlan::new(
            DiarMode::Online,
            DiarBackend::Nemo,
            format!(
                "live stream → nemo TitaNet embedder (best online backend at every \
                 length; DER {der}, RTF {rtf} — online is a latency-bound ~3-4x-offline \
                 approximation, refine_on_close helps long meetings)"
            ),
        );
    }

    // 3. OFFLINE short / dense regime — NeMo Sortformer wins by ~2.3× on short
    //    interleaved turns (DER 0.142 vs pyannote 0.330). Its 4-speaker cap means
    //    a *known* larger count must skip it, and "unknown duration" is
    //    deliberately NOT short — without a length we take the robust branch.
    let too_many_speakers = q.max_speakers.is_some_and(|n| n > SORTFORMER_MAX_SPEAKERS);
    let short_enough = q.duration_s.is_some_and(|d| d <= NEMO_MAX_DURATION_S);
    if short_enough && !too_many_speakers && q.nemo_available {
        let (der, rtf) =
            profile(DiarMode::Offline, DiarBackend::Nemo).expect("offline/nemo profiled");
        return BackendPlan::new(
            DiarMode::Offline,
            DiarBackend::Nemo,
            format!(
                "≤{NEMO_MAX_DURATION_S:.0}s, ≤{SORTFORMER_MAX_SPEAKERS} speakers → \
                 nemo Sortformer (end-to-end, confusion ~0; DER {der}, RTF {rtf})"
            ),
        );
    }

    // 4. OFFLINE long-form / unknown / many-speaker → pyannote, the robust default
    //    (AMI median DER 0.122); NeMo hangs past ~25 min and caps at 4 speakers.
    if q.pyannote_available {
        // Name the *actual* reason nemo was skipped so the operator nudge is
        // honest — a short clip can reach pyannote purely because the nemo extra
        // is absent, not because it is long or crowded.
        let why_long = if q.duration_s.is_none() {
            "unknown duration (treated as long-form)".to_string()
        } else if !short_enough {
            format!(">{NEMO_MAX_DURATION_S:.0}s")
        } else if too_many_speakers {
            format!(">{SORTFORMER_MAX_SPEAKERS} speakers")
        } else {
            "nemo extra not installed".to_string()
        };
        let (der, rtf) =
            profile(DiarMode::Offline, DiarBackend::Pyannote).expect("offline/pyannote profiled");
        return BackendPlan::new(
            DiarMode::Offline,
            DiarBackend::Pyannote,
            format!(
                "{why_long} → pyannote 3.1 (robust default; DER {der}, RTF {rtf}; \
                 nemo hangs past ~25 min / >4 speakers)"
            ),
        );
    }

    // 5. pyannote was the right call but is not installed/bundled — fall back to
    //    the torch-free sherpa rather than an unrunnable backend. (nemo is unsafe
    //    here: this branch is exactly the long/many-speaker case it fails on.)
    BackendPlan::new(
        DiarMode::Offline,
        DiarBackend::Sherpa,
        "pyannote unavailable on the long-form/robust branch → sherpa \
         (onnxruntime TitaNet); nemo is unsafe past ~25 min / >4 speakers",
    )
}

#[cfg(test)]
mod tests {
    //! Pure decision logic, no models — every rule of [`select_diarization`] is
    //! translated from the toolbox `tests/test_router.py` so the app's router
    //! stays behaviour-identical to its Python reference.
    use super::*;

    #[test]
    fn offline_routes_by_duration_and_speakers() {
        // (duration_s, max_speakers) -> backend, matching test_router.py's table.
        let short = select_diarization(DiarizationQuery {
            duration_s: Some(30.0),
            max_speakers: Some(2),
            ..Default::default()
        });
        assert_eq!(short.mode, DiarMode::Offline);
        assert_eq!(short.backend, DiarBackend::Nemo);
        assert_eq!((short.expected_der, short.expected_rtf), (0.142, 0.051));

        // At the 300 s boundary NeMo is still eligible (≤, inclusive).
        assert_eq!(
            select_diarization(DiarizationQuery {
                duration_s: Some(300.0),
                ..Default::default()
            })
            .backend,
            DiarBackend::Nemo
        );
        // Just past it → pyannote.
        assert_eq!(
            select_diarization(DiarizationQuery {
                duration_s: Some(301.0),
                ..Default::default()
            })
            .backend,
            DiarBackend::Pyannote
        );
        // Short but too many speakers → pyannote (Sortformer's 4-speaker cap).
        assert_eq!(
            select_diarization(DiarizationQuery {
                duration_s: Some(45.0),
                max_speakers: Some(5),
                ..Default::default()
            })
            .backend,
            DiarBackend::Pyannote
        );
        // Unknown duration is treated as long-form → pyannote.
        assert_eq!(
            select_diarization(DiarizationQuery::default()).backend,
            DiarBackend::Pyannote
        );
    }

    #[test]
    fn stream_always_picks_online_nemo() {
        for q in [
            DiarizationQuery {
                live: true,
                ..Default::default()
            },
            DiarizationQuery {
                live: true,
                duration_s: Some(20.0),
                ..Default::default()
            },
            DiarizationQuery {
                live: true,
                duration_s: Some(9999.0),
                max_speakers: Some(8),
                ..Default::default()
            },
        ] {
            let plan = select_diarization(q);
            assert_eq!(
                (plan.mode, plan.backend),
                (DiarMode::Online, DiarBackend::Nemo)
            );
            assert_eq!((plan.expected_der, plan.expected_rtf), (0.586, 0.030));
        }
    }

    #[test]
    fn torch_free_forces_sherpa_and_notes_rediarization() {
        assert_eq!(
            select_diarization(DiarizationQuery {
                duration_s: Some(20.0),
                torch_free: true,
                ..Default::default()
            })
            .backend,
            DiarBackend::Sherpa
        );
        assert_eq!(
            select_diarization(DiarizationQuery {
                live: true,
                duration_s: Some(9999.0),
                torch_free: true,
                ..Default::default()
            })
            .backend,
            DiarBackend::Sherpa
        );
        let live = select_diarization(DiarizationQuery {
            live: true,
            torch_free: true,
            ..Default::default()
        });
        assert_eq!(live.backend, DiarBackend::Sherpa);
        assert!(live.reason.contains("periodic offline re-diarization"));
    }

    #[test]
    fn pyannote_unavailable_falls_back_to_sherpa_not_nemo() {
        let plan = select_diarization(DiarizationQuery {
            duration_s: Some(3600.0),
            pyannote_available: false,
            ..Default::default()
        });
        assert_eq!(plan.backend, DiarBackend::Sherpa);
    }

    #[test]
    fn short_clip_without_nemo_extra_routes_to_pyannote() {
        let plan = select_diarization(DiarizationQuery {
            duration_s: Some(45.0),
            nemo_available: false,
            ..Default::default()
        });
        assert_eq!(plan.backend, DiarBackend::Pyannote);
        assert!(plan.reason.contains("nemo extra not installed"));
    }

    #[test]
    fn mode_tracks_the_live_flag() {
        assert_eq!(
            select_diarization(DiarizationQuery {
                live: false,
                duration_s: Some(45.0),
                max_speakers: Some(2),
                ..Default::default()
            })
            .mode,
            DiarMode::Offline
        );
        assert_eq!(
            select_diarization(DiarizationQuery {
                live: true,
                duration_s: Some(45.0),
                max_speakers: Some(2),
                ..Default::default()
            })
            .mode,
            DiarMode::Online
        );
    }

    #[test]
    fn every_plan_reports_self_consistent_profile_numbers() {
        // Sweep the meaningful corners; each plan's DER/RTF must equal the table.
        let corners = [
            DiarizationQuery {
                duration_s: Some(30.0),
                max_speakers: Some(2),
                ..Default::default()
            },
            DiarizationQuery {
                duration_s: Some(1800.0),
                ..Default::default()
            },
            DiarizationQuery {
                live: true,
                ..Default::default()
            },
            DiarizationQuery {
                torch_free: true,
                ..Default::default()
            },
            DiarizationQuery {
                duration_s: Some(3600.0),
                pyannote_available: false,
                ..Default::default()
            },
        ];
        for q in corners {
            let plan = select_diarization(q);
            assert_eq!(
                profile(plan.mode, plan.backend),
                Some((plan.expected_der, plan.expected_rtf))
            );
            assert!(plan.expected_der > 0.0 && plan.expected_rtf > 0.0);
            assert!(!plan.reason.is_empty()); // every decision is justified
        }
        // The study invariants the toolbox test pins down.
        let (_, nemo_rtf) = profile(DiarMode::Offline, DiarBackend::Nemo).unwrap();
        let (pyan_der, pyan_rtf) = profile(DiarMode::Offline, DiarBackend::Pyannote).unwrap();
        assert!(nemo_rtf < pyan_rtf); // offline nemo is faster
        let (online_der, _) = profile(DiarMode::Online, DiarBackend::Nemo).unwrap();
        let (offline_der, _) = profile(DiarMode::Offline, DiarBackend::Nemo).unwrap();
        assert!(online_der > 3.0 * offline_der); // online is a latency-bound ~4× approximation
        let (sherpa_der, _) = profile(DiarMode::Offline, DiarBackend::Sherpa).unwrap();
        assert!(sherpa_der > pyan_der); // sherpa trades DER for portability vs pyannote
    }
}
