//! End-of-turn classifier — a local LLM verdict on whether a turn is complete.
//!
//! Translated from the toolbox `vocal_helper.eot._classify`: the model half of the
//! semantic end-of-turn gate ([`nh_core::eot::gate_turns`]). Given a partial
//! transcript it asks a small local model (Ollama, e.g. `qwen2.5:3b`) to answer
//! YES/NO and parses the verdict liberally. Generic over [`LlmClient`], so it is
//! unit-tested with a mock and driven by the real [`crate::ollama::OllamaClient`]
//! at runtime — no new feature gate.

use nh_core::ports::EndOfTurnClassifier;
use nh_core::Result;

use crate::LlmClient;

/// The classifier instruction (system message). The utterance is the user message.
const EOT_SYSTEM: &str = "You are a speech end-of-turn classifier. Given the latest snippet \
of a single speaker's utterance, answer with exactly one word:\n \
- YES if the utterance looks like a complete turn (the speaker is done and could \
plausibly hand the floor over).\n \
- NO if it ends mid-thought, mid-clause, mid-word, or with a filler that signals \
the speaker is about to continue.";

/// An [`EndOfTurnClassifier`] backed by any [`LlmClient`] (Ollama in production).
pub struct LlmEotClassifier<C: LlmClient> {
    client: C,
}

impl<C: LlmClient> LlmEotClassifier<C> {
    /// Wrap an LLM transport as an end-of-turn classifier.
    pub fn new(client: C) -> Self {
        Self { client }
    }
}

/// Liberal YES/NO parser, translated verbatim from the toolbox: look for `yes`
/// somewhere in the first ~10 characters, but not if a `no` precedes it.
fn verdict_is_complete(answer: &str) -> bool {
    let lower = answer.trim().to_lowercase();
    let head: String = lower.chars().take(10).collect();
    match head.find("yes") {
        // "no" must not appear at or before the "yes" (within `yes`+3 chars).
        Some(pos) => {
            let end = (pos + 3).min(head.len());
            let prefix = head.get(..end).unwrap_or(&head);
            !prefix.contains("no")
        }
        None => false,
    }
}

impl<C: LlmClient> EndOfTurnClassifier for LlmEotClassifier<C> {
    fn is_complete_turn(&self, text: &str) -> Result<bool> {
        // Nothing to extend → complete (matches the toolbox short-circuit).
        if text.trim().is_empty() {
            return Ok(true);
        }
        let user = format!("Utterance: {text}\n\nAnswer:");
        match self.client.chat(EOT_SYSTEM, &user) {
            Ok(answer) => Ok(verdict_is_complete(&answer)),
            // Classifier offline → fail open (non-gated VAD behaviour), never a crash.
            Err(_) => Ok(true),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nh_core::CoreError;

    /// A mock LLM that always returns the same canned answer.
    struct CannedLlm(&'static str);
    impl LlmClient for CannedLlm {
        fn chat(&self, _system: &str, _user: &str) -> Result<String> {
            Ok(self.0.to_string())
        }
    }

    /// A mock LLM whose transport always fails.
    struct BrokenLlm;
    impl LlmClient for BrokenLlm {
        fn chat(&self, _system: &str, _user: &str) -> Result<String> {
            Err(CoreError::Synthesis("unreachable".into()))
        }
    }

    #[test]
    fn parses_yes_and_no_verdicts() {
        assert!(verdict_is_complete("YES"));
        assert!(verdict_is_complete("Yes, complete."));
        assert!(!verdict_is_complete("NO"));
        assert!(!verdict_is_complete("no, mid-thought"));
        // "no" before "yes" in the head → not complete.
        assert!(!verdict_is_complete("no yes"));
        // Nonsense / empty → not complete (the caller short-circuits empty input).
        assert!(!verdict_is_complete("maybe"));
    }

    #[test]
    fn empty_text_is_complete_without_calling_the_model() {
        let c = LlmEotClassifier::new(BrokenLlm);
        // Empty short-circuits before the (broken) transport is touched.
        assert!(c.is_complete_turn("   ").unwrap());
    }

    #[test]
    fn classifies_via_the_llm() {
        let complete = LlmEotClassifier::new(CannedLlm("YES"));
        assert!(complete.is_complete_turn("The meeting is over.").unwrap());
        let incomplete = LlmEotClassifier::new(CannedLlm("NO"));
        assert!(!incomplete.is_complete_turn("So the next thing we").unwrap());
    }

    #[test]
    fn transport_failure_fails_open() {
        let c = LlmEotClassifier::new(BrokenLlm);
        // A broken classifier must not swallow the turn → treated as complete.
        assert!(c.is_complete_turn("anything").unwrap());
    }
}
