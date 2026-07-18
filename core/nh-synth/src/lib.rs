//! `nh-synth` — local-LLM synthesis for `nh-core`, translated from `notes_helper/synth.py`.
//!
//! Module summary
//! --------------
//! Turns a diarized [`Transcript`] into a structured [`Summary`] with a **local** LLM,
//! map-reduce style (faithful to the Python `notes_helper/synth.py`):
//! - **map**: split the transcript into chunks and, per chunk, extract faithful partial
//!   notes as JSON;
//! - **reduce**: fold the partials into the final report JSON with a fixed key set;
//! - **normalize**: coerce that JSON into the exact [`Summary`] shape the renderers want.
//!
//! The LLM call is abstracted behind [`LlmClient`] so the prompt-building, tolerant JSON
//! parsing and normalization are unit-tested with a mock (no network). The real local
//! Ollama client ([`ollama::OllamaClient`]) sits behind the `ollama` feature.
//!
//! Sovereignty: the model is always local (Ollama on `127.0.0.1`, or llama.cpp later). A
//! single cloud call would break the thesis, so there is no cloud client here at all.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use nh_core::error::Result;
use nh_core::model::{Action, Chapter, MeetingContext, Quote, SpeakerId, Summary, Transcript};
use nh_core::ports::Synthesizer;

#[cfg(feature = "ollama")]
pub mod ollama;

/// Map-step system prompt (ported verbatim from `notes_helper._MAP_SYS`), `{lang}` filled.
const MAP_SYS: &str = "Tu es un secrétaire de séance rigoureux. À partir d'un extrait de \
transcription horodatée, extrais fidèlement, SANS inventer, en {lang}. \
Renvoie un JSON: {\"points\":[...], \"decisions\":[{\"decision\":..,\"contexte\":..,\"t\":sec}], \
\"actions\":[{\"action\":..,\"responsable\":..,\"echeance\":..}], \
\"citations\":[{\"speaker\":..,\"texte\":..,\"t\":sec}], \"themes\":[..]}. \
Les lignes sont préfixées par leur horodatage en secondes, ex. [1979s]; \
recopie CET entier (en secondes) tel quel dans le champ t. \
Chaque décision/citation garde le timestamp (secondes) d'où elle vient.";

/// Reduce-step system prompt (ported verbatim from `notes_helper._REDUCE_SYS`).
const REDUCE_SYS: &str = "Tu es un rédacteur de compte-rendu. À partir de notes partielles, \
produis le compte-rendu final en {lang}, JSON strict avec EXACTEMENT ces clés: \
\"resume\" (liste de paragraphes), \"points_cles\" (liste), \
\"decisions\" (liste {decision, contexte}), \
\"actions\" (liste {action, responsable, echeance}), \
\"chapitres\" (liste {t, titre, resume} — t en secondes), \
\"themes\" (liste {theme, points[]}), \
\"citations\" (liste {speaker, texte, t}). Fidèle aux notes, rien d'inventé.";

/// Roughly how many characters of transcript go into one map chunk (mirrors
/// `_CONTEXT_MAX_CHARS`), keeping each map call inside a local model's context window.
const CONTEXT_MAX_CHARS: usize = 8_000;

/// A local LLM chat transport: given a system and user message, return the raw content.
///
/// The real implementation talks to Ollama on localhost; tests inject a mock. Keeping the
/// transport behind a trait is what makes the map-reduce logic testable without a server.
pub trait LlmClient {
    /// Run one chat completion and return the assistant content (expected JSON).
    ///
    /// # Errors
    /// Returns [`CoreError::Synthesis`] if the local model is unreachable or errors.
    fn chat(&self, system: &str, user: &str) -> Result<String>;
}

/// Parse JSON while tolerating prose around it (translated from `_json_loads_lax`).
///
/// Local models sometimes wrap JSON in commentary; we try a direct parse, then the widest
/// `{...}` span. Crucially this **never fails**: an unparseable chunk degrades to an empty
/// object so one bad map call cannot abort the whole synthesis.
fn parse_lax(s: &str) -> serde_json::Value {
    // Fast path: the whole string is valid JSON.
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(s.trim()) {
        return value;
    }
    // Fallback: extract the widest brace span and parse that.
    if let (Some(start), Some(end)) = (s.find('{'), s.rfind('}')) {
        if end > start {
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(&s[start..=end]) {
                return value;
            }
        }
    }
    // Nothing parseable — degrade to an empty object rather than raising.
    serde_json::Value::Object(serde_json::Map::new())
}

/// Read a JSON value as a list of strings, tolerating a bare string or list of objects.
///
/// Objects are reduced to their first string-valued field, matching the leniency the
/// Python normaliser applies to a small model's drifting output.
fn as_str_list(value: &serde_json::Value) -> Vec<String> {
    match value {
        // A single string becomes a one-element list.
        serde_json::Value::String(s) => vec![s.clone()],
        serde_json::Value::Array(items) => items
            .iter()
            .filter_map(|item| match item {
                serde_json::Value::String(s) => Some(s.clone()),
                // For an object, take the first string field (e.g. {"point": "..."}).
                serde_json::Value::Object(map) => {
                    map.values().find_map(|v| v.as_str().map(str::to_string))
                }
                _ => None,
            })
            .filter(|s| !s.trim().is_empty())
            .collect(),
        _ => Vec::new(),
    }
}

