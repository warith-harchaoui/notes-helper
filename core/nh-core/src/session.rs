//! `Session` — one discussion, from resolved sources to a finished report.
//!
//! Module summary
//! --------------
//! A [`Session`] owns the identity of a discussion, its resolved input sources, and the
//! optional meeting context. Its orchestration methods drive the pipeline purely through
//! the ports in [`crate::ports`], so the exact same code runs with real engines (M1) or
//! with mocks (tests) — that decoupling is the whole point of M0.

use crate::error::Result;
use crate::model::{MeetingContext, Report, SessionId, Source};
use crate::ports::{AudioSource, Synthesizer, TranscriptionEngine};

/// A single discussion: its identity, its resolved input sources, and its context.
pub struct Session {
    /// Stable id for this discussion (scopes the per-session output folder).
    pub id: SessionId,
    /// The OBS-style source graph, already resolved to the inputs to process.
    pub sources: Vec<Source>,
    /// Optional user-provided context (people, notes) fed to the synthesizer.
    pub context: MeetingContext,
}

impl Session {
    /// Create a session with the given id and sources and an empty context.
    pub fn new(id: SessionId, sources: Vec<Source>) -> Self {
        Self {
            id,
            sources,
            context: MeetingContext::empty(),
        }
    }

    /// Run the OFFLINE path (whole-buffer, best quality) over a single resolved
    /// `source`, producing a full [`Report`].
    ///
    /// The engines are injected as ports, so this orchestration is identical whether the
    /// backends are real (M1) or mocked (tests).
    ///
    /// # Errors
    /// Propagates any [`crate::error::CoreError`] from loading, transcription, or
    /// synthesis.
    pub fn run_offline(
        &self,
        source: &dyn AudioSource,
        engine: &dyn TranscriptionEngine,
        synth: &dyn Synthesizer,
    ) -> Result<Report> {
        // 1) Pull the audio into the canonical 16 kHz mono buffer. Offline means we
        //    process the whole signal at once, which gives the best diarization error
        //    rate (streaming/online is the M4 path).
        let audio = source.load()?;
        tracing::info!(duration_s = audio.duration_s(), "loaded offline audio");

        // 2) Transcribe + diarize the whole buffer through the injected engine.
        let transcript = engine.transcribe(&audio)?;
        // Cast the length to u64: `tracing` records integer fields as u64/i64, not usize.
        tracing::info!(
            utterances = transcript.utterances.len() as u64,
            "transcribed and diarized"
        );

        // 3) Summarize locally, weaving in any user-provided meeting context. A single
        //    cloud call here would break the sovereignty thesis, so the port is always
        //    a local backend.
        let summary = synth.synthesize(&transcript, &self.context)?;

        // 4) Assemble the report. The title is derived from the session id for now;
        //    later milestones enrich the meta (date, participants, figures).
        let report = Report {
            session_id: self.id.clone(),
            title: format!("Notes — {}", self.id.as_str()),
            context: self.context.clone(),
            transcript,
            summary,
        };
        Ok(report)
    }
}
