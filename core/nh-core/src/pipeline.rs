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

use crate::eot::{gate_turns, VoicedSpan};
use crate::error::Result;
use crate::lid::{detect_language_regions, LangRegion};
use crate::model::{AudioBuffer, DiarizedSegment, Speaker, SpeakerId, Transcript, Utterance};
use crate::ports::{
    AsrEngine, DiarizationEngine, EndOfTurnClassifier, LanguageDetector, TranscriptionEngine,
};
use crate::router::{select_diarization, DiarizationQuery};

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

/// Language-region sampling parameters for the [`OfflinePipeline`] lid pass
/// (defaults are the toolbox `detect_language_regions` defaults).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LidParams {
    /// Posterior window length in seconds (context per detection).
    pub window_s: f64,
    /// Curve sampling step in seconds (boundary resolution).
    pub hop_s: f64,
    /// Gaussian smoothing sigma in seconds (anti-jitter; `<= 0` disables).
    pub smooth_s: f64,
    /// Shortest region kept before it is absorbed into a neighbour, in seconds.
    pub min_region_s: f64,
    /// Snap-to-silence search radius in seconds (`<= 0` disables).
    pub snap_s: f64,
}

impl Default for LidParams {
    fn default() -> Self {
        Self {
            window_s: 10.0,
            hop_s: 3.0,
            smooth_s: 6.0,
            min_region_s: 8.0,
            snap_s: 1.0,
        }
    }
}

/// End-of-turn gating parameters for the [`OfflinePipeline`] eot pass (defaults
/// are the toolbox `SemanticEOTStage` defaults).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EotParams {
    /// Hard cap on a merged chain's length in seconds (force-emit past it).
    pub max_merge_s: f64,
    /// Spans at least this long (ms) are presumed closed turns and emitted cheaply.
    pub min_incomplete_ms: f64,
}

impl Default for EotParams {
    fn default() -> Self {
        Self {
            max_merge_s: 4.0,
            min_incomplete_ms: 800.0,
        }
    }
}

/// The offline transcription pipeline with the ported `vocal_helper` stages wired in.
///
/// It composes, over injected ports:
/// 1. **router** — [`select_diarization`] reads the audio length and logs the justified
///    `(mode, backend)` plan with its DER/RTF, so the choice is observable (the concrete
///    diarizer is the adapter handed in);
/// 2. **diarize** — the whole buffer into speaker turns (best DER);
/// 3. **eot** (optional) — [`gate_turns`] merges breath-split turns *within each speaker's
///    run* so a mid-sentence pause doesn't fragment one thought;
/// 4. **ASR** — transcribe each (merged) turn, attaching its speaker;
/// 5. **lid** (optional) — [`detect_language_regions`] labels each utterance's language by
///    the region covering its midpoint.
///
/// Everything the pipeline adds is optional and injected, so `Session::run_offline` drives
/// it through the same [`TranscriptionEngine`] port with real engines or mocks.
pub struct OfflinePipeline<'a> {
    diarizer: &'a dyn DiarizationEngine,
    asr: &'a dyn AsrEngine,
    language_detector: Option<&'a dyn LanguageDetector>,
    eot_classifier: Option<&'a dyn EndOfTurnClassifier>,
    max_speakers: Option<u32>,
    torch_free: bool,
    lid: LidParams,
    eot: EotParams,
}

impl<'a> OfflinePipeline<'a> {
    /// Build the minimal pipeline: diarize → ASR (router logged, no lid/eot).
    pub fn new(diarizer: &'a dyn DiarizationEngine, asr: &'a dyn AsrEngine) -> Self {
        Self {
            diarizer,
            asr,
            language_detector: None,
            eot_classifier: None,
            max_speakers: None,
            torch_free: false,
            lid: LidParams::default(),
            eot: EotParams::default(),
        }
    }

    /// Enable the lid pass: label each utterance's language from `detector`.
    #[must_use]
    pub fn with_language_detector(mut self, detector: &'a dyn LanguageDetector) -> Self {
        self.language_detector = Some(detector);
        self
    }

    /// Enable the eot pass: merge breath-split same-speaker turns via `classifier`.
    #[must_use]
    pub fn with_eot(mut self, classifier: &'a dyn EndOfTurnClassifier) -> Self {
        self.eot_classifier = Some(classifier);
        self
    }

