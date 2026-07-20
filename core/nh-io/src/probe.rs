//! Cheap media-duration probe, for the diarization router.
//!
//! Translated from the toolbox `vocal_helper.sources.probe_duration_s`: the
//! router ([`nh_core::router::select_diarization`]) needs the audio length to
//! choose the offline backend — short/dense → NeMo Sortformer, long → pyannote.
//! This is a *metadata read* (via `ffprobe`), not a full decode, so it is O(1) in
//! the file length and safe to call before building the pipeline. Any failure
//! (unreadable file, missing `ffprobe`) yields `None`, which the router treats as
//! "unknown length" and routes to the robust long-form branch.

use std::path::Path;
use std::process::Command;

/// Best-effort media duration in seconds, using `ffprobe` on `PATH`.
///
/// Reads container metadata without decoding the stream. Returns `None` on any
/// failure or a non-positive reading — both mean "unknown length" to the router.
///
/// ```
/// # use nh_io::probe::probe_duration_s;
/// assert!(probe_duration_s("/nonexistent-file.wav").is_none());
/// ```
pub fn probe_duration_s(path: impl AsRef<Path>) -> Option<f64> {
    probe_duration_s_with(path, "ffprobe")
}

/// Like [`probe_duration_s`], but with an explicit `ffprobe` binary path
/// (e.g. a bundled build), mirroring [`crate::ffmpeg::FfmpegSource::with_binary`].
pub fn probe_duration_s_with(path: impl AsRef<Path>, ffprobe_bin: &str) -> Option<f64> {
    // `ffprobe` reads the container's format metadata and prints just the bare
    // duration in seconds — no decode, no wrapper text to strip.
    let output = Command::new(ffprobe_bin)
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
        ])
        .arg(path.as_ref())
        .output()
        .ok()?; // spawn failure (ffprobe absent) ⇒ unknown length
    if !output.status.success() {
        // Non-zero exit (unreadable / non-media file) ⇒ unknown length.
        return None;
    }
    // ffprobe may print "N/A" for streams without a known duration; `parse`
    // fails on that and we fall through to `None`, as intended.
    let text = String::from_utf8(output.stdout).ok()?;
    let seconds: f64 = text.trim().parse().ok()?;
    // A zero/negative reading is as good as unknown; don't feed it to the router.
    (seconds > 0.0).then_some(seconds)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_is_unknown_length() {
        assert!(probe_duration_s("/nonexistent-file-xyz.wav").is_none());
    }

    #[test]
    fn missing_ffprobe_binary_is_unknown_not_a_panic() {
        // A bogus binary name must degrade to `None`, never crash the caller.
        assert!(probe_duration_s_with("/etc/hosts", "ffprobe-does-not-exist").is_none());
    }
}
