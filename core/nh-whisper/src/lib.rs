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
//! The model is loaded **eagerly** by [`WhisperAsr::load`] — a bad path or missing file
//! fails fast at construction, not deep inside the first request, and there is no
//! first-call latency spike (the app's standing rule: pre-load, don't lazy-init). Beyond
//! transcription the adapter exposes [`WhisperAsr::detect_language`] via the
//! [`nh_core::ports::LanguageDetector`] port, reading whisper's language head (posterior
//! over every language code) — the model half the [`nh_core::lid`] segmenter consumes.
//! whisper.cpp's own console logging is routed through the `log` facade (silenced by
//! default) so it never pollutes the pipeline's output stream.
//!
//! The engine returns raw utterances with a placeholder speaker; real speaker labels come
//! from the diarization adapter (sherpa-onnx) and a composite that merges the two.

// `whisper-rs` encapsulates the unsafe FFI internally; our adapter code stays safe.
#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::path::PathBuf;

use nh_core::error::{CoreError, Result};
use nh_core::model::{AudioBuffer, Utterance};
#[cfg(not(feature = "whisper-cpp"))]
use nh_core::ports::{AsrEngine, LanguageDetector};

/// An [`AsrEngine`](nh_core::ports::AsrEngine) backed by a whisper.cpp ggml model on disk.
///
/// Construct with [`WhisperAsr::load`], which loads the model up front.
pub struct WhisperAsr {
    /// Path to the ggml model file (e.g. `ggml-base.bin`, or a quantized large-v3-turbo).
    model_path: PathBuf,
    /// Decode threads (whisper.cpp is CPU/Accelerate-bound off the GPU path).
    #[cfg(feature = "whisper-cpp")]
    threads: usize,
    /// The model, loaded eagerly at construction and reused across every call.
    #[cfg(feature = "whisper-cpp")]
    ctx: whisper_rs::WhisperContext,
}

impl WhisperAsr {
    /// Borrow the configured model path (useful for logging/diagnostics).
    #[must_use]
    pub fn model_path(&self) -> &std::path::Path {
        &self.model_path
    }
}

/// A sensible default decode thread count: the machine's cores, capped so we don't
/// oversubscribe on big hosts (whisper.cpp sees diminishing returns past ~8).
#[cfg(feature = "whisper-cpp")]
fn default_threads() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get().min(8))
        .unwrap_or(4)
}

// ---------------------------------------------------------------------------------------
// Real implementation — compiled only with the `whisper-cpp` feature.
// ---------------------------------------------------------------------------------------
#[cfg(feature = "whisper-cpp")]
mod real {
    use super::*;
    use nh_core::model::SpeakerId;
    use nh_core::ports::{AsrEngine, LanguageDetector};
    use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

    /// Route whisper.cpp + ggml's C-side `printf` logging through the `log` facade exactly
    /// once. With no `log` subscriber installed the messages go nowhere, so the raw
    /// `whisper_full_with_state: …` spew never reaches the pipeline's stderr.
    fn silence_native_logging_once() {
        use std::sync::Once;
        static ONCE: Once = Once::new();
        ONCE.call_once(whisper_rs::install_logging_hooks);
    }

