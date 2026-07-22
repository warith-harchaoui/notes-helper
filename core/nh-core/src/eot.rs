//! Semantic end-of-turn (EOT) gating — merge breath-split utterances into turns.
//!
//! Translated from the toolbox `vocal_helper.eot`: a VAD emits a segment after a
//! fixed trailing silence, so a speaker who takes a 350 ms breath mid-sentence gets
//! cut into two segments — the ASR sees two fragments and the diarizer two
//! embeddings (sometimes different speakers!). This gate re-joins them by asking a
//! small LLM whether the (growing) transcript looks like a *completed* turn, and
//! holding an incomplete segment back to merge with its successor.
//!
//! The toolbox stage is an async producer/consumer for the live path; this is the
//! offline analogue — a synchronous gate over a *list* of voiced spans, running the
//! identical decision state machine (long segment → emit; else transcribe + classify;
//! incomplete → hold and merge; `max_merge_s` cap → force emit; flush at the end).
//! The two engines are injected as ports ([`AsrEngine`] for the fast partial
//! transcript, [`EndOfTurnClassifier`] for the verdict), so the logic is testable
//! against mocks with whisper + Ollama swapped in at runtime.

use crate::model::{AudioBuffer, Utterance};
use crate::ports::{AsrEngine, EndOfTurnClassifier};

/// A voiced span of audio: its time bounds and mono PCM (mirrors the toolbox
/// `VoicedSegment`). The gate consumes and produces these.
#[derive(Debug, Clone, PartialEq)]
pub struct VoicedSpan {
    /// Span start time in seconds (relative to the session).
    pub t0: f64,
    /// Span end time in seconds.
    pub t1: f64,
    /// Sample rate of `pcm` in Hz.
    pub sample_rate: u32,
    /// Mono PCM samples for this span.
    pub pcm: Vec<f32>,
}

impl VoicedSpan {
    /// Span length in seconds.
    #[must_use]
    pub fn duration_s(&self) -> f64 {
        self.t1 - self.t0
    }
}

/// Concatenate two spans, preserving the parent time bounds and re-inserting the
/// inter-segment silence as zeros so the merged PCM stays time-aligned.
fn merge_spans(a: &VoicedSpan, b: &VoicedSpan) -> VoicedSpan {
    // The gap between a's end and b's start, in samples (never negative).
    let gap_samples = (((b.t0 - a.t1) * f64::from(a.sample_rate)).round() as i64).max(0) as usize;
    let mut pcm = Vec::with_capacity(a.pcm.len() + gap_samples + b.pcm.len());
    pcm.extend_from_slice(&a.pcm);
    pcm.extend(std::iter::repeat_n(0.0, gap_samples));
    pcm.extend_from_slice(&b.pcm);
    VoicedSpan {
        t0: a.t0,
        t1: b.t1,
        sample_rate: a.sample_rate,
        pcm,
    }
}

/// Run the fast partial transcript over a span; a decode failure is non-fatal and
/// yields an empty string (a benign classifier verdict rather than a crash).
fn partial_transcribe(stt: &dyn AsrEngine, span: &VoicedSpan) -> String {
    let audio = AudioBuffer::new(span.sample_rate, span.pcm.clone());
    match stt.transcribe(&audio) {
        Ok(utts) => utts
            .iter()
            .map(|u: &Utterance| u.text.trim())
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join(" "),
        // Decode error on a tiny/odd buffer — treat as "no text" and move on.
        Err(_) => String::new(),
    }
}