    /// Tell the router a known upper bound on the speaker count (routing hint).
    #[must_use]
    pub fn with_max_speakers(mut self, max_speakers: u32) -> Self {
        self.max_speakers = Some(max_speakers);
        self
    }

    /// Tell the router the deployment cannot install PyTorch (forces the sherpa plan).
    #[must_use]
    pub fn torch_free(mut self, torch_free: bool) -> Self {
        self.torch_free = torch_free;
        self
    }

    /// Override the lid sampling parameters (defaults are the toolbox defaults).
    #[must_use]
    pub fn with_lid_params(mut self, params: LidParams) -> Self {
        self.lid = params;
        self
    }

    /// Override the eot gating parameters (defaults are the toolbox defaults).
    #[must_use]
    pub fn with_eot_params(mut self, params: EotParams) -> Self {
        self.eot = params;
        self
    }
}

/// The language of the region whose span covers `t` (seconds), if any.
fn language_at(regions: &[LangRegion], t: f64) -> Option<String> {
    regions
        .iter()
        .find(|r| r.t0 <= t && t < r.t1)
        .map(|r| r.lang.clone())
}

/// Merge breath-split turns *within each maximal same-speaker run* of `segments`.
///
/// Semantic turn merging must never join two different speakers, so we gate each
/// same-speaker run independently with [`gate_turns`] and re-attach that run's
/// speaker to the merged spans. Segments outside a run pass through untouched.
fn eot_merge_same_speaker(
    segments: &[DiarizedSegment],
    audio: &AudioBuffer,
    asr: &dyn AsrEngine,
    classifier: &dyn EndOfTurnClassifier,
    params: &EotParams,
) -> Vec<DiarizedSegment> {
    let mut out: Vec<DiarizedSegment> = Vec::new();
    let mut i = 0;
    while i < segments.len() {
        // Extend a run while the speaker stays the same.
        let speaker = segments[i].speaker.clone();
        let mut j = i;
        while j < segments.len() && segments[j].speaker == speaker {
            j += 1;
        }
        // Slice each turn's audio into a voiced span and gate the run.
        let spans: Vec<VoicedSpan> = segments[i..j]
            .iter()
            .map(|s| {
                let buf = audio.slice(s.t0, s.t1);
                VoicedSpan {
                    t0: s.t0,
                    t1: s.t1,
                    sample_rate: buf.sample_rate,
                    pcm: buf.samples,
                }
            })
            .collect();
        for merged in gate_turns(
            &spans,
            asr,
            classifier,
            params.max_merge_s,
            params.min_incomplete_ms,
        ) {
            out.push(DiarizedSegment {
                t0: merged.t0,
                t1: merged.t1,
                speaker: speaker.clone(),
            });
        }
        i = j;
    }
    out
}

