//! End-to-end offline test: real media file → ffmpeg decode → baseline diarization →
//! real whisper.cpp ASR → Transcript. Proves the whole offline chain with real engines.
//!
//! Compiles only with the `whisper-cpp` feature and is `#[ignore]` (needs a model + a
//! media file via env vars):
//!
//! ```text
//! NH_WHISPER_MODEL=/path/ggml-base.bin \
//! NH_E2E_MEDIA=/path/to/any/audio_or_video \
//! cargo test -p nh-whisper --features whisper-cpp -- --ignored --nocapture end_to_end_offline
//! ```

#![cfg(feature = "whisper-cpp")]

use nh_core::pipeline::{DiarizeThenAsr, SingleSpeakerDiarizer};
use nh_core::ports::AudioSource;
use nh_core::ports::TranscriptionEngine;
use nh_io::FfmpegSource;
use nh_whisper::WhisperAsr;

#[test]
#[ignore = "needs NH_WHISPER_MODEL + NH_E2E_MEDIA and the whisper-cpp feature"]
fn end_to_end_offline() {
    // Resolve the model and any media file (ffmpeg decodes whatever the format is).
    let model = std::env::var("NH_WHISPER_MODEL").expect("set NH_WHISPER_MODEL");
    let media = std::env::var("NH_E2E_MEDIA").expect("set NH_E2E_MEDIA");

    // Decode any container/codec to the canonical 16 kHz mono buffer.
    let audio = FfmpegSource::new(media).load().expect("ffmpeg decode");

    // Compose the baseline single-speaker diarizer with the real whisper ASR into the
    // pipeline's TranscriptionEngine, then run it over the whole buffer.
    let engine = DiarizeThenAsr::new(
        SingleSpeakerDiarizer::new(),
        WhisperAsr::load(model).expect("load whisper model"),
    );
    let transcript = engine.transcribe(&audio).expect("transcribe");

    // The chain should produce at least one utterance attributed to S0 with real text.
    assert!(!transcript.utterances.is_empty(), "expected utterances");
    assert_eq!(transcript.speakers.len(), 1, "baseline = single speaker");
    let text = transcript
        .utterances
        .iter()
        .map(|u| u.text.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    assert!(!text.trim().is_empty(), "expected non-empty transcript");
    eprintln!(
        "E2E TRANSCRIPT ({} utt): {text}",
        transcript.utterances.len()
    );
}
