//! WAV-backed [`AudioSource`] adapter.
//!
//! Module summary
//! --------------
//! Decodes a WAV file into the pipeline's canonical [`AudioBuffer`] (16 kHz mono f32).
//! Integer PCM (any bit depth hound supports) and float WAV are both handled, and
//! multi-channel audio is down-mixed to mono. Arbitrary containers/rates (mp3, m4a,
//! 44.1 kHz, ...) are the job of the ffmpeg/symphonia decoding adapter that ships with
//! the capture layer (M3); here we require 16 kHz so we never silently feed a
//! wrong-rate buffer to the engines.

use std::path::PathBuf;

use nh_core::error::{CoreError, Result};
use nh_core::model::{AudioBuffer, PIPELINE_SAMPLE_RATE};
use nh_core::ports::AudioSource;

/// An [`AudioSource`] that loads a WAV file from disk.
pub struct WavFileSource {
    /// Path to the WAV file to decode.
    path: PathBuf,
}

impl WavFileSource {
    /// Build a source for the WAV file at `path`.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

/// Down-mix an interleaved multi-channel signal to mono by averaging channels.
///
/// Averaging (rather than dropping channels) preserves energy from every microphone in
/// the OBS-style graph and avoids losing a speaker who only appears on one channel.
fn downmix_to_mono(interleaved: &[f32], channels: u16) -> Vec<f32> {
    // Mono already: nothing to do, just copy through.
    if channels <= 1 {
        return interleaved.to_vec();
    }
    let channels = channels as usize;

    // Walk the interleaved samples one frame (one sample per channel) at a time and emit
    // the per-frame average.
    interleaved
        .chunks(channels)
        .map(|frame| frame.iter().sum::<f32>() / frame.len() as f32)
        .collect()
}

impl AudioSource for WavFileSource {
    fn load(&self) -> Result<AudioBuffer> {
        // Open the file; a missing/unreadable file is a capture-layer failure.
        let mut reader = hound::WavReader::open(&self.path)
            .map_err(|e| CoreError::Capture(format!("open {}: {e}", self.path.display())))?;
        let spec = reader.spec();

        // Normalize every sample to f32 in [-1.0, 1.0], regardless of on-disk encoding.
        let interleaved: Vec<f32> = match spec.sample_format {
            // Float WAV is already in range; just collect it.
            hound::SampleFormat::Float => reader
                .samples::<f32>()
                .collect::<std::result::Result<Vec<f32>, _>>()
                .map_err(|e| CoreError::Capture(format!("decode float wav: {e}")))?,
            // Integer PCM: divide by the full-scale value for this bit depth.
            hound::SampleFormat::Int => {
                // Full-scale magnitude for signed N-bit PCM is 2^(N-1).
                let full_scale = (1i64 << (spec.bits_per_sample - 1)) as f32;
                reader
                    .samples::<i32>()
                    .map(|s| s.map(|v| v as f32 / full_scale))
                    .collect::<std::result::Result<Vec<f32>, _>>()
                    .map_err(|e| CoreError::Capture(format!("decode int wav: {e}")))?
            }
        };

        // Collapse to mono so the diarization/ASR front-end sees a single channel.
        let mono = downmix_to_mono(&interleaved, spec.channels);

        // Require the canonical rate: resampling is deliberately not wired here (it
        // belongs to the ffmpeg/symphonia adapter, M3), so we fail loudly rather than
        // hand the engines a wrong-rate buffer.
        if spec.sample_rate != PIPELINE_SAMPLE_RATE {
            return Err(CoreError::Capture(format!(
                "expected {PIPELINE_SAMPLE_RATE} Hz mono WAV, got {} Hz — resampling not wired yet",
                spec.sample_rate
            )));
        }

        Ok(AudioBuffer::new(PIPELINE_SAMPLE_RATE, mono))
    }
}
