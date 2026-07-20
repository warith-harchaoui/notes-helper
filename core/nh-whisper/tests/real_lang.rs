//! Real language-head test — proves `WhisperAsr::detect_language` reads whisper's
//! language posterior on actual audio. Wired only when a model + clip are provided:
//!
//! ```bash
//! NH_WHISPER_MODEL=/path/to/ggml-base.bin \
//! NH_WHISPER_WAV=/path/to/clip16k.wav \
//! cargo test -p nh-whisper --features whisper-cpp -- --ignored --nocapture detects_language
//! ```

#![cfg(feature = "whisper-cpp")]

use nh_core::ports::{AudioSource, LanguageDetector};
use nh_io::WavFileSource;
use nh_whisper::WhisperAsr;

#[test]
#[ignore = "needs NH_WHISPER_MODEL + NH_WHISPER_WAV and the whisper-cpp feature"]
fn detects_language() {
    let model = std::env::var("NH_WHISPER_MODEL").expect("set NH_WHISPER_MODEL");
    let wav = std::env::var("NH_WHISPER_WAV").expect("set NH_WHISPER_WAV");

    let audio = WavFileSource::new(wav).load().expect("load wav");
    let engine = WhisperAsr::load(model).expect("load whisper model");

    let posterior = engine.detect_language(&audio).expect("detect language");
    assert!(!posterior.is_empty(), "expected a language posterior");
    // Probabilities form a distribution over the full candidate set.
    let total: f32 = posterior.iter().map(|(_, p)| p).sum();
    assert!((0.5..=1.5).contains(&total), "posterior sums to {total}");

    // Sorted most-likely first — surface the top few for a human to eyeball.
    let (top_code, top_p) = &posterior[0];
    eprintln!("TOP LANGUAGE: {top_code} ({top_p:.3})");
    eprintln!("NEXT: {:?}", posterior.iter().take(4).collect::<Vec<_>>());
    assert!(*top_p > 0.0, "top language should carry positive mass");
}

#[test]
#[ignore = "needs NH_WHISPER_MODEL + NH_WHISPER_WAV and the whisper-cpp feature"]
fn segments_language_regions_end_to_end() {
    // The whole lid method wired to the real model: window the clip, read whisper's
    // language head per window, and segment into mono-language spans. A single-language
    // French clip should collapse to one 'fr' region.
    let model = std::env::var("NH_WHISPER_MODEL").expect("set NH_WHISPER_MODEL");
    let wav = std::env::var("NH_WHISPER_WAV").expect("set NH_WHISPER_WAV");

    let audio = WavFileSource::new(wav).load().expect("load wav");
    let engine = WhisperAsr::load(model).expect("load whisper model");

    // 10 s windows every 3 s; smooth 6 s, keep >= 8 s regions, snap 1 s (toolbox defaults).
    let regions = nh_core::lid::detect_language_regions(&engine, &audio, 10.0, 3.0, 6.0, 8.0, 1.0)
        .expect("segment language regions");
    eprintln!("REGIONS: {regions:?}");
    assert!(!regions.is_empty(), "expected at least one language region");
    assert!(
        regions.iter().all(|r| r.lang == "fr"),
        "a French clip should segment to fr regions, got {regions:?}"
    );
}
