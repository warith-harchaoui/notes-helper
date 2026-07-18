//! Composite pipeline engines that orchestrate the ported `*-helper` logic.
//!
//! Module summary
//! --------------
//! [`DiarizeThenAsr`] is the offline strategy translated from `vocal_helper`: diarize the
//! whole buffer first (best DER), then run ASR under each speaker turn so every utterance
//! inherits the diarization's speaker. It combines a [`DiarizationEngine`] and an
//! [`AsrEngine`] into the single [`TranscriptionEngine`] the [`crate::Session`] consumes,
//! so swapping engines (sherpa/whisper, or mocks in tests) never touches the session.

use std::collections::BTreeMap;

use crate::error::Result;
use crate::model::{Speaker, SpeakerId, Transcript, Utterance};
use crate::ports::{AsrEngine, DiarizationEngine, TranscriptionEngine};

/// A [`TranscriptionEngine`] that diarizes first, then transcribes each speaker turn.
///
/// Faithful to `vocal_helper`'s offline pipeline: the speaker label comes from
/// diarization, and ASR only fills in the text for that turn's audio span. Batching
/// consecutive same-speaker turns into larger ASR windows (a speed optimization in
/// `vocal_helper`) is deferred; per-turn ASR is correct and simple for the baseline.
pub struct DiarizeThenAsr<D: DiarizationEngine, A: AsrEngine> {
    /// The diarization engine (who spoke when).
    diarizer: D,
    /// The ASR engine (what was said).
    asr: A,
}

impl<D: DiarizationEngine, A: AsrEngine> DiarizeThenAsr<D, A> {
    /// Combine a diarization engine and an ASR engine into one transcription engine.
    pub fn new(diarizer: D, asr: A) -> Self {
        Self { diarizer, asr }
    }
}

impl<D: DiarizationEngine, A: AsrEngine> TranscriptionEngine for DiarizeThenAsr<D, A> {
    fn transcribe(&self, audio: &crate::model::AudioBuffer) -> Result<Transcript> {
        // 1) Diarize the whole buffer into speaker turns (whole-buffer = best DER).
        let segments = self.diarizer.diarize(audio)?;

        let mut utterances = Vec::new();
        // Accumulate speaking time per speaker for the pie chart (keyed by label for a
        // stable, de-duplicated ordering via BTreeMap).
        let mut speaking: BTreeMap<String, f64> = BTreeMap::new();

        // 2) For each turn, transcribe only that span and attach the turn's speaker.
        for seg in &segments {
            // Slice the audio to the turn and run ASR on just that span.
            let sub = audio.slice(seg.t0, seg.t1);
            let parts = self.asr.transcribe(&sub)?;

            // Join the ASR pieces for this turn into one utterance's text, dropping empty
            // fragments (silence/hallucinated blanks the engine may emit).
            let text = parts
                .iter()
                .map(|u| u.text.trim())
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(" ");
            if text.is_empty() {
                // No speech recognized in this turn — do not emit an empty utterance.
                continue;
            }

            // Count this turn's duration toward the speaker's floor time.
            *speaking
                .entry(seg.speaker.as_str().to_string())
                .or_insert(0.0) += (seg.t1 - seg.t0).max(0.0);

            utterances.push(Utterance {
                t0: seg.t0,
                t1: seg.t1,
                speaker: seg.speaker.clone(),
                text,
                words: Vec::new(),
                language: None,
            });
        }

        // 3) Materialize the speaker list with accumulated speaking time.
        let speakers = speaking
            .into_iter()
            .map(|(label, secs)| {
                let mut speaker = Speaker::new(SpeakerId::new(label));
                speaker.speaking_time_s = secs;
                speaker
            })
            .collect();

        Ok(Transcript {
            utterances,
            speakers,
        })
    }
}

/// A baseline [`DiarizationEngine`] that treats the whole buffer as a single speaker.
///
/// It provisions no model and always emits one turn `[0, duration]` labelled `"S0"`. It
/// keeps the offline pipeline runnable end-to-end today (real ffmpeg + real ASR) and is
/// the honest fallback when no diarization model is available; the real multi-speaker
/// engine (sherpa-onnx, in `nh-sherpa`) replaces it without changing the pipeline.
pub struct SingleSpeakerDiarizer {
    /// The single label to attribute all speech to.
    label: String,
}