/// Gate a list of voiced spans by semantic end-of-turn, merging incomplete ones.
///
/// `min_incomplete_ms` is the cheap-heuristic threshold: with nothing pending, a
/// span at least this long is presumed a closed turn and emitted without an STT or
/// LLM round-trip. `max_merge_s` is the hard latency guard — a merged chain that
/// reaches this length is force-emitted regardless of the classifier. Both the
/// STT and the classifier fail *open* (a decode error → empty text; a classifier
/// error or empty text → "complete") so gating never loses a turn.
///
/// Returns the spans to emit, in order: each either a lone closed turn or a merged
/// super-span glued from a breath-split chain.
pub fn gate_turns(
    spans: &[VoicedSpan],
    stt: &dyn AsrEngine,
    classifier: &dyn EndOfTurnClassifier,
    max_merge_s: f64,
    min_incomplete_ms: f64,
) -> Vec<VoicedSpan> {
    let mut out: Vec<VoicedSpan> = Vec::new();
    // At most one span is held back at a time — the merge chain is linear. We carry
    // it with the transcript accumulated so far, matching the toolbox `_PendingSegment`.
    let mut pending: Option<(VoicedSpan, String)> = None;

    for seg in spans {
        let dur_ms = (seg.t1 - seg.t0) * 1000.0;

        // Cheap heuristic: with nothing pending, a long span is almost certainly a
        // closed turn — skip the STT + LLM round-trip and emit it.
        if pending.is_none() && dur_ms >= min_incomplete_ms {
            out.push(seg.clone());
            continue;
        }

        // Build the candidate — a fresh short span, or the pending chain glued to
        // this span so the classifier judges the growing whole, not a shard.
        let (candidate, acc_text) = match pending.take() {
            None => (seg.clone(), String::new()),
            Some((prev, txt)) => (merge_spans(&prev, seg), txt),
        };

        // Hard latency guard: never hold audio past max_merge_s — bounded lag beats
        // a lost turn. (pending is already cleared by the take() above.)
        if candidate.duration_s() >= max_merge_s {
            out.push(candidate);
            continue;
        }

        // Transcribe the candidate and ask whether the (accumulated) turn is complete.
        let text = partial_transcribe(stt, &candidate);
        let full_text = format!("{acc_text} {text}").trim().to_string();
        // Empty text → nothing to extend, emit. Otherwise classify, failing open.
        let complete =
            full_text.is_empty() || classifier.is_complete_turn(&full_text).unwrap_or(true);

        if complete {
            out.push(candidate);
        } else {
            // Incomplete → stash it; the next span extends this chain.
            pending = Some((candidate, full_text));
        }
    }

    // Flush any span still held back at end of stream.
    if let Some((held, _)) = pending {
        out.push(held);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::{CoreError, Result};
    use crate::model::SpeakerId;

    /// A mock STT that returns a fixed word for every span (enough to drive the gate).
    struct FixedWordStt(&'static str);
    impl AsrEngine for FixedWordStt {
        fn transcribe(&self, _audio: &AudioBuffer) -> Result<Vec<Utterance>> {
            Ok(vec![Utterance {
                t0: 0.0,
                t1: 0.0,
                speaker: SpeakerId::new("S0"),
                text: self.0.to_string(),
                words: Vec::new(),
                language: None,
                confidence: None, // fixed-word mock: no probabilities
            }])
        }
    }

    /// An STT that must never be consulted — proves the cheap/duration paths skip it.
    struct PanicStt;
    impl AsrEngine for PanicStt {
        fn transcribe(&self, _audio: &AudioBuffer) -> Result<Vec<Utterance>> {
            panic!("STT should not be called on this path");
        }
    }

    /// Complete iff the transcript has reached at least `min_words` words.
    struct WordCountComplete {
        min_words: usize,
    }
    impl EndOfTurnClassifier for WordCountComplete {
        fn is_complete_turn(&self, text: &str) -> Result<bool> {
            Ok(text.split_whitespace().count() >= self.min_words)
        }
    }

    /// A classifier that always says "mid-thought" — forces the merge/cap paths.
    struct AlwaysIncomplete;
    impl EndOfTurnClassifier for AlwaysIncomplete {
        fn is_complete_turn(&self, _text: &str) -> Result<bool> {
            Ok(false)
        }
    }

    /// A classifier that errors — the gate must fail open (treat as complete).
    struct BrokenClassifier;
    impl EndOfTurnClassifier for BrokenClassifier {
        fn is_complete_turn(&self, _text: &str) -> Result<bool> {
            Err(CoreError::Synthesis("classifier offline".into()))
        }
    }

    fn span(t0: f64, t1: f64) -> VoicedSpan {
        // 16 kHz of silence sized to the span — content is irrelevant to the mocks.
        let n = (((t1 - t0) * 16_000.0) as usize).max(1);
        VoicedSpan {
            t0,
            t1,
            sample_rate: 16_000,
            pcm: vec![0.0; n],
        }
    }

    #[test]
    fn long_segment_emits_directly_without_engines() {
        // 1.0 s >= 0.8 s min-incomplete and nothing pending → emit, engines untouched.
        let spans = [span(0.0, 1.0)];
        let out = gate_turns(
            &spans,
            &PanicStt,
            &WordCountComplete { min_words: 99 },
            4.0,
            800.0,
        );
        assert_eq!(out, spans);
    }

    #[test]
    fn incomplete_short_span_merges_with_its_successor() {
        // Two short spans; classifier needs 2 words. First held ("hi" = 1 word),
        // second merges → "hi hi" = 2 words → complete → one merged span emitted.
        let spans = [span(0.0, 0.5), span(0.6, 1.0)];
        let out = gate_turns(
            &spans,
            &FixedWordStt("hi"),
            &WordCountComplete { min_words: 2 },
            4.0,
            800.0,
        );
        assert_eq!(out.len(), 1);
        assert_eq!((out[0].t0, out[0].t1), (0.0, 1.0)); // spans A..B glued
    }

    #[test]
    fn max_merge_cap_force_emits_even_when_mid_thought() {
        // Classifier never completes; the merged chain hits the 1.0 s cap and emits.
        let spans = [span(0.0, 0.5), span(0.5, 1.0)];
        let out = gate_turns(&spans, &FixedWordStt("hi"), &AlwaysIncomplete, 1.0, 800.0);
        assert_eq!(out.len(), 1);
        assert_eq!((out[0].t0, out[0].t1), (0.0, 1.0));
    }

    #[test]
    fn classifier_error_fails_open_and_emits() {
        // A broken classifier must not swallow the turn — the short span is emitted.
        let spans = [span(0.0, 0.5)];
        let out = gate_turns(&spans, &FixedWordStt("hi"), &BrokenClassifier, 4.0, 800.0);
        assert_eq!(out.len(), 1);
        assert_eq!((out[0].t0, out[0].t1), (0.0, 0.5));
    }

    #[test]
    fn held_span_is_flushed_at_end_of_stream() {
        // A single short, incomplete span has no successor — it must still be emitted.
        let spans = [span(0.0, 0.5)];
        let out = gate_turns(&spans, &FixedWordStt("hi"), &AlwaysIncomplete, 4.0, 800.0);
        assert_eq!(out.len(), 1);
        assert_eq!((out[0].t0, out[0].t1), (0.0, 0.5));
    }
}
