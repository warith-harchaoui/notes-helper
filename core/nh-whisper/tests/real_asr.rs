//! Real ASR test — runs whisper.cpp on an actual ggml model and a 16 kHz mono WAV.
//!
//! The whole file compiles only with the `whisper-cpp` feature (so the default workspace
//! test run stays fast and green), and the test is `#[ignore]` so it runs only when asked
//! and the two env vars point at real files:
//!
//! ```text
//! NH_WHISPER_MODEL=/path/to/ggml-base.bin \
//! NH_WHISPER_WAV=/path/to/clip16k.wav \
//! cargo test -p nh-whisper --features whisper-cpp -- --ignored --nocapture
//! ```

#![cfg(feature = "whisper-cpp")]

use nh_core::ports::{AsrEngine, AudioSource};
use nh_io::WavFileSource;
use nh_whisper::WhisperAsr;

#[test]
#[ignore = "needs NH_WHISPER_MODEL + NH_WHISPER_WAV and the whisper-cpp feature"]
fn transcribes_real_clip() {
    // Resolve the model and audio from the environment so the test carries no machine
    // paths and only runs when explicitly wired.
    let model = std::env::var("NH_WHISPER_MODEL").expect("set NH_WHISPER_MODEL");
    let wav = std::env::var("NH_WHISPER_WAV").expect("set NH_WHISPER_WAV");

    // Load the clip through the same WAV port the pipeline uses.
    let audio = WavFileSource::new(wav).load().expect("load wav");

    // Transcribe with the real engine and assert we got non-empty text back.
    let engine = WhisperAsr::new(model);
    let utterances = engine.transcribe(&audio).expect("transcribe");
    assert!(!utterances.is_empty(), "expected at least one utterance");

    let text = utterances
        .iter()
        .map(|u| u.text.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    assert!(
        !text.trim().is_empty(),
        "expected non-empty transcript text"
    );

    // Surface the transcript when run with --nocapture so a human can eyeball quality.
    eprintln!("TRANSCRIPT: {text}");
}
