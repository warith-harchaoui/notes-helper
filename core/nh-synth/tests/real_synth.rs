//! Real synthesis test — runs the map/reduce against a live local Ollama server.
//!
//! Compiles only with the `ollama` feature and is `#[ignore]` (needs a running server and
//! `NH_OLLAMA_MODEL`):
//!
//! ```text
//! NH_OLLAMA_MODEL=qwen2.5:3b \
//! cargo test -p nh-synth --features ollama -- --ignored --nocapture real_ollama_synthesis
//! ```

#![cfg(feature = "ollama")]

use nh_core::model::{MeetingContext, SpeakerId, Transcript, Utterance};
use nh_core::ports::Synthesizer;
use nh_synth::ollama::OllamaClient;
use nh_synth::LocalSynthesizer;

#[test]
#[ignore = "needs a local Ollama server and NH_OLLAMA_MODEL"]
fn real_ollama_synthesis() {
    let model = std::env::var("NH_OLLAMA_MODEL").expect("set NH_OLLAMA_MODEL");

    // A tiny two-utterance French meeting with a clear decision and an action.
    let transcript = Transcript {
        utterances: vec![
            Utterance {
                t0: 0.0,
                t1: 5.0,
                speaker: SpeakerId::new("S0"),
                text:
                    "On a décidé de livrer le produit vendredi. Alice prépare les notes de version."
                        .to_string(),
                words: Vec::new(),
                language: Some("fr".to_string()),
                confidence: None, // real-synth fixture: confidence not exercised here
            },
            Utterance {
                t0: 5.0,
                t1: 10.0,
                speaker: SpeakerId::new("S1"),
                text: "Le budget est validé, on lance la campagne lundi.".to_string(),
                words: Vec::new(),
                language: Some("fr".to_string()),
                confidence: None, // real-synth fixture: confidence not exercised here
            },
        ],
        speakers: Vec::new(),
    };

    let synth = LocalSynthesizer::new(OllamaClient::new(model), "fr");
    let summary = synth
        .synthesize(&transcript, &MeetingContext::empty())
        .expect("synthesize");

    eprintln!("OVERVIEW: {}", summary.overview);
    eprintln!("KEY POINTS: {:?}", summary.key_points);
    eprintln!("DECISIONS: {:?}", summary.decisions);
    eprintln!("ACTIONS: {:?}", summary.actions);

    // A local 3B model should surface at least one of the content fields.
    assert!(
        !summary.overview.trim().is_empty()
            || !summary.decisions.is_empty()
            || !summary.key_points.is_empty(),
        "expected the local model to produce some structured content"
    );
}