impl SingleSpeakerDiarizer {
    /// Build a single-speaker diarizer labelling everything `"S0"`.
    #[must_use]
    pub fn new() -> Self {
        Self {
            label: "S0".to_string(),
        }
    }

    /// Build a single-speaker diarizer with an explicit label.
    #[must_use]
    pub fn with_label(label: impl Into<String>) -> Self {
        Self {
            label: label.into(),
        }
    }
}

impl Default for SingleSpeakerDiarizer {
    fn default() -> Self {
        Self::new()
    }
}

impl DiarizationEngine for SingleSpeakerDiarizer {
    fn diarize(
        &self,
        audio: &crate::model::AudioBuffer,
    ) -> Result<Vec<crate::model::DiarizedSegment>> {
        // An empty buffer has no turns at all.
        let duration = audio.duration_s();
        if duration <= 0.0 {
            return Ok(Vec::new());
        }
        // Otherwise, one turn spanning the whole recording for the single speaker.
        Ok(vec![crate::model::DiarizedSegment {
            t0: 0.0,
            t1: duration,
            speaker: SpeakerId::new(self.label.clone()),
        }])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{AudioBuffer, DiarizedSegment, PIPELINE_SAMPLE_RATE};

    /// A diarizer that returns two fixed one-second turns for two speakers.
    struct MockDiar;
    impl DiarizationEngine for MockDiar {
        fn diarize(&self, _audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
            Ok(vec![
                DiarizedSegment {
                    t0: 0.0,
                    t1: 1.0,
                    speaker: SpeakerId::new("S0"),
                },
                DiarizedSegment {
                    t0: 1.0,
                    t1: 2.0,
                    speaker: SpeakerId::new("S1"),
                },
            ])
        }
    }

    /// An ASR engine that returns one canned utterance for whatever span it is given.
    struct MockAsr;
    impl AsrEngine for MockAsr {
        fn transcribe(&self, _audio: &AudioBuffer) -> Result<Vec<Utterance>> {
            Ok(vec![Utterance {
                t0: 0.0,
                t1: 0.0,
                speaker: SpeakerId::new("?"),
                text: "hello".to_string(),
                words: Vec::new(),
                language: None,
            }])
        }
    }

    #[test]
    fn diarize_then_asr_attaches_speakers_and_times() {
        // Two seconds of audio so both one-second turns have samples to slice.
        let audio = AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            vec![0.0; 2 * PIPELINE_SAMPLE_RATE as usize],
        );
        let engine = DiarizeThenAsr::new(MockDiar, MockAsr);

        let transcript = engine.transcribe(&audio).expect("transcribe");

        // One utterance per turn, each carrying the diarization's speaker.
        assert_eq!(transcript.utterances.len(), 2);
        assert_eq!(transcript.utterances[0].speaker.as_str(), "S0");
        assert_eq!(transcript.utterances[1].speaker.as_str(), "S1");
        // Two speakers, each credited with their one second of floor time.
        assert_eq!(transcript.speakers.len(), 2);
        for speaker in &transcript.speakers {
            assert!((speaker.speaking_time_s - 1.0).abs() < 1e-9);
        }
    }

    #[test]
    fn single_speaker_diarizer_covers_whole_buffer() {
        // A 2 s buffer yields exactly one S0 turn spanning [0, 2]; empty yields none.
        let audio = AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            vec![0.0; 2 * PIPELINE_SAMPLE_RATE as usize],
        );
        let turns = SingleSpeakerDiarizer::new()
            .diarize(&audio)
            .expect("diarize");
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].speaker.as_str(), "S0");
        assert!((turns[0].t1 - 2.0).abs() < 1e-9);

        let empty = AudioBuffer::new(PIPELINE_SAMPLE_RATE, Vec::new());
        assert!(SingleSpeakerDiarizer::new()
            .diarize(&empty)
            .expect("diarize empty")
            .is_empty());
    }
}
