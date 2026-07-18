//! Typed error surface for `nh-core`.
//!
//! One variant per subsystem so callers (and logs) can tell a capture failure from a
//! transcription failure from an export failure. Library code returns these instead of
//! panicking; `.unwrap()`/`.expect()` on a fallible path is forbidden (CODING.md, Rust).

use thiserror::Error;

/// Convenience alias so the crate's fallible functions read as `Result<T>`.
pub type Result<T> = std::result::Result<T, CoreError>;

/// Every way a core operation can fail, grouped by the subsystem that produced it.
///
/// The payload is a human-readable message coming from the failing adapter; richer
/// structured causes can be added per variant as the engines land (M1+).
#[derive(Debug, Error)]
pub enum CoreError {
    /// A source could not be captured, decoded, or resampled.
    #[error("audio capture/decode failed: {0}")]
    Capture(String),

    /// ASR/diarization (whisper.cpp + sherpa-onnx, behind the port) failed.
    #[error("transcription failed: {0}")]
    Transcription(String),

    /// Local-LLM synthesis (llama.cpp, behind the port) failed.
    #[error("synthesis failed: {0}")]
    Synthesis(String),

    /// Persisting the raw recording as a user-recoverable file failed.
    #[error("recording store failed: {0}")]
    Store(String),

    /// Rendering/exporting the finished report (HTML/PDF/DOCX) failed.
    #[error("export failed: {0}")]
    Export(String),

    /// Publishing to the user's own share infrastructure failed (opt-in egress).
    #[error("share failed: {0}")]
    Share(String),

    /// Reading/writing the speaker-identity vault or its portable pack failed.
    #[error("identity vault failed: {0}")]
    Identity(String),

    /// Provisioning a model (fetch/verify/cache from the configured source) failed.
    #[error("model provisioning failed: {0}")]
    Model(String),
}
