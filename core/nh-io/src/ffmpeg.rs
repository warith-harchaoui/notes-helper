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
//!
//! **No temporary WAV is ever written.** The working audio is streamed from ffmpeg's stdout
//! straight into a `Vec<f32>` in RAM — a multi-hour recording never lands a ~500 MB 16 kHz
//! WAV on disk. The Python reference pipeline mirrors this exactly (`pipeline.decode_16k_mono`).

use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use nh_core::error::{CoreError, Result};
use nh_core::model::{AudioBuffer, PIPELINE_SAMPLE_RATE};
use nh_core::ports::AudioSource;

/// Reassemble tightly-packed little-endian `f32` bytes (ffmpeg `f32le`) into samples.
/// A trailing partial frame (< 4 bytes) is ignored — ffmpeg never emits one, but this stays
/// robust if a pipe read ever splits mid-sample.
fn samples_from_le_bytes(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(4)
        .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .collect()
}

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

    /// Decode just the window `[start_s, start_s + dur_s)` to 16 kHz mono — on demand.
    ///
    /// Uses ffmpeg **input seek** (`-ss` before `-i`) so only that slice is decoded, never
    /// the whole file. This is the offline "re-read one turn's audio for ASR" primitive:
    /// peak memory is O(window), so a 4 h recording is transcribed turn-by-turn without ever
    /// holding the full waveform.
    ///
    /// # Errors
    /// Returns [`CoreError::Capture`] if ffmpeg cannot be run or fails to decode the window.
    pub fn load_window(&self, start_s: f64, dur_s: f64) -> Result<AudioBuffer> {
        let output = Command::new(&self.ffmpeg_bin)
            .args(["-v", "error", "-nostdin"])
            .args(["-ss", &format!("{:.6}", start_s.max(0.0))])
            .args(["-t", &format!("{:.6}", dur_s.max(0.0))])
            .arg("-i")
            .arg(&self.path)
            .args([
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                &PIPELINE_SAMPLE_RATE.to_string(),
                "-",
            ])
            .output()
            .map_err(|e| {
                CoreError::Capture(format!(
                    "failed to run '{}': {e} (is ffmpeg installed and on PATH?)",
                    self.ffmpeg_bin
                ))
            })?;
        if !output.status.success() {
            return Err(CoreError::Capture(format!(
                "ffmpeg failed to decode window [{start_s:.3}, {:.3}) of {}: {}",
                start_s + dur_s,
                self.path.display(),
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        Ok(AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            samples_from_le_bytes(&output.stdout),
        ))
    }

    /// Stream the whole file to the callback in fixed-size blocks — **bounded memory**.
    ///
    /// ffmpeg decodes to stdout and we hand `on_block` roughly `block_samples`-long buffers
    /// as they arrive, so peak memory is O(block) rather than O(duration): a multi-hour file
    /// is processed (VAD, per-block embeddings, …) without ever materializing the full ~1 GB
    /// waveform. The final block may be shorter. Still fully offline/batch — this is the
    /// bounded-memory decode, not the online streaming path.
    ///
    /// # Errors
    /// Returns [`CoreError::Capture`] if ffmpeg cannot be run or exits non-zero, or if the
    /// callback returns an error (propagated, aborting the stream).
    pub fn stream_blocks(
        &self,
        block_samples: usize,
        mut on_block: impl FnMut(AudioBuffer) -> Result<()>,
    ) -> Result<()> {
        let block_samples = block_samples.max(1);
        let block_bytes = block_samples * 4;

        let mut child = Command::new(&self.ffmpeg_bin)
            .args(["-v", "error", "-nostdin", "-i"])
            .arg(&self.path)
            .args([
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                &PIPELINE_SAMPLE_RATE.to_string(),
                "-",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| {
                CoreError::Capture(format!(
                    "failed to run '{}': {e} (is ffmpeg installed and on PATH?)",
                    self.ffmpeg_bin
                ))
            })?;
        let mut stdout = child.stdout.take().expect("stdout was piped");

        // Accumulate raw bytes across reads (a pipe read can split a sample), emitting a
        // block whenever a full `block_bytes` is available. `acc` never exceeds one block
        // plus one read, so memory stays bounded regardless of file length.
        let mut acc: Vec<u8> = Vec::with_capacity(block_bytes + 64 * 1024);
        let mut chunk = vec![0u8; 64 * 1024];
        loop {
            let n = stdout
                .read(&mut chunk)
                .map_err(|e| CoreError::Capture(format!("reading ffmpeg stdout: {e}")))?;
            if n == 0 {
                break;
            }
            acc.extend_from_slice(&chunk[..n]);
            while acc.len() >= block_bytes {
                on_block(AudioBuffer::new(
                    PIPELINE_SAMPLE_RATE,
                    samples_from_le_bytes(&acc[..block_bytes]),
                ))?;
                acc.drain(..block_bytes);
            }
        }
        // Flush the trailing partial block (whole samples only).
        if acc.len() >= 4 {
            on_block(AudioBuffer::new(
                PIPELINE_SAMPLE_RATE,
                samples_from_le_bytes(&acc),
            ))?;
        }

        let status = child
            .wait()
            .map_err(|e| CoreError::Capture(format!("waiting on ffmpeg: {e}")))?;
        if !status.success() {
            let mut err = String::new();
            if let Some(mut s) = child.stderr.take() {
                let _ = s.read_to_string(&mut err);
            }
            return Err(CoreError::Capture(format!(
                "ffmpeg failed to decode {}: {}",
                self.path.display(),
                err.trim()
            )));
        }
        Ok(())
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

        // The stdout stream is tightly-packed f32 little-endian samples.
        Ok(AudioBuffer::new(
            PIPELINE_SAMPLE_RATE,
            samples_from_le_bytes(&output.stdout),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Generate a `secs`-long 16 kHz mono sine WAV via ffmpeg, returning its path (or
    /// `None` when ffmpeg is absent, so the suite stays green on a bare machine).
    fn fixture(secs: u32, tag: &str) -> Option<PathBuf> {
        let path = std::env::temp_dir().join(format!("nh_io_{}_{}.wav", std::process::id(), tag));
        let ok = Command::new("ffmpeg")
            .args(["-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i"])
            .arg(format!("sine=frequency=220:duration={secs}"))
            .args(["-ac", "1", "-ar", &PIPELINE_SAMPLE_RATE.to_string()])
            .arg(&path)
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        ok.then_some(path)
    }

    #[test]
    fn stream_blocks_reconstructs_full_load() {
        let Some(path) = fixture(2, "stream") else {
            return; // ffmpeg unavailable — skip
        };
        let src = FfmpegSource::new(&path);
        let full = src.load().expect("full load").samples;

        // Stream in 8000-sample blocks; every block but the last is exactly that size, and
        // concatenating them reproduces the full decode bit-for-bit.
        let block = 8000usize;
        let mut streamed: Vec<f32> = Vec::new();
        let mut sizes: Vec<usize> = Vec::new();
        src.stream_blocks(block, |buf| {
            sizes.push(buf.samples.len());
            streamed.extend_from_slice(&buf.samples);
            Ok(())
        })
        .expect("stream");

        assert_eq!(streamed, full, "streamed blocks must equal the full decode");
        assert!(
            sizes.len() >= 2,
            "2 s at 16 kHz / 8000 must be several blocks"
        );
        for (i, &sz) in sizes.iter().enumerate() {
            if i + 1 < sizes.len() {
                assert_eq!(sz, block, "non-final blocks are exactly block_samples");
            } else {
                assert!(sz <= block && sz > 0, "final block is the short remainder");
            }
        }
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn load_window_returns_only_the_requested_slice() {
        let Some(path) = fixture(4, "window") else {
            return; // ffmpeg unavailable — skip
        };
        let src = FfmpegSource::new(&path);
        let win = src.load_window(1.0, 1.5).expect("window");
        // 1.5 s at 16 kHz ≈ 24000 samples; allow codec/seek slack.
        let n = win.samples.len();
        assert!(
            (22000..=26000).contains(&n),
            "window length {n} should be ~24000 samples"
        );
        assert!(
            n < src.load().expect("full").samples.len(),
            "window is a slice"
        );
        let _ = std::fs::remove_file(&path);
    }
}