    /// Decode parameters with every whisper.cpp console print turned off.
    fn quiet_params<'a>(threads: usize) -> FullParams<'a, 'a> {
        let mut p = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        p.set_n_threads(threads as std::os::raw::c_int);
        p.set_print_progress(false);
        p.set_print_realtime(false);
        p.set_print_timestamps(false);
        p.set_print_special(false);
        p
    }

    impl WhisperAsr {
        /// Load the ggml model at `model_path` eagerly, returning a ready engine.
        ///
        /// The model is built up front so a bad path / unreadable file fails here rather
        /// than on the first `transcribe`, and there is no first-call latency spike.
        ///
        /// # Errors
        /// Returns [`CoreError::Transcription`] if the model cannot be loaded.
        pub fn load(model_path: impl Into<PathBuf>) -> Result<Self> {
            let model_path = model_path.into();
            // Silence the native logging before we touch whisper.cpp at all.
            silence_native_logging_once();
            let ctx = WhisperContext::new_with_params(
                &model_path.to_string_lossy(),
                WhisperContextParameters::default(),
            )
            .map_err(|e| CoreError::Transcription(format!("load whisper model: {e}")))?;
            Ok(Self {
                model_path,
                threads: default_threads(),
                ctx,
            })
        }

        /// Override the decode thread count (defaults to the machine's core count, capped).
        #[must_use]
        pub fn with_threads(mut self, threads: usize) -> Self {
            self.threads = threads.max(1);
            self
        }
    }

    impl LanguageDetector for WhisperAsr {
        /// Read whisper's language head over `audio`: a posterior over every language
        /// code, sorted most-likely first. This is the model half the [`nh_core::lid`]
        /// region segmenter consumes (`language_posterior_curve` in the toolbox).
        ///
        /// Returns `(code, probability)` pairs for whisper's full candidate set, so no
        /// language is filtered out before it can surface — language is discovered, never
        /// defaulted.
        fn detect_language(&self, audio: &AudioBuffer) -> Result<Vec<(String, f32)>> {
            let mut state = self
                .ctx
                .create_state()
                .map_err(|e| CoreError::Transcription(format!("whisper create state: {e}")))?;
            // The language head runs on the mel spectrogram, so compute it first.
            state
                .pcm_to_mel(&audio.samples, self.threads)
                .map_err(|e| CoreError::Transcription(format!("pcm_to_mel: {e}")))?;
            let (_top_id, probs) = state
                .lang_detect(0, self.threads)
                .map_err(|e| CoreError::Transcription(format!("lang_detect: {e}")))?;
            // Map each language id to its ISO code, pairing it with the model's posterior.
            let mut out: Vec<(String, f32)> = (0..=whisper_rs::get_lang_max_id())
                .filter_map(|id| {
                    whisper_rs::get_lang_str(id).map(|code| {
                        (
                            code.to_string(),
                            probs.get(id as usize).copied().unwrap_or(0.0),
                        )
                    })
                })
                .collect();
            // Most-likely first — convenient for a caller that just wants the top guess.
            out.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            Ok(out)
        }
    }

    impl AsrEngine for WhisperAsr {
        fn transcribe(&self, audio: &AudioBuffer) -> Result<Vec<Utterance>> {
            let mut state = self
                .ctx
                .create_state()
                .map_err(|e| CoreError::Transcription(format!("whisper create state: {e}")))?;

            // Greedy decoding is enough for the offline baseline; auto-detect the language
            // so code-switching meetings are not locked to one language up front.
            let mut params = quiet_params(self.threads);
            params.set_language(Some("auto"));

            // Run the model over the whole 16 kHz mono buffer (offline whole-buffer path).
            state
                .full(params, &audio.samples)
                .map_err(|e| CoreError::Transcription(format!("whisper decode: {e}")))?;

            // Turn each whisper segment into an utterance. The speaker is a placeholder;
            // the diarization step assigns the real one.
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
}

// ---------------------------------------------------------------------------------------
// Stub implementation — compiled when the `whisper-cpp` feature is OFF (the default).
// ---------------------------------------------------------------------------------------
#[cfg(not(feature = "whisper-cpp"))]
impl WhisperAsr {
    /// Stub: the native whisper.cpp build is not compiled in, so there is nothing to
    /// load. Fails fast (matching the eager-load contract) — enable the real engine with
    /// `--features whisper-cpp`.
    ///
    /// # Errors
    /// Always returns [`CoreError::Transcription`].
    pub fn load(model_path: impl Into<PathBuf>) -> Result<Self> {
        // Consume the path so the signature matches the real constructor exactly.
        let _ = model_path.into();
        Err(CoreError::Transcription(
            "nh-whisper built without the `whisper-cpp` feature".to_string(),
        ))
    }
}

#[cfg(not(feature = "whisper-cpp"))]
impl LanguageDetector for WhisperAsr {
    fn detect_language(&self, _audio: &AudioBuffer) -> Result<Vec<(String, f32)>> {
        Err(CoreError::Transcription(
            "nh-whisper built without the `whisper-cpp` feature".to_string(),
        ))
    }
}

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
