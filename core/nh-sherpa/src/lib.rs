//! `nh-sherpa` — sherpa-onnx speaker-diarization adapter for `nh-core`.
//!
//! Module summary
//! --------------
//! Implements [`nh_core::ports::DiarizationEngine`] using sherpa-onnx (through the
//! `sherpa-rs` crate). The real binding is heavy to build, so it lives behind the
//! `sherpa-onnx` feature; the default build compiles a stub that returns a clear error.
//! This keeps the workspace's default `cargo test` fast and green while the real engine is
//! opt-in and validated in a dedicated build
//! (`cargo test -p nh-sherpa --features sherpa-onnx`). Same shape as `nh-whisper`.
//!
//! The engine is the portable, torch-free ONNX pipeline selected by the diarization study
//! (ADR 0002): pyannote community-1 segmentation ONNX + NeMo TitaNet-large embedding ONNX +
//! fast agglomerative clustering. It runs whole-buffer and returns time-ordered speaker
//! turns; the diarize-then-ASR pipeline then transcribes under each turn.

// `sherpa-rs` encapsulates the unsafe sherpa-onnx FFI internally; our adapter stays safe.
#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::path::{Path, PathBuf};

use nh_core::error::Result;
use nh_core::model::{AudioBuffer, DiarizedSegment};
use nh_core::ports::DiarizationEngine;

/// Default cosine clustering threshold used when the speaker count is auto-detected.
///
/// 0.5 is sherpa-onnx's own default and the value the offline study ran; it is the primary
/// knob to sweep if a recording over- or under-splits speakers.
pub const DEFAULT_THRESHOLD: f32 = 0.5;

/// A [`DiarizationEngine`] backed by sherpa-onnx ONNX models on disk.
///
/// Pairs a segmentation ONNX (pyannote community-1) with a speaker-embedding ONNX
/// (TitaNet-large). Both are plain onnxruntime models — no torch, no HuggingFace at
/// runtime — so the exact same pipeline runs on every target, mobile included.
pub struct SherpaDiarizer {
    /// Path to the segmentation ONNX model (e.g. `community1-segmentation.onnx`).
    segmentation_model: PathBuf,
    /// Path to the speaker-embedding ONNX model (e.g. `nemo_en_titanet_large.onnx`).
    embedding_model: PathBuf,
    /// Cosine clustering threshold used when the speaker count is auto-detected.
    threshold: f32,
    /// Fixed speaker count, or `None` to auto-detect from `threshold`.
    num_clusters: Option<i32>,
}

impl SherpaDiarizer {
    /// Create a diarizer over the given segmentation + embedding ONNX models.
    ///
    /// Auto-detects the speaker count (via [`DEFAULT_THRESHOLD`]); use
    /// [`Self::with_num_clusters`] to fix it, or [`Self::with_threshold`] to tune the
    /// auto-detection.
    pub fn new(
        segmentation_model: impl Into<PathBuf>,
        embedding_model: impl Into<PathBuf>,
    ) -> Self {
        Self {
            segmentation_model: segmentation_model.into(),
            embedding_model: embedding_model.into(),
            threshold: DEFAULT_THRESHOLD,
            num_clusters: None,
        }
    }

    /// Set the cosine clustering threshold used when auto-detecting the speaker count.
    #[must_use]
    pub fn with_threshold(mut self, threshold: f32) -> Self {
        self.threshold = threshold;
        self
    }

    /// Fix the number of speakers instead of auto-detecting it.
    #[must_use]
    pub fn with_num_clusters(mut self, num_clusters: i32) -> Self {
        self.num_clusters = Some(num_clusters);
        self
    }

    /// Borrow the configured segmentation model path (useful for logging/diagnostics).
    #[must_use]
    pub fn segmentation_model(&self) -> &Path {
        &self.segmentation_model
    }

    /// Borrow the configured embedding model path (useful for logging/diagnostics).
    #[must_use]
    pub fn embedding_model(&self) -> &Path {
        &self.embedding_model
    }
}