/// Read a string field from an object, defaulting to empty.
fn field_str(obj: &serde_json::Value, key: &str) -> String {
    obj.get(key)
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// Read a numeric timestamp field (`t`), tolerating a number or numeric string.
fn field_secs(obj: &serde_json::Value, key: &str) -> f64 {
    match obj.get(key) {
        Some(serde_json::Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(serde_json::Value::String(s)) => s.trim().trim_end_matches('s').parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

/// Coerce the reduce-step JSON into the exact [`Summary`] shape (translated from
/// `normalize_synthese`, adapted to nh-core's leaner report model).
fn normalize(reduced: &serde_json::Value) -> Summary {
    // resume is a list of paragraphs in the Python schema → join into one overview.
    let overview =
        as_str_list(reduced.get("resume").unwrap_or(&serde_json::Value::Null)).join("\n\n");

    // Key points map straight across.
    let key_points = as_str_list(
        reduced
            .get("points_cles")
            .unwrap_or(&serde_json::Value::Null),
    );

    // Decisions: take each object's `decision` text (or a bare string).
    let decisions = reduced
        .get("decisions")
        .and_then(serde_json::Value::as_array)
        .map(|arr| {
            arr.iter()
                .map(|d| match d {
                    serde_json::Value::String(s) => s.clone(),
                    _ => field_str(d, "decision"),
                })
                .filter(|s| !s.trim().is_empty())
                .collect()
        })
        .unwrap_or_default();

    // Actions: description (+ responsable folded in) and due date.
    let actions = reduced
        .get("actions")
        .and_then(serde_json::Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|a| {
                    let action = field_str(a, "action");
                    if action.trim().is_empty() {
                        return None;
                    }
                    let responsable = field_str(a, "responsable");
                    // Identity linking (responsable → enrolled speaker) is a later step;
                    // for now fold the free-text owner into the description.
                    let description = if responsable.trim().is_empty() {
                        action
                    } else {
                        format!("{action} — responsable: {responsable}")
                    };
                    let due = field_str(a, "echeance");
                    Some(Action {
                        description,
                        assignee: None,
                        due: if due.trim().is_empty() {
                            None
                        } else {
                            Some(due)
                        },
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    // Chapters: {t, titre}.
    let chapters = reduced
        .get("chapitres")
        .and_then(serde_json::Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|c| {
                    let title = field_str(c, "titre");
                    if title.trim().is_empty() {
                        return None;
                    }
                    Some(Chapter {
                        t0: field_secs(c, "t"),
                        title,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    // Quotes: {speaker, texte, t}.
    let quotes = reduced
        .get("citations")
        .and_then(serde_json::Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|q| {
                    let text = field_str(q, "texte");
                    if text.trim().is_empty() {
                        return None;
                    }
                    Some(Quote {
                        speaker: SpeakerId::new(field_str(q, "speaker")),
                        text,
                        t0: field_secs(q, "t"),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    Summary {
        overview,
        key_points,
        decisions,
        actions,
        chapters,
        quotes,
    }
}

/// Format the transcript into chunks of ~[`CONTEXT_MAX_CHARS`] characters, each line
/// prefixed with its start timestamp in whole seconds (the unit the map prompt asks for).
fn chunk_transcript(transcript: &Transcript) -> Vec<String> {
    let mut chunks = Vec::new();
    let mut current = String::new();
    for utt in &transcript.utterances {
        // One line per utterance: "S0 [12s]: text".
        let line = format!(
            "{} [{}s]: {}\n",
            utt.speaker.as_str(),
            utt.t0 as i64,
            utt.text.trim()
        );
        // Start a new chunk once adding this line would overflow the budget.
        if !current.is_empty() && current.len() + line.len() > CONTEXT_MAX_CHARS {
            chunks.push(std::mem::take(&mut current));
        }
        current.push_str(&line);
    }
    if !current.is_empty() {
        chunks.push(current);
    }
    chunks
}

/// A [`Synthesizer`] that runs the map-reduce synthesis against an injected [`LlmClient`].
pub struct LocalSynthesizer<C: LlmClient> {
    /// The LLM transport (Ollama in production, a mock in tests).
    client: C,
    /// Output language code passed into the prompts (e.g. `"fr"`).
    language: String,
}

impl<C: LlmClient> LocalSynthesizer<C> {
    /// Build a synthesizer over `client`, producing output in `language`.
    pub fn new(client: C, language: impl Into<String>) -> Self {
        Self {
            client,
            language: language.into(),
        }
    }

    /// Append the user's free-text meeting context to a system prompt (guidance only).
    fn system_prompt(&self, base: &str, context: &MeetingContext) -> String {
        let mut prompt = base.replace("{lang}", &self.language);
        if !context.notes.trim().is_empty() {
            // The context sharpens proper nouns and framing; the model is still told
            // never to invent facts (that instruction lives in `base`).
            prompt.push_str("\nContexte (ne pas inventer): ");
            prompt.push_str(context.notes.trim());
        }
        prompt
    }
}

impl<C: LlmClient> Synthesizer for LocalSynthesizer<C> {
    fn synthesize(&self, transcript: &Transcript, context: &MeetingContext) -> Result<Summary> {
        // An empty transcript has nothing to summarize.
        if transcript.utterances.is_empty() {
            return Ok(Summary::empty());
        }

        // MAP: extract partial notes from each chunk. A chunk that fails to parse degrades
        // to an empty object (parse_lax) rather than aborting the whole synthesis.
        let map_sys = self.system_prompt(MAP_SYS, context);
        let mut partials: Vec<serde_json::Value> = Vec::new();
        for chunk in chunk_transcript(transcript) {
            let raw = self.client.chat(&map_sys, &chunk)?;
            partials.push(parse_lax(&raw));
        }

        // REDUCE: fold the partial notes (as JSON text) into the final report.
        let reduce_sys = self.system_prompt(REDUCE_SYS, context);
        let partials_text = serde_json::Value::Array(partials).to_string();
        let raw_final = self.client.chat(&reduce_sys, &partials_text)?;
        let reduced = parse_lax(&raw_final);

        Ok(normalize(&reduced))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nh_core::model::Utterance;
    use std::cell::RefCell;

    /// A mock LLM that returns queued canned responses in order (map calls, then reduce).
    struct MockLlm {
        responses: RefCell<Vec<String>>,
    }
    impl LlmClient for MockLlm {
        fn chat(&self, _system: &str, _user: &str) -> Result<String> {
            // Pop the next canned response; empty when exhausted.
            Ok(self
                .responses
                .borrow_mut()
                .drain(..1)
                .next()
                .unwrap_or_default())
        }
    }

    fn one_utterance_transcript() -> Transcript {
        Transcript {
            utterances: vec![Utterance {
                t0: 12.0,
                t1: 15.0,
                speaker: SpeakerId::new("S0"),
                text: "We decided to ship on Friday.".to_string(),
                words: Vec::new(),
                language: None,
            }],
            speakers: Vec::new(),
        }
    }

    #[test]
    fn parse_lax_tolerates_surrounding_prose() {
        // Prose around the JSON must still parse; garbage degrades to an empty object.
        assert_eq!(parse_lax("sure! {\"a\": 1} hope that helps")["a"], 1);
        assert!(parse_lax("no json here").as_object().unwrap().is_empty());
        assert!(parse_lax("{\"a\": 1, \"b\":")
            .as_object()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn map_reduce_builds_summary_from_llm_json() {
        // One map response (partial notes) then one reduce response (final report).
        let map_json =
            r#"{"points":["ship friday"],"decisions":[{"decision":"ship on Friday","t":12}]}"#;
        let reduce_json = r#"{
            "resume": ["The team decided to ship on Friday."],
            "points_cles": ["Ship Friday"],
            "decisions": [{"decision": "Ship on Friday", "contexte": "release"}],
            "actions": [{"action": "Prepare release notes", "responsable": "Alice", "echeance": "Thu"}],
            "chapitres": [{"t": 12, "titre": "Release decision", "resume": "..."}],
            "citations": [{"speaker": "S0", "texte": "We decided to ship on Friday.", "t": 12}]
        }"#;
        let client = MockLlm {
            responses: RefCell::new(vec![map_json.to_string(), reduce_json.to_string()]),
        };
        let synth = LocalSynthesizer::new(client, "en");

        let summary = synth
            .synthesize(&one_utterance_transcript(), &MeetingContext::empty())
            .expect("synthesize");

        // Every content field must be populated from the reduce JSON.
        assert!(summary.overview.contains("ship on Friday"));
        assert_eq!(summary.key_points, vec!["Ship Friday".to_string()]);
        assert_eq!(summary.decisions, vec!["Ship on Friday".to_string()]);
        assert_eq!(summary.actions.len(), 1);
        assert!(summary.actions[0].description.contains("Alice"));
        assert_eq!(summary.actions[0].due.as_deref(), Some("Thu"));
        assert_eq!(summary.chapters.len(), 1);
        assert!((summary.chapters[0].t0 - 12.0).abs() < 1e-9);
        assert_eq!(summary.quotes.len(), 1);
        assert_eq!(summary.quotes[0].speaker.as_str(), "S0");
    }

    #[test]
    fn empty_transcript_yields_empty_summary() {
        let client = MockLlm {
            responses: RefCell::new(Vec::new()),
        };
        let synth = LocalSynthesizer::new(client, "fr");
        let summary = synth
            .synthesize(
                &Transcript {
                    utterances: Vec::new(),
                    speakers: Vec::new(),
                },
                &MeetingContext::empty(),
            )
            .expect("synthesize empty");
        assert!(summary.is_empty());
    }
}
