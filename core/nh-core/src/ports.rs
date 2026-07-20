//! Ports — the trait boundary between `nh-core` and the outside world.
//!
//! Module summary
//! --------------
//! Adapters implement these traits: per engine (whisper.cpp / sherpa-onnx / llama.cpp,
//! wired in M1) and per OS/device (capture, storage, export, share, identity). The core
//! only ever depends on the traits, so opening a new platform means writing adapters,
//! never editing the core (ports-and-adapters; see `ARCHITECTURE.md`).

use crate::error::Result;
use crate::model::{
    AudioBuffer, DiarizedSegment, MeetingContext, Report, Summary, Transcript, Utterance,
};

/// Loads audio from a single, already-resolved offline source into a canonical
/// [`AudioBuffer`].
///
/// The OBS-style multi-source graph mixes several inputs upstream; this port models the
/// resolved, single-buffer offline input the pipeline consumes.
pub trait AudioSource {
    /// Decode/resample the source into a 16 kHz mono buffer.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Capture`] if the source cannot be read.
    fn load(&self) -> Result<AudioBuffer>;
}

/// Turns audio into a diarized, timestamped [`Transcript`].
///
/// This is the composite the pipeline consumes; a real implementation combines an
/// [`AsrEngine`] (text + timing) with a diarization pass (who-spoke-when) so the core
/// neither knows nor cares which engines back it.
pub trait TranscriptionEngine {
    /// Transcribe and diarize a whole buffer (offline, whole-buffer = best DER).
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Transcription`] on engine failure.
    fn transcribe(&self, audio: &AudioBuffer) -> Result<Transcript>;
}

/// Turns audio into raw ASR utterances (text + timing) WITHOUT speaker attribution.
///
/// whisper.cpp lives behind this port; diarization (sherpa-onnx) assigns speakers in a
/// separate step, and a composite adapter merges the two into a [`Transcript`]. Splitting
/// ASR from diarization keeps each engine independently swappable and testable.
pub trait AsrEngine {
    /// Transcribe a whole buffer into time-stamped utterances; the speaker field is a
    /// placeholder the diarization step overwrites.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Transcription`] on engine failure.
    fn transcribe(&self, audio: &AudioBuffer) -> Result<Vec<Utterance>>;
}

/// Reads a spoken-language posterior from a buffer (whisper's language head).
///
/// whisper.cpp lives behind this port too (see the nh-whisper adapter). Splitting it out
/// lets [`crate::lid::detect_language_regions`] segment a code-switching recording into
/// mono-language spans against a mock detector in tests, with the real model swapped in at
/// runtime. Language is discovered from the audio, never defaulted.
pub trait LanguageDetector {
    /// Return a posterior over language codes for `audio`: `(iso_code, probability)`
    /// pairs covering the model's full candidate set (so any language can surface).
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Transcription`] on engine failure.
    fn detect_language(&self, audio: &AudioBuffer) -> Result<Vec<(String, f32)>>;
}

/// Judges whether a (partial) utterance looks like a *completed* speaker turn.
///
/// A small local LLM lives behind this port (see the nh-synth adapter). It powers
/// [`crate::eot::gate_turns`], which merges a breath-split utterance back together
/// instead of letting a rigid VAD silence threshold fragment one thought into
/// several — the semantic end-of-turn idea from the toolbox. Kept a port so the
/// gate is testable against a mock, with Ollama swapped in at runtime.
pub trait EndOfTurnClassifier {
    /// Return `true` iff `text` reads as a complete turn (the speaker could hand
    /// over the floor). Implementations should fail *open* where sensible — an
    /// offline classifier returning an error is treated by the gate as "complete"
    /// so gating never swallows a turn.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Synthesis`] on classifier failure.
    fn is_complete_turn(&self, text: &str) -> Result<bool>;
}

/// Segments a buffer into speaker turns (who-spoke-when), WITHOUT transcribing.
///
/// sherpa-onnx (VAD + segmentation + speaker embeddings) lives behind this port. Its
/// output feeds the diarize-then-ASR pipeline, which runs ASR under each turn — the
/// offline strategy translated from `vocal_helper`.
pub trait DiarizationEngine {
    /// Diarize the whole buffer into time-ordered speaker turns.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Transcription`] on engine failure.
    fn diarize(&self, audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>>;
}

/// Produces the local-LLM [`Summary`] (llama.cpp behind this port).
pub trait Synthesizer {
    /// Summarize a transcript, weaving in any user-provided meeting context.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Synthesis`] on engine failure.
    fn synthesize(&self, transcript: &Transcript, context: &MeetingContext) -> Result<Summary>;
}

/// Persists the raw recording as a real, user-recoverable file FIRST ("local file
/// first") and returns where the user can find it.
///
/// Implemented per OS (Finder folder, iOS Files/iCloud/Photos, Android MediaStore/SAF).
pub trait RecordingStore {
    /// Persist `bytes` under a session-scoped, user-visible location; return a
    /// human-facing path/URI the user can open.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Store`] if persistence fails.
    fn persist(&self, session_id: &str, filename: &str, bytes: &[u8]) -> Result<String>;
}

/// The formats a finished [`Report`] can be exported to.
///
/// HTML is always available (self-contained, shareable as an attachment); PDF and DOCX
/// are produced locally on every platform without Pandoc (see TECHNICAL_QUESTIONS Q10).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExportFormat {
    /// Self-contained interactive HTML (the notes-helper-style report).
    Html,
    /// PDF, produced by printing the HTML through the system WebView.
    Pdf,
    /// DOCX, produced from the report model via `docx-rs`.
    Docx,
}

/// Renders/exports a finished [`Report`] to a file on disk (wired in M2+).
pub trait FileExport {
    /// Export the report to `format`, returning the path of the produced file.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Export`] on failure.
    fn export(&self, report: &Report, format: ExportFormat) -> Result<String>;
}

/// Publishes a self-contained artifact to the USER'S OWN infrastructure.
///
/// There is no developer-hosted default: this is unavailable until the user configures
/// their own bucket/SFTP, and it is the single explicit egress point (opt-in, Q8).
pub trait ShareTarget {
    /// Upload `bytes` and return a shareable URL the user can send by email.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Share`] on failure.
    fn publish(&self, filename: &str, bytes: &[u8]) -> Result<String>;
}

/// Stores/loads the on-device speaker-identity vault and moves it between the user's
/// own devices as an encrypted, portable pack (never an automatic cloud sync, Q12).
pub trait IdentityVault {
    /// Export the vault as an encrypted, portable pack.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Identity`] on failure.
    fn export_pack(&self) -> Result<Vec<u8>>;

    /// Import a previously exported pack, merging speakers/persons into the vault.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Identity`] on failure.
    fn import_pack(&self, pack: &[u8]) -> Result<()>;
}

/// Provisions model files from the configured source (Warith's FTP) with hash
/// verification and local caching, selecting the device tier (M1/Q11).
pub trait ModelProvider {
    /// Ensure the model identified by `name` is available locally; return its path.
    ///
    /// # Errors
    /// Returns [`crate::error::CoreError::Model`] if fetching or verifying fails.
    fn ensure(&self, name: &str) -> Result<String>;
}
