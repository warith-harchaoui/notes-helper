//! `nh-whisper` — whisper.cpp ASR adapter for `nh-core`.
//!
//! Module summary
//! --------------
//! Implements [`nh_core::ports::AsrEngine`] using whisper.cpp (through `whisper-rs`). The
//! real binding is heavy to build, so it lives behind the `whisper-cpp` feature; the
//! default build compiles a stub that returns a clear error. This keeps the workspace's
//! default `cargo test` fast and green while the real engine is opt-in and validated in a
//! dedicated build (`cargo test -p nh-whisper --features whisper-cpp`).
//!
//! The engine returns raw utterances with a placeholder speaker; real speaker labels come
//! from the diarization adapter (sherpa-onnx) and a composite that merges the two.

// `whisper-rs` encapsulates the unsafe FFI internally; our adapter code stays safe.
#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::path::PathBuf;

use nh_core::error::{CoreError, Result};
use nh_core::model::{AudioBuffer, Utterance};
use nh_core::ports::AsrEngine;

/// An [`AsrEngine`] backed by a whisper.cpp ggml model on disk.
pub struct WhisperAsr {
    /// Path to the ggml model file (e.g. `ggml-base.bin`, or a quantized large-v3-turbo).
    model_path: PathBuf,
}

impl WhisperAsr {
    /// Create an engine that will load the ggml model at `model_path`.
    pub fn new(model_path: impl Into<PathBuf>) -> Self {
        Self {
            model_path: model_path.into(),
        }
    }

    /// Borrow the configured model path (useful for logging/diagnostics).
    #[must_use]
    pub fn model_path(&self) -> &std::path::Path {
        &self.model_path
    }
}

// ---------------------------------------------------------------------------------------
// Real implementation — compiled only with the `whisper-cpp` feature.
// ---------------------------------------------------------------------------------------
#[cfg(feature = "whisper-cpp")]
impl AsrEngine for WhisperAsr {
    fn transcribe(&self, audio: &AudioBuffer) -> Result<Vec<Utterance>> {
        use nh_core::model::SpeakerId;
        use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

        // Load the ggml model. For M1 we load per call; caching the context across calls
        // is a later optimization once the pipeline holds the engine.
        let ctx = WhisperContext::new_with_params(
            &self.model_path.to_string_lossy(),
            WhisperContextParameters::default(),
        )
        .map_err(|e| CoreError::Transcription(format!("load whisper model: {e}")))?;
        let mut state = ctx
            .create_state()
            .map_err(|e| CoreError::Transcription(format!("whisper create state: {e}")))?;

        // Greedy decoding is enough for the offline baseline; auto-detect the language so
        // code-switching meetings are not locked to one language up front.
        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_language(Some("auto"));

        // Run the model over the whole 16 kHz mono buffer (offline whole-buffer path).
        state
            .full(params, &audio.samples)
            .map_err(|e| CoreError::Transcription(format!("whisper decode: {e}")))?;

        // Turn each whisper segment into an utterance. The speaker is a placeholder; the
        // diarization step assigns the real one.
        let segments = state
            .full_n_segments()
            .map_err(|e| CoreError::Transcription(format!("segment count: {e}")))?;
        let placeholder = SpeakerId::new("S0");
        let mut utterances = Vec::new();
        for i in 0..segments {
            let text = state
                .full_get_segment_text(i)
                .map_err(|e| CoreError::Transcription(format!("segment text: {e}")))?;
            // whisper timestamps are in centiseconds (10 ms units) → seconds.
            let t0 = state.full_get_segment_t0(i).unwrap_or(0) as f64 / 100.0;
            let t1 = state.full_get_segment_t1(i).unwrap_or(0) as f64 / 100.0;
            utterances.push(Utterance {
                t0,
                t1,
                speaker: placeholder.clone(),
                text: text.trim().to_string(),
                words: Vec::new(),
                language: None,
            });
        }
        Ok(utterances)
    }
}

// ---------------------------------------------------------------------------------------
// Stub implementation — compiled when the `whisper-cpp` feature is OFF (the default).
// ---------------------------------------------------------------------------------------
#[cfg(not(feature = "whisper-cpp"))]
impl AsrEngine for WhisperAsr {
    fn transcribe(&self, _audio: &AudioBuffer) -> Result<Vec<Utterance>> {
        // The native whisper.cpp build was not compiled in. Returning a typed error (not a
        // panic) keeps the port contract honest; enable the real engine with
        // `--features whisper-cpp`.
        Err(CoreError::Transcription(
            "nh-whisper built without the `whisper-cpp` feature".to_string(),
        ))
    }
}
