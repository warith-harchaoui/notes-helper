//! Integration test: prove the offline seam wires source → engine → synth → report
//! with mock adapters, before any real engine exists. This is the M0 acceptance test:
//! the orchestration and the port boundary hold together and the report serializes.

use nh_core::error::Result;
use nh_core::model::SessionId;
use nh_core::model::{
    AudioBuffer, MeetingContext, Speaker, SpeakerId, Summary, Transcript, Utterance,
    PIPELINE_SAMPLE_RATE,
};
use nh_core::ports::{AudioSource, Synthesizer, TranscriptionEngine};
use nh_core::Session;

/// A source that returns one second of silence at the canonical rate — enough signal to
/// exercise the seam without needing a real file or codec.
struct MockSource;

impl AudioSource for MockSource {
    fn load(&self) -> Result<AudioBuffer> {
        // One second of silence: `PIPELINE_SAMPLE_RATE` mono samples.
        Ok(AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            vec![0.0; PIPELINE_SAMPLE_RATE as usize],
        ))
    }
}

/// An engine that returns a single canned utterance, standing in for whisper+sherpa.
struct MockEngine;

impl TranscriptionEngine for MockEngine {
    fn transcribe(&self, audio: &AudioBuffer) -> Result<Transcript> {
        // The engine should always receive a non-empty buffer from a valid source.
        assert!(!audio.is_empty(), "engine received empty audio");

        // Fabricate one utterance attributed to a single speaker "S0".
        let speaker = SpeakerId::new("S0");
        let utterance = Utterance {
            t0: 0.0,
            t1: 1.0,
            speaker: speaker.clone(),
            text: "hello world".to_string(),
            words: Vec::new(),
            language: Some("en".to_string()),
        };

        Ok(Transcript {
            utterances: vec![utterance],
            speakers: vec![Speaker::new(speaker)],
        })
    }
}

/// A synthesizer that writes a trivial overview, standing in for the local LLM.
struct MockSynth;

impl Synthesizer for MockSynth {
    fn synthesize(&self, transcript: &Transcript, _context: &MeetingContext) -> Result<Summary> {
        // Produce a minimal, grounded overview: the number of utterances seen.
        let mut summary = Summary::empty();
        summary.overview = format!("{} utterance(s)", transcript.utterances.len());
        Ok(summary)
    }
}

#[test]
fn offline_seam_produces_report() {
    // Build a session with no pre-resolved sources (the mock source stands in) and run
    // the offline path end-to-end through the mock ports.
    let session = Session::new(SessionId::new("2026-07-18-demo"), Vec::new());
    let report = session
        .run_offline(&MockSource, &MockEngine, &MockSynth)
        .expect("offline run should succeed with valid mocks");

    // The report should carry the one utterance and the derived overview and title.
    assert_eq!(report.transcript.utterances.len(), 1);
    assert_eq!(report.summary.overview, "1 utterance(s)");
    assert!(report.title.contains("2026-07-18-demo"));
}

#[test]
fn report_serializes_to_json() {
    // Serialization is on the critical path for the HTML/JSON renderers (M2), so we
    // pin it here: a produced report must round-trip through serde_json without loss.
    let session = Session::new(SessionId::new("s"), Vec::new());
    let report = session
        .run_offline(&MockSource, &MockEngine, &MockSynth)
        .expect("offline run should succeed");

    let json = serde_json::to_string(&report).expect("report should serialize to JSON");
    assert!(
        json.contains("\"title\""),
        "serialized report missing title"
    );
}