impl TranscriptionEngine for OfflinePipeline<'_> {
    fn transcribe(&self, audio: &AudioBuffer) -> Result<Transcript> {
        // 1) ROUTER — read the audio length and log the justified diarization plan. The
        //    concrete diarizer is the injected adapter; this records *why* that backend is
        //    the right one for this recording so the choice is never a silent default.
        let plan = select_diarization(DiarizationQuery {
            live: false,
            duration_s: Some(audio.duration_s()),
            max_speakers: self.max_speakers,
            torch_free: self.torch_free,
            ..Default::default()
        });
        tracing::info!(
            backend = plan.backend.as_str(),
            mode = plan.mode.as_str(),
            expected_der = plan.expected_der,
            expected_rtf = plan.expected_rtf,
            reason = plan.reason.as_str(),
            "diarization plan"
        );

        // 2) DIARIZE the whole buffer into speaker turns (whole-buffer = best DER).
        let mut segments = self.diarizer.diarize(audio)?;

        // 3) EOT (optional) — merge a speaker's breath-split turns before ASR.
        if let Some(classifier) = self.eot_classifier {
            segments = eot_merge_same_speaker(&segments, audio, self.asr, classifier, &self.eot);
        }

        // 4) ASR each (merged) turn, attaching the turn's speaker.
        let mut utterances = Vec::new();
        let mut speaking: BTreeMap<String, f64> = BTreeMap::new();
        for seg in &segments {
            let sub = audio.slice(seg.t0, seg.t1);
            let parts = self.asr.transcribe(&sub)?;
            let text = parts
                .iter()
                .map(|u| u.text.trim())
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(" ");
            if text.is_empty() {
                continue;
            }
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

        // 5) LID (optional) — label each utterance by the language region over its middle.
        if let Some(detector) = self.language_detector {
            let regions = detect_language_regions(
                detector,
                audio,
                self.lid.window_s,
                self.lid.hop_s,
                self.lid.smooth_s,
                self.lid.min_region_s,
                self.lid.snap_s,
            )?;
            if !regions.is_empty() {
                for utt in &mut utterances {
                    utt.language = language_at(&regions, (utt.t0 + utt.t1) / 2.0);
                }
            }
        }

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

    /// A diarizer that returns two consecutive SHORT turns for the SAME speaker
    /// (a breath-split turn the eot pass should re-join).
    struct MockSplitDiar;
    impl DiarizationEngine for MockSplitDiar {
        fn diarize(&self, _audio: &AudioBuffer) -> Result<Vec<DiarizedSegment>> {
            Ok(vec![
                DiarizedSegment {
                    t0: 0.0,
                    t1: 0.5,
                    speaker: SpeakerId::new("S0"),
                },
                DiarizedSegment {
                    t0: 0.5,
                    t1: 1.0,
                    speaker: SpeakerId::new("S0"),
                },
            ])
        }
    }

    /// A language detector that always hears French — drives the lid wiring.
    struct AlwaysFrench;
    impl LanguageDetector for AlwaysFrench {
        fn detect_language(&self, _audio: &AudioBuffer) -> Result<Vec<(String, f32)>> {
            Ok(vec![("fr".to_string(), 0.9), ("en".to_string(), 0.1)])
        }
    }

    /// Complete iff the transcript has reached at least two words.
    struct TwoWordComplete;
    impl EndOfTurnClassifier for TwoWordComplete {
        fn is_complete_turn(&self, text: &str) -> Result<bool> {
            Ok(text.split_whitespace().count() >= 2)
        }
    }

    #[test]
    fn offline_pipeline_router_only_matches_diarize_then_asr() {
        // With no lid/eot, OfflinePipeline is DiarizeThenAsr plus a logged plan.
        let audio = AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            vec![0.0; 2 * PIPELINE_SAMPLE_RATE as usize],
        );
        let (diar, asr) = (MockDiar, MockAsr);
        let transcript = OfflinePipeline::new(&diar, &asr)
            .transcribe(&audio)
            .expect("transcribe");
        assert_eq!(transcript.utterances.len(), 2);
        assert_eq!(transcript.speakers.len(), 2);
        assert!(transcript.utterances.iter().all(|u| u.language.is_none()));
    }

    #[test]
    fn offline_pipeline_labels_utterance_language_via_lid() {
        // Small windows so a 2 s clip yields a region; the detector says fr throughout.
        let audio = AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            vec![0.0; 2 * PIPELINE_SAMPLE_RATE as usize],
        );
        let (diar, asr, det) = (MockDiar, MockAsr, AlwaysFrench);
        let pipe = OfflinePipeline::new(&diar, &asr)
            .with_language_detector(&det)
            .with_lid_params(LidParams {
                window_s: 1.0,
                hop_s: 0.5,
                smooth_s: 0.0,
                min_region_s: 0.5,
                snap_s: 0.0,
            });
        let transcript = pipe.transcribe(&audio).expect("transcribe");
        assert_eq!(transcript.utterances.len(), 2);
        assert!(transcript
            .utterances
            .iter()
            .all(|u| u.language.as_deref() == Some("fr")));
    }

    #[test]
    fn offline_pipeline_merges_breath_split_turns_via_eot() {
        // Two short S0 fragments; the first reads incomplete → merged into one turn.
        let audio = AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            vec![0.0; PIPELINE_SAMPLE_RATE as usize],
        );
        let (diar, asr, clf) = (MockSplitDiar, MockAsr, TwoWordComplete);
        let transcript = OfflinePipeline::new(&diar, &asr)
            .with_eot(&clf)
            .transcribe(&audio)
            .expect("transcribe");
        assert_eq!(
            transcript.utterances.len(),
            1,
            "the two fragments should merge"
        );
        assert_eq!(transcript.utterances[0].speaker.as_str(), "S0");
        assert!((transcript.utterances[0].t1 - 1.0).abs() < 1e-9);
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
