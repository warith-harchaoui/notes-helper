//! Real local Ollama client for [`crate::LlmClient`] (behind the `ollama` feature).
//!
//! Talks to an Ollama server on localhost via its `/api/chat` endpoint with
//! `format: "json"`, exactly like `notes_helper._ollama`. Stays on `127.0.0.1` — nothing
//! leaves the device — so no TLS is needed (the `ureq` dependency is built without it).

use nh_core::error::{CoreError, Result};

use crate::LlmClient;

/// A chat client for a local Ollama server.
pub struct OllamaClient {
    /// Base URL of the Ollama server (defaults to the local one).
    url: String,
    /// Model name to run (e.g. `"qwen2.5:3b"`).
    model: String,
}

impl OllamaClient {
    /// Build a client for `model` against the default local server.
    pub fn new(model: impl Into<String>) -> Self {
        Self {
            url: "http://127.0.0.1:11434".to_string(),
            model: model.into(),
        }
    }

    /// Build a client for `model` against an explicit server URL.
    pub fn with_url(model: impl Into<String>, url: impl Into<String>) -> Self {
        Self {
            url: url.into(),
            model: model.into(),
        }
    }
}

impl LlmClient for OllamaClient {
    fn chat(&self, system: &str, user: &str) -> Result<String> {
        // Build the chat request body, constraining the output to JSON like the Python.
        let body = serde_json::json!({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": false,
            "format": "json",
        });

        // POST to the local chat endpoint; any transport error is a synthesis failure that
        // the caller can treat as "no local model" (and fall back if desired).
        let response = ureq::post(&format!("{}/api/chat", self.url))
            .set("Content-Type", "application/json")
            .send_json(body)
            .map_err(|e| CoreError::Synthesis(format!("ollama request failed: {e}")))?;

        // Ollama returns `{"message": {"content": "..."}}`; pull the content string out.
        let value: serde_json::Value = response
            .into_json()
            .map_err(|e| CoreError::Synthesis(format!("ollama response parse: {e}")))?;
        value
            .get("message")
            .and_then(|m| m.get("content"))
            .and_then(serde_json::Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| {
                CoreError::Synthesis("ollama response missing message.content".to_string())
            })
    }
}
