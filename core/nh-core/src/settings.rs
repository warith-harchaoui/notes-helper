//! Local configuration loader — the model-bundle source resolution.
//!
//! Translated from the toolbox `vocal_helper._settings`: the app needs one piece
//! of optional configuration — where to fetch the self-hosted **diarization-engines
//! bundle** (the ONNX segmentation + embedding weights the torch-free `sherpa`
//! backend loads), so nothing is pulled from HuggingFace and no token is ever
//! used. [`ModelManager`](crate::models) consumes the resolved source.
//!
//! Configuration lives in an optional, git-ignored `settings.yaml` next to the
//! app or in the working directory (shipped only as `settings.yaml.example`).
//! The schema is intentionally tiny:
//!
//! ```yaml
//! engines:
//!   diarization_url: https://…/diarization-engines.zip
//! ```
//!
//! Resolution order for the bundle (highest priority first):
//! 1. the explicit value passed by the caller,
//! 2. the `NH_DIARIZATION_ENGINES` environment variable (URL or local dir),
//! 3. `engines.diarization_url` from `settings.yaml`.
//!
//! A missing file or key yields `None`; the caller then uses its built-in default.
//!
//! The YAML reader is hand-rolled (no dependency) and tolerates *only* the
//! documented two-level schema — top-level `section:` headers and indented
//! `key: value` pairs, with inline `#` comments stripped and surrounding quotes
//! peeled. Anything deeper or list-shaped is ignored; keep `settings.yaml` flat.

use std::collections::BTreeMap;
use std::env;
use std::path::PathBuf;

/// Environment override pointing directly at a `settings.yaml` file.
pub const SETTINGS_ENV: &str = "NH_SETTINGS";
/// Environment override for the diarization-engines bundle (URL or local dir).
pub const ENGINES_ENV: &str = "NH_DIARIZATION_ENGINES";

/// The placeholder shipped in `settings.yaml.example` — treated as "unset".
const PLACEHOLDER_ENGINES: &[&str] = &["", "https://example.com/diarization-engines.zip"];

/// Parsed settings: `section -> { key -> value }`. A `BTreeMap` keeps the two
/// levels ordered and lets callers chain lookups without guarding for `None`.
pub type Settings = BTreeMap<String, BTreeMap<String, String>>;

/// Drop the first un-quoted `#` comment, keeping `#` that sits inside quotes.
fn strip_comment(value: &str) -> &str {
    // Track quote context by hand — a URL like `https://…#frag` inside a quoted
    // value must survive, so a naive `split('#')` won't do.
    let mut in_single = false;
    let mut in_double = false;
    for (i, ch) in value.char_indices() {
        // A single quote only toggles state when we're NOT inside a double-quoted
        // run (and vice-versa); that's how the two styles nest without clobbering.
        match ch {
            '\'' if !in_double => in_single = !in_single,
            '"' if !in_single => in_double = !in_double,
            // A `#` outside every quote starts the comment — truncate here.
            '#' if !in_single && !in_double => return &value[..i],
            _ => {}
        }
    }
    value
}

/// Strip one matching pair of surrounding quotes, if present.
fn unquote(value: &str) -> &str {
    let value = value.trim();
    // Only peel quotes when both ends match the SAME quote char — a value like
    // `"a'` (mismatched) is left verbatim rather than corrupted.
    let bytes = value.as_bytes();
    if bytes.len() >= 2 {
        let first = bytes[0];
        if (first == b'\'' || first == b'"') && bytes[bytes.len() - 1] == first {
            return &value[1..value.len() - 1];
        }
    }
    value
}

/// Parse a flat two-level `section: { key: value }` document.
///
/// Only the structure used by `settings.yaml` is supported; deeper nesting or
/// sequences silently fall through.
///
/// ```
/// # use nh_core::settings::parse_minimal_yaml;
/// let s = parse_minimal_yaml("engines:\n  diarization_url: https://x/y.zip # note\n");
/// assert_eq!(s["engines"]["diarization_url"], "https://x/y.zip");
/// ```
pub fn parse_minimal_yaml(text: &str) -> Settings {
    let mut out: Settings = BTreeMap::new();
    // `current` names the section we're accumulating keys into; `None` means we're
    // at top level (or inside an unrecognised construct).
    let mut current: Option<String> = None;
    for raw_line in text.lines() {
        // Strip inline comments and trailing whitespace up front so the structural
        // checks below only ever see meaningful content.
        let line = strip_comment(raw_line).trim_end();
        if line.trim().is_empty() {
            continue;
        }
        // Zero indentation ⇒ a top-level line, i.e. a new section header.
        if !line.starts_with([' ', '\t']) {
            if let Some(name) = line.strip_suffix(':') {
                let name = name.trim().to_string();
                out.entry(name.clone()).or_default();
                current = Some(name);
            } else {
                // Top-level scalar — not part of the schema, skip.
                current = None;
            }
            continue;
        }
        // Indented line — only meaningful inside a known section. An indented key
        // before any header is orphaned; drop it.
        let Some(section) = current.as_deref() else {
            continue;
        };
        let stripped = line.trim_start();
        // `split_once` (not `split`) so a value that itself contains a colon —
        // e.g. an `https://` URL — keeps its right-hand side intact.
        let Some((key, value)) = stripped.split_once(':') else {
            continue;
        };
        out.get_mut(section)
            .expect("section inserted when the header was read")
            .insert(key.trim().to_string(), unquote(value).to_string());
    }
    out
}

