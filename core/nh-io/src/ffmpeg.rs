//! ffmpeg-backed [`AudioSource`] — decode ANY container/codec to 16 kHz mono f32.
//!
//! Module summary
//! --------------
//! This is the general audio input adapter: it shells out to **ffmpeg** (the same solid
//! engine the Python `audio-helper` wraps) to decode any format ffmpeg supports — mp3,
//! m4a, mp4, mov, flac, ogg, wav, … — down-mix to mono, and resample to the pipeline's
//! 16 kHz in a single pass. Prefer this over [`crate::wav::WavFileSource`] whenever ffmpeg
//! is available; the WAV reader remains as a zero-dependency fallback for 16 kHz WAV.
//!
//! Requirement: `ffmpeg` must be on `PATH` (see TECHNICAL_REQUIREMENTS.txt). A missing
//! binary surfaces as a clear [`CoreError::Capture`] rather than a silent failure.

use std::path::PathBuf;
use std::process::Command;

use nh_core::error::{CoreError, Result};
use nh_core::model::{AudioBuffer, PIPELINE_SAMPLE_RATE};
use nh_core::ports::AudioSource;

/// An [`AudioSource`] that decodes any ffmpeg-supported media file to 16 kHz mono.
pub struct FfmpegSource {
    /// Path to the input media (any container/codec ffmpeg can read).
    path: PathBuf,
    /// The ffmpeg binary to invoke (overridable for non-standard installs).
    ffmpeg_bin: String,
}

impl FfmpegSource {
    /// Build a source for `path`, using the `ffmpeg` binary found on `PATH`.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            ffmpeg_bin: "ffmpeg".to_string(),
        }
    }

    /// Build a source with an explicit ffmpeg binary path (e.g. a bundled build).
    pub fn with_binary(path: impl Into<PathBuf>, ffmpeg_bin: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            ffmpeg_bin: ffmpeg_bin.into(),
        }
    }
}

impl AudioSource for FfmpegSource {
    fn load(&self) -> Result<AudioBuffer> {
        // Ask ffmpeg to decode to raw 32-bit float little-endian, mono, 16 kHz, on stdout.
        // `-v error` keeps the log quiet, `-nostdin` prevents it from consuming our stdin.
        let output = Command::new(&self.ffmpeg_bin)
            .arg("-v")
            .arg("error")
            .arg("-nostdin")
            .arg("-i")
            .arg(&self.path)
            .arg("-f")
            .arg("f32le")
            .arg("-ac")
            .arg("1")
            .arg("-ar")
            .arg(PIPELINE_SAMPLE_RATE.to_string())
            .arg("-") // write to stdout (pipe:1)
            .output()
            .map_err(|e| {
                CoreError::Capture(format!(
                    "failed to run '{}': {e} (is ffmpeg installed and on PATH?)",
                    self.ffmpeg_bin
                ))
            })?;

        // A non-zero exit means ffmpeg could not decode the file; surface its stderr.
        if !output.status.success() {
            return Err(CoreError::Capture(format!(
                "ffmpeg failed to decode {}: {}",
                self.path.display(),
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }

        // The stdout stream is tightly-packed f32 little-endian samples. Reassemble them
        // four bytes at a time; `chunks_exact` cleanly ignores any trailing partial frame
        // (ffmpeg never emits one, but this stays robust if it ever did).
        let samples: Vec<f32> = output
            .stdout
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
            .collect();

        Ok(AudioBuffer::new(PIPELINE_SAMPLE_RATE, samples))
    }
}