// ---------------------------------------------------------------------------------------
// Real implementation — compiled only with the `sherpa-onnx` feature.
// ---------------------------------------------------------------------------------------
#[cfg(feature = "sherpa-onnx")]
impl DiarizationEngine for SherpaDiarizer {
    fn diarize(&self, audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
        use nh_core::error::CoreError;
        use nh_core::model::SpeakerId;
        use sherpa_rs::diarize::{Diarize, DiarizeConfig};

        // Auto-detect the speaker count by default: sherpa treats a negative num_clusters
        // as "estimate from the threshold". A fixed count (with_num_clusters) overrides it.
        let config = DiarizeConfig {
            num_clusters: Some(self.num_clusters.unwrap_or(-1)),
            threshold: Some(self.threshold),
            // Prune sub-0.3 s turns and split on 0.5 s silences — the study's settings.
            min_duration_on: Some(0.3),
            min_duration_off: Some(0.5),
            ..Default::default()
        };

        // Build the pipeline per call (loads the ONNX models). Caching the engine across
        // calls is a later optimization once the pipeline owns the diarizer, mirroring the
        // whisper adapter's per-call load.
        let mut diarizer = Diarize::new(&self.segmentation_model, &self.embedding_model, config)
            .map_err(|e| CoreError::Transcription(format!("sherpa diarization init: {e}")))?;

        // sherpa clusters the whole buffer inside one call; hand it the full recording.
        let segments = diarizer
            .compute(audio.samples.clone(), None)
            .map_err(|e| CoreError::Transcription(format!("sherpa diarization: {e}")))?;

        // Map sherpa's (start, end, speaker-int) to the port's DiarizedSegment, stringifying
        // the integer speaker id like the other adapters (`S0`, `S1`, …).
        Ok(segments
            .into_iter()
            .map(|s| DiarizedSegment {
                t0: f64::from(s.start),
                t1: f64::from(s.end),
                speaker: SpeakerId::new(format!("S{}", s.speaker)),
            })
            .collect())
    }
}

// ---------------------------------------------------------------------------------------
// Stub implementation — compiled when the `sherpa-onnx` feature is OFF (the default).
// ---------------------------------------------------------------------------------------
#[cfg(not(feature = "sherpa-onnx"))]
impl DiarizationEngine for SherpaDiarizer {
    fn diarize(&self, _audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
        use nh_core::error::CoreError;

        // The native sherpa-onnx build was not compiled in. Returning a typed error (not a
        // panic) keeps the port contract honest; enable the real engine with
        // `--features sherpa-onnx`.
        Err(CoreError::Transcription(
            "nh-sherpa built without the `sherpa-onnx` feature".to_string(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builder_sets_paths_and_knobs() {
        let d = SherpaDiarizer::new("seg.onnx", "emb.onnx")
            .with_threshold(0.6)
            .with_num_clusters(3);
        assert_eq!(d.segmentation_model(), Path::new("seg.onnx"));
        assert_eq!(d.embedding_model(), Path::new("emb.onnx"));
        assert_eq!(d.threshold, 0.6);
        assert_eq!(d.num_clusters, Some(3));
    }

    // Without the `sherpa-onnx` feature the adapter must fail cleanly, never panic, so the
    // default workspace build/CI stays green with no native dependency.
    #[cfg(not(feature = "sherpa-onnx"))]
    #[test]
    fn stub_returns_typed_error() {
        use nh_core::model::AudioBuffer;
        use nh_core::ports::DiarizationEngine;

        let d = SherpaDiarizer::new("seg.onnx", "emb.onnx");
        let audio = AudioBuffer {
            sample_rate: 16_000,
            samples: vec![0.0; 16_000],
        };
        let err = d.diarize(&audio).unwrap_err();
        assert!(err.to_string().contains("sherpa-onnx"));
    }
}