/// Return the resolved `settings.yaml` path, or `None` if absent.
///
/// Search order: (1) `$NH_SETTINGS` if it points at an existing file; (2)
/// `settings.yaml` in the current working directory; (3) `settings.yaml` beside
/// the running executable. The example file is never searched — users opt in by
/// copying it to `settings.yaml`.
pub fn settings_path() -> Option<PathBuf> {
    // (1) Explicit override wins — but only if it resolves to a file, so a stale
    // env var can't shadow a valid on-disk settings file.
    if let Some(override_path) = env::var_os(SETTINGS_ENV) {
        let p = PathBuf::from(override_path);
        if p.is_file() {
            return Some(p);
        }
    }
    // (2) CWD first so a per-project settings.yaml beats a global one, then (3)
    // the directory of the executable.
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = env::current_dir() {
        candidates.push(cwd.join("settings.yaml"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("settings.yaml"));
        }
    }
    candidates.into_iter().find(|p| p.is_file())
}

/// Read `settings.yaml` and return its parsed mapping.
///
/// Returns an empty map when the file is missing or unreadable, so callers can
/// chain lookups without guarding — config is optional and must never crash a
/// pipeline that would otherwise fall back to built-in defaults.
pub fn load_settings() -> Settings {
    match settings_path() {
        Some(p) => std::fs::read_to_string(p)
            .map(|t| parse_minimal_yaml(&t))
            .unwrap_or_default(),
        None => Settings::new(),
    }
}

/// Return the diarization-engines bundle source (URL or local dir), or `None`.
///
/// Implements the documented precedence — `explicit` argument, then the
/// `NH_DIARIZATION_ENGINES` environment variable, then `engines.diarization_url`
/// in `settings.yaml` — so every call site behaves identically. The example
/// placeholder is treated as unset. `None` means the caller should use its own
/// built-in default.
pub fn resolve_diarization_engines_url(explicit: Option<&str>) -> Option<String> {
    // (1) Caller-supplied value always wins.
    if let Some(e) = explicit {
        if !e.is_empty() {
            return Some(e.to_string());
        }
    }
    // (2) Environment override — used by tests to point at a local bundle.
    if let Some(env_val) = env::var_os(ENGINES_ENV) {
        let env_val = env_val.to_string_lossy().into_owned();
        if !env_val.is_empty() {
            return Some(env_val);
        }
    }
    // (3) The documented settings.yaml key — the canonical config source.
    let url = load_settings()
        .get("engines")
        .and_then(|e| e.get("diarization_url"))
        .cloned();
    match url {
        Some(u) if !PLACEHOLDER_ENGINES.contains(&u.as_str()) => Some(u),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_documented_schema() {
        let s = parse_minimal_yaml("engines:\n  diarization_url: https://h/e.zip\n");
        assert_eq!(s["engines"]["diarization_url"], "https://h/e.zip");
    }

    #[test]
    fn strips_inline_comments_but_keeps_hash_in_quotes() {
        assert_eq!(strip_comment("a b # note"), "a b ");
        assert_eq!(
            strip_comment("\"https://x#frag\" # note"),
            "\"https://x#frag\" "
        );
    }

    #[test]
    fn peels_only_matched_surrounding_quotes() {
        assert_eq!(unquote("  'val'  "), "val");
        assert_eq!(unquote("\"val\""), "val");
        assert_eq!(unquote("\"a'"), "\"a'"); // mismatched — left verbatim
    }

    #[test]
    fn url_value_with_colon_survives_partition() {
        let s = parse_minimal_yaml("engines:\n  diarization_url: 'https://h:8080/e.zip'\n");
        assert_eq!(s["engines"]["diarization_url"], "https://h:8080/e.zip");
    }

    #[test]
    fn orphan_indented_and_toplevel_scalar_are_ignored() {
        // Indented key before any header is dropped; a bare top-level scalar
        // resets the section so its following keys are also dropped.
        let s = parse_minimal_yaml("  stray: 1\nloose_scalar\n  child: 2\nengines:\n  k: v\n");
        assert!(!s.contains_key("stray"));
        assert_eq!(s["engines"]["k"], "v");
        assert_eq!(s.len(), 1);
    }

    #[test]
    fn explicit_wins_over_everything() {
        assert_eq!(
            resolve_diarization_engines_url(Some("https://explicit/e.zip")).as_deref(),
            Some("https://explicit/e.zip")
        );
    }

    #[test]
    fn empty_explicit_falls_through() {
        // An empty string is not a real source; it must not shadow the fallbacks.
        // (With no env var and no settings file present, this resolves to None.)
        // SAFETY of assumption: tests run without NH_DIARIZATION_ENGINES set.
        if env::var_os(ENGINES_ENV).is_none() {
            assert_eq!(resolve_diarization_engines_url(Some("")), None);
        }
    }
}
